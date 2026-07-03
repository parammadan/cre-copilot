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
import sys
import time

import pandas as pd
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
from shared import kusto  # noqa: E402
from agents.assistants import run_stream_sync  # noqa: E402 (hosted Azure OpenAI assistants)

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
        aiso = pd.Timestamp(a.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")
        cand = kusto.query(f"Correlate('{a.Service}', datetime({aiso}))")
        if cand.empty:
            continue
        top = cand.iloc[0]
        impact = kusto.query(f"ImpactAssessment('{top.Service}')")
        incidents.append({
            "alertService": a.Service, "severity": a.Severity,
            "alertTime": pd.Timestamp(a.Timestamp).strftime("%H:%M"),
            "description": a.Description,
            "candidates": _records(cand),
            "rootCause": {"service": top.Service, "version": top.Version,
                          "confidence": float(top.confidence)},
            "impact": _records(impact),
        })

    # Current health = recent (8 min) latency vs each service's baseline — this is what
    # heals RED->GREEN after remediation (unlike Detect(), which analyses the incident).
    health = kusto.query(
        "let tmax=toscalar(Telemetry|summarize max(Timestamp));"
        "let recent=Telemetry|where Metric=='latency_ms' and Timestamp>tmax-8m|summarize cur=avg(Value) by Service;"
        "let base=Telemetry|where Metric=='latency_ms' and Timestamp between (tmax-4h .. tmax-30m)|summarize b=avg(Value) by Service;"
        "recent|join kind=inner base on Service|extend ratio=cur/b|project Service, ratio=round(ratio,2)"
    )
    health_map = {r.Service: float(r.ratio) for r in health.itertuples(index=False)}

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
async def incident_stream() -> StreamingResponse:
    """SSE: streams the HOSTED Azure-assistant conversation live. The assistants SDK is
    synchronous, so we run it in a thread and bridge events to async via a queue."""
    import asyncio
    import threading
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_event_loop()

    def worker():
        try:
            for ev in run_stream_sync():
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


@app.post("/api/remediate")
async def remediate(req: Request) -> JSONResponse:
    """Simulate the rollback taking effect: write recovery telemetry (baseline values,
    going forward) for the root-cause service AND its downstream victims. Synchronous
    .set-or-append -> queryable immediately -> the dashboard heals RED->GREEN at once."""
    body = await req.json()
    service = "".join(c for c in str(body.get("service", "")) if c.isalnum() or c in "-_")
    if not service:
        return JSONResponse({"error": "no service"}, status_code=400)
    deps = kusto.query(f"ServiceDependencies | where DependsOn=='{service}' | project Service")
    healed = [service] + deps["Service"].tolist()
    healed_kql = "dynamic([" + ",".join(f"'{s}'" for s in healed) + "])"
    # Continue EVERY service 15 min forward so none drop out of the recent window:
    #   healed services -> return to baseline; others -> continue their current level
    #   (so the escalated 'auth' stays elevated, healthy services stay healthy).
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
        "| project Timestamp, Service, Metric, Value, Environment"
    )
    return JSONResponse({"healed": healed})


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
