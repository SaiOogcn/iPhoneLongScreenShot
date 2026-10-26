#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x ../.venv/bin/python ]]; then
  PYTHON=../.venv/bin/python
else
  echo "Python environment not found. Run ./setup.sh first." >&2
  exit 1
fi

export LONGSHOT_HOST="${LONGSHOT_HOST:-0.0.0.0}"
export LONGSHOT_PORT="${LONGSHOT_PORT:-8787}"
echo "Longshot will listen on http://0.0.0.0:${LONGSHOT_PORT}; open http://<server-ip>:${LONGSHOT_PORT} from your iPhone." >&2
exec "$PYTHON" app.py
