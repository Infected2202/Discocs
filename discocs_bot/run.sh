#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

case "$(uname -s 2>/dev/null || printf unknown)" in
    Linux*|Darwin*)
        VENV_DIR=".venv-linux"
        ;;
    *)
        VENV_DIR=".venv"
        ;;
esac
PYTHON=""
ACTIVATE=""

find_python() {
    for candidate in python3.11 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' >/dev/null 2>&1; then
                printf '%s\n' "$candidate"
                return 0
            fi
        fi
    done

    return 1
}

resolve_venv() {
    if [ -x "$VENV_DIR/bin/python" ]; then
        PYTHON="$VENV_DIR/bin/python"
        ACTIVATE="$VENV_DIR/bin/activate"
        return 0
    fi

    if [ -x "$VENV_DIR/Scripts/python.exe" ] && [ -f "$VENV_DIR/Scripts/activate" ]; then
        PYTHON="$VENV_DIR/Scripts/python.exe"
        ACTIVATE="$VENV_DIR/Scripts/activate"
        return 0
    fi

    return 1
}

if ! resolve_venv; then
    BASE_PYTHON="$(find_python)" || {
        echo "[run] ERROR: failed to find Python 3.11+."
        exit 1
    }

    echo "[run] Creating virtual environment in $VENV_DIR..."
    "$BASE_PYTHON" -m venv "$VENV_DIR"
    resolve_venv || {
        echo "[run] ERROR: failed to locate venv Python after creation."
        exit 1
    }
fi

# shellcheck disable=SC1091
source "$ACTIVATE"

if ! "$PYTHON" -c "import telegram, httpx, pydantic_settings, aiosqlite" >/dev/null 2>&1; then
    echo "[run] Installing dependencies..."
    "$PYTHON" -m pip install --upgrade pip
    "$PYTHON" -m pip install -r requirements.txt
    "$PYTHON" -m pip install -e .
fi

echo "[run] Starting Discocs Bot..."
exec "$PYTHON" -m bot.main
