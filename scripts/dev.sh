#!/usr/bin/env bash
# One local command: API on 8741, Next.js on 43123. SQLite, demo mode, mock provider.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
mkdir -p data
export PRISM_DATABASE_URL="${PRISM_DATABASE_URL:-sqlite:///./data/prism.db}"
export PRISM_WORKER_MODE="${PRISM_WORKER_MODE:-embedded}"
export PRISM_DEMO_MODE="${PRISM_DEMO_MODE:-true}"
export PRISM_LLM_PROVIDER="${PRISM_LLM_PROVIDER:-mock}"

.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8741 &
API_PID=$!
cleanup() {
  kill "$API_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

for _ in 1 2 3 4 5 6 7 8 9 10; do
  if curl -sf "http://127.0.0.1:8741/api/v1/health" >/dev/null; then
    break
  fi
  sleep 0.4
done

cd "$ROOT/frontend"
if [[ ! -d node_modules ]]; then
  npm ci
fi
export BACKEND_URL="${BACKEND_URL:-http://127.0.0.1:8741}"
exec npx next dev --hostname 0.0.0.0 --port 43123
