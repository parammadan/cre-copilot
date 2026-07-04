# CRE Copilot — Demo Script & Talk Track

An ~8-minute live demo: a multi-agent **live-site incident commander** on Azure that detects,
investigates real evidence, decides (behind a deterministic gate + human approval), heals, and
**independently verifies** recovery. **Lead with the story. Click things. Let the agents *work* —
they don't chat, they investigate.**

> Two ways to run it: **local** (instant, 100% reliable — use this as primary) or the **live Azure
> deployment** (the "and it's actually deployed" flourish). Decide up front which you'll drive.

---

## 0. Pre-flight (before the call)

**Warm everything up — cold ADX + the first Azure OpenAI call are slow and look bad live.**
```bash
./start.sh                              # start the ADX cluster (~2–5 min) — do this FIRST
./services/run_services.sh              # 4 microservices
TELEMETRY_SOURCE=services ./data/.venv/bin/python collector/collector.py &   # real telemetry → ADX
./demo/console.sh                       # console at http://localhost:8000
```
Open **http://localhost:8000/?skip=1**. Click **▶ Run incident response** ONCE to warm the agents,
then reset. Have a terminal ready for the edge cases.

**Live cloud (optional flourish):** `https://cre-backend.redbeach-42251964.eastus.azurecontainerapps.io`
— confirm `…/api/workspace/status` says **READY** before the call.

**Fallback plan:** if the cloud is slow/misbehaving, drive the local console — never debug live.

---

## 1. Framing (30 sec, before you click)
> "Live-site incidents have a painful middle: an alert fires, then a human spends 30–90 minutes on
> *what changed* and *is it safe to act*. I built a multi-agent incident commander — hosted on Azure
> OpenAI — that detects the anomaly, **investigates real evidence**, correlates a root cause with a
> **confidence score**, and a **deterministic gate** decides: act autonomously when sure, escalate to
> a human when not. It's all Azure-native — Kusto, Azure OpenAI, Container Apps, managed identity —
> and it's deployed and running."

## 2. It knows its own health (20 sec) — point at the top of the console
> "Before anything: this **Workspace Status** card is all live backend checks — ADX ping, Azure OpenAI
> reachability, the collector, every microservice's `/health`. Nothing hardcoded. And the **Security &
> Governance** card shows the real posture — managed identity, Key Vault, RBAC, read-only tools,
> deterministic gate, human approval, audit logging."

*(This 20-second beat signals production thinking before you even run an incident.)*

## 3. The headline — the live cure loop (3–4 min)
Point at the **topology**: payment-service pulsing **RED**, blast radius amber to checkout-api.
> "Real telemetry in Azure Data Explorer, collected from real microservices — noisy, with an incident buried in it."

Click **▶ Run incident response**. This is the money shot — narrate the **operations center**:
> "Notice the agents don't chat — they *work*, like an ops room. Each is a live worker with its
> status, the tool it's running, and its last result. And this **evidence feed** on the right is the
> raw tool→result stream."

- **Detector** → "Calling my KQL — `series_decompose_anomalies`. Anomaly on payment-service."
- **Correlator** → "The clever bit: the *most recent* deploy was inventory, but it correctly blames
  **payment-service** — bigger anomaly, upstream in the graph, backed by its logs. **Confidence 0.82.**"
- **Impact** → "Blast radius from the real dependency graph: checkout-api degraded."
- **Gate** → "Deterministic — 0.82 ≥ 0.70, so it *proposes* auto-remediation."

The **approval card** appears. Pause.
> "Even when confident, a human approves — the LLM never fires remediation on its own. That's the guardrail."

Click **✓ Approve**. Then narrate the back half — this is what sets it apart:
> "Approve triggers a **real** `/recover` on the service — topology heals RED→GREEN. Then the
> **Verifier** agent independently re-checks the real `/health` and logs before we close: *'RECOVERY
> CONFIRMED — payment-service healthy, deps healthy, 0 errors.'* And a **Postmortem** agent writes the
> review and authors a runbook if the failure was novel."

## 4. The gate is a real knob (30 sec)
Drag the **confidence-threshold slider** up past 0.82.
> "The gate is policy, not decoration. Raise the bar and the same incident escalates instead of acting.
> In production you'd set this per action-risk — auto-restart at 0.6, auto-rollback at 0.8, DB failover never."

## 5. Microsoft Teams (30 sec, optional)
Click **📣 Post to Teams**.
> "It posts a real Adaptive Card to a Teams channel — root cause, confidence vs gate, blast radius,
> and an Approve action that calls back to remediate. Same human-in-the-loop, in the tool on-call already lives in."

*(If no webhook: it shows the exact card as an honest preview — say so.)*

## 6. Edge cases — what real CRE engineers face (2 min)
Run each in the terminal, then **▶ Run incident response** again.
```bash
python demo/inject_incident.py proactive    # no alert yet — trend projects a breach in ~15 min → catch it early
python demo/inject_incident.py ambiguous     # two deploys, neither clears 0.70 → escalate with ranked candidates
python demo/inject_incident.py falsealarm     # transient blip, no deploy → correctly does NOTHING
```
> "Knowing when you *don't* know — and not acting — is as important as acting."

## 7. The payoff metric (20 sec) — scroll to the KPIs
> "Autonomous resolution takes **MTTR from 81 minutes to 13** — 6× — by acting in seconds on the sure
> cases and saving humans for the ambiguous ones."

---

## 8. Q&A — crisp, defensible answers
- **"Real agents or a script?"** → Hosted **Azure OpenAI Assistant** objects — persistent, server-managed threads, real tool-calling. Not Semantic Kernel (that's a legacy fallback in the repo); the live path is the Assistants API.
- **"Does the LLM decide to roll back?"** → No. The **gate is deterministic Python**; the LLM investigates and explains, a human approves, and a **Verifier** confirms. The LLM has no remediation tool — investigation is read-only.
- **"Could it hallucinate the numbers?"** → Every number (confidence, ratios, blast radius) comes from **KQL tools + real `/health`**; agents only narrate them. Confidence is a fixed formula, not model output.
- **"Real incident vs noise?"** → Sustained deviation vs each service's own baseline (`series_decompose_anomalies`, ≥5 pts); transient blips rejected. I can show one it ignores.
- **"Security?"** → Managed identity everywhere (keyless), Key Vault for the one secret, per-resource RBAC, zero-trust (UI never touches Azure), full audit trail. The `/api/security/status` card reports it live. **Honest gap I'll name myself:** the ADX role is Admin, not least-privilege — documented in `docs/security.md`, and I know how I'd split it.
- **"How would this be real?"** → Swap the synthetic source for Azure Monitor/ICM signals, and wire `/recover` to an Azure DevOps rollback with the gate as a **release gate**. The decision architecture is already the real part.

## 9. Honest framing (say this — it builds trust)
> "To be clear: this is a **demo workspace**. Telemetry is synthetic or from a local microservice lab,
> and remediation is a simulated recovery, not a production rollback. Everything through the *decision* —
> detection, correlation, confidence, the gate, human approval, verification — is real and runs on Azure."

## 10. After the demo — stop the meter
```bash
./stop.sh                                   # stop ADX (the main hourly cost) + local
# cloud: scale apps to 0, then teardown by the deadline:
./infra/teardown_containerapps.sh           # removes the 6 apps + env
```
