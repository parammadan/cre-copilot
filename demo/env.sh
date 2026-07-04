# Source this before running demo commands:  source demo/env.sh
export PATH="/opt/homebrew/bin:$PATH"
export ADX_CLUSTER_URI="https://crecopilotadxvxxmsm.eastus.kusto.windows.net"
export ADX_DATABASE="CopilotDb"
export AZURE_OPENAI_ENDPOINT="https://crecopilot-aoai-vxxmsm.openai.azure.com/"
export AZURE_OPENAI_DEPLOYMENT="gpt-5-mini"
export CRE_ROOT="/Users/parammadan/cre-copilot"
export PY="$CRE_ROOT/data/.venv/bin/python"

# Load local secrets (gitignored) if present — e.g. TEAMS_WEBHOOK_URL
if [ -f "$CRE_ROOT/demo/.env" ]; then set -a; . "$CRE_ROOT/demo/.env"; set +a; fi
