#!/usr/bin/env bash
# Plant a fresh incident (timestamps anchored to "now") and wait for ingestion.
# Run this right before the demo so the data looks live.
set -euo pipefail
source "$(dirname "$0")/env.sh"

echo ">> Planting a fresh incident into ADX..."
"$PY" "$CRE_ROOT/data/generate_and_ingest.py" 2>/dev/null | grep -E "planted|queued"

echo ">> Waiting for ingestion..."
for i in $(seq 1 15); do
  n=$("$PY" "$CRE_ROOT/data/q.py" "Telemetry | count | project Count" 2>/dev/null | tail -1 | tr -dc '0-9')
  if [ -n "$n" ] && [ "$n" -eq 7200 ] 2>/dev/null; then echo ">> Ready."; exit 0; fi
  sleep 8
done
echo ">> (still ingesting — give it a few more seconds)"
