# CRE Copilot

A multi-agent **live-site incident-response** system on Azure. It detects an anomaly,
correlates it to a root cause with a **confidence score**, and a **confidence gate**
decides whether to **act autonomously** or **escalate to a human** — then computes blast
radius and drafts a status update.

Built to demonstrate SRE / service-engineering + multi-agent AI on the Microsoft/Azure stack.

---

## The idea
Live-site incidents have a slow middle: an alert fires, then a human spends 30–90 minutes on
"what changed?" and "is it safe to act?". CRE Copilot collapses that middle.

```
Telemetry ─▶ ADX (Kusto)
              │
   Detector ──┤  series_decompose_anomalies  → which services are anomalous?
   Correlator ┤  proximity × anomaly × topology → root cause + confidence
              ▼
        CONFIDENCE GATE ──▶ confidence ≥ 0.70 ? ACT autonomously : ESCALATE to human
              │
   Impact  ───┤  downstream blast radius
   Comms   ───┘  status-page update
              ▼
        Incidents table ─▶ dashboard (MTTR, auto vs escalated, confidence)

Security: Key Vault + Managed Identity + RBAC — no secrets in code.
```

## Headline result
Autonomous resolution cuts **MTTR from 81 min → 13 min** (6×) by acting in seconds on
high-confidence cases and reserving humans for the ambiguous ones.

---

## Run it
```bash
# prereqs: az login; ADX cluster running
source demo/env.sh

./demo/reset.sh        # plant two concurrent incidents into ADX
./demo/run.sh          # handles both at once:
                       #   checkout-api → payment-service (0.80) → 🤖 auto-remediate
                       #   auth         → auth v8.0.0     (0.57) → 🧑 escalate to human
./demo/run.sh 0.80     # stricter gate → both escalate (threshold is a real knob)
```

## What's built
| Piece | Where | Notes |
|---|---|---|
| Infra (IaC) | `infra/main.bicep` | ADX, Key Vault (RBAC), managed identity; Function tier behind `deployFunctions` flag |
| Telemetry + planted incident | `data/generate_and_ingest.py` | payment-service deploy → checkout-api cascade + decoys |
| **Detector** | `data/kql/03_detector.kql` | anomaly detection, baseline learned from clean history (`test_points`) |
| **Correlator** | `data/kql/02_functions.kql` | root cause + confidence; beats "blame the latest deploy"; path-aware so concurrent incidents don't cross-blame |
| **Confidence gate** | `functions/shared/confidence.py` | pure, testable act-vs-escalate logic |
| Impact + Comms | `data/kql/04_impact.kql`, `functions/shared/comms.py` | blast radius + status update |
| Orchestrator | `functions/orchestrate.py` | chains all four + the gate; writes `Incidents` |
| Dashboard | `data/kql/05_dashboard.kql` | 7 ADX-dashboard tiles |
| Copilot Studio agents | `agents/copilot_studio_setup.md` | 4 agents via the ADX connector; gate = condition node |

## Notes
- **Functions tier** is gated off (`deployFunctions=false`): new subscriptions ship with 0 App Service
  compute quota. The orchestrator runs locally against the live cloud ADX; flip the flag once a quota
  increase is granted — same code, same managed-identity model.
- Everything authenticates via `az login` locally / managed identity in the cloud. No keys anywhere.
