#!/usr/bin/env bash
# CRE Copilot — Phase 0 deploy
# Prereqs: az CLI logged in (`az login`), a subscription selected.
set -euo pipefail

# ---- config -------------------------------------------------------------
RG="rg-cre-copilot"
LOCATION="eastus"    # Functions are gated off, so no VM-quota issue; eastus has the ADX Dev SKU
DEPLOYMENT="cre-copilot-phase0"
# ------------------------------------------------------------------------

echo ">> Using subscription: $(az account show --query name -o tsv)"

echo ">> Creating resource group ${RG} in ${LOCATION}..."
az group create --name "$RG" --location "$LOCATION" -o none

echo ">> Deploying infrastructure (ADX cluster takes ~10-15 min)..."
az deployment group create \
  --resource-group "$RG" \
  --name "$DEPLOYMENT" \
  --template-file "$(dirname "$0")/main.bicep" \
  -o none

echo ">> Done. Key outputs:"
az deployment group show \
  --resource-group "$RG" \
  --name "$DEPLOYMENT" \
  --query "properties.outputs" -o jsonc

cat <<'EOF'

Next:
  Save the outputs above — later phases use ADX_CLUSTER_URI, the Key Vault name, etc.

Cost control (ADX is the only meaningful cost):
  Stop:  az kusto cluster stop  --name <adxClusterName> --resource-group rg-cre-copilot
  Start: az kusto cluster start --name <adxClusterName> --resource-group rg-cre-copilot
EOF
