#!/usr/bin/env python3
"""CRE Copilot — live console backend.

Exposes the real detection/correlation/impact pipeline (the same KQL functions the
CLI orchestrator uses) as JSON for the interactive web console. The act-vs-escalate
gate is applied client-side against a live threshold slider, so no server round-trip
is needed to watch decisions flip.

Run:  ./demo/console.sh   (or: uvicorn app.server:app --port 8000)
"""
from __future__ import annotations
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


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    with open(os.path.join(HERE, "index.html")) as f:
        return f.read()


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


_SERVICE_PORTS = {"checkout-api": 8101, "payment-service": 8102, "inventory-service": 8103, "auth-service": 8104}
_SERVICE_ENV = {"checkout-api": "CHECKOUT_URL", "payment-service": "PAYMENT_URL",
                "inventory-service": "INVENTORY_URL", "auth-service": "AUTH_URL"}


def _service_base(service: str) -> str | None:
    """Base URL for a microservice — env override (Azure internal DNS) else local port. Additive."""
    override = os.environ.get(_SERVICE_ENV.get(service, ""))
    if override:
        return override.rstrip("/")
    port = _SERVICE_PORTS.get(service)
    return f"http://127.0.0.1:{port}" if port else None


def _remediate_service(service: str) -> list:
    """Shared remediation. REAL action first: POST /recover to the actual service (if the
    local lab is running); ALSO write recovery telemetry so the ADX-based health view heals
    even without the services (fallback). Used by the console AND Teams."""
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
    from shared.obs import log
    log("remediation.applied", service=service, healed=healed)
    return healed


@app.post("/api/remediate")
async def remediate(req: Request) -> JSONResponse:
    """Simulate the rollback taking effect (console Approve button)."""
    service = (await req.json()).get("service", "")
    if not service:
        return JSONResponse({"error": "no service"}, status_code=400)
    return JSONResponse({"healed": _remediate_service(service)})


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


@app.get("/api/teams/approve")
async def teams_approve(service: str = "") -> HTMLResponse:
    """Approve callback from the Teams card (Action.OpenUrl) — runs remediation."""
    healed = _remediate_service(service)
    ok = bool(healed)
    return HTMLResponse(
        "<html><body style='font-family:system-ui;background:#0b0d13;color:#e6e8ee;padding:48px;text-align:center'>"
        + (f"<h2 style='color:#22c55e'>✅ Approved &amp; remediated</h2>"
           f"<p>Rolled back <b>{service}</b>. Healed: {', '.join(healed)}.</p>"
           "<p style='color:#868da0'>The console shows RED→GREEN. You can close this tab.</p>"
           if ok else "<h2 style='color:#d03b3b'>No service specified.</h2>")
        + "</body></html>")


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
