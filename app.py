"""dv365-downloader: a loopback-only web interface for yt-dlp."""
from __future__ import annotations

import argparse
import atexit
import errno
import importlib.metadata
import json
import os
import secrets
import shutil
import threading
import time
import webbrowser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from downloader import DownloadManager, TaskError, open_directory

BASE_DIR = Path(__file__).resolve().parent


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def port_in_use(error: OSError) -> bool:
    """Recognize address conflicts on POSIX and Windows."""
    # Windows can report an occupied or reserved local port as WSAEACCES (10013).
    return error.errno == errno.EADDRINUSE or getattr(error, "winerror", None) in {10048, 10013}


def recorded_address(data_dir: Path) -> str | None:
    """Find the last bound port so repeated launches also work after a fallback."""
    try:
        port = int((data_dir / "server.port").read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return f"http://127.0.0.1:{port}" if 1 <= port <= 65535 else None


def running_instance(address: str, data_dir: Path) -> bool:
    """Recognize our local service, never a proxy or unrelated port listener."""
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(address + "/api/config", timeout=0.8) as response:
            config = json.loads(response.read(65536))
        if not isinstance(config, dict):
            return False
        if config.get("app_id") == "dv365-downloader":
            directory = config.get("data_dir")
            return isinstance(directory, str) and Path(directory).resolve() == data_dir.resolve()
        # A previous release may still be running with a different brand identifier.
        # Match its data directory and updated page before reusing that instance.
        if "data_dir" in config:
            directory = config["data_dir"]
            if not isinstance(directory, str) or Path(directory).resolve() != data_dir.resolve():
                return False
        elif ("app_id" in config or data_dir.resolve() != (BASE_DIR / ".data").resolve()
              or config.get("download_dir") != str(BASE_DIR / "downloads")):
            return False
        if (not isinstance(config.get("download_dir"), str)
                or not isinstance(config.get("ffmpeg"), bool)
                or not isinstance(config.get("yt_dlp_version"), str)
                or not isinstance(config.get("csrf_token"), str)):
            return False
        with opener.open(address + "/", timeout=0.8) as response:
            page = response.read(65536).decode("utf-8")
        return "<title>音视频下载 dv365-downloader · 本地视频下载</title>" in page and '/static/app.js' in page
    except HTTPError as exc:
        exc.close()
        return False
    except (OSError, ValueError, UnicodeError):
        return False


def create_app(data_dir: Path | None = None, start_worker: bool = True):
    app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
    app.config["MAX_CONTENT_LENGTH"] = 16384
    custom_dir = data_dir or os.environ.get("DV365_DOWNLOADER_DATA_DIR")
    manager = DownloadManager(Path(custom_dir) if custom_dir else BASE_DIR / ".data",
                              start_worker=start_worker,
                              download_dir=None if custom_dir else BASE_DIR / "downloads")
    app.config["MANAGER"] = manager
    token = secrets.token_urlsafe(32)
    try:
        version = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        version = "未安装"

    def config():
        return dict(download_dir=str(manager.download_dir), ffmpeg=bool(shutil.which("ffmpeg")),
                    yt_dlp_version=version, csrf_token=token, app_id="dv365-downloader",
                    data_dir=str(manager.data_dir))

    def payload():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise TaskError("请求内容必须是 JSON 对象。")
        return body

    @app.before_request
    def local_only():
        try:
            host = urlsplit("http://" + request.host).hostname
        except ValueError:
            host = None
        if host not in {"localhost", "127.0.0.1", "::1"}:
            raise TaskError("此应用仅允许从本机访问。", 403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("Origin")
            if origin and origin != request.host_url.rstrip("/"):
                raise TaskError("已拒绝来自其他网页的请求。", 403)
            supplied = request.headers.get("X-App-Token", "")
            if not secrets.compare_digest(supplied, token):
                raise TaskError("页面会话已失效，请刷新页面后重试。", 403)

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' https: http: data:; connect-src 'self'; "
            "object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.errorhandler(TaskError)
    def task_error(error):
        return jsonify(error=str(error)), error.status

    @app.errorhandler(HTTPException)
    def http_error(error):
        return jsonify(error=error.description), error.code

    @app.errorhandler(OSError)
    def storage_error(error):
        app.logger.exception("Local filesystem operation failed")
        return jsonify(error="无法访问本地文件或目录，请检查路径、磁盘空间和权限。"), 400

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/config")
    def get_config():
        return jsonify(config())

    @app.patch("/api/config")
    def set_config():
        manager.set_download_dir(payload().get("download_dir"))
        return jsonify(config())

    @app.get("/api/tasks")
    def tasks():
        return jsonify(tasks=manager.list_tasks())

    @app.post("/api/tasks")
    def create_task():
        body = payload()
        return jsonify(manager.create_task(body.get("url"), body.get("quality", "best"),
                                           kind=body.get("kind", "video"),
                                           include_subpages=body.get("include_subpages", False))), 201

    @app.post("/api/tasks/<task_id>/cancel")
    def cancel_task(task_id):
        return jsonify(manager.cancel_task(task_id))

    @app.post("/api/tasks/<task_id>/retry")
    def retry_task(task_id):
        return jsonify(manager.retry_task(task_id))

    @app.post("/api/tasks/<task_id>/open")
    def open_task(task_id):
        task = manager.get_task(task_id)
        path = Path(task["output_dir"])
        open_directory(path if path.is_dir() else path.parent)
        return jsonify(ok=True)

    @app.get("/api/tasks/<task_id>/file")
    def task_file(task_id):
        return send_file(manager.completed_file(task_id), as_attachment=True)

    @app.post("/api/open-folder")
    def open_folder():
        open_directory(manager.download_dir)
        return jsonify(ok=True)

    return app


def main():
    parser = argparse.ArgumentParser(description="音视频下载 dv365-downloader · 本地视频下载器")
    parser.add_argument("--port", type=int, default=8765,
                        help="首选本机端口（默认 8765；被占用时自动选择可用端口）")
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1–65535 之间")
    from waitress import create_server

    preferred_address = f"http://127.0.0.1:{args.port}"
    try:
        app = create_app()
    except TaskError as exc:
        if exc.status == 409:
            data_dir = Path(os.environ.get("DV365_DOWNLOADER_DATA_DIR") or BASE_DIR / ".data")
            # Another launcher may have acquired the lock just before binding HTTP.
            for attempt in range(4):
                addresses = [preferred_address]
                fallback_address = recorded_address(data_dir)
                if fallback_address and fallback_address != preferred_address:
                    addresses.append(fallback_address)
                for address in addresses:
                    if running_instance(address, data_dir):
                        print(f"dv365-downloader is already running: {address}", flush=True)
                        if not args.no_browser:
                            webbrowser.open(address)
                        return
                if attempt < 3:
                    time.sleep(0.15)
        parser.exit(1, f"无法启动 dv365-downloader：{exc}\n")
    except OSError as exc:
        parser.exit(1, f"无法启动 dv365-downloader：{exc}\n")
    manager = app.config["MANAGER"]
    atexit.register(manager.shutdown)
    try:
        try:
            server = create_server(app, host="127.0.0.1", port=args.port, threads=6)
        except OSError as exc:
            if not port_in_use(exc):
                raise
            print(f"Port {args.port} is already in use; selecting an available port automatically.",
                  flush=True)
            server = create_server(app, host="127.0.0.1", port=0, threads=6)
    except OSError as exc:
        manager.shutdown()
        parser.exit(1, f"无法启动 dv365-downloader：{exc}\n")

    actual_port = server.effective_port
    address = f"http://127.0.0.1:{actual_port}"
    port_file = manager.data_dir / "server.port"
    try:
        port_file.write_text(f"{actual_port}\n", encoding="ascii")
    except OSError as exc:
        print(f"Warning: could not record the active port for repeated launches: {exc}", flush=True)
    print(f"\n  dv365-downloader: {address}\n  Downloads: {manager.download_dir}\n  Ctrl+C to stop.\n", flush=True)
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(address)).start()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()
        try:
            if port_file.read_text(encoding="ascii").strip() == str(actual_port):
                port_file.unlink()
        except OSError:
            pass
        manager.shutdown()


if __name__ == "__main__":
    main()
