"""Offline contract tests for the local yt-dlp application.

The worker is disabled deliberately: these tests exercise the application without
contacting video sites, downloading media, or opening desktop applications.
"""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app import create_app


class AppContractTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="dv365-downloader-test-")
        self.data_dir = Path(self.directory.name) / "data"
        self.app = create_app(data_dir=self.data_dir, start_worker=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.manager = self.app.config["MANAGER"]
        config_response = self.client.get("/api/config")
        self.assertEqual(config_response.status_code, 200)
        self.config = config_response.get_json()
        self.headers = {"X-App-Token": self.config["csrf_token"]}

    def tearDown(self):
        self.manager.shutdown()
        self.directory.cleanup()

    def create_task(self, url="https://www.example.com/watch?v=one", quality="best"):
        response = self.client.post(
            "/api/tasks", json={"url": url, "quality": quality}, headers=self.headers
        )
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        payload = response.get_json()
        return payload.get("task", payload)

    def post_action(self, task_id, action):
        return self.client.post(
            f"/api/tasks/{task_id}/{action}", json={}, headers=self.headers
        )

    def restart(self):
        self.manager.shutdown()
        self.app = create_app(data_dir=self.data_dir, start_worker=False)
        self.app.config.update(TESTING=True)
        self.client = self.app.test_client()
        self.manager = self.app.config["MANAGER"]
        self.config = self.client.get("/api/config").get_json()
        self.headers = {"X-App-Token": self.config["csrf_token"]}

    def test_initial_configuration_and_empty_queue(self):
        self.assertTrue(Path(self.config["download_dir"]).is_absolute())
        self.assertTrue(self.config["csrf_token"])
        self.assertIn("ffmpeg", self.config)
        self.assertIn("yt_dlp_version", self.config)
        self.assertEqual(self.client.get("/api/tasks").get_json(), {"tasks": []})

    def test_web_interface_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.content_type)
        self.assertIn(b"<html", response.data.lower())
        response.close()

    def test_create_and_list_a_download(self):
        task = self.create_task()
        self.assertTrue(task["id"])
        self.assertEqual(task["url"], "https://www.example.com/watch?v=one")
        self.assertEqual(task["quality"], "best")
        self.assertEqual(task["status"], "queued")
        listed = self.client.get("/api/tasks").get_json()["tasks"]
        self.assertEqual([item["id"] for item in listed], [task["id"]])

    @patch("downloader.shutil.which", return_value="test-tool")
    def test_supported_qualities_can_be_queued(self, _which):
        for quality in ("best", "1080", "720", "audio"):
            with self.subTest(quality=quality):
                task = self.create_task(
                    url=f"https://example.com/{quality}", quality=quality
                )
                self.assertEqual(task["quality"], quality)

    def test_url_whitespace_is_trimmed(self):
        task = self.create_task(url="  https://example.com/video  ")
        self.assertEqual(task["url"], "https://example.com/video")

    def test_invalid_urls_are_rejected_without_creating_tasks(self):
        invalid_urls = (
            "", "a video", "example.com/video", "file:///C:/private.txt",
            "ftp://example.com/video", "javascript:alert(1)", "https://",
            "https://exa mple.com/video", "https://user:secret@example.com/video",
            "https://example.com:invalid/video", None, 123,
        )
        for url in invalid_urls:
            with self.subTest(url=url):
                response = self.client.post(
                    "/api/tasks", json={"url": url, "quality": "best"},
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.manager.list_tasks(), [])

    def test_invalid_qualities_are_rejected(self):
        for quality in ("8k", "--exec", "", None, 1080):
            with self.subTest(quality=quality):
                response = self.client.post(
                    "/api/tasks", json={"url": "https://example.com/video", "quality": quality},
                    headers=self.headers,
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.manager.list_tasks(), [])

    def test_malformed_request_body_is_rejected(self):
        for data, content_type in (("{invalid", "application/json"), ("hello", "text/plain"), ("[]", "application/json")):
            with self.subTest(data=data):
                response = self.client.post(
                    "/api/tasks", data=data, content_type=content_type, headers=self.headers
                )
                self.assertEqual(response.status_code, 400)

    def test_duplicate_active_download_is_rejected(self):
        self.create_task()
        response = self.client.post(
            "/api/tasks", json={"url": "https://www.example.com/watch?v=one", "quality": "best"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.manager.list_tasks()), 1)

    def test_cancel_queued_task_and_retry(self):
        task = self.create_task()
        response = self.post_action(task["id"], "cancel")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.manager.get_task(task["id"])["status"], "cancelled")
        response = self.post_action(task["id"], "retry")
        self.assertEqual(response.status_code, 200)
        retried = self.manager.get_task(task["id"])
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["id"], task["id"])
        self.assertEqual(len(self.manager.list_tasks()), 1)

    def test_failed_download_can_be_retried_with_error_cleared(self):
        task = self.create_task()
        self.manager.update_task(task["id"], status="failed", error="Network unavailable", progress=40)
        response = self.post_action(task["id"], "retry")
        self.assertEqual(response.status_code, 200)
        retried = self.manager.get_task(task["id"])
        self.assertEqual(retried["status"], "queued")
        self.assertFalse(retried.get("error"))
        self.assertIn(retried["progress"], (None, 0))

    def test_retry_active_task_returns_conflict(self):
        task = self.create_task()
        self.assertEqual(self.post_action(task["id"], "retry").status_code, 409)
        self.assertEqual(self.manager.get_task(task["id"])["status"], "queued")

    def test_cancel_completed_task_returns_conflict(self):
        task = self.create_task()
        self.manager.update_task(task["id"], status="completed", progress=100)
        self.assertEqual(self.post_action(task["id"], "cancel").status_code, 409)
        self.assertEqual(self.manager.get_task(task["id"])["status"], "completed")

    def test_unknown_task_actions_and_file_return_not_found(self):
        for action in ("cancel", "retry", "open"):
            with self.subTest(action=action):
                self.assertEqual(self.post_action("does-not-exist", action).status_code, 404)
        self.assertEqual(self.client.get("/api/tasks/does-not-exist/file").status_code, 404)

    def test_mutations_require_valid_app_token(self):
        task = self.create_task()
        cases = (
            ("post", "/api/tasks", {"url": "https://example.com/new", "quality": "best"}),
            ("post", f"/api/tasks/{task['id']}/cancel", {}),
            ("post", f"/api/tasks/{task['id']}/retry", {}),
            ("post", f"/api/tasks/{task['id']}/open", {}),
            ("post", "/api/open-folder", {}),
            ("patch", "/api/config", {"download_dir": str(self.data_dir / "elsewhere")}),
        )
        for method, route, payload in cases:
            for headers in ({}, {"X-App-Token": "wrong"}):
                with self.subTest(method=method, route=route, headers=headers):
                    response = getattr(self.client, method)(route, json=payload, headers=headers)
                    self.assertEqual(response.status_code, 403)
        self.assertEqual(self.manager.get_task(task["id"])["status"], "queued")

    def test_non_loopback_host_is_rejected(self):
        for host in ("evil.example", "192.168.1.42", "localhost.evil.example", "127.0.0.1.evil.example"):
            with self.subTest(host=host):
                response = self.client.get("/api/config", headers={"Host": host})
                self.assertIn(response.status_code, (400, 403))
                self.assertNotIn("csrf_token", response.get_data(as_text=True))

    def test_loopback_hostnames_are_accepted(self):
        for host in ("localhost", "localhost:8765", "127.0.0.1:8765", "[::1]:8765"):
            with self.subTest(host=host):
                self.assertEqual(self.client.get("/api/config", headers={"Host": host}).status_code, 200)

    def test_external_origins_are_rejected_even_with_valid_token(self):
        for origin in ("https://evil.example", "null", "http://localhost.evil.example"):
            with self.subTest(origin=origin):
                response = self.client.post(
                    "/api/tasks", json={"url": "https://example.com/video", "quality": "best"},
                    headers={**self.headers, "Origin": origin},
                )
                self.assertEqual(response.status_code, 403)
        self.assertEqual(self.manager.list_tasks(), [])

    def test_same_origin_mutation_is_accepted(self):
        response = self.client.post(
            "/api/tasks", json={"url": "https://example.com/video", "quality": "best"},
            headers={**self.headers, "Origin": "http://localhost"},
        )
        self.assertEqual(response.status_code, 201)

    def test_set_download_directory_and_persist_setting(self):
        output_dir = Path(self.directory.name) / "视频 output"
        response = self.client.patch(
            "/api/config", json={"download_dir": str(output_dir)}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertTrue(output_dir.is_dir())
        self.assertEqual(Path(self.client.get("/api/config").get_json()["download_dir"]), output_dir)
        self.restart()
        self.assertEqual(Path(self.config["download_dir"]), output_dir)

    def test_invalid_output_directories_are_rejected(self):
        existing_file = Path(self.directory.name) / "file.txt"
        existing_file.write_text("not a directory", encoding="utf-8")
        for path in ("relative/path", "", str(existing_file), None, 123):
            with self.subTest(path=path):
                response = self.client.patch(
                    "/api/config", json={"download_dir": path}, headers=self.headers
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/config").get_json()["download_dir"], self.config["download_dir"])

    def test_queued_tasks_survive_restart(self):
        task = self.create_task(quality="720")
        self.restart()
        tasks = self.client.get("/api/tasks").get_json()["tasks"]
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["id"], task["id"])
        self.assertEqual(tasks[0]["quality"], "720")
        self.assertEqual(tasks[0]["status"], "queued")

    def test_interrupted_download_is_recoverable_after_restart(self):
        task = self.create_task()
        self.manager.update_task(task["id"], status="downloading", progress=30)
        self.restart()
        recovered = self.manager.get_task(task["id"])
        self.assertEqual(recovered["status"], "failed")
        self.assertTrue(recovered.get("error"))
        self.assertEqual(self.post_action(task["id"], "retry").status_code, 200)
        self.assertEqual(self.manager.get_task(task["id"])["status"], "queued")

    def test_completed_history_survives_restart(self):
        task = self.create_task()
        self.manager.update_task(task["id"], status="completed", progress=100, title="A video")
        self.restart()
        recovered = self.manager.get_task(task["id"])
        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(recovered["title"], "A video")

    def test_completed_media_file_can_be_saved_from_browser(self):
        task = self.create_task()
        path = Path(task["output_dir"]) / "video.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test-video-content")
        self.manager.update_task(task["id"], status="completed", filepath=str(path), progress=100)
        response = self.client.get(f"/api/tasks/{task['id']}/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"test-video-content")
        self.assertIn("attachment", response.headers.get("Content-Disposition", ""))
        response.close()

    def test_unfinished_file_is_not_downloadable(self):
        task = self.create_task()
        response = self.client.get(f"/api/tasks/{task['id']}/file")
        self.assertIn(response.status_code, (404, 409))

    def test_completed_missing_file_returns_not_found(self):
        task = self.create_task()
        self.manager.update_task(
            task["id"], status="completed", filepath=str(Path(self.config["download_dir"]) / "missing.mp4")
        )
        self.assertEqual(self.client.get(f"/api/tasks/{task['id']}/file").status_code, 404)

    def test_completed_file_outside_output_directory_is_not_served(self):
        task = self.create_task()
        outside = Path(self.directory.name) / "private.txt"
        outside.write_text("private-content", encoding="utf-8")
        self.manager.update_task(task["id"], status="completed", filepath=str(outside))
        response = self.client.get(f"/api/tasks/{task['id']}/file")
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn(b"private-content", response.data)

    def test_download_directory_change_preserves_existing_task_files(self):
        old_task = self.create_task()
        old_directory = Path(old_task["output_dir"])
        path = old_directory / "older-video.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"older-video")
        self.manager.update_task(old_task["id"], status="completed", filepath=str(path))
        new_directory = Path(self.directory.name) / "new-downloads"
        response = self.client.patch(
            "/api/config", json={"download_dir": str(new_directory)}, headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        new_task = self.create_task(url="https://example.com/new-video")
        self.assertEqual(Path(new_task["output_dir"]).parent, new_directory)
        self.assertEqual(Path(self.manager.get_task(old_task["id"])["output_dir"]), old_directory)
        response = self.client.get(f"/api/tasks/{old_task['id']}/file")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, b"older-video")
        response.close()

    def test_similarly_named_sibling_directory_is_not_treated_as_output(self):
        task = self.create_task()
        output = Path(self.config["download_dir"])
        sibling = output.with_name(output.name + "-private")
        sibling.mkdir(parents=True, exist_ok=True)
        outside = sibling / "private.txt"
        outside.write_text("private-sibling-content", encoding="utf-8")
        self.manager.update_task(task["id"], status="completed", filepath=str(outside))
        response = self.client.get(f"/api/tasks/{task['id']}/file")
        self.assertIn(response.status_code, (403, 404))
        self.assertNotIn(b"private-sibling-content", response.data)


if __name__ == "__main__":
    unittest.main()
