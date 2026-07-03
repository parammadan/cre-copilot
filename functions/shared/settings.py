"""Single source of truth for tunables. Everything (gate, server, UI, eval) reads from
here — so there's exactly one place to change the threshold, endpoints, or windows.
Values can be overridden by environment variables for different environments."""
import os

# --- the confidence gate (the one number that decides act vs escalate) ---
ACT_THRESHOLD = float(os.environ.get("CRE_ACT_THRESHOLD", "0.70"))

# --- Azure Data Explorer ---
ADX_CLUSTER_URI = os.environ.get("ADX_CLUSTER_URI", "https://crecopilotadxvxxmsm.eastus.kusto.windows.net")
ADX_DATABASE = os.environ.get("ADX_DATABASE", "CopilotDb")

# --- Azure OpenAI ---
AOAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://crecopilot-aoai-vxxmsm.openai.azure.com/")
AOAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
AOAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

# --- detection / correlation windows (used by KQL + eval) ---
CORRELATE_LOOKBACK_MIN = int(os.environ.get("CRE_CORRELATE_LOOKBACK_MIN", "30"))
DETECT_SUSTAINED_POINTS = int(os.environ.get("CRE_DETECT_SUSTAINED_POINTS", "5"))

# --- resilience ---
KUSTO_TIMEOUT_SEC = int(os.environ.get("CRE_KUSTO_TIMEOUT_SEC", "30"))

# --- Microsoft Teams integration ---
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")           # your channel's Incoming Webhook
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000")  # public URL for the Approve callback
