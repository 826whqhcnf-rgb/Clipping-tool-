#!/usr/bin/env bash
# One-command setup for Shorts Clipper.
#
#   ./setup.sh              full setup: check computer → install → enter keys
#   ./setup.sh --keys-only  just add or change your keys later
#
# Your answers are saved to a file called .env in this folder. The app reads
# it automatically — you never need to touch shell profiles or "export".
set -euo pipefail
cd "$(dirname "$0")"

say() { printf "\n\033[1m%s\033[0m\n" "$1"; }

if [ "${1:-}" != "--keys-only" ]; then
  say "Step 1 of 3 — Checking your computer…"
  ok=1
  if command -v python3 >/dev/null 2>&1; then
    echo "  ✓ Python found ($(python3 -V 2>&1))"
  else
    echo "  ✗ Python 3 is missing → install from https://python.org"
    echo "      (Windows: tick 'Add Python to PATH' during install)"
    ok=0
  fi
  if command -v ffmpeg >/dev/null 2>&1; then
    echo "  ✓ ffmpeg found"
  else
    echo "  ✗ ffmpeg is missing → install it:"
    echo "      Mac:     brew install ffmpeg"
    echo "      Windows: winget install ffmpeg     (in PowerShell)"
    echo "      Linux:   sudo apt install ffmpeg"
    ok=0
  fi
  if [ "$ok" -ne 1 ]; then
    say "Install the missing pieces above, then run ./setup.sh again."
    exit 1
  fi

  say "Step 2 of 3 — Installing the app (first time takes a few minutes)…"
  [ -d .venv ] || python3 -m venv .venv
  # shellcheck disable=SC1091
  . .venv/bin/activate
  pip install -q --upgrade pip
  pip install -q -r requirements.txt
  echo "  ✓ installed"
fi

say "Step 3 of 3 — Your keys (ALL optional — press Enter to skip any)"
echo "  Keys are saved to .env in this folder. Re-run './setup.sh --keys-only'"
echo "  any time to change them."

if [ ! -t 0 ] && [ "${CLIP_SETUP_ASSUME_TTY:-}" != "1" ]; then
  echo "  (no terminal attached — skipping key questions, keeping existing .env)"
else
  # current(KEY) -> existing value from .env, if any
  current() { [ -f .env ] && grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

  ask() { # ask KEY "question" "where to get it"
    key="$1"; question="$2"; hint="$3"
    cur="$(current "$key")"
    echo
    echo "• $question"
    if [ -n "$hint" ]; then echo "  Get it at: $hint"; fi
    if [ -n "$cur" ]; then
      printf "  [saved: %s…] Enter = keep, or paste a new one: " "$(printf %.8s "$cur")"
    else
      printf "  Paste it here (or press Enter to skip): "
    fi
    IFS= read -r answer || answer=""
    val="${answer:-$cur}"
    if [ -n "$val" ]; then printf '%s=%s\n' "$key" "$val" >> .env.new; fi
  }

  : > .env.new
  {
    echo "# Shorts Clipper settings — created by setup.sh"
    echo "# Re-run './setup.sh --keys-only' to change these."
  } >> .env.new

  ask GEMINI_API_KEY \
    "Google Gemini key — makes the AI pick the best clip moments (free)" \
    "https://aistudio.google.com/apikey"
  ask PEXELS_API_KEY \
    "Pexels key — stock footage behind generated Shorts (free)" \
    "https://www.pexels.com/api/"
  ask YOUTUBE_CLIENT_ID \
    "YouTube Client ID — lets the app post to your channel" \
    "https://console.cloud.google.com (see README → Posting to YouTube)"
  ask YOUTUBE_CLIENT_SECRET \
    "YouTube Client Secret — pairs with the ID above" \
    ""

  echo
  cur_watch="$(current CLIP_WATCH)"
  if [ "$cur_watch" = "1" ]; then
    printf "• Keep the drop-folder on? (drop a video in data/watch → clips post themselves) [Y/n]: "
  else
    printf "• Turn on the drop-folder? (drop a video in data/watch → clips post themselves) [y/N]: "
  fi
  IFS= read -r watch || watch=""
  case "${watch:-$cur_watch}" in
    y|Y|yes|YES|1) echo "CLIP_WATCH=1" >> .env.new; echo "  ✓ drop-folder on (preset: data/campaign.json)";;
    *) : ;;
  esac

  mv .env.new .env
  echo
  echo "  ✓ saved to .env"
fi

say "Done! Start the app any time with:  ./run.sh"
echo "Then open http://localhost:8000 in your browser."
