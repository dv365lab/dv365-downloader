from pathlib import Path
import tempfile
import unittest

from downloader import DownloadManager, TaskError


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_cannot_change_running_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            first = DownloadManager(directory, start_worker=False)
            second = None
            try:
                task = first.create_task("https://example.com/video")
                first.update_task(task["id"], status="downloading")
                with self.assertRaises(TaskError):
                    second = DownloadManager(directory, start_worker=False)
            finally:
                if second:
                    second.shutdown()
                first.shutdown()
            restored = DownloadManager(directory, start_worker=False)
            try:
                self.assertEqual(restored.get_task(task["id"])["status"], "failed")
            finally:
                restored.shutdown()
