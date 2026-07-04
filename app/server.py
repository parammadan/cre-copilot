#!/usr/bin/env python3
"""CRE Copilot — live console backend.

Exposes the real detection/correlation/impact pipeline (the same KQL functions the
CLI orchestrator uses) as JSON for the interactive web console. The act-vs-escalate
gate is applied client-side against a live threshold slider, so no server round-trip
is needed to watch decisions flip.

Run:  ./demo/console.sh   (or: uvicorn app.server:app --port 8000)
"""
from __future__ import annotations
import hashlib
import json
import os
import subprocess
import requests
import sys
import time
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
from shared import kusto  # noqa: E402
from agents.assistants import run_stream_sync, run_stream_dynamic  # noqa: E402 (hosted Azure OpenAI assistants)

HERE = os.path.dirname(__file__)
ROOT = os.path.join(HERE, "..")
app = FastAPI(title="CRE Copilot Console")

# ---- Autonomous mode: if true, auto-remediation-eligible incidents skip human approval. ----
AUTONOMOUS_MODE = os.environ.get("AUTONOMOUS_MODE", "false").lower() == "true"
# Durable incident + approval state lives in ADX (shared/incidents.py) — the system of record.

# ---- Frontend mode: legacy (vanilla console) | react (Vite build). Default legacy = no change. ----
FRONTEND_MODE = os.environ.get("FRONTEND_MODE", "legacy").lower()
_DIST = os.path.join(HERE, "..", "frontend", "dist")
_REACT_INDEX = os.path.join(_DIST, "index.html")
_REACT_AVAILABLE = os.path.isdir(os.path.join(_DIST, "assets")) and os.path.isfile(_REACT_INDEX)
if _REACT_AVAILABLE:  # serve the built SPA's static assets (inert in legacy mode)
    from fastapi.staticfiles import StaticFiles  # noqa: E402
    app.mount("/assets", StaticFiles(directory=os.path.join(_DIST, "assets")), name="assets")


def _records(df: pd.DataFrame) -> list[dict]:
    out = []
    for rec in df.to_dict("records"):
        for k, v in rec.items():
            if isinstance(v, pd.Timestamp):
                rec[k] = pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%SZ")
            elif hasattr(v, "item"):
                rec[k] = v.item()
        out.append(rec)
    return out


def _persisted_incidents() -> list:
    """Active incidents from the durable store (system of record). Guarded so a down/absent
    store never breaks /api/state."""
    try:
        from shared import incidents as _inc
        return _inc.list_active()
    except Exception:
        return []


def _legacy_html() -> str:
    with open(os.path.join(HERE, "index.html")) as f:
        return f.read()


def _react_html() -> str:
    with open(_REACT_INDEX) as f:
        return f.read()


@app.get("/legacy", response_class=HTMLResponse)
@app.get("/console", response_class=HTMLResponse)
def legacy_console() -> str:
    """The full operations console (topology, sandbox, agents, chat, Teams, workspace +
    security cards). Served at both /console and /legacy, ALWAYS, regardless of FRONTEND_MODE.
    The React landing's 'Launch Workspace' opens this."""
    return _legacy_html()


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    if FRONTEND_MODE == "react" and _REACT_AVAILABLE:
        return _react_html()
    return _legacy_html()


@app.get("/api/state")
def state() -> JSONResponse:
    deps = kusto.query("ServiceDependencies")
    services = kusto.query("Telemetry | distinct Service | order by Service asc")
    anomalies = kusto.query("Detect()")

    series = kusto.query(
        "let tmax=toscalar(Telemetry|summarize max(Timestamp));"
        "Telemetry | where Metric=='latency_ms' and Timestamp>tmax-90m "
        "| summarize v=round(avg(Value),0) by Service, t=bin(Timestamp,2m) | order by t asc"
    )
    tel: dict[str, list] = {}
    for r in series.itertuples(index=False):
        tel.setdefault(r.Service, []).append(
            {"t": pd.Timestamp(r.t).strftime("%H:%M"), "v": float(r.v)}
        )

    alerts = kusto.query(
        "let amax=toscalar(Alerts|summarize max(Timestamp));"
        "Alerts | where Timestamp>amax-60m "
        "| project Service, Timestamp, Severity, Description | order by Timestamp asc"
    )
    incidents = []
    for a in alerts.itertuples(index=False):
      try:  # one bad alert must not sink the whole page
        aiso = pd.Timestamp(a.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")
        cand = kusto.query_safe(f"Correlate('{a.Service}', datetime({aiso}))")
        if cand.empty:
            continue
        top = cand.iloc[0]
        impact = kusto.query_safe(f"ImpactAssessment('{top.Service}')")
        incidents.append({
            "alertService": a.Service, "severity": a.Severity,
            "alertTime": pd.Timestamp(a.Timestamp).strftime("%H:%M"),
            "description": a.Description,
            "candidates": _records(cand),
            "rootCause": {"service": top.Service, "version": top.Version,
                          "confidence": float(top.confidence)},
            "impact": _records(impact),
        })
      except Exception as e:
        from shared.obs import log
        log("state.incident_failed", alert=str(getattr(a, "Service", "?")), error=str(e)[:160])
        continue

    # Current health = recent (8 min) latency vs each service's baseline — this is what
    # heals RED->GREEN after remediation (unlike Detect(), which analyses the incident).
    health = kusto.query(
        "let tmax=toscalar(Telemetry|summarize max(Timestamp));"
        "let recent=Telemetry|where Metric=='latency_ms' and Timestamp>tmax-8m|summarize cur=avg(Value) by Service;"
        "let base=Telemetry|where Metric=='latency_ms' and Timestamp between (tmax-4h .. tmax-30m)|summarize b=avg(Value) by Service;"
        "recent|join kind=inner base on Service|extend ratio=cur/b|project Service, ratio=round(ratio,2)"
    )
    health_map = {r.Service: float(r.ratio) for r in health.itertuples(index=False)}

    # Proactive: rising trends projected to breach (may exist with NO alert yet).
    trends = _records(kusto.query_safe("DetectTrend() | where willBreach == true"))

    m = kusto.query(
        "Incidents | summarize Total=count(), "
        "AutoResolved=countif(Status=='auto-resolved'), Escalated=countif(Status=='escalated')"
    ).iloc[0]
    mttr = kusto.query(
        "Incidents | where isnotempty(EndTime) "
        "| extend mttr=datetime_diff('minute',EndTime,StartTime) "
        "| summarize m=round(avg(mttr),0) by Status"
    )
    conf = kusto.query(
        "Incidents | summarize c=count() by b=bin(Confidence,0.1) | order by b asc"
    )

    return JSONResponse({
        "services": services["Service"].tolist(),
        "health": health_map,
        "trends": trends,
        "threshold": __import__("shared.settings", fromlist=["ACT_THRESHOLD"]).ACT_THRESHOLD,
        "edges": [{"from": r.DependsOn, "to": r.Service} for r in deps.itertuples(index=False)],
        "anomalies": _records(anomalies),
        "telemetry": tel,
        "incidents": incidents,
        "persistedIncidents": _persisted_incidents(),   # durable state (system of record)
        "metrics": {
            "total": int(m.Total), "autoResolved": int(m.AutoResolved), "escalated": int(m.Escalated),
            "mttr": {r.Status: int(r.m) for r in mttr.itertuples(index=False)},
            "confidence": [{"bucket": round(float(r.b), 1), "count": int(r.c)}
                           for r in conf.itertuples(index=False)],
        },
    })


@app.get("/api/incident/stream")
async def incident_stream(request: Request) -> StreamingResponse:
    """SSE: streams the HOSTED Azure-assistant conversation live. The assistants SDK is
    synchronous, so we run it in a thread and bridge events to async via a queue.
    ?mode=dynamic runs the autonomous investigation; default is the fixed pipeline."""
    import asyncio
    import threading
    gen = run_stream_dynamic if request.query_params.get("mode") == "dynamic" else run_stream_sync
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def worker():
        try:
            for ev in gen():
                loop.call_soon_threadsafe(q.put_nowait, ev)
        except Exception as e:
            loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "message": str(e)[:200]})
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=worker, daemon=True).start()

    async def gen():
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


from shared.services import SERVICE_PORTS as _SERVICE_PORTS, service_base as _service_base  # noqa: E402
# (name resolution + env-override + port live in shared/services.py — one source of truth)


def _remediate_service(service: str, source: str = "console", approver: str = "human") -> list:
    """Shared remediation. REAL action first: POST /recover to the actual service (if the
    local lab is running); ALSO write recovery telemetry so the ADX-based health view heals
    even without the services (fallback). Used by the console AND Teams. `source`/`approver`
    are recorded in the audit trail — this is a state-changing, human-approved action."""
    service = "".join(c for c in str(service) if c.isalnum() or c in "-_")
    if not service:
        return []
    base = _service_base(service)
    if base:  # real action on the real service (best-effort)
        try:
            requests.post(f"{base}/recover", timeout=1.5)
            from shared.obs import log
            log("remediation.recover_called", service=service)
        except Exception:
            pass
    deps = kusto.query(f"ServiceDependencies | where DependsOn=='{service}' | project Service")
    healed = [service] + deps["Service"].tolist()
    healed_kql = "dynamic([" + ",".join(f"'{s}'" for s in healed) + "])"
    kusto.command(
        ".set-or-append Telemetry <| "
        "let tmax=toscalar(Telemetry|summarize max(Timestamp)); "
        f"let healed={healed_kql}; "
        "let base=Telemetry | where Timestamp between (tmax-4h .. tmax-30m) and Metric in ('latency_ms','error_rate') "
        "| summarize b=avg(Value) by Service, Metric; "
        "let recent=Telemetry | where Timestamp>tmax-8m and Metric in ('latency_ms','error_rate') "
        "| summarize r=avg(Value) by Service, Metric; "
        "let targets=base | join kind=inner recent on Service, Metric "
        "| extend target=iff(set_has_element(healed, Service), b, r); "
        "range i from 1 to 15 step 1 | extend d=1 | join kind=inner (targets|extend d=1) on d "
        "| extend Timestamp=tmax+i*1m, Value=target*(1.0+(rand()-0.5)*0.08), Environment='prod' "
        "| project Timestamp, Service, Metric, Value, Environment")
    from shared import obs
    obs.log_remediation(service, healed, source=source, approver=approver)   # audit trail
    return healed


@app.post("/api/remediate")
async def remediate(req: Request) -> JSONResponse:
    """Simulate the rollback taking effect (console Approve button) + update the durable record."""
    service = (await req.json()).get("service", "")
    if not service:
        return JSONResponse({"error": "no service"}, status_code=400)
    healed = _remediate_service(service, source="console")
    try:
        from shared import incidents as _inc
        _inc.transition(_inc.make_id(service), "REMEDIATING", ApprovalStatus="approved", RemediationStatus="applied")
    except Exception:
        pass
    return JSONResponse({"healed": healed})


# ============================ INTERACTIVE SIMULATOR ============================
# Knobs mutate SIM_STATE; _apply_sim() writes telemetry FORWARD into Kusto reflecting
# it, so the Detector (recent window) + agents react to whatever the operator changes.
SIM_STATE = {"traffic": 1.0, "noise": 1.0, "services": {}}  # services: {svc: {lat, err}}
_SVCS = None
_DOWNSTREAM = None


def _svcs():
    global _SVCS
    if _SVCS is None:
        _SVCS = kusto.query("Telemetry | distinct Service")["Service"].tolist()
    return _SVCS


def _downstream():
    global _DOWNSTREAM
    if _DOWNSTREAM is None:
        d = kusto.query("ServiceDependencies")  # Service DependsOn upstream
        m: dict[str, list] = {}
        for r in d.itertuples(index=False):
            m.setdefault(r.DependsOn, []).append(r.Service)   # downstream_of[upstream] = [service]
        _DOWNSTREAM = m
    return _DOWNSTREAM


def _apply_sim():
    """Write ~8 min of telemetry forward for every service reflecting the current knobs."""
    t0 = pd.Timestamp(kusto.query("Telemetry | summarize m=max(Timestamp)").iloc[0].m).strftime("%Y-%m-%dT%H:%M:%SZ")
    eff = {s: {"lat": SIM_STATE["traffic"] * SIM_STATE["services"].get(s, {}).get("lat", 1.0),
               "err": SIM_STATE["services"].get(s, {}).get("err", 1.0)} for s in _svcs()}
    # blast radius: a badly degraded service drags its downstream dependents up too
    for s in _svcs():
        if eff[s]["lat"] >= 4:
            for down in _downstream().get(s, []):
                eff[down]["lat"] = max(eff[down]["lat"], 2.3)
    rows = ",".join(f"'{s}',real({eff[s]['lat']}),real({eff[s]['err']})" for s in _svcs())
    noise = SIM_STATE["noise"]
    kusto.command(
        ".set-or-append Telemetry <| "
        f"let t0=datetime({t0}); let mult=datatable(Service:string, latM:real, errM:real)[{rows}]; "
        "let base=Telemetry|where Timestamp between (t0-4h..t0-30m) and Metric in ('latency_ms','error_rate')|summarize b=avg(Value) by Service,Metric; "
        "base | join kind=inner mult on Service | extend m=iff(Metric=='latency_ms', latM, errM) "
        "| extend d=1 | join kind=inner (range i from 1 to 8 step 1|extend d=1) on d "
        f"| extend Timestamp=t0+i*1m, Value=b*m*(1.0+({noise})*(rand()-0.5)*0.2), Environment='prod' "
        "| project Timestamp, Service, Metric, Value, Environment")


def _sanitize(s):
    return "".join(c for c in str(s) if c.isalnum() or c in "-_")


@app.post("/api/sim/traffic")
async def sim_traffic(req: Request) -> JSONResponse:
    SIM_STATE["traffic"] = max(0.5, min(4.0, float((await req.json()).get("value", 1.0))))
    _apply_sim(); return JSONResponse({"traffic": SIM_STATE["traffic"]})


@app.post("/api/sim/noise")
async def sim_noise(req: Request) -> JSONResponse:
    SIM_STATE["noise"] = max(0.0, min(5.0, float((await req.json()).get("value", 1.0))))
    _apply_sim(); return JSONResponse({"noise": SIM_STATE["noise"]})


@app.post("/api/sim/errors")
async def sim_errors(req: Request) -> JSONResponse:
    b = await req.json(); svc = _sanitize(b.get("service", "")); lvl = float(b.get("value", 6.0))
    if svc: SIM_STATE["services"].setdefault(svc, {})["err"] = lvl
    _apply_sim(); return JSONResponse({"service": svc, "err": lvl})


@app.post("/api/sim/deploy")
async def sim_deploy(req: Request) -> JSONResponse:
    svc = _sanitize((await req.json()).get("service", ""))
    if not svc: return JSONResponse({"error": "no service"}, status_code=400)
    SIM_STATE["services"].setdefault(svc, {}).update(lat=5.0, err=12.0)   # bad deploy = latency+error spike
    t0 = pd.Timestamp(kusto.query("Telemetry | summarize m=max(Timestamp)").iloc[0].m).strftime("%Y-%m-%dT%H:%M:%SZ")
    kusto.command(f".set-or-append Deployments <| print Timestamp=datetime({t0}), Service='{svc}', "
                  "Version='v9.9.9', CommitId='baddeploy', Author='demo', Pipeline='azure-pipelines-ci', Environment='prod'")
    kusto.command(f".set-or-append Alerts <| print Timestamp=datetime({t0})+9m, AlertId='ALT-sim', Service='{svc}', "
                  f"Metric='latency_ms', Severity='Sev2', Threshold=300.0, ObservedValue=640.0, Description='{svc} p95 latency breached 300ms threshold'")
    _apply_sim(); return JSONResponse({"service": svc, "injected": "bad deploy"})


@app.post("/api/sim/kill")
async def sim_kill(req: Request) -> JSONResponse:
    svc = _sanitize((await req.json()).get("service", ""))
    if svc: SIM_STATE["services"].setdefault(svc, {}).update(lat=8.0, err=20.0)  # cascades to downstream in _apply_sim
    _apply_sim(); return JSONResponse({"service": svc, "killed": True})


@app.post("/api/sim/reset")
async def sim_reset() -> JSONResponse:
    SIM_STATE["traffic"] = 1.0; SIM_STATE["noise"] = 1.0; SIM_STATE["services"] = {}
    _apply_sim(); return JSONResponse({"reset": True})


@app.post("/api/break")
async def break_service(req: Request) -> JSONResponse:
    """Inject a live failure into the chosen service (spike telemetry forward + a deploy +
    an alert) so the agents then detect and fix it. Interactive: 'break X, watch them respond'."""
    body = await req.json()
    service = "".join(c for c in str(body.get("service", "")) if c.isalnum() or c in "-_")
    if not service:
        return JSONResponse({"error": "no service"}, status_code=400)
    t0 = pd.Timestamp(kusto.query("Telemetry | summarize m=max(Timestamp)").iloc[0].m).strftime("%Y-%m-%dT%H:%M:%SZ")
    # spike the chosen service, continue everyone else (so none drop from the recent window)
    kusto.command(
        ".set-or-append Telemetry <| "
        f"let t0=datetime({t0}); let broke=dynamic(['{service}']); "
        "let base=Telemetry|where Timestamp between (t0-4h..t0-30m) and Metric in ('latency_ms','error_rate')|summarize b=avg(Value) by Service,Metric; "
        "let recent=Telemetry|where Timestamp>t0-8m and Metric in ('latency_ms','error_rate')|summarize r=avg(Value) by Service,Metric; "
        "let targets=base|join kind=inner recent on Service,Metric|extend target=iff(set_has_element(broke,Service), b*iff(Metric=='latency_ms',5.0,15.0), r); "
        "range i from 1 to 12 step 1|extend d=1|join kind=inner (targets|extend d=1) on d"
        "|extend Timestamp=t0+i*1m, Value=target*(1+(rand()-0.5)*0.08), Environment='prod'"
        "|project Timestamp,Service,Metric,Value,Environment")
    kusto.command(
        f".set-or-append Deployments <| print Timestamp=datetime({t0})+4m, Service='{service}', "
        "Version='v9.9.9', CommitId='demo0bad', Author='demo', Pipeline='azure-pipelines-ci', Environment='prod'")
    kusto.command(
        f".set-or-append Alerts <| print Timestamp=datetime({t0})+11m, AlertId='ALT-brk', Service='{service}', "
        f"Metric='latency_ms', Severity='Sev2', Threshold=300.0, ObservedValue=640.0, Description='{service} p95 latency breached 300ms threshold'")
    return JSONResponse({"broke": service})


@app.post("/api/ask")
async def ask_copilot(req: Request) -> JSONResponse:
    """Ask CRE Copilot — a conversational agent with all tools, answering on live data."""
    from fastapi.concurrency import run_in_threadpool
    from agents.assistants import ask
    body = await req.json()
    q = str(body.get("question", "")).strip()
    if not q:
        return JSONResponse({"error": "empty question"}, status_code=400)
    try:
        answer, tid = await run_in_threadpool(ask, q, body.get("thread_id"))
        return JSONResponse({"answer": answer, "thread_id": tid})
    except Exception as e:
        return JSONResponse({"answer": "Copilot hit an error: " + str(e)[:160], "thread_id": body.get("thread_id")})


@app.post("/api/postmortem")
async def postmortem_ep(req: Request) -> JSONResponse:
    """Run the Postmortem agent after an incident resolves (writes review + authors runbook if novel)."""
    from fastapi.concurrency import run_in_threadpool
    from agents.assistants import postmortem
    body = await req.json()
    service = str(body.get("service", "")).strip()
    if not service:
        return JSONResponse({"error": "no service"}, status_code=400)
    facts = body.get("facts") or f"Root cause: {service}. Remediated via rollback to previous release."
    try:
        review = await run_in_threadpool(postmortem, service, facts)
        return JSONResponse({"review": review})
    except Exception as e:
        return JSONResponse({"review": "Postmortem error: " + str(e)[:160]})


def _check_adx() -> dict:
    """Real ADX health: a lightweight `print` query, timed. No table scan."""
    from shared import settings as _s
    host = _s.ADX_CLUSTER_URI.split("//")[-1].split(".")[0]
    t0 = time.perf_counter()
    try:
        kusto.query("print ping=1")
        return {"name": "Azure Data Explorer", "connected": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "cluster": host, "database": _s.ADX_DATABASE, "last_query": time.strftime("%H:%M:%S")}
    except Exception as e:
        return {"name": "Azure Data Explorer", "connected": False, "cluster": host, "error": str(e)[:140]}


def _check_aoai() -> dict:
    """Real Azure OpenAI reachability: a cheap models list (GET). No completion is generated."""
    from shared import settings as _s
    ep = _s.AOAI_ENDPOINT.split("//")[-1].rstrip("/")
    t0 = time.perf_counter()
    try:
        from agents.assistants import client
        client.models.list()  # inexpensive metadata GET — does NOT create a completion
        return {"name": "Azure OpenAI", "connected": True,
                "latency_ms": round((time.perf_counter() - t0) * 1000),
                "deployment": _s.AOAI_DEPLOYMENT, "endpoint": ep, "last_request": time.strftime("%H:%M:%S")}
    except Exception as e:
        return {"name": "Azure OpenAI", "connected": False, "deployment": _s.AOAI_DEPLOYMENT, "error": str(e)[:140]}


def _check_collector() -> dict:
    """Is the collector live, and how fresh is the telemetry it writes? 'running' is true if the
    local process is found OR telemetry is fresh (the latter covers a separate collector container)."""
    interval = int(os.environ.get("COLLECTOR_INTERVAL_SEC", "12"))
    source = os.environ.get("TELEMETRY_SOURCE", "synthetic")
    local_proc = False
    try:
        r = subprocess.run(["pgrep", "-f", "collector.py"], capture_output=True, text=True, timeout=2)
        local_proc = bool(r.stdout.strip())
    except Exception:
        pass
    last_sync, age = None, None
    try:
        df = kusto.query("Telemetry | summarize m=max(Timestamp)")
        if df is not None and len(df) and df.iloc[0, 0] is not None:
            lt = pd.Timestamp(df.iloc[0, 0])
            lt = lt.tz_localize("UTC") if lt.tzinfo is None else lt
            age = max(0, round((pd.Timestamp.now(tz="UTC") - lt).total_seconds()))  # clamp: synthetic data can be forward-dated
            local_tz = datetime.now().astimezone().tzinfo
            last_sync = lt.tz_convert(local_tz).strftime("%H:%M:%S")  # local time, consistent with other checks
    except Exception:
        pass
    # fresh = telemetry written within ~3 poll intervals → collector is alive (even if in another container)
    fresh = source == "services" and age is not None and age <= interval * 3 + 15
    return {"name": "Telemetry Collector", "running": local_proc or fresh, "source": source,
            "poll_interval_sec": interval, "monitored_services": len(_SERVICE_PORTS),
            "last_sync": last_sync, "last_sync_age_sec": age}


def _check_services() -> list:
    """Real /health probe of every microservice, timed. Unreachable -> offline (honest)."""
    out = []
    for svc in _SERVICE_PORTS:
        t0 = time.perf_counter()
        try:
            j = requests.get(f"{_service_base(svc)}/health", timeout=1.0).json()
            out.append({"service": svc, "status": str(j.get("status", "unknown")).lower(),
                        "response_ms": round((time.perf_counter() - t0) * 1000), "checked": time.strftime("%H:%M:%S")})
        except Exception:
            out.append({"service": svc, "status": "offline", "response_ms": None, "checked": time.strftime("%H:%M:%S")})
    return out


@app.get("/api/workspace/status")
async def workspace_status() -> JSONResponse:
    """Live, backend-driven workspace health — every value comes from a real check (no placeholders)."""
    from fastapi.concurrency import run_in_threadpool
    adx = await run_in_threadpool(_check_adx)
    aoai = await run_in_threadpool(_check_aoai)
    collector = await run_in_threadpool(_check_collector)
    services = await run_in_threadpool(_check_services)
    core_ok = bool(adx.get("connected") and aoai.get("connected"))
    states = [s["status"] for s in services]
    all_healthy = bool(states) and all(s == "healthy" for s in states)
    if not core_ok:
        overall = "OFFLINE"          # can't operate without ADX + Azure OpenAI
    elif all_healthy:
        overall = "READY"
    else:
        overall = "DEGRADED"          # core up, but a service/collector is not fully healthy
    return JSONResponse({"overall": overall, "adx": adx, "aoai": aoai,
                         "collector": collector, "services": services, "checked": time.strftime("%H:%M:%S")})


@app.get("/api/security/status")
def security_status() -> JSONResponse:
    """Real security-posture report (no placeholders). Architecture facts are constant;
    secrets source is detected from the environment (Key Vault in cloud, .env in dev)."""
    kv = os.environ.get("KEY_VAULT_URI", "")
    in_cloud = bool(os.environ.get("CONTAINER_APP_NAME") or os.environ.get("WEBSITE_SITE_NAME") or kv)
    items = [
        {"key": "managed_identity", "label": "Managed Identity auth — no API keys", "status": "implemented",
         "detail": "DefaultAzureCredential: managed identity in Azure, az login locally. No keys in code."},
        {"key": "secrets", "label": "Secrets managed by Key Vault", "status": ("implemented" if kv else "dev"),
         "detail": (f"Key Vault reference: {kv}" if kv else "local .env (gitignored) in dev; Key Vault reference in production")},
        {"key": "rbac", "label": "Least-privilege RBAC per resource", "status": "implemented",
         "detail": "AcrPull, Azure OpenAI User, Key Vault Secrets User, ADX role — scoped per resource (see docs/security.md)."},
        {"key": "zero_trust_ui", "label": "Zero-trust — UI never touches Azure directly", "status": "implemented",
         "detail": "The browser calls only the backend API; every Azure call is server-side."},
        {"key": "readonly_investigation", "label": "Read-only investigation tools", "status": "implemented",
         "detail": "Agents use detect/correlate/get_logs/get_service_health/assess_impact — no remediation tool exists for the LLM."},
        {"key": "deterministic_gate", "label": "Deterministic confidence gate", "status": "implemented",
         "detail": "Pure Python (confidence.py). The LLM never sets confidence and cannot bypass the gate."},
        {"key": "human_approval", "label": "Human approval before remediation", "status": "implemented",
         "detail": "Remediation is a separate human-triggered endpoint; a Verifier then confirms recovery independently."},
        {"key": "audit_logging", "label": "Audit logging — tool / decision / remediation / verify", "status": "implemented",
         "detail": "Structured JSON via obs.py (trace id) → stdout → App Insights / Log Analytics."},
    ]
    implemented = sum(1 for i in items if i["status"] == "implemented")
    return JSONResponse({"items": items, "implemented": implemented, "total": len(items),
                         "environment": "cloud" if in_cloud else "local"})


@app.post("/api/verify")
async def verify_ep(req: Request) -> JSONResponse:
    """Verifier agent — independently confirm recovery (real /health + logs) after remediation."""
    from fastapi.concurrency import run_in_threadpool
    from agents.assistants import verify
    service = str((await req.json()).get("service", "")).strip()
    if not service:
        return JSONResponse({"error": "no service"}, status_code=400)
    try:
        verdict = await run_in_threadpool(verify, service)
        from shared import obs, incidents as _inc
        obs.log_verify(service, verdict)          # audit trail: independent recovery result
        try:
            okv = "CONFIRMED" in verdict.upper() and "NOT CONFIRMED" not in verdict.upper()
            _inc.transition(_inc.make_id(service), "RESOLVED" if okv else "FAILED",
                            VerifierStatus=verdict[:200], RemediationStatus="verified" if okv else "unconfirmed")
        except Exception:
            pass
        return JSONResponse({"verdict": verdict})
    except Exception as e:
        return JSONResponse({"verdict": "Verifier error: " + str(e)[:160]})


@app.get("/api/teams/config")
def teams_config() -> JSONResponse:
    """Report whether Teams is really configured — so the UI can disable the button honestly
    (no secret is exposed, only booleans)."""
    from shared.settings import TEAMS_WEBHOOK_URL, PUBLIC_BASE_URL
    local = PUBLIC_BASE_URL.startswith("http://localhost") or PUBLIC_BASE_URL.startswith("http://127.")
    return JSONResponse({"configured": bool(TEAMS_WEBHOOK_URL),
                         "approve_callback_public": bool(TEAMS_WEBHOOK_URL) and not local})


@app.post("/api/teams/notify")
async def teams_notify() -> JSONResponse:
    """Build the incident Adaptive Card from live data and post it to the Teams channel webhook.
    If no webhook is configured, return the card as a PREVIEW (honest: nothing was posted) so the
    exact card CRE Copilot generates can still be shown."""
    from shared import teams
    from shared.settings import TEAMS_WEBHOOK_URL, PUBLIC_BASE_URL
    al = kusto.query_safe("let amax=toscalar(Alerts|summarize max(Timestamp)); "
                          "Alerts|where Timestamp>amax-60m|top 1 by Severity asc|project Service,Timestamp,Severity")
    if al.empty:
        return JSONResponse({"posted": False, "reason": "no active alert"})
    a = al.iloc[0]
    aiso = pd.Timestamp(a.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")
    cand = kusto.query_safe(f"Correlate('{a.Service}', datetime({aiso}))")
    if cand.empty:
        return JSONResponse({"posted": False, "reason": "no root cause"})
    top = cand.iloc[0]
    inc = {"alertService": a.Service, "severity": a.Severity,
           "rootCause": {"service": top.Service, "version": top.Version, "confidence": float(top.confidence)},
           "impact": _records(kusto.query_safe(f"ImpactAssessment('{top.Service}')"))}
    card = teams.build_incident_card(inc, f"{PUBLIC_BASE_URL}/api/teams/approve", PUBLIC_BASE_URL)
    if not TEAMS_WEBHOOK_URL:
        return JSONResponse({"posted": False, "reason": "Teams integration not configured", "preview": card})
    return JSONResponse(teams.post_card(TEAMS_WEBHOOK_URL, card))


_INFLIGHT: set = set()   # same-process guard against rapid double-clicks (store handles cross-replica/restart)


def _top_evidence(inc: dict) -> list:
    """A few real evidence lines for the escalation card (from correlation + impact)."""
    rc = inc.get("rootCause", {})
    ev = [f"Root cause candidate {rc.get('service', '?')} {rc.get('version', '')} at confidence {float(rc.get('confidence', 0)):.2f}"]
    deg = [x for x in inc.get("impact", []) if x.get("LatencyIncrease", 0) >= 1.3]
    if deg:
        w = max(deg, key=lambda x: x["LatencyIncrease"])
        ev.append(f"Blast radius: {w.get('AffectedService', '?')} {w['LatencyIncrease']:.1f}x latency")
    if inc.get("description"):
        ev.append(f"Alert: {inc['description']}")
    return ev


@app.post("/api/alerts/ingest")
async def alerts_ingest(req: Request) -> JSONResponse:
    """Event-driven entry point: accept an Azure Monitor / generic alert, write a real Alert row,
    create/reuse a DURABLE incident record, run the deterministic gate, and auto-escalate to Teams
    referencing the persisted incident_id. This is how a real signal (not a button) starts a case."""
    from shared import incidents, teams, obs
    from shared.confidence import decide
    from shared.settings import TEAMS_WEBHOOK_URL, PUBLIC_BASE_URL, ACT_THRESHOLD
    b = await req.json()
    service = str(b.get("service", "")).strip()
    if not service:
        return JSONResponse({"error": "service required"}, status_code=400)
    severity = str(b.get("severity", "Sev3"))
    metric = str(b.get("metric", "latency_ms"))
    value = float(b.get("value", 0) or 0)
    threshold = float(b.get("threshold", 0) or 0)
    desc = str(b.get("description", f"{metric} breach on {service}"))
    ts = b.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        ts_iso = pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # 1) write a real Alert row so the existing detection/correlation pipeline sees it
    aid = "AL-" + hashlib.sha1(f"{service}{ts_iso}".encode()).hexdigest()[:8]
    kusto.command(".set-or-append Alerts <| datatable(Timestamp:datetime,AlertId:string,Service:string,"
                  "Metric:string,Severity:string,Threshold:real,ObservedValue:real,Description:string)"
                  f"[datetime({ts_iso}),'{aid}','{service}','{metric}','{severity}',real({threshold}),real({value}),"
                  f"\"{str(desc)[:300].replace(chr(34), chr(39))}\"]")

    # 2) durable incident (dedup by service)
    inc = incidents.create_or_get(service, severity, metric, value, threshold, desc)
    iid = inc["IncidentId"]
    trace = obs.new_trace()
    incidents.transition(iid, "INVESTIGATING", TraceId=trace)
    obs.log("alert_ingested", incident=iid, service=service, severity=severity, metric=metric, trace=trace)

    # 3) deterministic correlation + gate (no LLM in this path)
    out = {"incident_id": iid, "status": "INVESTIGATING", "teams_posted": False}
    cand = kusto.query_safe(f"Correlate('{service}', datetime({ts_iso}))")
    if cand.empty:
        incidents.transition(iid, "AWAITING_APPROVAL", GateDecision="escalate",
                             ApprovalStatus="pending", RemediationStatus="none", VerifierStatus="none")
        out["status"] = "AWAITING_APPROVAL"
        return JSONResponse(out)
    top = cand.iloc[0]
    conf = float(top.confidence)
    d = decide(conf, top.Service, top.Version, ACT_THRESHOLD)
    incidents.transition(iid, "AWAITING_APPROVAL", RootCauseService=top.Service, RootCauseVersion=top.Version,
                         Confidence=conf, GateDecision=d.action, ApprovalStatus="pending")
    out.update({"status": "AWAITING_APPROVAL", "gate_decision": d.action, "confidence": conf,
                "root_cause": f"{top.Service} {top.Version}"})

    # 4) auto-escalate to Teams (references the durable incident id)
    incview = {"alertService": service, "severity": severity, "description": desc,
               "rootCause": {"service": top.Service, "version": top.Version, "confidence": conf},
               "impact": _records(kusto.query_safe(f"ImpactAssessment('{top.Service}')"))}
    reason = d.reason if d.action == "escalate" else \
        f"auto-remediation eligible (confidence {conf:.2f} ≥ gate) — human approval required (AUTONOMOUS_MODE off)."
    if not TEAMS_WEBHOOK_URL:
        out["teams_reason"] = "Teams integration not configured"
        return JSONResponse(out)
    approve_url = f"{PUBLIC_BASE_URL}/api/teams/approve?service={top.Service}&incident={iid}"
    reject_url = f"{PUBLIC_BASE_URL}/api/teams/reject?incident={iid}"
    card = teams.build_escalation_card(incview, iid, reason, _top_evidence(incview), approve_url, reject_url, PUBLIC_BASE_URL)
    posted = bool(teams.post_card(TEAMS_WEBHOOK_URL, card).get("posted"))
    incidents.transition(iid, TeamsPosted=posted)
    if posted:
        obs.log("teams_posted", incident=iid, service=service, severity=severity, decision=d.action, confidence=conf, trace=trace)
    out["teams_posted"] = posted
    return JSONResponse(out)


@app.post("/api/escalate")
async def escalate() -> JSONResponse:
    """DETERMINISTIC auto-escalation over live incidents (used after the manual Run). Backend (not the
    LLM) runs the gate; escalates → posts a Teams card (deduped via the DURABLE incident record).
    AUTONOMOUS_MODE + auto-eligible → remediate+verify without a human."""
    from fastapi.concurrency import run_in_threadpool
    from shared import teams, obs, incidents
    from shared.confidence import decide
    from shared.settings import TEAMS_WEBHOOK_URL, PUBLIC_BASE_URL, ACT_THRESHOLD
    st = json.loads(bytes(state().body))
    threshold = st.get("threshold", ACT_THRESHOLD)
    out = {"configured": bool(TEAMS_WEBHOOK_URL), "autonomous_mode": AUTONOMOUS_MODE,
           "posted": [], "duplicates": [], "auto_remediated": [], "not_configured": []}
    for inc in st.get("incidents", []):
        rc = inc.get("rootCause", {})
        svc = rc.get("service", "")
        d = decide(float(rc.get("confidence", 0)), svc, rc.get("version", ""), threshold)
        rec = incidents.create_or_get(inc.get("alertService", svc), inc.get("severity", "Sev3"),
                                      description=inc.get("description", ""))
        iid = rec["IncidentId"]
        trace = rec.get("TraceId") or obs.new_trace()
        incidents.transition(iid, "AWAITING_APPROVAL", RootCauseService=svc, RootCauseVersion=rc.get("version", ""),
                             Confidence=float(rc.get("confidence", 0)), GateDecision=d.action, TraceId=trace)

        if d.action == "auto_remediate" and AUTONOMOUS_MODE:
            if rec.get("RemediationStatus") not in (None, "", "none"):
                out["duplicates"].append(iid); continue
            healed = _remediate_service(svc, source="autonomous", approver="autonomous")
            incidents.transition(iid, "VERIFYING", ApprovalStatus="auto", RemediationStatus="applied")
            obs.log("remediation_started", incident=iid, service=svc, mode="autonomous", trace=trace)
            try:
                from agents.assistants import verify
                verdict = await run_in_threadpool(verify, svc)
                obs.log_verify(svc, verdict, trace=trace)
            except Exception as e:
                verdict = "Verifier error: " + str(e)[:120]
            okv = "CONFIRMED" in verdict.upper() and "NOT CONFIRMED" not in verdict.upper()
            incidents.transition(iid, "RESOLVED" if okv else "FAILED", VerifierStatus=verdict[:200])
            out["auto_remediated"].append(iid); continue

        needs_human = d.action == "escalate" or (d.action == "auto_remediate" and not AUTONOMOUS_MODE)
        if not needs_human:
            continue
        if rec.get("TeamsPosted"):
            out["duplicates"].append(iid); continue     # dedup via durable state — never post twice
        reason = d.reason if d.action == "escalate" else \
            f"auto-remediation eligible (confidence {rc.get('confidence', 0):.2f} ≥ gate) — human approval required (AUTONOMOUS_MODE off)."
        if not TEAMS_WEBHOOK_URL:
            incidents.transition(iid, ApprovalStatus="pending")
            out["not_configured"].append(iid); continue
        approve_url = f"{PUBLIC_BASE_URL}/api/teams/approve?service={svc}&incident={iid}"
        reject_url = f"{PUBLIC_BASE_URL}/api/teams/reject?incident={iid}"
        card = teams.build_escalation_card(inc, iid, reason, _top_evidence(inc), approve_url, reject_url, PUBLIC_BASE_URL)
        posted = bool(teams.post_card(TEAMS_WEBHOOK_URL, card).get("posted"))
        incidents.transition(iid, ApprovalStatus="pending", TeamsPosted=posted)
        if posted:
            obs.log("teams_posted", incident=iid, service=svc, severity=inc.get("severity"),
                    decision=d.action, confidence=rc.get("confidence"), trace=trace)
            out["posted"].append(iid)
        else:
            out["not_configured"].append(iid)
    return JSONResponse(out)


@app.get("/api/teams/state")
def teams_state() -> JSONResponse:
    """Durable incident state (dedup + audit visibility)."""
    from shared import incidents
    return JSONResponse({"incidents": incidents.list_active(), "autonomous_mode": AUTONOMOUS_MODE})


@app.get("/api/teams/reject", response_class=HTMLResponse)
def teams_reject(incident: str = "") -> str:
    """Reject callback — record on the durable record, keep the incident open (no remediation)."""
    from shared import obs, incidents
    if incident:
        incidents.transition(incident, ApprovalStatus="rejected")
    obs.log("teams_approval_received", incident=incident, decision="rejected")
    return ("<html><body style='font-family:system-ui;background:#0a0a0b;color:#fafafa;padding:48px;text-align:center'>"
            "<h2 style='color:#e0a915'>🔎 Rejected — keeping investigation open</h2>"
            "<p style='color:#8a8a93'>No remediation was applied. The incident stays open for a human. You can close this tab.</p>"
            "</body></html>")


@app.get("/api/teams/approve")
async def teams_approve(service: str = "", incident: str = "") -> HTMLResponse:
    """Approve callback — IDEMPOTENT via the durable incident record: runs remediation, THEN the
    Verifier, and records every transition. A second click never remediates twice."""
    from fastapi.concurrency import run_in_threadpool
    from shared import obs, incidents
    iid = incident or incidents.make_id(service)
    # idempotency: same-process guard + durable-state guard + action key
    if iid in _INFLIGHT:
        return HTMLResponse(_teams_page("⏳ Already processing", "This approval is being handled. You can close this tab.", "#e0a915"))
    cur = incidents.get(iid) or {}
    if cur.get("ApprovalStatus") == "approved" or cur.get("Status") in ("REMEDIATING", "VERIFYING", "RESOLVED", "CLOSED"):
        return HTMLResponse(_teams_page("✓ Already approved", f"Incident {iid} was already remediated — no action taken.", "#22c55e"))
    if not incidents.mark_action(iid, "approve"):
        return HTMLResponse(_teams_page("✓ Already approved", f"Incident {iid} approval already recorded.", "#22c55e"))
    _INFLIGHT.add(iid)
    try:
        trace = cur.get("TraceId")
        obs.log("teams_approval_received", incident=iid, service=service, decision="approved", trace=trace)
        incidents.transition(iid, "REMEDIATING", ApprovalStatus="approved")
        healed = _remediate_service(service, source="teams")
        incidents.transition(iid, RemediationStatus="applied")
        obs.log("remediation_started", incident=iid, service=service, healed=healed, trace=trace)
        incidents.transition(iid, "VERIFYING")
        verdict = ""
        if service:
            try:
                from agents.assistants import verify
                verdict = await run_in_threadpool(verify, service)
                obs.log_verify(service, verdict, trace=trace)
            except Exception as e:
                verdict = "Verifier error: " + str(e)[:120]
        okv = "CONFIRMED" in verdict.upper() and "NOT CONFIRMED" not in verdict.upper()
        incidents.transition(iid, "RESOLVED" if okv else "FAILED", VerifierStatus=verdict[:200],
                             RemediationStatus="verified" if okv else "unconfirmed")
    finally:
        _INFLIGHT.discard(iid)
    ok = bool(healed)
    vhtml = f"<p style='color:#8a8a93'>🔎 Verifier: {verdict}</p>" if verdict else ""
    body = (f"<p>Rolled back <b>{service}</b>. Healed: {', '.join(healed)}.</p>{vhtml}"
            "<p style='color:#8a8a93'>Incident record updated. The console shows RED→GREEN. You can close this tab.</p>") if ok else "No service specified."
    return HTMLResponse(_teams_page("✅ Approved &amp; remediated" if ok else "⚠ No service", body, "#22c55e" if ok else "#ef4444"))


def _teams_page(title: str, body: str, color: str) -> str:
    return ("<html><body style='font-family:system-ui;background:#0a0a0b;color:#fafafa;padding:48px;text-align:center'>"
            f"<h2 style='color:{color}'>{title}</h2><div>{body}</div></body></html>")


@app.get("/api/portal-agent/config")
def portal_agent_config() -> JSONResponse:
    """Portal Agent config (LOCAL-ONLY): whether it's enabled, and whether to embed the noVNC
    live browser panel (vs an external headed window)."""
    from shared.settings import AZURE_PORTAL_AGENT_ENABLED, PORTAL_AGENT_NOVNC, PORTAL_NOVNC_URL
    return JSONResponse({"enabled": bool(AZURE_PORTAL_AGENT_ENABLED),
                         "novnc": bool(PORTAL_AGENT_NOVNC),
                         "novnc_url": PORTAL_NOVNC_URL if PORTAL_AGENT_NOVNC else ""})


def _portal():
    """Import the portal_agent module (repo root on path)."""
    sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))
    from portal_agent import portal_agent as pa
    return pa


@app.post("/api/portal-agent/session/start")
async def portal_agent_start(req: Request) -> JSONResponse:
    """Start an event-aware Portal Agent session (opens the headed browser). Flag-gated, local-only."""
    from shared.settings import AZURE_PORTAL_AGENT_ENABLED
    if not AZURE_PORTAL_AGENT_ENABLED:
        from shared.obs import log
        log("portal.browser_skipped", reason="AZURE_PORTAL_AGENT_ENABLED=false")
        return JSONResponse({"ok": False, "reason": "disabled"})
    b = await req.json()
    event = str(b.get("event", "generic"))
    service = "".join(c for c in str(b.get("service", "")) if c.isalnum() or c in "-_")
    try:
        _portal().start_session(event, service)
        return JSONResponse({"ok": True, "event": event, "service": service})
    except Exception as e:
        return JSONResponse({"ok": False, "reason": str(e)[:140]})


@app.post("/api/portal-agent/session/advance")
async def portal_agent_advance(req: Request) -> JSONResponse:
    """Advance the session to the next contextual page for the given investigation stage."""
    stage = str((await req.json()).get("stage", ""))
    try:
        s = _portal().session()
        if s and not s.done:
            s.advance(stage)
            return JSONResponse({"ok": True})
    except Exception:
        pass
    return JSONResponse({"ok": False})


@app.post("/api/portal-agent/session/stop")
async def portal_agent_stop() -> JSONResponse:
    """Finish the session (linger briefly, then close the browser)."""
    try:
        s = _portal().session()
        if s and not s.done:
            s.stop()
    except Exception:
        pass
    return JSONResponse({"ok": True})


@app.get("/api/portal-agent/stream")
async def portal_agent_stream() -> StreamingResponse:
    """Stream the active Portal Agent session's events (status/evidence) to the console. Bridges the
    session's thread queue to SSE. If the flag is off / no session, emits a status and ends."""
    import asyncio
    import threading
    from shared.settings import AZURE_PORTAL_AGENT_ENABLED

    async def _one(events):
        for e in events:
            yield f"data: {json.dumps(e)}\n\n"

    if not AZURE_PORTAL_AGENT_ENABLED:
        return StreamingResponse(_one([{"type": "portal_status", "status": "disabled"}, {"type": "done"}]),
                                 media_type="text/event-stream")
    s = _portal().session()
    if s is None:
        return StreamingResponse(_one([{"type": "portal_status", "status": "Idle"}, {"type": "done"}]),
                                 media_type="text/event-stream")

    loop = asyncio.get_event_loop()
    q: asyncio.Queue = asyncio.Queue()

    def pump():
        try:
            for ev in s.drain():
                loop.call_soon_threadsafe(q.put_nowait, ev)
        finally:
            loop.call_soon_threadsafe(q.put_nowait, None)

    threading.Thread(target=pump, daemon=True).start()

    async def gen():
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield f"data: {json.dumps(ev)}\n\n"
        yield "data: {\"type\": \"done\"}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/reset")
def reset() -> JSONResponse:
    """Re-plant a fresh scenario, then return the new state."""
    py = os.path.join(ROOT, "data", ".venv", "bin", "python")
    subprocess.run([py, os.path.join(ROOT, "data", "generate_and_ingest.py")],
                   capture_output=True, cwd=os.path.join(ROOT, "data"))
    for _ in range(15):
        n = kusto.query("Telemetry | count | project Count").iloc[0].Count
        if int(n) >= 7200:
            break
        time.sleep(4)
    return state()


# SPA fallback — client-side routes (e.g. /workspace) return the React shell in react mode.
# Registered LAST so it never shadows /api/*, /legacy, or the /assets mount. GET-only.
@app.get("/{full_path:path}", response_class=HTMLResponse)
def spa_fallback(full_path: str) -> str:
    if FRONTEND_MODE == "react" and _REACT_AVAILABLE and not full_path.startswith("api"):
        return _react_html()
    from fastapi import HTTPException
    raise HTTPException(status_code=404)
