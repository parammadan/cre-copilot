# Telemetry collector — polls the 4 services and ingests real metrics+logs into ADX.
# Runs continuously (TELEMETRY_SOURCE=services). Auth via Managed Identity
# (DefaultAzureCredential) in Azure; service URLs + ADX config come from env.
FROM python:3.11-slim

WORKDIR /app
COPY collector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# collector.py inserts ../functions on sys.path for shared.kusto
COPY functions/ /app/functions/
COPY collector/ /app/collector/

ENV TELEMETRY_SOURCE=services \
    COLLECTOR_INTERVAL_SEC=10
CMD ["python", "collector/collector.py"]
