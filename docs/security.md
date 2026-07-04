# CRE Copilot — Security & Governance

CRE Copilot follows Microsoft's engineering-security principles: **keyless auth, least
privilege, zero trust, deterministic safety controls, and a full audit trail.** This document
states what is *actually* implemented (the console's **Security & Governance** card reports the
same status live at `/api/security/status`).

## 1. Secrets — no keys in code
- **Azure services need no secrets at all** — auth is via managed identity / Entra tokens
  (see §2), so there are no connection strings or API keys to store.
- The only real secret is the **Teams webhook URL**.
  - **Local dev:** `demo/.env` (gitignored) → loaded by `demo/env.sh`. Never committed.
  - **Production (Container Apps):** stored in **Azure Key Vault** and injected as a
    Key Vault *secret reference*; the app reads it from the environment but the value lives in
    Key Vault, fetched at runtime via the managed identity. Nothing is baked into the image.
- CI-safety: `.gitignore` excludes `.env`, `demo/.env`; the repo was scanned to confirm no
  webhook value is tracked.

## 2. Authentication — Managed Identity, no API keys
Every Azure call uses **`DefaultAzureCredential`**:
- **In Azure:** the Container App's **user-assigned managed identity** (Entra tokens). No keys.
- **Locally:** the developer's `az login`. Same code path.

This covers **Azure Data Explorer**, **Azure OpenAI** (keyless — the OpenAI client uses an Entra
token provider, not an API key), and **Key Vault**. No component holds a long-lived credential.

## 3. Least privilege (RBAC)
One user-assigned identity (`crecopilot-mi-*`), scoped per resource:

| Identity | Resource | Role | Why |
|---|---|---|---|
| `crecopilot-mi-*` | Azure Container Registry | **AcrPull** | pull images only (no push) |
| `crecopilot-mi-*` | Azure OpenAI | **Cognitive Services OpenAI User** | invoke the model; not manage the account |
| `crecopilot-mi-*` | Key Vault | **Key Vault Secrets User** | read the webhook secret; not manage the vault |
| `crecopilot-mi-*` | Azure Data Explorer (`CopilotDb`) | **Admin** (scoped to the demo DB) | see note |
| developer (you) | Key Vault / ADX | Secrets Officer / DB Admin | deploy-time only |

> **ADX note (honest):** the identity has **Database Admin on `CopilotDb`** — broader than ideal.
> The backend must **write** recovery telemetry and the collector must **ingest** + create the
> `Logs` table, and ADX `.set-or-append` into tables owned by another principal requires
> table/database-admin rights. A tighter split (backend = Viewer + table-scoped ingest, collector =
> Ingestor) is possible but adds table-ownership plumbing; scoped to a single throwaway demo
> database, Admin is an acceptable, documented trade-off. This is the one item that is *broader than
> least-privilege*, called out rather than hidden.

## 4. Zero trust
- The **browser never touches Azure.** The single-page console calls only the backend HTTP API.
- **All** ADX / Azure OpenAI / Key Vault access is server-side, under the managed identity.
- Each component (backend, 4 microservices, collector) is a separate Container App with its own
  ingress scope — the 4 services + collector are **internal-only**; only the backend is public.

## 5. Read-only investigation
The AI agents can only **read**. Their tools are `detect`, `get_alerts`, `detect_trend`,
`correlate`, `assess_impact`, `get_service_health`, `get_logs` — all read-only queries. **No
remediation tool is exposed to the LLM**, in either the fixed pipeline or dynamic mode. (The only
agent *writes* are knowledge-base entries — a runbook or postmortem row — never a live-system change.)

## 6. Safe remediation — the gate cannot be bypassed
```
agent investigates ▶ deterministic Gate (confidence.py) ▶ human approval ▶ recover() ▶ Verifier ▶ closed
```
- The **gate is pure Python** (`functions/shared/confidence.py`), unit-tested. The LLM never sets a
  confidence value and has no path to act on its own.
- **Remediation is a separate, human-triggered endpoint** (`/api/remediate`, or the Teams Approve
  callback) — not a tool the model can call.
- After remediation, the **Verifier agent** independently confirms recovery from real `/health` +
  logs before the incident is considered closed.

## 7. Audit trail
Structured JSON (one event per line) via `functions/shared/obs.py` → stdout → **App Insights /
Log Analytics** in Azure. Every material action is recorded with a **trace id**:

| Event | Fields |
|---|---|
| `agent.tool_call` | trace, agent, tool, args |
| `gate.decision` | trace, service, confidence, action, threshold |
| `remediation.applied` | service, healed[], **source** (console/teams), **approver**, trace |
| `verify.result` | service, **confirmed** (bool), verdict, trace |
| `runbook.authored` / `postmortem.written` | trace, id, service, novel |

## 8. What's *not* here (honesty)
- **User identity on approvals** — the demo has no end-user auth, so `approver` is recorded as
  `human`/`teams` rather than a specific principal. Real SSO (Entra ID) would populate it. *Planned.*
- **ADX least-privilege split** — see §3; currently Admin on the demo DB.

Everything above is additive and does not change the multi-agent architecture or the demo flow.
