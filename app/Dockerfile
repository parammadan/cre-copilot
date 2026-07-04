# CRE Copilot backend/console. server.py adds ../functions to sys.path, so the
# image mirrors the repo layout: /app/app + /app/functions. Auth via Managed
# Identity (DefaultAzureCredential) in Azure. Config (ADX, Azure OpenAI, service
# URLs, PUBLIC_BASE_URL, TEAMS_WEBHOOK_URL, FRONTEND_MODE) is injected as env / Key Vault refs.

# ---- Stage 1: build the React frontend (Vite) ----
FROM node:20-slim AS webbuild
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build      # → /web/dist

# ---- Stage 2: python backend, with the built SPA baked in ----
FROM python:3.11-slim

WORKDIR /app
COPY app/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY functions/ /app/functions/
COPY app/ /app/app/
# server.py resolves the SPA at <app>/../frontend/dist  →  /app/frontend/dist
COPY --from=webbuild /web/dist /app/frontend/dist

EXPOSE 8000
CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "/app"]
