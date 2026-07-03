#!/usr/bin/env bash
# CRE Copilot — subscription cost guardrail
# Creates a $10/month budget with email alerts at 50% / 80% / 100%.
# NOTE: Azure budgets ALERT (email) — they do not hard-stop spend. The real
# kill switch is: az group delete --name rg-cre-copilot --yes
set -euo pipefail

AMOUNT="${1:-10}"                       # dollars; override: ./set_budget.sh 10
EMAIL="${2:-paramm0583@gmail.com}"      # alert recipient
BUDGET_NAME="cre-copilot-cap"

SUB="$(az account show --query id -o tsv)"
START="$(date -u +%Y-%m-01T00:00:00Z)"          # first of current month
END="$(date -u -v+12m +%Y-%m-01T00:00:00Z 2>/dev/null || date -u -d '+12 months' +%Y-%m-01T00:00:00Z)"

echo ">> Setting \$${AMOUNT} budget '${BUDGET_NAME}' on subscription ${SUB}"

az rest --method put \
  --url "https://management.azure.com/subscriptions/${SUB}/providers/Microsoft.Consumption/budgets/${BUDGET_NAME}?api-version=2023-11-01" \
  --headers "Content-Type=application/json" \
  --body "{
    \"properties\": {
      \"category\": \"Cost\",
      \"amount\": ${AMOUNT},
      \"timeGrain\": \"Monthly\",
      \"timePeriod\": { \"startDate\": \"${START}\", \"endDate\": \"${END}\" },
      \"notifications\": {
        \"alert50\":  { \"enabled\": true, \"operator\": \"GreaterThanOrEqualTo\", \"threshold\": 50,  \"contactEmails\": [\"${EMAIL}\"] },
        \"alert80\":  { \"enabled\": true, \"operator\": \"GreaterThanOrEqualTo\", \"threshold\": 80,  \"contactEmails\": [\"${EMAIL}\"] },
        \"alert100\": { \"enabled\": true, \"operator\": \"GreaterThanOrEqualTo\", \"threshold\": 100, \"contactEmails\": [\"${EMAIL}\"] }
      }
    }
  }" \
  --query "{name:name, amount:properties.amount, alerts:keys(properties.notifications)}" -o jsonc

echo ">> Budget set. Alerts at 50% / 80% / 100% -> ${EMAIL}"
