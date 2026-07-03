# CRE Copilot — Demo Script & Talk Track

An ~8-minute live demo: an **Autonomous Incident Commander** — hosted multi-agent AI on
Azure that detects, correlates, decides (with a human gate), and heals a live-site incident.
**Lead with the story. Click things. Let the agents talk.**

---

## 0. Pre-flight (before the call)
```bash
./start.sh                     # start the ADX cluster (~2-5 min)
python demo/inject_incident.py classic   # plant the headline scenario
./demo/console.sh              # serve the console at http://localhost:8000
```
Open **http://localhost:8000** in a browser. Have a terminal ready for the edge cases.

---

## 1. Framing (30 sec, before you click)
> "Live-site incidents have a painful middle: an alert fires, then a human spends 30–90
> minutes on *what changed* and *is it safe to act*. I built an autonomous incident commander —
> four AI agents, hosted on Azure OpenAI, that detect the anomaly, correlate it to a root cause
> with a **confidence score**, and then a **gate** decides: act autonomously when sure, escalate
> to a human when not. Everything's on the Azure stack — Kusto, Azure OpenAI, managed identity."

---

## 2. The headline — the live cure loop (3 min)
Point at the **topology**: payment-service pulsing **RED**, blast radius amber to checkout-api.
> "This is real telemetry in Azure Data Explorer — noisy, with a real incident buried in it."

Click **▶ Run incident response**. Narrate as the agents stream:
- **Commander** lights up → "It's calling my KQL tools live — detect, alerts, trend."
- **Correlator** → "Here's the clever bit: the *most recent* deploy was inventory, but it correctly
  blames **payment-service** — bigger anomaly, upstream in the graph. **Confidence 0.80.**"
- **Impact** → "Blast radius: checkout-api 2.3× slower."
- **Gate** → "Deterministic — 0.80 ≥ 0.70, so it *proposes* auto-remediation."

The **approval card** appears. Pause.
> "Even when it's confident, a human approves — that's the guardrail. An LLM never fires a rollback on its own."

Click **✓ APPROVE & remediate**. Watch topology heal **RED → GREEN**.
> "Rollback applied, latency back to baseline. Note auth stayed amber — low confidence, escalated to a human, *not* auto-touched. Two incidents, two different decisions."

---

## 3. The gate is a real knob (30 sec)
Drag the **confidence-threshold slider** up past 0.80.
> "The gate is policy, not decoration. Raise the bar and the same incident now escalates instead of
> acting. In production you'd set this per action risk — auto-restart at 0.6, auto-rollback at 0.8, DB failover never."

---

## 4. Edge cases — what real CRE engineers face (2–3 min)
Run each in the terminal, then click **▶ Run incident response** again.

**① Proactive catch (the headline for an SRE):**
```bash
python demo/inject_incident.py proactive
```
> "No alert has fired. But the Commander's trend tool sees checkout-api climbing — 263ms now,
> **projected to breach 300ms in ~15 minutes**. A good SRE fixes it *before* it pages. We caught it early."

**② Ambiguous root cause:**
```bash
python demo/inject_incident.py ambiguous
```
> "Two deploys landed close together. The Correlator ranks both — but neither clears 0.70. So it does
> **not** guess; it escalates with ranked candidates for a human. Knowing when you *don't* know is the point."

**③ False alarm:**
```bash
python demo/inject_incident.py falsealarm
```
> "An alert fired — but it was a transient blip that already recovered, and there's no deploy behind it.
> Low confidence, nothing to act on → the system correctly does **nothing**. No self-inflicted outage."

---

## 5. The payoff metric (30 sec) — scroll to the KPIs
> "Across 30 days: autonomous resolution takes **MTTR from 81 minutes to 13** — 6× faster — because it
> acts in seconds on the sure cases and saves humans for the ambiguous ones."

---

## 6. Q&A — crisp, defensible answers
- **"Are these real agents or a script?"** → Hosted **Azure OpenAI Assistant** objects — persistent, server-managed threads, tool-calling. Foundry Agent Service is the newer wrapper over the same primitives; it wasn't enabled on my trial sub, so I used the platform it's built on.
- **"Does the LLM decide to roll back?"** → No. The **Gate calls deterministic code**; the LLM orchestrates and explains. Plus a human approves. Two guardrails.
- **"Could it hallucinate the numbers?"** → Every number (confidence, ratios, blast radius) comes from my **KQL tools**; the agents only narrate them.
- **"How does it tell a real incident from noise?"** → Sustained deviation vs each service's own baseline (`series_decompose_anomalies`), ≥5 min — transient blips are rejected. I can show one it ignores.
- **"Security?"** → Managed identity + RBAC + Key Vault; keyless auth to Azure OpenAI and Kusto. No secrets in code.
- **Honest framing:** hosted prototype on synthetic telemetry; remediation is simulated (writes recovery data). Everything through the *decision* is real.

---

## 7. After the demo — save cost
```bash
./stop.sh      # stops the ADX cluster (the only hourly cost) + local server
```
