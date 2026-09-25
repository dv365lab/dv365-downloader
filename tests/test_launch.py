"""Repeated launch should reopen the app without disturbing its download queue."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from werkzeug.serving import WSGIRequestHandler, make_server

import app as application


class QuietHandler(WSGIRequestHandler):
    def log(self, *args, **kwargs):
        pass


class RepeatedLaunchTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="dv365-downloader-launch-")
        self.data_dir = Path(self.temporary.name)
        self.app = application.create_app(self.data_dir, start_worker=False)
        self.manager = self.app.config["MANAGER"]
        self.server = make_server("127.0.0.1", 0, self.app, request_handler=QuietHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.manager.shutdown()
        self.temporary.cleanup()

    def test_repeated_launch_opens_existing_app_and_preserves_tasks(self):
        running = self.manager.create_task("https://example.com/running")
        self.manager.update_task(running["id"], status="downloading", progress=32)
        self.manager.create_task("https://example.com/queued")
        before = self.manager.list_tasks()
        database_before = (self.data_dir / "downloads.db").read_bytes()
        with patch.dict(os.environ, {"DV365_DOWNLOADER_DATA_DIR": str(self.data_dir)}), \
                patch("sys.argv", ["app.py", "--port", str(self.port)]), \
                patch("app.webbrowser.open", return_value=True) as browser, \
                patch("waitress.create_server") as create_server, \
                contextlib.redirect_stdout(io.StringIO()):
            application.main()
        browser.assert_called_once_with(f"http://127.0.0.1:{self.port}")
        create_server.assert_not_called()
        self.assertEqual(self.manager.list_tasks(), before)
        self.assertEqual((self.data_dir / "downloads.db").read_bytes(), database_before)

    def test_repeated_launch_respects_no_browser(self):
        with patch.dict(os.environ, {"DV365_DOWNLOADER_DATA_DIR": str(self.data_dir)}), \
                patch("sys.argv", ["app.py", "--port", str(self.port), "--no-browser"]), \
                patch("app.webbrowser.open") as browser, \
                contextlib.redirect_stdout(io.StringIO()):
            application.main()
        browser.assert_not_called()


class InstanceRecognitionTests(unittest.TestCase):
    def probe(self, config, data_dir, page=""):
        opener = Mock()
        opener.open.side_effect = [io.BytesIO(json.dumps(config).encode()), io.BytesIO(page.encode())]
        with patch("app.build_opener", return_value=opener):
            return application.running_instance("http://127.0.0.1:8765", data_dir)

    def test_different_data_directory_is_not_reused(self):
        directory = application.BASE_DIR / ".data"
        self.assertFalse(self.probe({"app_id": "dv365-downloader", "data_dir": str(directory / "other")}, directory))

    def test_unrelated_server_is_not_reused(self):
        self.assertFalse(self.probe({"status": "ok"}, application.BASE_DIR / ".data"))

    def test_previous_brand_instance_uses_directory_and_current_page_to_identify_itself(self):
        directory = application.BASE_DIR / ".data"
        config = dict(app_id="previous-release", data_dir=str(directory),
                      download_dir=str(application.BASE_DIR / "downloads"), ffmpeg=True,
                      yt_dlp_version="2026.3.17", csrf_token="local-token")
        page = '<title>音视频下载 dv365-downloader · 本地视频下载</title><script src="/static/app.js"></script>'
        self.assertTrue(self.probe(config, directory, page))
        self.assertFalse(self.probe(config, directory, "<title>Another app</title>"))
        config["data_dir"] = str(directory / "other")
        self.assertFalse(self.probe(config, directory, page))

    def test_legacy_running_version_can_be_reused_without_restart(self):
        config = dict(download_dir=str(application.BASE_DIR / "downloads"), ffmpeg=True,
                      yt_dlp_version="2026.3.17", csrf_token="local-token")
        self.assertTrue(self.probe(config, application.BASE_DIR / ".data",
                                  '<title>音视频下载 dv365-downloader · 本地视频下载</title><script src="/static/app.js"></script>'))
        self.assertFalse(self.probe(config, application.BASE_DIR / ".data", "<title>Another app</title>"))

    def test_invalid_json_and_unavailable_server_are_not_reused(self):
        for result in (io.BytesIO(b"not JSON"), OSError("connection refused")):
            with self.subTest(result=result):
                opener = Mock()
                opener.open.side_effect = [result]
                with patch("app.build_opener", return_value=opener):
                    self.assertFalse(application.running_instance("http://127.0.0.1:8765", application.BASE_DIR))

    def test_other_startup_errors_do_not_open_a_running_app(self):
        for error in (application.TaskError("bad directory"), OSError("permission denied")):
            with self.subTest(error=error), \
                    patch("sys.argv", ["app.py"]), \
                    patch("app.create_app", side_effect=error), \
                    patch("app.running_instance") as probe, \
                    patch("app.webbrowser.open") as browser, \
                    contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as stopped:
                    application.main()
                self.assertEqual(stopped.exception.code, 1)
                probe.assert_not_called()
                browser.assert_not_called()

    def test_locked_but_unavailable_instance_keeps_a_clear_error(self):
        with patch("sys.argv", ["app.py"]), \
                patch("app.create_app", side_effect=application.TaskError("already running", 409)), \
                patch("app.running_instance", return_value=False), \
                patch("app.time.sleep"), \
                patch("app.webbrowser.open") as browser, \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as stopped:
                application.main()
            self.assertEqual(stopped.exception.code, 1)
            browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
