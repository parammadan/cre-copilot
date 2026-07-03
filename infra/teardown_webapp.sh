#!/usr/bin/env bash
# Delete the App Service web app + plan (back to $0 for the hosted console).
# Does NOT touch ADX / Azure OpenAI / Key Vault — those stay for local run.
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
RG="rg-cre-copilot"; PLAN="crecopilot-web-plan"
APP="crecopilot-web-$(az account show --query id -o tsv | cut -c1-6)"

echo ">> Deleting web app $APP ..."
az webapp delete --name "$APP" --resource-group "$RG" 2>/dev/null || true
echo ">> Deleting plan $PLAN ..."
az appservice plan delete --name "$PLAN" --resource-group "$RG" --yes 2>/dev/null || true
echo ">> Done. Hosted console removed; local run + all data services untouched."
