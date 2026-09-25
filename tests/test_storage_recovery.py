"""Saved output paths must remain repairable through the local settings UI."""

from pathlib import Path
import tempfile
import unittest

from app import create_app


class StorageRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="videodown-storage-test-")
        self.addCleanup(self.directory.cleanup)
        self.data_dir = Path(self.directory.name) / "data"

    def make_app(self):
        app = create_app(data_dir=self.data_dir, start_worker=False)
        app.config.update(TESTING=True)
        self.addCleanup(app.config["MANAGER"].shutdown)
        return app

    def test_new_default_download_directory_is_created(self):
        app = self.make_app()
        self.assertTrue(app.config["MANAGER"].download_dir.is_dir())

    def test_unavailable_saved_directory_can_be_changed_after_restart(self):
        app = self.make_app()
        old_directory = Path(self.directory.name) / "saved-output"
        app.config["MANAGER"].set_download_dir(str(old_directory))
        app.config["MANAGER"].shutdown()
        old_directory.rmdir()
        old_directory.write_text("This path is no longer a folder.", encoding="utf-8")

        restarted = self.make_app()
        client = restarted.test_client()
        config_response = client.get("/api/config")
        self.assertEqual(config_response.status_code, 200)
        config = config_response.get_json()
        self.assertEqual(Path(config["download_dir"]), old_directory)
        with client.get("/") as index_response:
            self.assertEqual(index_response.status_code, 200)

        replacement = Path(self.directory.name) / "replacement-output"
        response = client.patch(
            "/api/config", json={"download_dir": str(replacement)},
            headers={"X-App-Token": config["csrf_token"]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Path(response.get_json()["download_dir"]), replacement)
        self.assertTrue(replacement.is_dir())

        restarted.config["MANAGER"].shutdown()
        recovered = self.make_app()
        self.assertEqual(recovered.config["MANAGER"].download_dir, replacement)


if __name__ == "__main__":
    unittest.main()
