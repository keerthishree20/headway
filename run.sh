#!/usr/bin/env bash
# Starts the ingestion API and the dashboard together, and cleans both up on
# Ctrl-C. For anything beyond local development, run the two separately.
set -euo pipefail
cd "$(dirname "$0")"

# `python3` is not always the newest interpreter on PATH -- on some machines it
# is an old build shadowing the system one -- so pick a version that can
# actually run the backend, and say so loudly if there isn't one.
PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PYTHON="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON" ]; then
  echo "Need Python 3.10 or newer. Install one, or set PYTHON=/path/to/python3.12" >&2
  exit 1
fi

if [ ! -d backend/.venv ]; then
  echo "Creating backend virtualenv with $($PYTHON --version)..."
  "$PYTHON" -m venv backend/.venv
  backend/.venv/bin/pip install -q --upgrade pip
  backend/.venv/bin/pip install -q -r backend/requirements.txt
fi

if [ ! -d frontend/node_modules ]; then
  echo "Installing frontend dependencies..."
  (cd frontend && npm install)
fi

(cd backend && exec .venv/bin/uvicorn app.main:app --port 8100) &
API=$!
(cd frontend && exec npm run dev) &
WEB=$!

trap 'kill $API $WEB 2>/dev/null' EXIT INT TERM
echo "API  http://127.0.0.1:8100/api/health"
echo "Dash http://127.0.0.1:3100"
wait
