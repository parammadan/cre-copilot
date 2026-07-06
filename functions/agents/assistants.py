"""Multi-agent incident response on the Azure OpenAI ASSISTANTS platform.

Each agent is a HOSTED assistant object on Azure OpenAI (persistent, server-managed
threads/runs) — not a local script. They hand off on a shared thread and call the
EXISTING KQL / deterministic gate as function tools.

Run (plain):  cd functions && PYTHONPATH=. AZURE_OPENAI_ENDPOINT=... ../data/.venv/bin/python -m agents.assistants
"""
from __future__ import annotations
import json
import os
import time
import warnings

warnings.filterwarnings("ignore")
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AzureOpenAI

from shared import kusto, obs, settings, security
from shared.confidence import decide

# Tools whose output contains UNTRUSTED, attacker-influenceable text (log lines, alert
# descriptions) — scanned for prompt injection before it can influence the model.
_UNTRUSTED_TOOLS = {"get_logs", "get_alerts", "get_container_app_logs", "get_container_app_system_logs"}

_TRACE = None  # correlation id for the current incident run

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://crecopilot-aoai-vxxmsm.openai.azure.com/")
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

_tp = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
client = AzureOpenAI(azure_endpoint=ENDPOINT, azure_ad_token_provider=_tp, api_version=API_VERSION)

KICKOFF = "A live-site incident was triggered. Run the incident response end to end."


def _kickoff(base: str, target: str | None, event: str | None) -> str:
    """DEMO_MODE only: bias THIS RUN's kickoff message so the operator's sandbox target is the
    primary suspect. This is a per-run user message — it never changes the agents' persistent
    instructions or the deterministic gate, so production (DEMO_MODE off) is unaffected."""
    if not (settings.DEMO_MODE and target):
        return base
    return (f"The operator just triggered a '{event or 'fault'}' on the service '{target}'. "
            f"Treat '{target}' as the PRIMARY suspect and BEGIN the investigation there — inspect "
            f"'{target}' first (get_service_health('{target}'), get_logs('{target}'), "
            f"get_container_app_status('{target}')). Only name a DIFFERENT root cause if correlate() "
            f"explicitly ranks another service higher with clear evidence. " + base)

# ---- tool implementations (reuse the KQL + gate) --------------------------
_KNOWN = None
def _svc(s):
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = set(kusto.query("Telemetry | distinct Service")["Service"].tolist())
    s = str(s)
    for k in _KNOWN:
        if k in s:
            return k
    return "".join(c for c in s if c.isalnum() or c in "-_")[:40]

def t_detect(_):
    df = kusto.query("Detect()"); return df.to_json(orient="records") if not df.empty else "[]"
def t_alerts(_):
    df = kusto.query("let amax=toscalar(Alerts|summarize max(Timestamp)); Alerts|where Timestamp>amax-60m|project Service,Timestamp,Severity,Description|order by Timestamp asc")
    import pandas as pd
    return json.dumps([{"service": r.Service, "time_iso": pd.Timestamp(r.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        "severity": r.Severity, "description": r.Description} for r in df.itertuples(index=False)])
def t_trend(_):
    df = kusto.query("DetectTrend()"); return df.to_json(orient="records") if not df.empty else "[]"
def t_correlate(a):
    ts = "".join(c for c in str(a.get("alert_time_iso","")) if c.isalnum() or c in "-:T.Z")
    return kusto.query(f"Correlate('{_svc(a.get('alert_service',''))}', datetime({ts}))").to_json(orient="records")
def t_impact(a):
    return kusto.query(f"ImpactAssessment('{_svc(a.get('service_name',''))}')").to_json(orient="records")


# ---- EVIDENCE tools (real service state; graceful if services aren't running) ----
from shared.services import service_base as _service_base, canonical as _canonical  # noqa: E402
# service naming + reachability come from shared/services.py (one source of truth)


def t_service_health(a):
    import requests
    svc = _canonical(_svc(a.get("service_name", "")))    # e.g. 'auth' → 'auth-service'
    base = _service_base(svc)
    if not base:
        return json.dumps({"service": svc, "status": "unknown"})
    try:
        return json.dumps(requests.get(f"{base}/health", timeout=1.5).json())
    except Exception:
        return json.dumps({"service": svc, "status": "unreachable", "note": "service not running — rely on telemetry evidence"})


def t_get_logs(a):
    svc = _svc(a.get("service_name", ""))
    try:
        c = kusto.query(f"Logs | where Service=='{svc}' and Timestamp>ago(2h) "
                        "| summarize errors=countif(Level in ('ERROR','FATAL')), warns=countif(Level=='WARN'), total=count()")
        counts = c.to_dict("records")[0] if not c.empty else {"errors": 0, "warns": 0, "total": 0}
        s = kusto.query(f"Logs | where Service=='{svc}' and Level in ('ERROR','FATAL','WARN') "
                        "| top 4 by Timestamp desc | project Level, Message")
        return json.dumps({"service": svc, "errors": int(counts["errors"]), "warns": int(counts["warns"]),
                           "samples": s.to_dict("records")})
    except Exception as e:
        return json.dumps({"service": svc, "error": str(e)[:120]})
# ---- LIVE AZURE RESOURCE tools (read-only Container Apps state via az CLI) ----
from shared import azure_resources as _azr  # noqa: E402


def _tail(a):
    try:
        return max(1, min(50, int(a.get("tail", 20))))
    except Exception:
        return 20


def t_ca_status(a):    return json.dumps(_azr.get_container_app_status(_svc(a.get("service_name", ""))))
def t_ca_revisions(a): return json.dumps(_azr.get_container_app_revisions(_svc(a.get("service_name", ""))))
def t_ca_limits(a):    return json.dumps(_azr.get_container_app_resource_limits(_svc(a.get("service_name", ""))))
def t_ca_logs(a):      return json.dumps(_azr.get_container_app_logs(_svc(a.get("service_name", "")), _tail(a)))
def t_ca_syslogs(a):   return json.dumps(_azr.get_container_app_system_logs(_svc(a.get("service_name", "")), _tail(a)))


def t_gate(a):
    d = decide(float(a.get("confidence",0)), _svc(a.get("service_name","")), str(a.get("version",""))[:20])
    obs.log_decision(_TRACE, _svc(a.get("service_name","")), d.confidence, d.action, d.threshold)
    return json.dumps({"action": d.action, "confidence": d.confidence, "threshold": d.threshold,
                       "remediation": d.remediation, "reason": d.reason})

def t_match_runbook(a):
    svc = _svc(a.get("service_name", ""))
    df = kusto.query(f"Runbooks | where Service=='{svc}' | top 1 by CreatedAt desc")
    if df.empty:
        return json.dumps({"match": False, "service": svc, "signature": f"{svc}:bad_deploy"})
    r = df.iloc[0]
    return json.dumps({"match": True, "runbookId": r.RunbookId, "signature": r.Signature,
                       "steps": r.Steps, "timesUsed": int(r.TimesUsed)})


def t_write_runbook(a):
    svc = _svc(a.get("service_name", ""))
    steps = str(a.get("steps", ""))[:400].replace('"', "'").replace("\n", " ")
    rid = "RB-" + str(int(time.time()))[-5:]
    kusto.command(
        f".set-or-append Runbooks <| print RunbookId='{rid}', Signature='{svc}:bad_deploy', "
        f"Service='{svc}', FailureType='bad_deploy', Steps=\"{steps}\", CreatedBy='agent', "
        "CreatedAt=now(), TimesUsed=long(0)")
    obs.log("runbook.authored", trace=_TRACE, runbook=rid, service=svc)
    return json.dumps({"created": rid, "service": svc, "steps": steps})


def t_write_postmortem(a):
    svc = _svc(a.get("service_name", ""))
    review = str(a.get("review", ""))[:1500].replace('"', "'").replace("\n", " ")
    novel = str(a.get("novel", False)).lower() in ("true", "1", "yes")
    pid = "PM-" + str(int(time.time()))[-6:]
    kusto.command(
        f".set-or-append Postmortems <| print PostmortemId='{pid}', Service='{svc}', "
        f"RootCause='{_svc(a.get('root_cause', svc))}', Review=\"{review}\", "
        f"Novel={'true' if novel else 'false'}, CreatedAt=now()")
    obs.log("postmortem.written", trace=_TRACE, postmortem=pid, service=svc, novel=novel)
    return json.dumps({"created": pid, "service": svc, "novel": novel})


DISPATCH = {"detect": t_detect, "get_alerts": t_alerts, "detect_trend": t_trend,
            "correlate": t_correlate, "assess_impact": t_impact, "apply_gate": t_gate,
            "match_runbook": t_match_runbook, "write_runbook": t_write_runbook,
            "write_postmortem": t_write_postmortem,
            "get_service_health": t_service_health, "get_logs": t_get_logs,
            "get_container_app_status": t_ca_status, "get_container_app_revisions": t_ca_revisions,
            "get_container_app_resource_limits": t_ca_limits, "get_container_app_logs": t_ca_logs,
            "get_container_app_system_logs": t_ca_syslogs}


def _safe_dispatch(name, args):
    """Run a tool; on ANY error return an error result to the agent instead of crashing the run.
    A transient ADX/network blip in one tool must not kill the whole investigation."""
    try:
        return DISPATCH.get(name, lambda a: "{}")(args)
    except Exception as e:
        obs.log("tool.error", tool=name, error=str(e)[:200])
        return json.dumps({"error": f"{name} failed: {str(e)[:160]}",
                           "note": "transient tool error — proceed with the evidence you have"})

def _fn(name, desc, props=None, required=None):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}

TOOLDEFS = {
    "detect": _fn("detect", "Detect anomalous services in live telemetry (rejects noise). JSON."),
    "get_alerts": _fn("get_alerts", "Currently firing alerts. JSON with service, time_iso, severity."),
    "detect_trend": _fn("detect_trend", "Services with a rising latency trend projected to breach soon (proactive). JSON."),
    "correlate": _fn("correlate", "Rank root-cause deploy candidates for one alert.",
                     {"alert_service": {"type": "string"}, "alert_time_iso": {"type": "string"}},
                     ["alert_service", "alert_time_iso"]),
    "assess_impact": _fn("assess_impact", "Downstream blast radius for one service.",
                         {"service_name": {"type": "string"}}, ["service_name"]),
    "apply_gate": _fn("apply_gate", "Apply the DETERMINISTIC confidence gate (not an LLM decision).",
                      {"confidence": {"type": "number"}, "service_name": {"type": "string"}, "version": {"type": "string"}},
                      ["confidence", "service_name", "version"]),
    "match_runbook": _fn("match_runbook", "Look up a runbook matching the root-cause service's failure signature.",
                         {"service_name": {"type": "string"}}, ["service_name"]),
    "write_runbook": _fn("write_runbook", "Author a new runbook for a service and add it to the store.",
                         {"service_name": {"type": "string"}, "steps": {"type": "string"}}, ["service_name", "steps"]),
    "write_postmortem": _fn("write_postmortem", "Store the post-incident review.",
                            {"service_name": {"type": "string"}, "review": {"type": "string"},
                             "novel": {"type": "boolean"}, "root_cause": {"type": "string"}},
                            ["service_name", "review"]),
    "get_service_health": _fn("get_service_health", "Live /health of a service (status + dependency checks).",
                              {"service_name": {"type": "string"}}, ["service_name"]),
    "get_logs": _fn("get_logs", "Error/warning counts + recent log lines for a service from the Logs table.",
                    {"service_name": {"type": "string"}}, ["service_name"]),
    "get_container_app_status": _fn("get_container_app_status",
        "LIVE Azure Container App state (read-only): provisioning/running status, active revision, replica count, CPU/memory limits.",
        {"service_name": {"type": "string"}}, ["service_name"]),
    "get_container_app_revisions": _fn("get_container_app_revisions",
        "LIVE Azure revision history (read-only): each revision's active flag, traffic %, replicas, health, created time. Shows a recent deploy.",
        {"service_name": {"type": "string"}}, ["service_name"]),
    "get_container_app_resource_limits": _fn("get_container_app_resource_limits",
        "LIVE Azure resource limits (read-only): CPU/memory per container and min/max replica scale bounds.",
        {"service_name": {"type": "string"}}, ["service_name"]),
    "get_container_app_logs": _fn("get_container_app_logs",
        "LIVE Azure CONSOLE logs (read-only): recent app stdout/stderr from the running replica.",
        {"service_name": {"type": "string"}, "tail": {"type": "integer"}}, ["service_name"]),
    "get_container_app_system_logs": _fn("get_container_app_system_logs",
        "LIVE Azure SYSTEM logs (read-only): platform events (scaling, health, restarts) + a restart-signal count.",
        {"service_name": {"type": "string"}, "tail": {"type": "integer"}}, ["service_name"]),
}

AGENTS = [
    ("Commander", ["detect", "get_alerts", "detect_trend", "get_service_health", "get_logs", "get_container_app_status"],
     "You are the Incident Commander. Call detect(), get_alerts(), detect_trend(); then for each anomalous "
     "service call get_service_health(), get_logs(), and get_container_app_status() to gather REAL evidence "
     "(telemetry + LIVE Azure resource state). Output terse lines citing that evidence, e.g. "
     "'payment-service /health=degraded, Azure running/1 replica, Logs 38 errors (score 95)', 'ALERT checkout-api Sev2', "
     "'TREND checkout-api → breach ~15m'. No prose, no next-steps. End with 'Correlator →'."),
    ("Correlator", ["correlate", "get_logs", "get_container_app_status", "get_container_app_revisions",
                    "get_container_app_resource_limits", "get_container_app_logs", "get_container_app_system_logs"],
     "You are the Correlator + Diagnostician. For the top alert: (1) call correlate(alert_service, alert_time_iso) "
     "for the ADX root-cause ranking + confidence; (2) call get_container_app_revisions() and get_container_app_status() "
     "on the root-cause candidate for LIVE Azure resource evidence (recent revision, running state, replicas, CPU/mem); "
     "(3) call get_logs() and get_container_app_system_logs() (and get_container_app_logs() if useful) for log evidence. "
     "Then output a DIAGNOSIS with EXACTLY these labelled lines, numbers from tools only:\n"
     "Root cause: <service> <version>\n"
     "Evidence (ADX): <ratio>x anomaly, upstream, <n>m before deploy | conf <0.xxx>\n"
     "Evidence (Azure resource): revision <rev> active, <runningStatus>, <r> replicas, <cpu>/<mem>\n"
     "Evidence (logs): <e> errors/<w> warns, <restartSignals> restart signals; <one representative line>\n"
     "Recommended action: <rollback to previous revision | restart | scale> \n"
     "Confidence: <0.xxx>\n"
     "Human approval required: <YES if conf < 0.70, else NO — the Gate makes the binding call>\n"
     "If any live-Azure tool returns available=false, write 'Evidence (Azure resource): unavailable (<note>)' and "
     "continue with ADX + logs. Never invent numbers. End with 'Impact →'."),
    ("Impact", ["assess_impact"],
     "You are the Impact analyst. Call assess_impact(service_name) for each root cause. "
     "Output ONE line per affected service ONLY: '<service> <ratio>x latency (degraded)'. No prose. End with 'Gate →'."),
    ("Gate", ["apply_gate"],
     "You are the Gate — deterministic, you do NOT decide. Call apply_gate(confidence, service_name, version) for each root cause. "
     "Output ONE line each ONLY: '<service> → AUTO-REMEDIATE (conf ≥ 0.70)' or '<service> → ESCALATE (conf < 0.70)'. "
     "No essays, no next-steps, no offers to run commands. Stop after the decisions."),
    ("Runbook", ["match_runbook"],
     "You are the Runbook agent, running DURING triage. Call match_runbook(service_name) for the root cause. "
     "If match=true: output ONE line 'RUNBOOK <id> matched (used <n>×): <steps>' — suggest this known fix now. "
     "If match=false: output ONE line 'NOVEL incident — no runbook matches this signature; the Postmortem agent "
     "will author one after resolution.' You do NOT author runbooks (that's the Postmortem agent). One line only."),
]


def _ensure_assistants():
    """Create the 4 hosted assistants (idempotent by name)."""
    existing = {a.name: a for a in client.beta.assistants.list(limit=100).data if a.name and a.name.startswith("CRE-")}
    out = []
    for name, tools, instr in AGENTS:
        key = f"CRE-{name}"
        tdefs = [TOOLDEFS[t] for t in tools]
        if key in existing:
            a = client.beta.assistants.update(existing[key].id, model=MODEL, instructions=instr, tools=tdefs)
        else:
            a = client.beta.assistants.create(model=MODEL, name=key, instructions=instr, tools=tdefs)
        out.append((name, a.id))
    return out


def _run_agent(thread_id, assistant_id):
    run = client.beta.threads.runs.create(thread_id=thread_id, assistant_id=assistant_id)
    while run.status in ("queued", "in_progress", "requires_action"):
        if run.status == "requires_action":
            outs = []
            for tc in run.required_action.submit_tool_outputs.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                outs.append({"tool_call_id": tc.id, "output": _safe_dispatch(tc.function.name, args)})
            run = client.beta.threads.runs.submit_tool_outputs(thread_id=thread_id, run_id=run.id, tool_outputs=outs)
        else:
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
    return run


_COPILOT_ID = None


def _copilot():
    """A single conversational assistant with ALL tools — powers 'Ask CRE Copilot'."""
    global _COPILOT_ID
    if _COPILOT_ID:
        return _COPILOT_ID
    tools = [TOOLDEFS[t] for t in ("detect", "get_alerts", "detect_trend", "correlate",
                                   "assess_impact", "apply_gate", "match_runbook", "write_runbook",
                                   "get_service_health", "get_logs",
                                   "get_container_app_status", "get_container_app_revisions",
                                   "get_container_app_resource_limits", "get_container_app_logs",
                                   "get_container_app_system_logs")]
    instr = ("You are CRE Copilot, an SRE assistant for the live incident console. Answer questions about "
             "the CURRENT live-site state using your tools — anomalies (detect), alerts (get_alerts), "
             "rising trends (detect_trend), root cause + confidence (correlate), blast radius (assess_impact), "
             "the act-vs-escalate decision (apply_gate), and runbooks (match_runbook). Use ONLY numbers the "
             "tools return; never invent them. Be concise and concrete (a few sentences). If asked whether to "
             "act, explain what the deterministic gate would decide and why.")
    existing = {a.name: a for a in client.beta.assistants.list(limit=100).data if a.name == "CRE-Copilot"}
    if "CRE-Copilot" in existing:
        a = client.beta.assistants.update(existing["CRE-Copilot"].id, model=MODEL, instructions=instr, tools=tools)
    else:
        a = client.beta.assistants.create(model=MODEL, name="CRE-Copilot", instructions=instr, tools=tools)
    _COPILOT_ID = a.id
    return _COPILOT_ID


def ask(question: str, thread_id: str | None = None):
    """One-shot (thread-preserving) Q&A for the Copilot chat. Returns (answer, thread_id)."""
    aid = _copilot()
    if not thread_id:
        thread_id = client.beta.threads.create().id
    client.beta.threads.messages.create(thread_id=thread_id, role="user", content=question[:1000])
    obs.log("copilot.ask", question=question[:120])
    _run_agent(thread_id, aid)
    msg = client.beta.threads.messages.list(thread_id=thread_id, order="desc", limit=1).data[0]
    answer = msg.content[0].text.value if msg.content else "(no answer)"
    return answer, thread_id


_PM_ID = None


def _postmortem_agent():
    global _PM_ID
    if _PM_ID:
        return _PM_ID
    tools = [TOOLDEFS[t] for t in ("match_runbook", "write_runbook", "write_postmortem")]
    instr = ("You are the Postmortem agent — you run AFTER an incident resolves. From the incident facts given, "
             "write a concise post-incident review with these sections: What happened, Root cause, Impact, "
             "Remediation, Follow-ups. THEN call match_runbook(service_name): if match=false the incident was "
             "NOVEL — call write_runbook(service_name, steps) to add a runbook distilled from this incident; "
             "if match=true, note the existing runbook was reused. Finally call "
             "write_postmortem(service_name, review, novel, root_cause) to store it. Return the review text.")
    existing = {a.name: a for a in client.beta.assistants.list(limit=100).data if a.name == "CRE-Postmortem"}
    if "CRE-Postmortem" in existing:
        a = client.beta.assistants.update(existing["CRE-Postmortem"].id, model=MODEL, instructions=instr, tools=tools)
    else:
        a = client.beta.assistants.create(model=MODEL, name="CRE-Postmortem", instructions=instr, tools=tools)
    _PM_ID = a.id
    return _PM_ID


def postmortem(service: str, facts: str) -> str:
    """Run the Postmortem agent after resolution: writes the review + authors a runbook if novel."""
    aid = _postmortem_agent()
    tid = client.beta.threads.create().id
    client.beta.threads.messages.create(
        thread_id=tid, role="user",
        content=f"Incident on {service} has RESOLVED. Facts:\n{facts}\n\nWrite the post-incident review and handle the runbook per your instructions.")
    obs.log("postmortem.run", service=service)
    _run_agent(tid, aid)
    msg = client.beta.threads.messages.list(thread_id=tid, order="desc", limit=1).data[0]
    return msg.content[0].text.value if msg.content else "(no review)"


_VER_ID = None


def _verifier_agent():
    global _VER_ID
    if _VER_ID:
        return _VER_ID
    tools = [TOOLDEFS[t] for t in ("get_service_health", "get_logs", "assess_impact",
                                   "get_container_app_status", "get_container_app_revisions",
                                   "get_container_app_system_logs")]
    instr = ("You are the Verifier — you INDEPENDENTLY confirm recovery AFTER remediation. Call "
             "get_service_health(service_name), get_logs(service_name), and get_container_app_status(service_name) "
             "(plus get_container_app_system_logs / get_container_app_revisions if useful). Confirm the service /health "
             "is healthy, its LIVE Azure revision is running/healthy with no fresh restart signals, and error counts are "
             "low. Output ONE line: 'RECOVERY CONFIRMED — <service> healthy, Azure revision <rev> running, <n> errors' OR "
             "'NOT CONFIRMED — <reason>'. Evidence only, no prose.")
    existing = {a.name: a for a in client.beta.assistants.list(limit=100).data if a.name == "CRE-Verifier"}
    if "CRE-Verifier" in existing:
        a = client.beta.assistants.update(existing["CRE-Verifier"].id, model=MODEL, instructions=instr, tools=tools)
    else:
        a = client.beta.assistants.create(model=MODEL, name="CRE-Verifier", instructions=instr, tools=tools)
    _VER_ID = a.id
    return _VER_ID


def verify(service: str) -> str:
    """Independent recovery check after remediation — real /health + logs evidence."""
    aid = _verifier_agent()
    tid = client.beta.threads.create().id
    client.beta.threads.messages.create(thread_id=tid, role="user",
                                        content=f"Remediation was applied to {service}. Independently verify recovery now.")
    obs.log("verify.run", service=service)
    _run_agent(tid, aid)
    msg = client.beta.threads.messages.list(thread_id=tid, order="desc", limit=1).data[0]
    return msg.content[0].text.value if msg.content else "(no verdict)"


def run():
    agents = _ensure_assistants()
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=KICKOFF)
    for name, aid in agents:
        _run_agent(thread.id, aid)
        msg = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1).data[0]
        text = msg.content[0].text.value if msg.content else ""
        print(f"\n{'='*68}\n  {name}  (hosted assistant {aid})\n{'='*68}\n{text}")


def _arg_target(args: dict) -> str:
    """The service a tool call is acting on — a REAL tool input, surfaced so each agent card can
    show its input target (never fabricated; empty if the tool takes no service)."""
    if not isinstance(args, dict):
        return ""
    return str(args.get("service_name") or args.get("alert_service") or args.get("service") or "")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _humanize(tool: str, out: str) -> str:
    """A short, human-readable summary DERIVED FROM THE REAL (full) tool output — e.g. '3 anomalies
    found', 'top: inventory v9.9.9 conf 0.82'. Display metadata only; it never alters the tool
    result, the KQL, the correlation, or the gate. Falls back to a raw snippet if parsing fails."""
    try:
        d = json.loads(out) if isinstance(out, str) else out
    except Exception:
        d = None
    ln = len(d) if isinstance(d, list) else 0
    if tool == "detect":
        return f"{ln} anomal{'y' if ln == 1 else 'ies'} found" if ln else "no anomalies"
    if tool == "get_alerts":
        sev = d[0].get("severity") if ln and isinstance(d[0], dict) else ""
        return (f"{ln} alert(s)" + (f", top {sev}" if sev else "")) if ln else "no alerts"
    if tool == "detect_trend":
        return f"{ln} rising trend(s)" if ln else "no rising trends"
    if tool == "correlate":
        if ln and isinstance(d[0], dict):
            r = d[0]
            return f"top: {r.get('Service')} {r.get('Version', '')} conf {float(r.get('confidence', 0)):.2f}"
        return "no correlation candidates"
    if tool == "assess_impact":
        if ln:
            w = max(d, key=lambda x: x.get("LatencyIncrease", 0))
            return f"{ln} downstream affected; worst {w.get('AffectedService')} {w.get('LatencyIncrease')}x"
        return "no downstream impact"
    if isinstance(d, dict):
        if tool == "apply_gate":
            return f"{d.get('action')} · conf {d.get('confidence')} vs threshold {d.get('threshold')}"
        if tool == "match_runbook":
            return f"matched {d.get('runbookId')}" if d.get("match") else "no runbook — novel incident"
        if tool == "get_service_health":
            return f"{d.get('service')} {d.get('status')}"
        if tool == "get_logs":
            return f"{d.get('errors', 0)} errors / {d.get('warns', 0)} warns"
        if tool == "get_container_app_status":
            return (f"{d.get('service')} {d.get('runningStatus')}, rev {d.get('activeRevision')}"
                    if d.get("available") else f"{d.get('service')} unavailable")
        if tool == "get_container_app_revisions":
            return f"{d.get('count', 0)} revision(s)" if d.get("available") else "revisions unavailable"
        if tool == "get_container_app_resource_limits":
            return (f"cpu {d.get('cpu')} / mem {d.get('memory')}" if d.get("available") else "limits unavailable")
        if tool == "get_container_app_logs":
            return f"{d.get('count', 0)} console log lines" if d.get("available") else "console logs unavailable"
        if tool == "get_container_app_system_logs":
            return (f"{d.get('count', 0)} system events, {d.get('restartSignals', 0)} restart signals"
                    if d.get("available") else "system logs unavailable")
    return (str(out)[:70] + "…") if out and len(str(out)) > 70 else str(out or "")


def _consume(stream, thread_id, name):
    """Yield token/tool_call events from a run stream; handle tool calls + continue."""
    with stream as s:
        for event in s:
            et = event.event
            if et == "thread.message.delta":
                for block in (event.data.delta.content or []):
                    if getattr(block, "type", "") == "text" and block.text and block.text.value:
                        yield {"type": "token", "agent": name, "text": block.text.value}
            elif et == "thread.run.requires_action":
                run_obj = event.data
                outs = []
                for tc in run_obj.required_action.submit_tool_outputs.tool_calls:
                    obs.log_tool(_TRACE, name, tc.function.name)
                    args = json.loads(tc.function.arguments or "{}")
                    tgt = _arg_target(args)
                    yield {"type": "tool_call", "agent": name, "tool": tc.function.name, "target": tgt,
                           "args": args, "trace": _TRACE, "ts": _now_ms()}
                    _t0 = time.time()
                    out = _safe_dispatch(tc.function.name, args)
                    _ms = int((time.time() - _t0) * 1000)
                    _inj = security.scan_untrusted(out) if tc.function.name in _UNTRUSTED_TOOLS else None
                    if _inj and not _inj["clean"]:
                        obs.log("security.prompt_injection_flagged", tool=tc.function.name,
                                matches=[m["tag"] for m in _inj["matches"]], trace=_TRACE)
                    yield {"type": "evidence", "agent": name, "tool": tc.function.name, "target": tgt,
                           "summary": str(out)[:180], "human": _humanize(tc.function.name, out),
                           "args": args, "raw": str(out)[:6000], "ms": _ms, "trace": _TRACE, "ts": _now_ms(),
                           "injection": _inj}
                    outs.append({"tool_call_id": tc.id, "output": out})
                nxt = client.beta.threads.runs.submit_tool_outputs_stream(
                    thread_id=thread_id, run_id=run_obj.id, tool_outputs=outs)
                yield from _consume(nxt, thread_id, name)
                return


def run_stream_sync(target: str | None = None, event: str | None = None):
    """Sync generator of UI events across the hosted assistants (bridged to SSE by the server).
    In DEMO_MODE, `target` (the sandbox service) becomes the primary suspect for this run."""
    global _TRACE
    _TRACE = obs.new_trace()
    obs.log("incident.run_started", trace=_TRACE, target=target, sim_event=event)
    agents = _ensure_assistants()
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=_kickoff(KICKOFF, target, event))
    yield {"type": "incident_start", "trace": _TRACE, "target": target if settings.DEMO_MODE else None, "ts": _now_ms()}
    for name, aid in agents:
        yield {"type": "agent_start", "agent": name, "trace": _TRACE, "ts": _now_ms()}
        yield from _consume(client.beta.threads.runs.stream(thread_id=thread.id, assistant_id=aid), thread.id, name)
        yield {"type": "agent_end", "agent": name, "trace": _TRACE, "ts": _now_ms()}
    obs.log("incident.run_done", trace=_TRACE)
    yield {"type": "done"}


# ===================== DYNAMIC (autonomous) INVESTIGATION MODE =====================
# One orchestrator agent decides which READ-ONLY tool to call next, based on evidence.
# Guardrails: read-only toolset (+ deterministic apply_gate), max 8 tool calls, NO
# remediation tool, human approval still required before recovery. Falls back to fixed.
_DYN_ID = None
MAX_STEPS = 8
DYN_KICKOFF = ("A live-site incident may be occurring. Investigate AUTONOMOUSLY: at each step call the single "
               "most useful read-only tool based on the evidence so far, and first say briefly (a) what the last "
               "evidence showed and (b) why you're calling the next tool. Start broad (detect, get_alerts, "
               "detect_trend), then drill into the suspect service (get_service_health, get_logs, correlate, "
               "assess_impact, match_runbook). When you have a root cause + confidence, call apply_gate ONCE for the "
               "deterministic decision, then STOP with a 2-line summary. You have NO remediation tools — you "
               "recommend; a human approves the fix. Use at most 8 tool calls.")


def _dynamic_agent():
    global _DYN_ID
    if _DYN_ID:
        return _DYN_ID
    tools = [TOOLDEFS[t] for t in ("detect", "get_alerts", "detect_trend", "get_service_health", "get_logs",
                                   "get_container_app_status", "get_container_app_revisions",
                                   "get_container_app_resource_limits", "get_container_app_logs",
                                   "get_container_app_system_logs",
                                   "correlate", "assess_impact", "match_runbook", "apply_gate")]  # all read-only/pure
    existing = {a.name: a for a in client.beta.assistants.list(limit=100).data if a.name == "CRE-Orchestrator"}
    if "CRE-Orchestrator" in existing:
        a = client.beta.assistants.update(existing["CRE-Orchestrator"].id, model=MODEL, instructions=DYN_KICKOFF, tools=tools)
    else:
        a = client.beta.assistants.create(model=MODEL, name="CRE-Orchestrator", instructions=DYN_KICKOFF, tools=tools)
    _DYN_ID = a.id
    return _DYN_ID


def _consume_dynamic(stream, thread_id, name, counter):
    with stream as s:
        for event in s:
            et = event.event
            if et == "thread.message.delta":
                for block in (event.data.delta.content or []):
                    if getattr(block, "type", "") == "text" and block.text and block.text.value:
                        yield {"type": "token", "agent": name, "text": block.text.value}
            elif et == "thread.run.requires_action":
                run_obj = event.data
                outs = []
                for tc in run_obj.required_action.submit_tool_outputs.tool_calls:
                    counter[0] += 1
                    if counter[0] > MAX_STEPS:
                        try:
                            client.beta.threads.runs.cancel(thread_id=thread_id, run_id=run_obj.id)
                        except Exception:
                            pass
                        yield {"type": "tool_call", "agent": name, "tool": f"(step cap reached — {MAX_STEPS})"}
                        return
                    obs.log_tool(_TRACE, name, tc.function.name)
                    args = json.loads(tc.function.arguments or "{}")
                    tgt = _arg_target(args)
                    yield {"type": "tool_call", "agent": name, "tool": tc.function.name, "target": tgt,
                           "args": args, "trace": _TRACE, "ts": _now_ms()}
                    _t0 = time.time()
                    out = _safe_dispatch(tc.function.name, args)
                    _ms = int((time.time() - _t0) * 1000)
                    _inj = security.scan_untrusted(out) if tc.function.name in _UNTRUSTED_TOOLS else None
                    if _inj and not _inj["clean"]:
                        obs.log("security.prompt_injection_flagged", tool=tc.function.name,
                                matches=[m["tag"] for m in _inj["matches"]], trace=_TRACE)
                    yield {"type": "evidence", "agent": name, "tool": tc.function.name, "target": tgt,
                           "summary": str(out)[:180], "human": _humanize(tc.function.name, out),
                           "args": args, "raw": str(out)[:6000], "ms": _ms, "trace": _TRACE, "ts": _now_ms(),
                           "injection": _inj}
                    outs.append({"tool_call_id": tc.id, "output": out})
                nxt = client.beta.threads.runs.submit_tool_outputs_stream(
                    thread_id=thread_id, run_id=run_obj.id, tool_outputs=outs)
                yield from _consume_dynamic(nxt, thread_id, name, counter)
                return


def run_stream_dynamic(target: str | None = None, event: str | None = None):
    """Autonomous investigation: the Commander picks tools step by step. Falls back to fixed on error.
    In DEMO_MODE, `target` (the sandbox service) becomes the primary suspect for this run."""
    global _TRACE
    _TRACE = obs.new_trace()
    obs.log("incident.dynamic_started", trace=_TRACE, target=target, sim_event=event)
    yield {"type": "incident_start", "trace": _TRACE, "mode": "dynamic",
           "target": target if settings.DEMO_MODE else None, "ts": _now_ms()}
    yield {"type": "agent_start", "agent": "Commander", "trace": _TRACE, "ts": _now_ms()}
    try:
        aid = _dynamic_agent()
        tid = client.beta.threads.create().id
        client.beta.threads.messages.create(thread_id=tid, role="user", content=_kickoff(DYN_KICKOFF, target, event))
        counter = [0]
        yield from _consume_dynamic(client.beta.threads.runs.stream(thread_id=tid, assistant_id=aid),
                                    tid, "Commander", counter)
        yield {"type": "agent_end", "agent": "Commander", "trace": _TRACE, "ts": _now_ms()}
        obs.log("incident.dynamic_done", trace=_TRACE, steps=counter[0])
        yield {"type": "done", "mode": "dynamic"}
    except Exception as e:
        obs.log("incident.dynamic_failed", trace=_TRACE, error=str(e)[:150])
        yield {"type": "token", "agent": "Commander",
               "text": f"\n[dynamic mode error: {str(e)[:70]} — falling back to fixed pipeline]\n"}
        yield {"type": "agent_end", "agent": "Commander"}
        yield from run_stream_sync(target, event)   # safe fallback (keeps the sandbox target)


if __name__ == "__main__":
    run()

