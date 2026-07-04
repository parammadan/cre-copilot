# Durable Incident State — why it matters

CRE Copilot started as a button-driven demo: incidents were recomputed from ADX on every
request, and escalation/approval state lived in in-memory dicts (`_TEAMS_STATE`, `SIM_STATE`).
That is fine for a single-process demo and wrong for a reliability platform. This document
explains the durable-state design and why each property matters.

## The design
- **ADX is the system of record.** A dedicated append-only table `IncidentRecords` stores one row
  per state transition. Current state = `IncidentRecords | summarize arg_max(UpdatedAt, *) by IncidentId`.
- **Event-driven entry.** `POST /api/alerts/ingest` accepts an Azure Monitor / generic alert,
  writes a real `Alerts` row, creates/reuses a durable incident (dedup by service), runs the
  deterministic gate, and auto-escalates to Teams referencing the persisted `incident_id`.
- **Lifecycle:** `OPEN → INVESTIGATING → AWAITING_APPROVAL → REMEDIATING → VERIFYING →
  RESOLVED | FAILED → CLOSED`. Every transition is appended (never mutated), giving a full history.
- **Persisted fields:** incident_id, service, severity, status, root-cause candidate, confidence,
  gate decision, teams_posted, approval_status, remediation_status, verifier_status, trace_id,
  idempotency keys, created_at, updated_at.

## Why it matters

### 1. Restart safety
The backend can crash, redeploy, or be scaled by the platform at any time. With in-memory state,
an incident that was *awaiting approval* simply disappears on restart — the on-call clicks Approve
in Teams and nothing happens. With ADX as the record, the incident is read back after restart.
*Verified: an ingested incident survived a full `uvicorn` restart.*

### 2. Multi-replica correctness
Container Apps runs 1..N replicas. The investigation may run on replica A while the Teams
`Approve` callback is routed to replica B — which never held that incident in memory. In-memory
state is per-replica and divergent. A shared store means any replica resolves the same incident
identically. This is a correctness bug, not a performance one.

### 3. Teams callback correctness + idempotency
Teams action buttons are just URLs; they can be clicked twice, retried by the client, or opened on
two devices. Remediation is a state-changing action — doing it twice can make an incident worse.
Approval is made idempotent three ways: an in-process in-flight guard (rapid double-click), a
durable status check (`ApprovalStatus == approved` or status already past AWAITING_APPROVAL), and a
recorded idempotency key per incident+action. *Verified: a second Approve returns "Already approved"
and `remediation.applied` fires exactly once.*

### 4. Auditability
Because every transition is appended with a trace id and timestamp, the incident's entire life —
who/what triggered it, the gate decision, when Teams was posted, who approved, what the Verifier
found — is queryable after the fact:
```kusto
IncidentRecords | where IncidentId == 'INC-…' | order by UpdatedAt asc
```
That's the difference between "the demo said it healed" and an auditable record a reliability team
(or an incident review) can trust.

## What did NOT change
- The manual **Run incident response** flow still works; it creates/uses a durable record via the
  escalation step.
- Sandbox controls, the SSE investigation timeline, and the agent pipeline are untouched.
- `/api/state` still returns the transient (rich) incident view for the console **and** now exposes
  `persistedIncidents` from the store.

## Endpoints
| Endpoint | Role |
|---|---|
| `POST /api/alerts/ingest` | event-driven entry: alert → durable incident → gate → escalate |
| `POST /api/escalate` | deterministic escalation over live incidents (post-Run), persisted + deduped |
| `GET /api/teams/state` | active incidents from the durable store |
| `GET /api/teams/approve` | idempotent: remediate → Verifier, updates the record |
| `GET /api/teams/reject` | records rejection, keeps incident open |
| `POST /api/remediate`, `/api/verify` | console flow; also update the durable record |

Store implementation: `functions/shared/incidents.py`. Table schema: `IncidentRecords` (append-only).
