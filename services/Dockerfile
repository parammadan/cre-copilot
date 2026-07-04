# One image for all 4 workload microservices. Which one runs is chosen at
# runtime by SERVICE_MODULE (checkout_api | payment_service | inventory_service | auth_service).
# In Azure Container Apps, checkout-api's dependency URLs come from *_URL env vars
# (internal DNS); locally they default to 127.0.0.1 ports.
FROM python:3.11-slim

WORKDIR /app
COPY services/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY services/ /app/

ENV SERVICE_MODULE=payment_service
EXPOSE 8000
# shell form so SERVICE_MODULE expands
CMD uvicorn ${SERVICE_MODULE}:app --host 0.0.0.0 --port 8000
