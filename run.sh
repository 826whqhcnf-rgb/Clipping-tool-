#!/usr/bin/env bash
# Launch the Shorts Clipper locally.
#   ./run.sh            # starts on http://localhost:8000
#   PORT=9000 ./run.sh  # custom port
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "⚠  ffmpeg not found. Install it first:"
  echo "     macOS:        brew install ffmpeg"
  echo "     Ubuntu/Debian: sudo apt install ffmpeg"
  echo
fi

# Create / reuse a virtualenv.
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate

pip install -q --upgrade pip
pip install -q -r requirements.txt

echo "▶  Starting Shorts Clipper on http://localhost:${PORT:-8000}"
exec uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}"
