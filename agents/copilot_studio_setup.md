# CRE Copilot — Copilot Studio agent setup

The 4 agents live in Copilot Studio and reason over **live ADX** via the built-in
**Azure Data Explorer connector** — they call the stored KQL functions we already
built (`Detect`, `Correlate`, `ImpactAssessment`). The **confidence gate** is a
condition node in the orchestration topic. No deployed Function App required.

> Why this works without the Function tier: the heavy logic already lives *inside*
> ADX as stored functions. Copilot Studio just invokes them and routes on the result.

---

## Prerequisites (one-time)
- Cluster URI: `https://crecopilotadxvxxmsm.eastus.kusto.windows.net`, Database: `CopilotDb`
- In Copilot Studio: **Settings → Generative AI** on (for the Comms drafting).
- Add the **Azure Data Explorer** connector (Power Platform):
  - **Data → Connections → New → "Azure Data Explorer"**, sign in with your Azure account.
  - Your user already has ADX **Admin** on `CopilotDb`, so queries authorize immediately.

Each agent below = one **topic** (or a child agent) that calls a KQL function through the
ADX connector action **"Run KQL query / control command"** and returns the result.

---

## Agent 1 — Detector
- **Purpose:** find anomalous services from raw telemetry.
- **Instructions (system prompt):**
  > You are the Detector. When an incident check is requested, run the ADX query below and
  > report which services/metrics are anomalous, highest score first. If none, report "all healthy."
- **ADX action query:** `Detect()`
- **Output:** table of `Service, Metric, MaxScore, FirstSeen` → pass to the orchestration topic.

## Agent 2 — Correlator  *(the deep one)*
- **Purpose:** given the alerting service + time, rank root-cause candidates with a confidence score.
- **Instructions:**
  > You are the Correlator. Given an alert's service and timestamp, run the query and return the
  > top candidate as the root cause with its confidence. Explain *why* (proximity, anomaly, topology).
- **ADX action query (parameterized):**
  `Correlate('{alertService}', datetime({alertTime}))`
  - Map `alertService`, `alertTime` from the incoming alert (see orchestration topic).
- **Output:** `Service, Version, confidence` for the top row → drives the gate.

## Agent 3 — Impact
- **Purpose:** blast radius of the root cause.
- **Instructions:**
  > You are the Impact agent. Given the root-cause service, list downstream services and how
  > degraded each is right now.
- **ADX action query:** `ImpactAssessment('{rootService}')`
- **Output:** affected services + latency multiples.

## Agent 4 — Comms
- **Purpose:** draft the status update.
- **Instructions (generative):**
  > You are the Comms agent. Using the incident's severity, root cause, confidence, chosen action,
  > and impact, write a concise 4-line status update: headline+severity, root cause with confidence %,
  > impact summary, action taken / next step. Professional, status-page tone.
- **Input:** the fields gathered by the orchestration topic. No ADX call needed — pure generative.

---

## Orchestration topic — chaining + the CONFIDENCE GATE
Wire the agents in a single topic:

1. **Trigger:** "check live site" (or an incoming alert payload with `service`, `time`).
2. Call **Detector**. If it reports healthy → end ("No incident.").
3. Read the alert (ADX action: `Alerts | top 1 by Timestamp desc | project Service, Timestamp, Severity`)
   → set variables `alertService`, `alertTime`, `severity`.
4. Call **Correlator** with those → set `rootService`, `rootVersion`, `confidence`.
5. **CONFIDENCE GATE — Condition node:**
   - `confidence >= 0.70`  → **Act:** message "Auto-remediating: rollback {rootService} {rootVersion}",
     then (optionally) an ADX control command to log the incident (see below).
   - `else`               → **Escalate:** message "Escalating to on-call — confidence {confidence} below
     threshold", hand off to a human / create a ticket.
6. Call **Impact** with `rootService` → set `impact`.
7. Call **Comms** with all fields → post the status update.

**Optional: persist the outcome** (same write the Python orchestrator does) via an ADX control-command action:
```
.set-or-append Incidents <| print IncidentId=strcat("INC-", tostring(now())),
  StartTime=now(), EndTime=now(), Service="{alertService}", Severity="{severity}",
  RootCause="{rootService} {rootVersion}", Status="auto-resolved", ResolvedBy="auto",
  Confidence={confidence}
```

---

## Talking point
> "The agents are thin — the intelligence is stored KQL functions inside ADX, invoked through the
> native connector. The confidence gate is an explicit condition in the orchestration, so act-vs-escalate
> is auditable, not buried in a prompt. Same logic runs headless via the Python orchestrator (`orchestrate.py`)
> — Copilot Studio is just the conversational front door."
