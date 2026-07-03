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

from shared import kusto, obs
from shared.confidence import decide

_TRACE = None  # correlation id for the current incident run

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://crecopilot-aoai-vxxmsm.openai.azure.com/")
MODEL = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")

_tp = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
client = AzureOpenAI(azure_endpoint=ENDPOINT, azure_ad_token_provider=_tp, api_version=API_VERSION)

KICKOFF = "A live-site incident was triggered. Run the incident response end to end."

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
def t_gate(a):
    d = decide(float(a.get("confidence",0)), _svc(a.get("service_name","")), str(a.get("version",""))[:20])
    obs.log_decision(_TRACE, _svc(a.get("service_name","")), d.confidence, d.action, d.threshold)
    return json.dumps({"action": d.action, "confidence": d.confidence, "threshold": d.threshold,
                       "remediation": d.remediation, "reason": d.reason})

DISPATCH = {"detect": t_detect, "get_alerts": t_alerts, "detect_trend": t_trend,
            "correlate": t_correlate, "assess_impact": t_impact, "apply_gate": t_gate}

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
}

AGENTS = [
    ("Commander", ["detect", "get_alerts", "detect_trend"],
     "You are the Incident Commander. Call detect(), get_alerts(), detect_trend(). "
     "Output ONE terse line per finding, e.g. 'ANOMALY payment-service latency+errors (score 95)', "
     "'ALERT checkout-api Sev2', 'TREND checkout-api → breach ~15m'. No preamble, no next-steps, no paragraphs. End with 'Correlator →'."),
    ("Correlator", ["correlate"],
     "You are the Correlator. For EACH alert call correlate(alert_service, alert_time_iso). "
     "Output ONE line per alert ONLY: '<alert> ROOT CAUSE <service> <version> | conf <0.xxx> | <ratio>x anomaly, upstream, <n>m before'. "
     "Numbers from the tool only. No prose. End with 'Impact →'."),
    ("Impact", ["assess_impact"],
     "You are the Impact analyst. Call assess_impact(service_name) for each root cause. "
     "Output ONE line per affected service ONLY: '<service> <ratio>x latency (degraded)'. No prose. End with 'Gate →'."),
    ("Gate", ["apply_gate"],
     "You are the Gate — deterministic, you do NOT decide. Call apply_gate(confidence, service_name, version) for each root cause. "
     "Output ONE line each ONLY: '<service> → AUTO-REMEDIATE (conf ≥ 0.70)' or '<service> → ESCALATE (conf < 0.70)'. "
     "No essays, no next-steps, no offers to run commands. Stop after the decisions."),
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
                outs.append({"tool_call_id": tc.id, "output": DISPATCH.get(tc.function.name, lambda a: "{}")(args)})
            run = client.beta.threads.runs.submit_tool_outputs(thread_id=thread_id, run_id=run.id, tool_outputs=outs)
        else:
            time.sleep(1)
            run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)
    return run


def run():
    agents = _ensure_assistants()
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=KICKOFF)
    for name, aid in agents:
        _run_agent(thread.id, aid)
        msg = client.beta.threads.messages.list(thread_id=thread.id, order="desc", limit=1).data[0]
        text = msg.content[0].text.value if msg.content else ""
        print(f"\n{'='*68}\n  {name}  (hosted assistant {aid})\n{'='*68}\n{text}")


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
                    yield {"type": "tool_call", "agent": name, "tool": tc.function.name}
                    args = json.loads(tc.function.arguments or "{}")
                    outs.append({"tool_call_id": tc.id,
                                 "output": DISPATCH.get(tc.function.name, lambda a: "{}")(args)})
                nxt = client.beta.threads.runs.submit_tool_outputs_stream(
                    thread_id=thread_id, run_id=run_obj.id, tool_outputs=outs)
                yield from _consume(nxt, thread_id, name)
                return


def run_stream_sync():
    """Sync generator of UI events across the hosted assistants (bridged to SSE by the server)."""
    global _TRACE
    _TRACE = obs.new_trace()
    obs.log("incident.run_started", trace=_TRACE)
    agents = _ensure_assistants()
    thread = client.beta.threads.create()
    client.beta.threads.messages.create(thread_id=thread.id, role="user", content=KICKOFF)
    yield {"type": "incident_start", "trace": _TRACE}
    for name, aid in agents:
        yield {"type": "agent_start", "agent": name}
        yield from _consume(client.beta.threads.runs.stream(thread_id=thread.id, assistant_id=aid), thread.id, name)
        yield {"type": "agent_end", "agent": name}
    obs.log("incident.run_done", trace=_TRACE)
    yield {"type": "done"}


if __name__ == "__main__":
    run()

