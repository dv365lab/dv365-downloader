#!/bin/bash

# Finder runs .command files in Terminal, and the initial directory is not
# guaranteed to be this project. Linux start.sh also uses this bootstrap.
# Always resolve paths relative to this file.
set -u

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 1
cd "$SCRIPT_DIR" || exit 1

VENV_DIR="$SCRIPT_DIR/.venv"
VENV_PYTHON="$VENV_DIR/bin/python"
REQUIREMENTS_PATH="$SCRIPT_DIR/requirements.txt"
STAMP_PATH="$VENV_DIR/requirements.sha256"

pause_after_error() {
    status=$?
    if [ "$status" -ne 0 ]; then
        printf '\n启动失败。请查看上方提示。\n' >&2
        if [ -t 0 ]; then
            printf '按回车键关闭窗口...'
            IFS= read -r _unused
        fi
    fi
    return 0
}
trap pause_after_error EXIT

fail() {
    printf 'dv365-downloader：%s\n' "$1" >&2
    exit 1
}

python_is_supported() {
    "$1" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

if [ ! -x "$VENV_PYTHON" ]; then
    BOOTSTRAP_PYTHON=''
    for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10 python; do
        if command -v "$candidate" >/dev/null 2>&1 && python_is_supported "$candidate"; then
            BOOTSTRAP_PYTHON="$candidate"
            break
        fi
    done

    if [ -z "$BOOTSTRAP_PYTHON" ]; then
        fail '需要 Python 3.10 或更新版本。请先安装 Python 3，然后重新双击启动文件。'
    fi

    printf '正在创建本地 Python 环境...\n'
    "$BOOTSTRAP_PYTHON" -m venv "$VENV_DIR" || \
        fail '无法创建 .venv，请确认 Python 安装包含 venv 和 pip。'
fi

if ! python_is_supported "$VENV_PYTHON"; then
    fail '.venv 中的 Python 版本低于 3.10，请删除 .venv 后重新双击启动。'
fi

[ -f "$REQUIREMENTS_PATH" ] || fail '找不到 requirements.txt，请确认启动文件位于完整的项目目录中。'

REQUIREMENTS_HASH="$("$VENV_PYTHON" -c \
    'import hashlib, pathlib, sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest().upper())' \
    "$REQUIREMENTS_PATH")" || fail '无法读取 requirements.txt。'

SAVED_HASH=''
if [ -f "$STAMP_PATH" ]; then
    SAVED_HASH="$(tr -d '[:space:]' < "$STAMP_PATH")"
fi

DEPENDENCIES_MISSING=0
"$VENV_PYTHON" -c \
    "import importlib.util,sys; sys.exit(0 if all(importlib.util.find_spec(name) for name in ['flask', 'waitress', 'yt_dlp']) else 1)" \
    >/dev/null 2>&1 || DEPENDENCIES_MISSING=1

if [ "$DEPENDENCIES_MISSING" -ne 0 ] || [ "$SAVED_HASH" != "$REQUIREMENTS_HASH" ]; then
    printf '正在安装依赖（首次运行需要联网）...\n'
    "$VENV_PYTHON" -m pip install -r "$REQUIREMENTS_PATH" || \
        fail '依赖安装失败，请检查网络连接后重试。'
    printf '%s\n' "$REQUIREMENTS_HASH" > "$STAMP_PATH" || \
        fail '无法保存依赖安装状态。'
fi

printf '正在启动 dv365-downloader...\n'
printf '下载期间请保留此窗口；按 Control+C 可停止服务。\n\n'
"$VENV_PYTHON" "$SCRIPT_DIR/app.py" "$@" || fail '应用未能正常启动。'
