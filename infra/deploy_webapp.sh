#!/usr/bin/env bash
# Phase 7 — deploy the console to Azure App Service (hosted prototype, synthetic data).
# PREREQ: App Service compute quota > 0. New trial subs ship with 0 ("Total VMs") — if this
# fails on quota, request an increase (portal -> Quotas -> App Service) or use local run.
# Auth in the app is DefaultAzureCredential, so the Web App's managed identity is used in cloud.
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
RG="rg-cre-copilot"; LOC="eastus"
PLAN="crecopilot-web-plan"; APP="crecopilot-web-$(az account show --query id -o tsv | cut -c1-6)"
SKU="${1:-F1}"   # F1 (free) if quota allows, else pass B1

echo ">> Creating $SKU App Service plan..."
az appservice plan create --name "$PLAN" --resource-group "$RG" --sku "$SKU" --is-linux --location "$LOC" -o none

echo ">> Creating Python web app: $APP"
az webapp create --name "$APP" --resource-group "$RG" --plan "$PLAN" --runtime "PYTHON:3.11" -o none
az webapp identity assign --name "$APP" --resource-group "$RG" -o none
MI=$(az webapp identity show --name "$APP" --resource-group "$RG" --query principalId -o tsv)

echo ">> Granting the web app's managed identity access to ADX + Azure OpenAI..."
SUB=$(az account show --query id -o tsv)
AOAI="/subscriptions/$SUB/resourceGroups/$RG/providers/Microsoft.CognitiveServices/accounts/crecopilot-aoai-vxxmsm"
az role assignment create --assignee "$MI" --role "Cognitive Services OpenAI Contributor" --scope "$AOAI" -o none || true
az kusto database-principal-assignment create --cluster-name crecopilotadxvxxmsm --database-name CopilotDb \
  --resource-group "$RG" --principal-assignment-name webAdmin \
  --principal-id "$MI" --principal-type App --role Admin --tenant-id "$(az account show --query tenantId -o tsv)" -o none || true

echo ">> App settings + startup command..."
az webapp config appsettings set --name "$APP" --resource-group "$RG" --settings \
  ADX_CLUSTER_URI="https://crecopilotadxvxxmsm.eastus.kusto.windows.net" \
  ADX_DATABASE="CopilotDb" \
  AZURE_OPENAI_ENDPOINT="https://crecopilot-aoai-vxxmsm.openai.azure.com/" \
  AZURE_OPENAI_DEPLOYMENT="gpt-5-mini" \
  SCM_DO_BUILD_DURING_DEPLOYMENT=true -o none
az webapp config set --name "$APP" --resource-group "$RG" \
  --startup-file "python -m uvicorn app.server:app --host 0.0.0.0 --port 8000" -o none

echo ">> Packaging + deploying code (app/ + functions/ + requirements)..."
cd "$(dirname "$0")/.."
cp data/requirements.txt requirements.txt
zip -qr /tmp/crecopilot-web.zip app functions requirements.txt
az webapp deploy --name "$APP" --resource-group "$RG" --src-path /tmp/crecopilot-web.zip --type zip -o none
rm -f requirements.txt

echo ">> Deployed: https://${APP}.azurewebsites.net  (first load builds deps; give it a minute)"
echo ">> Tear down after the demo:  ./infra/teardown_webapp.sh"
