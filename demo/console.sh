#!/usr/bin/env bash
# Launch the CRE Copilot interactive console at http://localhost:8000
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$CRE_ROOT"
echo ">> Console at http://localhost:8000  (Ctrl-C to stop)"
exec "$CRE_ROOT/data/.venv/bin/uvicorn" app.server:app --port 8000 --app-dir "$CRE_ROOT"
