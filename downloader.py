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
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pdf_collect

ACTIVE = {"queued", "extracting", "downloading", "processing"}
QUALITIES = {"best", "1080", "720", "audio"}
KINDS = {"video", "pdf"}
PROGRESS_INTERVAL = 0.5


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


def _folder_slug(url):
    """A readable, filesystem-safe folder prefix for a PDF collection task."""
    host = urlsplit(url).hostname or "page"
    slug = re.sub(r"[^0-9A-Za-z._-]+", "-", host).strip("-")[:40]
    return slug or "page"


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
        self.cancel_events = {}
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
            return [self._public_task(t)
                    for t in sorted(self.tasks.values(), key=lambda t: t["created_at"], reverse=True)]

    @staticmethod
    def _public_task(task):
        """Trim a task for the UI: finished files are only counted, not listed."""
        view = dict(task)
        if task.get("kind") == "pdf":
            view["files"] = [dict(item) for item in task.get("files", [])
                             if item.get("status") != "completed"]
        return view

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

    def create_task(self, url, quality="best", kind="video", include_subpages=False):
        url = validate_url(url)
        if not isinstance(kind, str) or kind not in KINDS:
            raise TaskError("请选择有效的下载类型。")
        if kind == "pdf":
            if include_subpages not in (True, False):
                raise TaskError("子页面选项无效。")
            quality = "pdf"
        else:
            include_subpages = False
            if not isinstance(quality, str) or quality not in QUALITIES:
                raise TaskError("请选择有效的下载画质。")
            if quality == "audio" and not shutil.which("ffmpeg"):
                raise TaskError("下载 MP3 需要 FFmpeg，请先安装并重新启动应用。")
        with self.lock:
            if any(t["url"] == url and t.get("kind", "video") == kind and t["status"] in ACTIVE
                   for t in self.tasks.values()):
                raise TaskError("这个网址已经在下载队列中。", 409)
            task_id = uuid.uuid4().hex
            stamp = now()
            folder = f"{_folder_slug(url)}-{task_id[:8]}" if kind == "pdf" else task_id[:12]
            task = dict(id=task_id, kind=kind, url=url, title=urlsplit(url).hostname, quality=quality,
                        status="queued", progress=None, downloaded_bytes=0, total_bytes=None,
                        speed=None, eta=None, error=None, filename=None, filepath=None,
                        output_dir=str(self.download_dir / folder),
                        created_at=stamp, updated_at=stamp, thumbnail=None)
            if kind == "pdf":
                task.update(include_subpages=include_subpages, files=[], file_count=0,
                            completed_count=0, failed_count=0, pages_scanned=0)
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
            event = self.cancel_events.get(task_id)
            if event:
                event.set()
            proc = self.processes.get(task_id)
            if proc:
                self._terminate(proc)
            return result

    def retry_task(self, task_id):
        with self.lock:
            task = self.get_task(task_id)
            partial = task.get("kind") == "pdf" and task.get("failed_count")
            if task["status"] not in {"failed", "cancelled"} and not partial:
                raise TaskError("只有失败或取消的任务可以重试。", 409)
            if task_id in self.processes:
                raise TaskError("正在结束上一次下载，请稍后重试。", 409)
            if any(t["id"] != task_id and t["url"] == task["url"] and t["status"] in ACTIVE
                   for t in self.tasks.values()):
                raise TaskError("这个网址已经在下载队列中。", 409)
            fields = dict(status="queued", error=None, progress=None, speed=None, eta=None,
                          downloaded_bytes=0, total_bytes=None, filename=None, filepath=None)
            result = self.update_task(task_id, **fields)
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
        with self.lock:
            task = self.get_task(task_id)
        if task.get("kind") == "pdf":
            self._collect_pdfs(task_id)
        else:
            self._download_media(task_id)

    def _download_media(self, task_id):
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

    def _fail_task(self, task_id, message):
        with self.lock:
            if self.tasks[task_id]["status"] == "cancelled" or self.stopping.is_set():
                return
            self.update_task(task_id, status="failed", error=str(message)[-2000:], speed=None, eta=None)

    def _collect_pdfs(self, task_id):
        event = threading.Event()
        try:
            with self.lock:
                task = self.get_task(task_id)
                if task["status"] != "queued" or self.stopping.is_set():
                    return
                self.cancel_events[task_id] = event
                output_dir = Path(task["output_dir"])
                output_dir.mkdir(parents=True, exist_ok=True)
                self.update_task(task_id, status="extracting", progress=None, speed=None, eta=None, error=None)
            self._run_pdf_collection(task_id, task, output_dir, event)
        except pdf_collect.Cancelled:
            return
        except TaskError as exc:
            self._fail_task(task_id, str(exc))
        except pdf_collect.FetchError as exc:
            self._fail_task(task_id, f"无法读取网页：{exc}")
        except Exception as exc:
            self._fail_task(task_id, f"抓取失败：{exc}")
        finally:
            with self.lock:
                self.cancel_events.pop(task_id, None)

    def _run_pdf_collection(self, task_id, task, output_dir, event):
        def stop():
            return event.is_set() or self.stopping.is_set()

        url = task["url"]
        allow_private = pdf_collect.is_private_host(urlsplit(url).hostname)
        title, links, scanned = pdf_collect.discover(
            url, include_subpages=bool(task.get("include_subpages")), should_stop=stop)
        if stop():
            raise pdf_collect.Cancelled
        if not links:
            raise TaskError("这个网页里没有找到 PDF 链接，请确认地址指向一个包含 PDF 的页面。")

        # Anything already on disk is kept, so a retry only fetches the gaps.
        # A partial download never survives (files are renamed into place), which
        # makes the presence of a named PDF a safe completion signal.
        previous = {item.get("url"): item for item in task.get("files", [])}
        files, taken = [], set()
        for link in links:
            known = previous.get(link) or {}
            name = known.get("name") or pdf_collect.filename_for(link)
            existing = output_dir / name
            if name not in taken and existing.is_file():
                files.append(dict(url=link, name=name, status="completed",
                                  size=existing.stat().st_size, error=None))
                taken.add(name)
            else:
                files.append(dict(url=link, name=pdf_collect.filename_for(link),
                                  status="pending", size=None, error=None))
        total = len(files)
        downloaded = sum(item.get("size") or 0 for item in files if item["status"] == "completed")
        completed = sum(1 for item in files if item["status"] == "completed")
        failed = 0
        started = time.monotonic()
        last_push = [0.0]
        with self.lock:
            self.update_task(task_id, status="downloading", title=title or task["title"],
                             file_count=total, completed_count=completed, failed_count=0,
                             files=[dict(item) for item in files], pages_scanned=scanned,
                             progress=completed / total * 100 if completed else 0,
                             downloaded_bytes=downloaded, total_bytes=None,
                             speed=None, eta=None, error=None)

        def push(current_bytes, partial):
            stamp = time.monotonic()
            if partial and stamp - last_push[0] < PROGRESS_INTERVAL:
                return
            last_push[0] = stamp
            elapsed = max(0.001, stamp - started)
            finished = completed + failed
            self.update_task(task_id, files=[dict(item) for item in files],
                             completed_count=completed, failed_count=failed,
                             progress=min(99.9, (finished + (0.5 if partial else 0)) / total * 100),
                             downloaded_bytes=downloaded + (current_bytes if partial else 0),
                             speed=downloaded / elapsed if downloaded else None,
                             eta=(elapsed / finished) * (total - finished) if finished else None)

        for item in files:
            if item["status"] == "completed":
                continue
            if stop():
                raise pdf_collect.Cancelled
            destination = pdf_collect.unique_path(output_dir, item["name"], taken)
            taken.add(destination.name)
            item["name"] = destination.name
            item["status"] = "downloading"
            item["error"] = None
            try:
                _, size = pdf_collect.download_pdf(
                    item["url"], destination, allow_private=allow_private, should_stop=stop,
                    on_progress=lambda current: push(current, True))
            except pdf_collect.Cancelled:
                raise
            except pdf_collect.FetchError as exc:
                failed += 1
                item["status"] = "failed"
                item["error"] = str(exc)[:300]
            else:
                completed += 1
                downloaded += size
                item["status"] = "completed"
                item["size"] = size
            push(0, False)

        if stop():
            raise pdf_collect.Cancelled
        names = "、".join(item["name"] for item in files if item["status"] == "failed")[:200]
        error = f"{failed} 个文件下载失败：{names}" if failed else None
        if len(links) >= pdf_collect.MAX_PDF_FILES:
            note = f"页面 PDF 数量达到上限 {pdf_collect.MAX_PDF_FILES} 个，可能还有链接未被抓取。"
            error = f"{error}；{note}" if error else note
        saved = [output_dir / item["name"] for item in files if item["status"] == "completed"]
        with self.lock:
            self.update_task(task_id, status="completed" if completed else "failed",
                             files=[dict(item) for item in files],
                             completed_count=completed, failed_count=failed,
                             progress=100 if completed else None, speed=None, eta=None,
                             downloaded_bytes=downloaded, error=error,
                             filepath=str(saved[0]) if len(saved) == 1 else None,
                             filename=saved[0].name if len(saved) == 1 else None)

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
            for event in self.cancel_events.values():
                event.set()
            for proc in self.processes.values():
                self._terminate(proc)
        if self.worker:
            self.worker.join(timeout=15)
        with self.lock:
            if not self.worker or not self.worker.is_alive():
                self.db.close()
                self.instance_lock.close()
                self.closed = True
