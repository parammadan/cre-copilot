#!/usr/bin/env bash
# Tear down the Container Apps deployment. Keeps ADX + Azure OpenAI + Key Vault
# (created by main.bicep) intact. Use this to stop Container Apps billing.
#
#   ./infra/teardown_containerapps.sh            # delete the 6 apps + env
#   ./infra/teardown_containerapps.sh --all      # also delete ACR + Log Analytics
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
RG="rg-cre-copilot"

echo ">> Deleting container apps..."
for app in cre-backend cre-collector checkout-api payment-service inventory-service auth-service; do
  az containerapp delete -n "$app" -g "$RG" --yes -o none 2>/dev/null && echo "   deleted $app" || true
done

echo ">> Deleting Container Apps environment(s)..."
for e in $(az containerapp env list -g "$RG" --query "[?starts_with(name,'crecopilot-cae')].name" -o tsv); do
  az containerapp env delete -n "$e" -g "$RG" --yes -o none 2>/dev/null && echo "   deleted env $e" || true
done

if [ "${1:-}" = "--all" ]; then
  echo ">> --all: deleting ACR + Log Analytics..."
  for acr in $(az acr list -g "$RG" --query "[?starts_with(name,'crecopilotacr')].name" -o tsv); do
    az acr delete -n "$acr" -g "$RG" --yes -o none && echo "   deleted acr $acr" || true
  done
  for law in $(az monitor log-analytics workspace list -g "$RG" --query "[?starts_with(name,'crecopilot-calaw')].name" -o tsv); do
    az monitor log-analytics workspace delete -g "$RG" -n "$law" --yes -o none && echo "   deleted law $law" || true
  done
fi

echo ">> Done. ADX + Azure OpenAI + Key Vault untouched (stop ADX separately with ./stop.sh)."
