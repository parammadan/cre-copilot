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

FQDN="$(az containerapp show -n cre-backend -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)"

echo ">> [4/4] Wiring PUBLIC_BASE_URL (+ TEAMS_WEBHOOK_URL if set locally)..."
SETTINGS=(PUBLIC_BASE_URL="https://${FQDN}")
if [ -f "$ROOT/demo/.env" ]; then . "$ROOT/demo/.env"; fi
if [ -n "${TEAMS_WEBHOOK_URL:-}" ]; then SETTINGS+=(TEAMS_WEBHOOK_URL="$TEAMS_WEBHOOK_URL"); fi
az containerapp update -n cre-backend -g "$RG" --set-env-vars "${SETTINGS[@]}" -o none

echo ""
echo ">> DONE. Console:  https://${FQDN}"
echo ">> Smoke test:     curl -s https://${FQDN}/api/workspace/status | jq .overall"
echo ">> Logs:           az containerapp logs show -n cre-backend -g $RG --follow"
echo ">> Tip: TEAMS_WEBHOOK_URL is best stored in Key Vault; this script reads demo/.env for convenience."
