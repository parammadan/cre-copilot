"""Single source of truth for tunables. Everything (gate, server, UI, eval) reads from
here — so there's exactly one place to change the threshold, endpoints, or windows.
Values can be overridden by environment variables for different environments."""
import os

# --- the confidence gate (the one number that decides act vs escalate) ---
ACT_THRESHOLD = float(os.environ.get("CRE_ACT_THRESHOLD", "0.70"))

# --- demo targeting (OFF by default; never changes production logic) ---
# When on, a sandbox action makes its target the PRIMARY suspect for that run: the agents begin
# there and the console prioritizes that incident. Production (DEMO_MODE off) is untouched — the
# agents investigate purely from telemetry with no operator-supplied bias.
DEMO_MODE = os.environ.get("CRE_DEMO_MODE", "false").lower() == "true"

# --- self-healing safety (permission gate + guardrails; deterministic, no LLM) ---
# A PRE-EXECUTION safety layer evaluated before ANY remediation (human or autonomous):
#   kill switch (emergency stop), rate limit, concurrency cap, and a tier-0 blast-radius rule
#   (autonomous heals of a tier-0 service are downgraded to human approval). Production-safe
#   defaults: permissive for human-approved actions, conservative for autonomous ones.
SELF_HEAL_KILL_SWITCH = os.environ.get("CRE_SELF_HEAL_KILL_SWITCH", "false").lower() == "true"
SELF_HEAL_MAX_PER_HOUR = int(os.environ.get("CRE_SELF_HEAL_MAX_PER_HOUR", "5"))
SELF_HEAL_MAX_CONCURRENT = int(os.environ.get("CRE_SELF_HEAL_MAX_CONCURRENT", "1"))
TIER0_SERVICES = {s.strip() for s in os.environ.get("CRE_TIER0_SERVICES", "auth-service").split(",") if s.strip()}
BLAST_RADIUS_CAP = int(os.environ.get("CRE_BLAST_RADIUS_CAP", "2"))   # > this many downstream → human approval

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

# --- Portal Agent (headed Playwright, LOCAL-ONLY, READ-ONLY, off by default) ---
# Opens a visible Chromium beside the app and navigates deep-links to OUR resources in the
# Azure Portal (read-only). Never enabled in cloud (no display). No secrets — reuses a logged-in
# local browser profile.
AZURE_PORTAL_AGENT_ENABLED = os.environ.get("AZURE_PORTAL_AGENT_ENABLED", "false").lower() == "true"
AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "c1ae007a-d508-435f-b93e-79a29fe07589")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "54fc64b7-f5a8-41cf-a1f2-ba13b2831628")
AZURE_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "rg-cre-copilot")
PORTAL_USER_DATA_DIR = os.environ.get("PORTAL_USER_DATA_DIR", os.path.expanduser("~/.cre-portal-profile"))
AZURE_APPINSIGHTS_NAME = os.environ.get("AZURE_APPINSIGHTS_NAME", "crecopilot-ai-vxxmsm")  # for the Failures/Exceptions blades

# Optional EMBEDDED browser mode: Playwright drives Chromium inside a Docker VNC container; noVNC
# streams it into the console's right panel. Default off → external headed Chromium (fallback).
PORTAL_AGENT_NOVNC = os.environ.get("PORTAL_AGENT_NOVNC", "false").lower() == "true"
PORTAL_CDP_URL = os.environ.get("PORTAL_CDP_URL", "http://localhost:9222")            # container Chromium (CDP)
PORTAL_NOVNC_URL = os.environ.get("PORTAL_NOVNC_URL", "http://localhost:6080/vnc.html?autoconnect=1&resize=scale&reconnect=1")
# Service-name resolution (topology name → real Container App / microservice) now lives in
# shared/services.py (canonical()), used by get_service_health, the Portal Agent, and remediation.
