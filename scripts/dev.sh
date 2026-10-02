#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
for command in uv ffmpeg ffprobe redis-server npm; do
  command -v "$command" >/dev/null || { echo "Missing $command. On Mac: brew install uv ffmpeg redis"; exit 1; }
done
exec uv run --no-sync python scripts/dev.py
