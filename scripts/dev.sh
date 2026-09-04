#!/usr/bin/env bash
# Start the Trace API and the web UI together. Ctrl-C stops both.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_PORT="${TRACE_API_PORT:-8000}"
WEB_PORT="${TRACE_WEB_PORT:-3000}"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and fill in your keys."
  exit 1
fi

cleanup() {
  jobs -p 2>/dev/null | xargs kill 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "API   http://localhost:${API_PORT}"
python3 -m uvicorn trace_app.api:app --reload --port "${API_PORT}" &

if [ -d web/node_modules ]; then
  echo "Web   http://localhost:${WEB_PORT}"
  (cd web && npm run dev -- --port "${WEB_PORT}") &
else
  echo "Web   skipped: run 'cd web && npm install' first."
fi

wait
