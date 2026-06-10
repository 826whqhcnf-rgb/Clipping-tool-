#!/usr/bin/env bash
# Start the Shorts Clipper server (idempotent — safe to run repeatedly).
# Used by the Codespaces devcontainer to auto-launch on attach.
set -euo pipefail
cd "$(dirname "$0")/.."

if pgrep -f "uvicorn backend.main:app" >/dev/null 2>&1; then
  echo "✅ Shorts Clipper is already running on port 8000."
  exit 0
fi

mkdir -p data
echo "▶  Starting Shorts Clipper on port 8000…"
nohup uvicorn backend.main:app --host 0.0.0.0 --port "${PORT:-8000}" \
  > data/server.log 2>&1 &

echo "✅ Started. Open the forwarded port 8000. Logs: data/server.log"
