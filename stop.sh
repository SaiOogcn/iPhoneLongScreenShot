#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -f .longshot.pid ]]; then
  echo "Longshot is not running (no .longshot.pid)."
  exit 0
fi
PID="$(cat .longshot.pid)"
if kill -0 "$PID" 2>/dev/null; then
  kill "$PID"
  for _ in $(seq 1 20); do
    kill -0 "$PID" 2>/dev/null || break
    sleep 0.25
  done
fi
rm -f .longshot.pid
echo "Longshot stopped."
