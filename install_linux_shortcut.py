#!/usr/bin/env python3
"""Create a Linux double-click launcher for this checkout (no dependencies)."""
from pathlib import Path
import sys


def desktop_value(value):
    """Escape a Desktop Entry string value, before any Exec token parsing."""
    return (str(value).replace("\\", "\\\\").replace(" ", "\\s").replace("\n", "\\n")
            .replace("\r", "\\r").replace("\t", "\\t"))


def exec_argument(value):
    # Exec has its own quoting layer after Desktop Entry string unescaping.
    value = str(value).replace("%", "%%")
    for character in ("\\", '"', "`", "$"):
        value = value.replace(character, "\\" + character)
    return desktop_value('"' + value + '"')


def install_shortcut(project_dir):
    project_dir = Path(project_dir).resolve()
    for name in ("start.sh", "start.command", "app.py", "requirements.txt"):
        if not (project_dir / name).is_file():
            raise OSError("Missing {}. Keep the launcher in the complete project folder.".format(name))
    shortcut = project_dir / "dv365-downloader.desktop"
    shortcut.write_text(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=DV365 Downloader\n"
        "Comment=Start the local video downloader\n"
        "Exec=/bin/bash {}\n"
        "Path={}\n"
        "Icon={}\n"
        "Terminal=true\n"
        "Categories=AudioVideo;\n".format(
            exec_argument(project_dir / "start.sh"),
            desktop_value(project_dir),
            desktop_value(project_dir / "static" / "dv365-logo.png"),
        ),
        encoding="utf-8",
    )
    shortcut.chmod(0o755)
    return shortcut


def main():
    if not sys.platform.startswith("linux"):
        print("Use start.bat on Windows or start.command on macOS.", file=sys.stderr)
        return 1
    try:
        shortcut = install_shortcut(Path(__file__).resolve().parent)
    except OSError as error:
        print("Could not create launcher: {}".format(error), file=sys.stderr)
        return 1
    print("Double-click: {}".format(shortcut))
    print('If your file manager asks, choose "Allow Launching" / "Mark Executable".')
    print("Keep the terminal open while downloading. Press Ctrl+C to stop.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
