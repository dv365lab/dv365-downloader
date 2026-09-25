"""Checks for the Finder-compatible macOS launcher."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import unittest


PROJECT_DIR = Path(__file__).resolve().parents[1]
LAUNCHER = PROJECT_DIR / "start.command"


class MacLauncherTests(unittest.TestCase):
    def test_launcher_is_executable_and_has_valid_bash_syntax(self):
        self.assertTrue(LAUNCHER.stat().st_mode & stat.S_IXUSR)
        result = subprocess.run(
            ["/bin/bash", "-n", str(LAUNCHER)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_uses_its_own_directory_and_forwards_arguments(self):
        with tempfile.TemporaryDirectory(prefix="dv365-mac-launcher-") as temporary:
            project = Path(temporary) / "project with spaces"
            (project / ".venv" / "bin").mkdir(parents=True)
            shutil.copy2(LAUNCHER, project / "start.command")
            shutil.copy2(PROJECT_DIR / "requirements.txt", project / "requirements.txt")
            (project / "app.py").write_text("# launcher test\n", encoding="utf-8")

            requirements_hash = hashlib.sha256(
                (project / "requirements.txt").read_bytes()
            ).hexdigest().upper()
            (project / ".venv" / "requirements.sha256").write_text(
                requirements_hash + "\n", encoding="ascii"
            )

            fake_python = project / ".venv" / "bin" / "python"
            fake_python.write_text(
                """#!/bin/bash
if [ "$1" = "-c" ]; then
    case "$2" in
        *hashlib*) printf '%s\\n' "$DV365_TEST_REQUIREMENTS_HASH" ;;
    esac
    exit 0
fi
printf '%s\\n' "$PWD" > "$DV365_TEST_LAUNCH_LOG"
printf '%s\\n' "$@" >> "$DV365_TEST_LAUNCH_LOG"
""",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            log_path = Path(temporary) / "launch.log"
            environment = os.environ.copy()
            environment.update(
                DV365_TEST_REQUIREMENTS_HASH=requirements_hash,
                DV365_TEST_LAUNCH_LOG=str(log_path),
            )
            result = subprocess.run(
                ["/bin/bash", str(project / "start.command"), "--port", "9876", "--no-browser"],
                cwd=Path(temporary),
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            resolved_project = project.resolve()
            self.assertEqual(
                log_path.read_text(encoding="utf-8").splitlines(),
                [
                    str(resolved_project),
                    str(resolved_project / "app.py"),
                    "--port",
                    "9876",
                    "--no-browser",
                ],
            )
            self.assertNotIn("正在安装依赖", result.stdout)


if __name__ == "__main__":
    unittest.main()
