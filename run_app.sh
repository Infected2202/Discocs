#!/usr/bin/env bash
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  echo "Run this script as ./run_app.sh, not with source, so it cannot replace your shell." >&2
  return 2
fi

cleanup_terminal() {
  stty sane 2>/dev/null || true
  printf '\033[?25h' 2>/dev/null || true
}
trap cleanup_terminal EXIT INT TERM

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

if [[ ! -f ".venv/bin/activate" ]]; then
  echo "Missing .venv/bin/activate. Create the environment first:" >&2
  echo "  python -m venv .venv" >&2
  echo "  source .venv/bin/activate" >&2
  echo "  python -m pip install -e '.[dev,essentia]'" >&2
  exit 1
fi

source ".venv/bin/activate"

export DISCOCS_DB_PATH="${DISCOCS_DB_PATH:-data/app.db}"
export DISCOCS_DATA_DIR="${DISCOCS_DATA_DIR:-data}"
export DISCOCS_MODEL_DIR="${DISCOCS_MODEL_DIR:-models}"
export DISCOCS_INDEX_DIR="${DISCOCS_INDEX_DIR:-data}"
export DISCOCS_DEFAULT_MODEL="${DISCOCS_DEFAULT_MODEL:-discogs_multi}"
export DISCOCS_AUDIO_LOADER="${DISCOCS_AUDIO_LOADER:-ffmpeg}"

export TF_CPP_MIN_LOG_LEVEL="${TF_CPP_MIN_LOG_LEVEL:-3}"
export CUDA_VISIBLE_DEVICES="${CUDA_VISIBLE_DEVICES:--1}"

HOST="${DISCOCS_HOST:-0.0.0.0}"
PORT="${DISCOCS_PORT:-8711}"

echo "Starting discocs on http://${HOST}:${PORT}"
echo "DB: ${DISCOCS_DB_PATH}"
echo "Models: ${DISCOCS_MODEL_DIR}"

uvicorn app.main:app --host "$HOST" --port "$PORT"
