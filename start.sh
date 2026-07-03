#!/usr/bin/env bash
# Start the ADX cluster for a work/demo session and wait until it's ready.
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
RG="rg-cre-copilot"; ADX="crecopilotadxvxxmsm"

echo ">> Starting ADX cluster (takes ~2-5 min)..."
az kusto cluster start --name "$ADX" --resource-group "$RG" --no-wait
echo ">> Waiting until Running..."
until [ "$(az kusto cluster show --name "$ADX" --resource-group "$RG" --query state -o tsv 2>/dev/null)" = "Running" ]; do
  sleep 15; echo "   ...still starting"
done
echo ">> ADX is Running. Ready to work."
echo ">> Next: ./demo/console.sh  (local console)  or  ./demo/reset.sh && ./demo/run.sh"
