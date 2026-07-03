#!/usr/bin/env bash
# Run the full incident-response flow. Optional arg = gate threshold.
#   ./demo/run.sh        -> normal (payment-service 0.77 -> auto-remediate)
#   ./demo/run.sh 0.80   -> stricter gate (same incident -> escalate to human)
set -euo pipefail
source "$(dirname "$0")/env.sh"
cd "$CRE_ROOT/functions"
"$PY" orchestrate.py ${1:+"$1"} 2>/dev/null
