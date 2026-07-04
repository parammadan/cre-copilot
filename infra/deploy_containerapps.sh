#!/usr/bin/env bash
# Deploy CRE Copilot to Azure Container Apps: 4 microservices + collector + backend.
# Reuses the existing ADX cluster + Azure OpenAI account. Keyless (managed identity).
#
#   ./infra/deploy_containerapps.sh            # build+push images, deploy, wire URLs
#
# Rollback / cost: see teardown_containerapps.sh and the notes at the bottom.
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"

RG="rg-cre-copilot"
LOC="eastus"
ADX="crecopilotadxvxxmsm"
AOAI="crecopilot-aoai-vxxmsm"
AOAI_DEPLOYMENT="gpt-5-mini"
TAG="$(date +%Y%m%d%H%M)"
SUB6="$(az account show --query id -o tsv | tr -d '-' | cut -c1-6)"
ACR="crecopilotacr${SUB6}"                     # globally-unique, alphanumeric
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo ">> [1/4] Ensuring ACR ($ACR) exists..."
az acr show -n "$ACR" -g "$RG" -o none 2>/dev/null || \
  az acr create -n "$ACR" -g "$RG" --sku Basic --location "$LOC" -o none

echo ">> [2/4] Building + pushing 3 images (server-side, no local Docker needed)..."
az acr build -r "$ACR" -t "cre-service:${TAG}"   -f "$ROOT/services/Dockerfile"   "$ROOT" -o none
az acr build -r "$ACR" -t "cre-collector:${TAG}" -f "$ROOT/collector/Dockerfile"  "$ROOT" -o none
az acr build -r "$ACR" -t "cre-backend:${TAG}"   -f "$ROOT/app/Dockerfile"        "$ROOT" -o none

echo ">> [3/4] Deploying Container Apps (bicep)..."
az deployment group create -g "$RG" -f "$ROOT/infra/containerapps.bicep" \
  -p acrName="$ACR" imageTag="$TAG" adxClusterName="$ADX" aoaiName="$AOAI" aoaiDeployment="$AOAI_DEPLOYMENT" \
  -o none

# ADX Admin (not just Viewer): the backend writes recovery telemetry and the collector
# ingests/creates tables, so read-only is not enough. Retried because a brand-new MI can
# take a while to be visible to ADX's directory (restarting the cluster refreshes its cache).
echo ">> [3.5/4] Granting ADX Admin to the managed identity (retry — AAD propagation)..."
MI_CLIENT="$(az identity list -g "$RG" --query "[?starts_with(name,'crecopilot-mi')].clientId | [0]" -o tsv)"
TENANT="$(az account show --query tenantId -o tsv)"
for attempt in $(seq 1 8); do
  if az kusto database-principal-assignment create --cluster-name "$ADX" --database-name CopilotDb \
       --resource-group "$RG" --principal-assignment-name caAdmin \
       --principal-id "$MI_CLIENT" --principal-type App --role Admin --tenant-id "$TENANT" -o none 2>/dev/null; then
    echo "   ADX Admin granted."; break
  fi
  echo "   attempt $attempt failed (principal not yet visible) — retrying in 20s"; sleep 20
done

FQDN="$(az containerapp show -n cre-backend -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"

echo ">> [4/4] Wiring PUBLIC_BASE_URL + Teams webhook (via Key Vault) ..."
if [ -f "$ROOT/demo/.env" ]; then . "$ROOT/demo/.env"; fi
SETTINGS=(PUBLIC_BASE_URL="https://${FQDN}")

# Teams webhook → Key Vault secret referenced by the app's managed identity (no plaintext in config).
KV="$(az keyvault list -g "$RG" --query "[0].name" -o tsv 2>/dev/null)"
if [ -n "${TEAMS_WEBHOOK_URL:-}" ] && [ -n "$KV" ]; then
  KVURI="$(az keyvault show -n "$KV" -g "$RG" --query properties.vaultUri -o tsv)"
  MI_PID="$(az identity list -g "$RG" --query "[?starts_with(name,'crecopilot-mi')].principalId | [0]" -o tsv)"
  MI_ID="$(az identity list -g "$RG" --query "[?starts_with(name,'crecopilot-mi')].id | [0]" -o tsv)"
  echo "   granting the identity 'Key Vault Secrets User' + storing secret..."
  az role assignment create --assignee-object-id "$MI_PID" --assignee-principal-type ServicePrincipal \
    --role "Key Vault Secrets User" --scope "$(az keyvault show -n "$KV" -g "$RG" --query id -o tsv)" -o none 2>/dev/null || true
  az keyvault secret set --vault-name "$KV" --name teams-webhook --value "$TEAMS_WEBHOOK_URL" -o none 2>/dev/null \
    && az containerapp secret set -n cre-backend -g "$RG" \
         --secrets "teams-webhook=keyvaultref:${KVURI}secrets/teams-webhook,identityref:${MI_ID}" -o none 2>/dev/null \
    && SETTINGS+=(TEAMS_WEBHOOK_URL=secretref:teams-webhook KEY_VAULT_URI="$KVURI") \
    && echo "   Teams webhook stored in Key Vault + referenced by the app." \
    || { echo "   (Key Vault wiring skipped — needs Secrets Officer + propagation; falling back to env)"; \
         SETTINGS+=(TEAMS_WEBHOOK_URL="$TEAMS_WEBHOOK_URL"); }
elif [ -n "${TEAMS_WEBHOOK_URL:-}" ]; then
  SETTINGS+=(TEAMS_WEBHOOK_URL="$TEAMS_WEBHOOK_URL")
fi
az containerapp update -n cre-backend -g "$RG" --set-env-vars "${SETTINGS[@]}" -o none

echo ""
echo ">> DONE. Console:  https://${FQDN}"
echo ">> Smoke test:     curl -s https://${FQDN}/api/workspace/status | jq .overall"
echo ">> Security card:  curl -s https://${FQDN}/api/security/status | jq '.implemented,.environment'"
echo ">> Logs:           az containerapp logs show -n cre-backend -g $RG --follow"
