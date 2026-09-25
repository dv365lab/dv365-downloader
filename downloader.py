"""Persistent local download queue with isolated, cancellable yt-dlp processes."""
from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ACTIVE = {"queued", "extracting", "downloading", "processing"}
QUALITIES = {"best", "1080", "720", "audio"}


class TaskError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def validate_url(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 8192:
        raise TaskError("请粘贴一个有效的视频网址。")
    value = value.strip()
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        parsed.port
        if (parsed.scheme not in {"http", "https"} or not host or parsed.username
                or parsed.password or re.search(r"[\s\x00-\x1f\x7f]", value)
                or any(c in host for c in '\\<>"{}|^`')):
            raise ValueError
        host.encode("idna")
    except (ValueError, UnicodeError):
        raise TaskError("网址必须以 http:// 或 https:// 开头，且不能包含空格或账号密码。") from None
    return value


def numeric(value):
    return value if isinstance(value, (int, float)) and math.isfinite(value) else None


def open_directory(path: Path):
    if not path.is_dir():
        raise TaskError("下载目录已被移动或删除。", 404)
    if os.name == "nt":
        os.startfile(str(path))
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(path)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class DownloadManager:
    def __init__(self, data_dir: Path, start_worker: bool = True, download_dir: Path | None = None):
        self.data_dir = Path(data_dir).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # Lock before reading history: two launchers must not resume the same queue.
        self.instance_lock = (self.data_dir / "instance.lock").open("a+b")
        if self.instance_lock.tell() == 0:
            self.instance_lock.write(b"0")
            self.instance_lock.flush()
        self.instance_lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.instance_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.instance_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.instance_lock.close()
            raise TaskError("应用已经在运行，请打开已有页面，或先关闭之前的应用窗口。", 409) from None
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.data_dir / "downloads.db", check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
        self.db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.tasks = {row[0]: json.loads(row[1]) for row in self.db.execute("SELECT id, body FROM tasks")}
        setting = self.db.execute("SELECT value FROM settings WHERE key='download_dir'").fetchone()
        self.download_dir = Path(setting[0]) if setting else (download_dir or self.data_dir / "downloads")
        if not setting:
            self.download_dir.mkdir(parents=True, exist_ok=True)
        self.pending = queue.Queue()
        self.stopping = threading.Event()
        self.processes = {}
        self.worker = None
        self.closed = False
        for task in self.tasks.values():
            if task["status"] in ACTIVE - {"queued"}:
                self.update_task(task["id"], status="failed", speed=None, eta=None,
                                 error="上次运行已中断，可以点击重试继续下载。")
        for task in sorted(self.tasks.values(), key=lambda item: item["created_at"]):
            if task["status"] == "queued":
                self.pending.put(task["id"])
        if start_worker:
            self.worker = threading.Thread(target=self._work, name="download-worker", daemon=True)
            self.worker.start()

    def _save(self, task):
        self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?, ?)",
                        (task["id"], json.dumps(task, ensure_ascii=False)))
        self.db.commit()

    def list_tasks(self):
        with self.lock:
            return [dict(t) for t in sorted(self.tasks.values(), key=lambda t: t["created_at"], reverse=True)]

    def get_task(self, task_id):
        with self.lock:
            if task_id not in self.tasks:
                raise TaskError("没有找到这个下载任务。", 404)
            return dict(self.tasks[task_id])

    def update_task(self, task_id, **fields):
        with self.lock:
            task = self.get_task(task_id)
            task.update(fields, updated_at=now())
            self._save(task)
            self.tasks[task_id] = task
            return dict(task)

    def create_task(self, url, quality="best"):
        url = validate_url(url)
        if not isinstance(quality, str) or quality not in QUALITIES:
            raise TaskError("请选择有效的下载画质。")
        if quality == "audio" and not shutil.which("ffmpeg"):
            raise TaskError("下载 MP3 需要 FFmpeg，请先安装并重新启动应用。")
        with self.lock:
            if any(t["url"] == url and t["status"] in ACTIVE for t in self.tasks.values()):
                raise TaskError("这个网址已经在下载队列中。", 409)
            task_id = uuid.uuid4().hex
            stamp = now()
            task = dict(id=task_id, url=url, title=urlsplit(url).hostname, quality=quality,
                        status="queued", progress=None, downloaded_bytes=0, total_bytes=None,
                        speed=None, eta=None, error=None, filename=None, filepath=None,
                        output_dir=str(self.download_dir / task_id[:12]),
                        created_at=stamp, updated_at=stamp, thumbnail=None)
            self._save(task)
            self.tasks[task_id] = task
            self.pending.put(task_id)
            return dict(task)

    def set_download_dir(self, value):
        if not isinstance(value, str) or not value.strip():
            raise TaskError("请输入完整的本地文件夹路径。")
        path = Path(value.strip()).expanduser()
        if not path.is_absolute():
            raise TaskError("请使用绝对路径，例如 F:\\Videos。")
        try:
            path = path.resolve()
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=path):
                pass
        except (OSError, ValueError):
            raise TaskError("这个目录无法写入，请检查路径和访问权限。") from None
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('download_dir', ?)", (str(path),))
            self.db.commit()
            self.download_dir = path

    def cancel_task(self, task_id):
        with self.lock:
            task = self.get_task(task_id)
            if task["status"] not in ACTIVE:
                raise TaskError("只有正在排队或下载的任务可以取消。", 409)
            result = self.update_task(task_id, status="cancelled", speed=None, eta=None, error=None)
            proc = self.processes.get(task_id)
            if proc:
                self._terminate(proc)
            return result

    def retry_task(self, task_id):
        with self.lock:
            task = self.get_task(task_id)
            if task["status"] not in {"failed", "cancelled"}:
                raise TaskError("只有失败或取消的任务可以重试。", 409)
            if task_id in self.processes:
                raise TaskError("正在结束上一次下载，请稍后重试。", 409)
            if any(t["id"] != task_id and t["url"] == task["url"] and t["status"] in ACTIVE
                   for t in self.tasks.values()):
                raise TaskError("这个网址已经在下载队列中。", 409)
            result = self.update_task(task_id, status="queued", error=None, progress=None,
                                      speed=None, eta=None, downloaded_bytes=0, total_bytes=None,
                                      filename=None, filepath=None)
            self.pending.put(task_id)
            return result

    def completed_file(self, task_id):
        task = self.get_task(task_id)
        if task["status"] != "completed" or not task.get("filepath"):
            raise TaskError("这个任务还没有可用的下载文件。", 409)
        path = Path(task["filepath"]).resolve()
        root = Path(task["output_dir"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise TaskError("文件已被移动或删除，请检查下载目录。", 404)
        return path

    def build_command(self, task):
        command = [sys.executable, "-m", "yt_dlp", "--ignore-config", "--no-plugin-dirs",
                   "--no-playlist", "--playlist-items", "1", "--no-simulate", "--newline",
                   "--progress", "--progress-delta", "0.5", "--encoding", "utf-8",
                   "--color", "never", "--socket-timeout", "20", "--retries", "3",
                   "--fragment-retries", "3", "--windows-filenames", "--trim-filenames", "160",
                   "--paths", task["output_dir"], "--output", "%(title).120B [%(id)s].%(ext)s",
                   "--print", "before_dl:VD_META:%(.{title,thumbnail,duration,extractor})j",
                   "--print", "after_move:VD_FILE:%(filepath)j",
                   "--progress-template", "download:VD_PROGRESS:%(progress.{downloaded_bytes,total_bytes,total_bytes_estimate,speed,eta,status})j",
                   "--progress-template", "postprocess:VD_PROCESS:%(progress.status)j"]
        if shutil.which("node"):
            command += ["--js-runtimes", "node"]
        quality = task["quality"]
        if quality == "audio":
            command += ["-f", "bestaudio/best", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
        else:
            cap = f"[height<=?{quality}]" if quality in {"1080", "720"} else ""
            fmt = f"bv*{cap}+ba/b{cap}" if shutil.which("ffmpeg") else f"b{cap}"
            command += ["-f", fmt, "--merge-output-format", "mp4/mkv"]
        return command + ["--", task["url"]]

    def _work(self):
        while not self.stopping.is_set():
            try:
                task_id = self.pending.get(timeout=0.3)
            except queue.Empty:
                continue
            try:
                self._download(task_id)
            finally:
                self.pending.task_done()

    def _download(self, task_id):
        proc = None
        tail = deque(maxlen=12)
        final_path = None
        try:
            with self.lock:
                task = self.get_task(task_id)
                if task["status"] != "queued" or self.stopping.is_set():
                    return
                self.update_task(task_id, status="extracting")
                Path(task["output_dir"]).mkdir(parents=True, exist_ok=True)
                kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
                proc = subprocess.Popen(self.build_command(task), stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                        errors="replace", bufsize=1, **kwargs)
                self.processes[task_id] = proc
            for raw_line in proc.stdout:
                line = raw_line.strip()
                with self.lock:
                    if self.tasks[task_id]["status"] == "cancelled" or self.stopping.is_set():
                        continue
                    if line.startswith("VD_META:"):
                        meta = json.loads(line[8:])
                        self.update_task(task_id, title=meta.get("title") or task["title"],
                                         thumbnail=meta.get("thumbnail"), status="downloading")
                    elif line.startswith("VD_PROGRESS:"):
                        progress = json.loads(line[12:])
                        downloaded = numeric(progress.get("downloaded_bytes")) or 0
                        total = numeric(progress.get("total_bytes")) or numeric(progress.get("total_bytes_estimate"))
                        self.update_task(task_id, status="downloading", downloaded_bytes=downloaded,
                                         total_bytes=total, progress=min(99.9, downloaded / total * 100) if total else None,
                                         speed=numeric(progress.get("speed")), eta=numeric(progress.get("eta")))
                    elif line.startswith("VD_PROCESS:"):
                        self.update_task(task_id, status="processing", progress=None, speed=None, eta=None)
                    elif line.startswith("VD_FILE:"):
                        final_path = json.loads(line[8:])
                    elif line:
                        tail.append(line)
            returncode = proc.wait()
            with self.lock:
                if self.tasks[task_id]["status"] == "cancelled" or self.stopping.is_set():
                    return
                path = Path(final_path).resolve() if isinstance(final_path, str) else None
                if returncode != 0 or path is None:
                    detail = next((line for line in reversed(tail) if line.startswith("ERROR:")), None)
                    if (detail and "Requested format is not available" in detail
                            and task["quality"] != "audio" and not shutil.which("ffmpeg")):
                        detail = ("未找到 FFmpeg，当前只能下载自带音轨的视频格式。"
                                  "请安装 FFmpeg 并重新启动应用，再点击“重新下载”，"
                                  "以支持合并分离的音视频流。\n原始错误：" + detail)
                    raise TaskError(detail or ("\n".join(tail)[-1500:] if tail else "下载未生成文件，请检查链接是否受支持。"))
                if not path.is_relative_to(Path(task["output_dir"]).resolve()) or not path.is_file():
                    raise TaskError("下载结束，但没有在目标目录找到文件。")
                self.update_task(task_id, status="completed", progress=100, speed=None, eta=None,
                                 error=None, filepath=str(path), filename=path.name,
                                 downloaded_bytes=path.stat().st_size, total_bytes=path.stat().st_size)
        except Exception as exc:
            with self.lock:
                if self.tasks[task_id]["status"] != "cancelled" and not self.stopping.is_set():
                    self.update_task(task_id, status="failed", error=str(exc)[-2000:], speed=None, eta=None)
        finally:
            if proc:
                if proc.poll() is None:
                    self._terminate(proc)
                proc.wait()
                proc.stdout.close()
            with self.lock:
                self.processes.pop(task_id, None)

    @staticmethod
    def _terminate(proc):
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if proc.poll() is None:
            proc.kill()

    def shutdown(self):
        if self.closed:
            return
        self.stopping.set()
        with self.lock:
            for proc in self.processes.values():
                self._terminate(proc)
        if self.worker:
            self.worker.join(timeout=15)
        with self.lock:
            if not self.worker or not self.worker.is_alive():
                self.db.close()
                self.instance_lock.close()
                self.closed = True
