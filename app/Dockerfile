# CRE Copilot backend/console. server.py adds ../functions to sys.path, so the
# image mirrors the repo layout: /app/app + /app/functions. Auth via Managed
# Identity (DefaultAzureCredential) in Azure. Config (ADX, Azure OpenAI, service
# URLs, PUBLIC_BASE_URL, TEAMS_WEBHOOK_URL) is injected as env / Key Vault refs.
FROM python:3.11-slim

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY functions/ /app/functions/
COPY app/ /app/app/

EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]
