#!/usr/bin/env bash
# Start the 4 workload microservices locally (ports 8101-8104).
# checkout-api(8101) depends on payment(8102), inventory(8103), auth(8104).
#   ./services/run_services.sh          # start
#   ./services/run_services.sh stop     # stop
set -euo pipefail
export PATH="/opt/homebrew/bin:$PATH"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UVICORN="$ROOT/data/.venv/bin/uvicorn"
cd "$ROOT/services"

if [ "${1:-}" = "stop" ]; then
  pkill -f "uvicorn (checkout_api|payment_service|inventory_service|auth_service):app" 2>/dev/null || true
  echo ">> services stopped"; exit 0
fi

echo ">> starting microservices (logs -> /tmp/cre-*.log)..."
"$UVICORN" payment_service:app   --port 8102 --host 127.0.0.1 >/tmp/cre-payment.log 2>&1 &
"$UVICORN" inventory_service:app --port 8103 --host 127.0.0.1 >/tmp/cre-inventory.log 2>&1 &
"$UVICORN" auth_service:app      --port 8104 --host 127.0.0.1 >/tmp/cre-auth.log 2>&1 &
"$UVICORN" checkout_api:app      --port 8101 --host 127.0.0.1 >/tmp/cre-checkout.log 2>&1 &
sleep 3
echo ">> up:  checkout :8101  payment :8102  inventory :8103  auth :8104"
echo ">> test: curl -s localhost:8101/health | jq"
echo ">> stop: ./services/run_services.sh stop"
