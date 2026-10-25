#!/usr/bin/env bash
# Build the front end into web/dist, then serve it and the API from one process
# on http://127.0.0.1:8000 — localhost only, no auto-reload.
#
#   scripts/serve.sh
set -euo pipefail
cd "$(dirname "$0")/.."

[ -d web/node_modules ] || npm --prefix web ci
npm --prefix web run build

exec .venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8000
