#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ -f .longshot.pid ]] && kill -0 "$(cat .longshot.pid)" 2>/dev/null; then
  echo "Longshot is already running (PID $(cat .longshot.pid))."
  exit 0
fi

export LONGSHOT_HOST="${LONGSHOT_HOST:-0.0.0.0}"
export LONGSHOT_PORT="${LONGSHOT_PORT:-8787}"
export LONGSHOT_WORK_DIR="${LONGSHOT_WORK_DIR:-/tmp/longshot-work}"
mkdir -p "$LONGSHOT_WORK_DIR"

if [[ -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
elif [[ -x ../.venv/bin/python ]]; then
  PYTHON=../.venv/bin/python
else
  echo "Python environment not found. Run ./setup.sh first." >&2
  exit 1
fi

healthcheck() {
  "$PYTHON" -c \
    'import sys, urllib.request; urllib.request.urlopen("http://127.0.0.1:%s/healthz" % sys.argv[1], timeout=1).read()' \
    "$LONGSHOT_PORT" >/dev/null 2>&1
}

if healthcheck; then
  echo "Port ${LONGSHOT_PORT} is already serving HTTP. Refusing to start a duplicate." >&2
  echo "Stop the existing Longshot process first, or choose another LONGSHOT_PORT." >&2
  exit 1
fi

rm -f .longshot.pid
LAUNCH=(bash -c 'echo $$ > .longshot.pid; exec "$@"' _ "$PYTHON" app.py)
if command -v setsid >/dev/null 2>&1; then
  setsid -f "${LAUNCH[@]}" >> longshot.log 2>&1 < /dev/null
else
  nohup "${LAUNCH[@]}" >> longshot.log 2>&1 < /dev/null &
fi

for _ in $(seq 1 20); do
  [[ -s .longshot.pid ]] && break
  sleep 0.1
done
if [[ ! -s .longshot.pid ]]; then
  echo "Could not record Longshot PID. See longshot.log" >&2
  exit 1
fi
PID="$(cat .longshot.pid)"

for _ in $(seq 1 40); do
  if healthcheck; then
    if [[ -n "${LONGSHOT_PUBLIC_URL:-}" ]]; then
      echo "Longshot started: ${LONGSHOT_PUBLIC_URL} (PID $PID)"
    else
      echo "Longshot started on port ${LONGSHOT_PORT} (PID $PID)."
      echo "Open http://<server-ip>:${LONGSHOT_PORT} from your iPhone."
    fi
    exit 0
  fi
  if ! kill -0 "$PID" 2>/dev/null; then
    echo "Longshot failed to start. See longshot.log" >&2
    tail -40 longshot.log >&2 || true
    rm -f .longshot.pid
    exit 1
  fi
  sleep 0.5
done

echo "Longshot process is running but health check timed out. See longshot.log" >&2
exit 1
