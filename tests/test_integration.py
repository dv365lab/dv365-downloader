"""Opt-in smoke test through real HTTP, yt-dlp and FFmpeg (no public site dependency)."""
import functools
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from app import create_app


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@unittest.skipUnless(os.environ.get("VIDEODOWN_INTEGRATION") == "1", "Set VIDEODOWN_INTEGRATION=1 for real yt-dlp/FFmpeg smoke test")
class RealDownloadTests(unittest.TestCase):
    def test_video_download_and_mp3_conversion(self):
        self.assertIsNotNone(shutil.which("ffmpeg"), "FFmpeg is required for this integration test")
        self.assertIsNotNone(shutil.which("ffprobe"))
        with tempfile.TemporaryDirectory(prefix="videodown-real-") as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "testsrc=size=320x180:rate=24", "-f", "lavfi", "-i", "sine=frequency=440",
                "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                "-movflags", "+faststart", str(source / "demo.mp4")
            ], check=True, timeout=30, capture_output=True)
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(source)))
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            app = create_app(root / "app-data")
            client = app.test_client()
            manager = app.config["MANAGER"]
            token = client.get("/api/config").get_json()["csrf_token"]
            try:
                for quality, extension in (("720", ".mp4"), ("audio", ".mp3")):
                    with self.subTest(quality=quality):
                        response = client.post("/api/tasks", json={
                            "url": f"http://127.0.0.1:{httpd.server_port}/demo.mp4", "quality": quality
                        }, headers={"X-App-Token": token})
                        self.assertEqual(response.status_code, 201, response.get_json())
                        task = response.get_json()
                        deadline = time.monotonic() + 45
                        while time.monotonic() < deadline:
                            task = manager.get_task(task["id"])
                            if task["status"] in {"completed", "failed", "cancelled"}:
                                break
                            time.sleep(0.1)
                        self.assertEqual(task["status"], "completed", task)
                        path = Path(task["filepath"])
                        self.assertEqual(path.suffix, extension)
                        self.assertGreater(path.stat().st_size, 1000)
                        result = subprocess.run([
                            "ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type",
                            "-of", "json", str(path)
                        ], check=True, capture_output=True, text=True, timeout=15)
                        media = json.loads(result.stdout)
                        self.assertGreater(float(media["format"]["duration"]), 1.5)
                        types = {stream["codec_type"] for stream in media["streams"]}
                        self.assertIn("audio", types)
                        self.assertEqual("video" in types, quality != "audio")
                        with client.get(f"/api/tasks/{task['id']}/file") as saved:
                            self.assertEqual(saved.status_code, 200)
                            self.assertEqual(saved.data, path.read_bytes())
            finally:
                manager.shutdown()
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
