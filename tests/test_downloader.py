"""Offline subprocess integration tests for the download worker.

A real, temporary Python child uses the same output protocol as yt-dlp. Tests
therefore cover process lifecycle and queue behavior without external websites.
"""

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from yt_dlp import YoutubeDL

from downloader import DownloadManager, TaskError


CHILD_SCRIPT = '''
import json
from pathlib import Path
import sys
import time

mode, folder = sys.argv[1:]
output = Path(folder)

def emit(kind, value):
    print("VD_" + kind + ":" + json.dumps(value), flush=True)

def wait_for(name):
    while not (output / name).exists():
        time.sleep(0.01)

if mode == "error":
    print("ordinary diagnostic", flush=True)
    print("ERROR: fixture download denied", flush=True)
    sys.exit(2)
if mode == "format_error":
    print("ERROR: [youtube] fixture: Requested format is not available. Use --list-formats for a list of available formats", flush=True)
    sys.exit(1)
if mode == "missing":
    print("fixture exited without creating media", flush=True)
    sys.exit(0)
if mode == "malformed":
    print("VD_META:{broken-json", flush=True)
    time.sleep(60)
if mode == "outside":
    path = output.parent / "private.txt"
    path.write_bytes(b"private")
    emit("FILE", str(path))
    sys.exit(0)

emit("META", {"title": "Fixture video", "thumbnail": "https://example.com/thumb.jpg"})
emit("PROGRESS", {"downloaded_bytes": 250, "total_bytes": 1000, "speed": 125, "eta": 6})
if mode == "idle":
    time.sleep(60)
if mode == "staged":
    wait_for("allow_processing")
emit("PROCESS", "started")
if mode == "staged":
    wait_for("allow_completion")
path = output / "fixture.mp4"
path.write_bytes(b"fixture-video-bytes")
emit("FILE", str(path))
'''


class WorkerIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dv365-downloader-worker-")
        self.root = Path(self.directory.name)
        self.script = self.root / "child.py"
        self.script.write_text(CHILD_SCRIPT, encoding="utf-8")
        self.manager = DownloadManager(self.root / "data", start_worker=True)
        self.command_patch = patch.object(self.manager, "build_command", side_effect=self.command)
        self.command_patch.start()

    def tearDown(self):
        self.manager.shutdown()
        self.command_patch.stop()
        self.directory.cleanup()

    def command(self, task):
        mode = task["url"].rsplit("/", 1)[-1]
        return [sys.executable, "-u", str(self.script), mode, task["output_dir"]]

    def wait_until(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = predicate()
            if result:
                return result
            threading.Event().wait(0.01)
        self.fail("Worker condition timed out; tasks=" + json.dumps(self.manager.list_tasks()))

    def wait_status(self, task, status):
        def matched():
            current = self.manager.get_task(task["id"])
            return current if current["status"] == status else None
        return self.wait_until(matched)

    def task(self, mode):
        return self.manager.create_task("https://example.com/" + mode, "best")

    def test_live_progress_processing_and_completed_file(self):
        task = self.task("staged")
        current = self.wait_until(
            lambda: (self.manager.get_task(task["id"]) if self.manager.get_task(task["id"])["downloaded_bytes"] == 250 else None)
        )
        self.assertEqual(current["status"], "downloading")
        self.assertEqual(current["title"], "Fixture video")
        self.assertEqual(current["thumbnail"], "https://example.com/thumb.jpg")
        self.assertEqual(current["progress"], 25)
        self.assertEqual(current["total_bytes"], 1000)
        self.assertEqual(current["speed"], 125)
        self.assertEqual(current["eta"], 6)
        output = Path(task["output_dir"])
        (output / "allow_processing").touch()
        processing = self.wait_status(task, "processing")
        self.assertIsNone(processing["progress"])
        self.assertIsNone(processing["speed"])
        (output / "allow_completion").touch()
        done = self.wait_status(task, "completed")
        self.assertEqual(done["progress"], 100)
        self.assertEqual(done["filename"], "fixture.mp4")
        self.assertEqual(Path(done["filepath"]).read_bytes(), b"fixture-video-bytes")
        self.assertEqual(done["downloaded_bytes"], len(b"fixture-video-bytes"))
        self.assertEqual(done["total_bytes"], done["downloaded_bytes"])
        self.assertIsNone(done["error"])

    def test_failed_process_exposes_useful_error_and_queue_continues(self):
        failed = self.task("error")
        next_task = self.task("success")
        result = self.wait_status(failed, "failed")
        self.assertIn("fixture download denied", result["error"])
        self.assertIsNone(result["filepath"])
        self.wait_status(next_task, "completed")

    @patch("downloader.shutil.which", return_value=None)
    def test_unavailable_video_format_explains_missing_ffmpeg(self, _which):
        task = self.task("format_error")
        result = self.wait_status(task, "failed")
        self.assertIn("请安装 FFmpeg", result["error"])
        self.assertIn("重新下载", result["error"])
        self.assertIn("ERROR: [youtube] fixture: Requested format is not available", result["error"])
        self.assertIsNone(result["filepath"])

    @patch("downloader.shutil.which", return_value="fixture-ffmpeg")
    def test_unavailable_format_with_ffmpeg_preserves_original_error(self, _which):
        task = self.task("format_error")
        result = self.wait_status(task, "failed")
        self.assertEqual(result["error"], "ERROR: [youtube] fixture: Requested format is not available. "
                         "Use --list-formats for a list of available formats")

    @patch("downloader.shutil.which", return_value=None)
    def test_unrelated_error_does_not_blame_missing_ffmpeg(self, _which):
        task = self.task("error")
        result = self.wait_status(task, "failed")
        self.assertEqual(result["error"], "ERROR: fixture download denied")

    def test_zero_exit_without_media_is_a_failure(self):
        task = self.task("missing")
        result = self.wait_status(task, "failed")
        self.assertTrue(result["error"])
        self.assertIsNone(result["filepath"])

    def test_child_cannot_report_completed_file_outside_its_directory(self):
        task = self.task("outside")
        result = self.wait_status(task, "failed")
        self.assertTrue(result["error"])
        self.assertIsNone(result["filepath"])

    def test_malformed_child_protocol_is_contained_and_next_task_runs(self):
        failed = self.task("malformed")
        next_task = self.task("success")
        self.wait_status(failed, "failed")
        self.wait_status(next_task, "completed")
        with self.manager.lock:
            self.assertNotIn(failed["id"], self.manager.processes)

    def test_cancel_terminates_live_child_and_queue_continues(self):
        task = self.task("idle")
        self.wait_status(task, "downloading")
        with self.manager.lock:
            child = self.manager.processes[task["id"]]
        self.assertIsNone(child.poll())
        next_task = self.task("success")
        self.manager.cancel_task(task["id"])
        self.wait_until(lambda: child.poll() is not None)
        self.wait_status(next_task, "completed")
        self.assertEqual(self.manager.get_task(task["id"])["status"], "cancelled")
        self.assertIsNotNone(child.returncode)

    def test_shutdown_stops_child_and_persisted_queue_can_recover(self):
        task = self.task("idle")
        self.wait_status(task, "downloading")
        next_task = self.task("success")
        with self.manager.lock:
            child = self.manager.processes[task["id"]]
        self.manager.shutdown()
        self.assertIsNotNone(child.poll())
        self.assertFalse(self.manager.worker.is_alive())
        restored = DownloadManager(self.root / "data", start_worker=False)
        try:
            self.assertEqual(restored.get_task(task["id"])["status"], "failed")
            self.assertEqual(restored.get_task(next_task["id"])["status"], "queued")
        finally:
            restored.shutdown()


class DownloadCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dv365-downloader-command-")
        self.manager = DownloadManager(Path(self.directory.name) / "data", start_worker=False)

    def tearDown(self):
        self.manager.shutdown()
        self.directory.cleanup()

    def test_url_remains_one_argument_after_option_separator(self):
        url = "https://example.com/watch?v=1&next=--exec%20echo%20oops"
        task = self.manager.create_task(url, "best")
        command = self.manager.build_command(task)
        self.assertEqual(command[-2:], ["--", url])
        self.assertEqual(command.count(url), 1)
        self.assertIn("--ignore-config", command)
        self.assertIn("--no-plugin-dirs", command)
        self.assertIn("--no-playlist", command)
        self.assertNotIn("--exec", command)
        self.assertEqual(command[command.index("--paths") + 1], task["output_dir"])

    @patch("downloader.shutil.which", return_value="fixture-tool")
    def test_video_quality_caps_are_in_download_selection(self, _which):
        for quality in ("720", "1080"):
            with self.subTest(quality=quality):
                task = self.manager.create_task("https://example.com/" + quality, quality)
                command = self.manager.build_command(task)
                selection = command[command.index("-f") + 1]
                self.assertIn("height<=?" + quality, selection)
                self.assertIn("+ba", selection)

    @patch("downloader.shutil.which", return_value=None)
    def test_video_fallback_does_not_require_ffmpeg(self, _which):
        task = self.manager.create_task("https://example.com/video", "720")
        command = self.manager.build_command(task)
        selection = command[command.index("-f") + 1]
        self.assertNotIn("+", selection)
        self.assertIn("height<=?720", selection)

    def select_fixture_formats(self, quality, formats):
        task = self.manager.create_task("https://example.com/" + quality, quality)
        command = self.manager.build_command(task)
        selection = command[command.index("-f") + 1]
        # Exercise yt-dlp's actual format filtering without fetching any URLs.
        with YoutubeDL({"quiet": True, "format": selection}) as ydl:
            return ydl.process_ie_result({"id": "fixture", "title": "Fixture", "formats": formats},
                                         download=False)

    @patch("downloader.shutil.which", return_value="fixture-tool")
    def test_separate_video_and_audio_formats_are_selected_with_ffmpeg(self, _which):
        for quality, height in (("best", 2160), ("1080", 1080), ("720", 720)):
            with self.subTest(quality=quality):
                formats = [{"format_id": "audio", "url": "https://example.com/audio.m4a",
                            "ext": "m4a", "vcodec": "none", "acodec": "mp4a.40.2"}]
                formats += [{"format_id": str(resolution), "url": f"https://example.com/{resolution}.mp4",
                             "ext": "mp4", "height": resolution, "vcodec": "avc1", "acodec": "none"}
                            for resolution in (720, 1080, 2160)]
                selected = self.select_fixture_formats(quality, formats)
                self.assertEqual([item["format_id"] for item in selected["requested_formats"]],
                                 [str(height), "audio"])

    @patch("downloader.shutil.which", return_value=None)
    def test_combined_video_format_still_downloads_without_ffmpeg(self, _which):
        formats = [{"format_id": "combined", "url": "https://example.com/combined.mp4",
                    "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a.40.2"},
                   {"format_id": "1080", "url": "https://example.com/1080.mp4",
                    "ext": "mp4", "height": 1080, "vcodec": "avc1", "acodec": "none"}]
        selected = self.select_fixture_formats("720", formats)
        self.assertEqual(selected["format_id"], "combined")
        self.assertNotIn("requested_formats", selected)

    @patch("downloader.shutil.which", return_value="fixture-ffmpeg")
    def test_audio_download_requests_mp3_conversion(self, _which):
        task = self.manager.create_task("https://example.com/audio", "audio")
        command = self.manager.build_command(task)
        self.assertIn("-x", command)
        self.assertEqual(command[command.index("--audio-format") + 1], "mp3")

    @patch("downloader.shutil.which", return_value=None)
    def test_audio_download_without_ffmpeg_is_explained(self, _which):
        with self.assertRaisesRegex(TaskError, "FFmpeg"):
            self.manager.create_task("https://example.com/audio", "audio")
        self.assertEqual(self.manager.list_tasks(), [])

    def test_tasks_use_distinct_directories_to_avoid_filename_collisions(self):
        first = self.manager.create_task("https://example.com/one", "best")
        second = self.manager.create_task("https://example.com/two", "best")
        self.assertNotEqual(first["output_dir"], second["output_dir"])
        for task in (first, second):
            self.assertTrue(Path(task["output_dir"]).is_relative_to(self.manager.download_dir))


if __name__ == "__main__":
    unittest.main()
