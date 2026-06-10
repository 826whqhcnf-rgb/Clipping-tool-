#!/usr/bin/env bash
# Restart the Shorts Clipper server (use after `git pull` to load new code).
set -euo pipefail
cd "$(dirname "$0")/.."
pkill -f "uvicorn backend.main:app" 2>/dev/null || true
sleep 1
exec bash scripts/start.sh
