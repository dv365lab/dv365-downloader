#!/bin/bash
# Desktop launchers may start from any working directory.
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)" || exit 1
# Graphical sessions may omit tools installed for the current user.
if [ -d "$HOME/.local/bin" ]; then
    export PATH="$HOME/.local/bin:$PATH"
fi
exec /bin/bash "$SCRIPT_DIR/start.command" "$@"
