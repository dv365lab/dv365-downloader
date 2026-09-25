"""Exercise Linux launchers without opening a browser or changing desktop settings."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest

from install_linux_shortcut import install_shortcut


PROJECT_DIR = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux launchers")
class LinuxLauncherTests(unittest.TestCase):
    def test_shell_launcher_finds_project_and_forwards_arguments(self):
        with tempfile.TemporaryDirectory(prefix="dv365-linux-launcher-") as temporary:
            project = Path(temporary) / "project with spaces"
            project.mkdir()
            shutil.copy2(PROJECT_DIR / "start.sh", project / "start.sh")
            (project / "start.command").write_text(
                '#!/bin/bash\nprintf "%s\\n" "$0" "$@"\n', encoding="utf-8"
            )
            result = subprocess.run(
                ["/bin/bash", str(project / "start.sh"), "--port", "9876",
                 "--no-browser", "argument with spaces", "$literal"],
                cwd=temporary, capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.splitlines(), [
                str(project / "start.command"), "--port", "9876", "--no-browser",
                "argument with spaces", "$literal",
            ])

    @unittest.skipUnless(Path("/usr/bin/python3").is_file(), "System Python unavailable")
    def test_desktop_launcher_handles_special_characters_in_project_path(self):
        probe = subprocess.run(
            ["/usr/bin/python3", "-c", "from gi.repository import Gio; Gio.DesktopAppInfo"],
            capture_output=True, check=False,
        )
        if probe.returncode:
            self.skipTest("GIO Python bindings unavailable")
        with tempfile.TemporaryDirectory(prefix="dv365-desktop-launcher-") as temporary:
            project = Path(temporary) / "project 空 格 $cash `tick` \"quote\" 'apostrophe' %f %% \\backslash "
            project.mkdir()
            for name in ("start.command", "app.py", "requirements.txt"):
                (project / name).touch()
            (project / "start.sh").write_text(
                '#!/bin/bash\nprintf "%s\\n" "$PWD" "$0" > "$DV365_TEST_LAUNCH_LOG"\n',
                encoding="utf-8",
            )
            shortcut = install_shortcut(project)
            self.assertTrue(shortcut.stat().st_mode & stat.S_IXUSR)
            contents = shortcut.read_text(encoding="utf-8")
            self.assertIn("\nTerminal=true\n", contents)
            if shutil.which("desktop-file-validate"):
                validation = subprocess.run(
                    ["desktop-file-validate", str(shortcut)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)

            # Keep the real GIO Exec/Path parsing while avoiding a visible terminal.
            shortcut.write_text(contents.replace("Terminal=true", "Terminal=false"), encoding="utf-8")
            log_path = Path(temporary) / "launch.log"
            environment = os.environ.copy()
            environment["DV365_TEST_LAUNCH_LOG"] = str(log_path)
            result = subprocess.run(
                ["/usr/bin/python3", "-c",
                 "import sys; from gi.repository import Gio; "
                 "app = Gio.DesktopAppInfo.new_from_filename(sys.argv[1]); "
                 "assert app is not None; assert app.launch([], None)", str(shortcut)],
                cwd=temporary, env=environment,
                capture_output=True, text=True, check=False, timeout=10,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            expected = [str(project), str(project / "start.sh")]
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if log_path.exists() and log_path.read_text(encoding="utf-8").splitlines() == expected:
                    break
                time.sleep(0.01)
            self.assertTrue(log_path.exists(), "GIO did not execute the generated launcher")
            self.assertEqual(log_path.read_text(encoding="utf-8").splitlines(), expected)

    def test_incomplete_project_does_not_create_shortcut(self):
        with tempfile.TemporaryDirectory(prefix="dv365-incomplete-project-") as temporary:
            project = Path(temporary)
            (project / "start.sh").touch()
            with self.assertRaisesRegex(OSError, "Missing start.command"):
                install_shortcut(project)
            self.assertFalse((project / "dv365-downloader.desktop").exists())


if __name__ == "__main__":
    unittest.main()
