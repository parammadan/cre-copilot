#!/usr/bin/env bash
# Stop all billable activity. Only the ADX cluster has a real hourly cost.
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
RG="rg-cre-copilot"; ADX="crecopilotadxvxxmsm"

echo ">> Stopping ADX cluster (the only meaningful hourly cost)..."
az kusto cluster stop --name "$ADX" --resource-group "$RG" --no-wait
echo ">> Stop requested (fully stops in ~1-2 min). Cluster cost -> ~\$0/hr."

# Stop the local console server if it's running (not billable, just tidy).
pkill -f "uvicorn app.server" 2>/dev/null && echo ">> Local console server stopped." || true

cat <<'EOF'
>> Everything else (Storage, Key Vault, Log Analytics, App Insights, Azure OpenAI)
   has no meaningful idle cost — safe to leave.
>> Azure OpenAI bills per token only; nothing runs when you're not calling it.

Full nuke (deletes EVERYTHING, back to $0, but you'd redeploy next time):
   az group delete --name rg-cre-copilot --yes
EOF
