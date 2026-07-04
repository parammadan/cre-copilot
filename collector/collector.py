#!/usr/bin/env python3
"""Telemetry collector — polls the local microservices and ingests REAL measured
telemetry + logs into the existing ADX tables.

FEATURE-FLAGGED and non-destructive:
  * Runs only when TELEMETRY_SOURCE=services (otherwise it idles — the synthetic
    generator, generate_and_ingest.py, stays the source).
  * Writes into the SAME Telemetry table (Timestamp, Service, Metric, Value, Environment)
    the agents already read — so no agent/KQL/UI change is required.
  * Writes logs into the Logs table (created on startup if missing).

Usage:
  TELEMETRY_SOURCE=services python collector/collector.py            # poll forever
  TELEMETRY_SOURCE=services python collector/collector.py once 3     # poll 3 times then exit
Rollback: just stop it. Nothing it wrote is required by the synthetic demo.
"""
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
os.environ.setdefault("ADX_CLUSTER_URI", "https://crecopilotadxvxxmsm.eastus.kusto.windows.net")
os.environ.setdefault("ADX_DATABASE", "CopilotDb")
from shared import kusto  # noqa: E402

SOURCE = os.environ.get("TELEMETRY_SOURCE", "synthetic")
INTERVAL = int(os.environ.get("COLLECTOR_INTERVAL_SEC", "12"))
SERVICES = {  # env-driven: localhost for local dev, internal DNS in Azure Container Apps
    "checkout-api":      os.environ.get("CHECKOUT_URL",  "http://127.0.0.1:8101"),
    "payment-service":   os.environ.get("PAYMENT_URL",   "http://127.0.0.1:8102"),
    "inventory-service": os.environ.get("INVENTORY_URL", "http://127.0.0.1:8103"),
    "auth-service":      os.environ.get("AUTH_URL",      "http://127.0.0.1:8104"),
}
_seen_logs: set = set()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_tables() -> None:
    kusto.command(".create-merge table Logs (Timestamp:datetime, Service:string, Level:string, Message:string)")


def _q(s: str) -> str:
    return str(s).replace('"', "'").replace("\n", " ")[:300]


def poll_once() -> tuple[int, int]:
    ts = _now()
    trows, lrows = [], []
    for svc, url in SERVICES.items():
        try:
            m = requests.get(url + "/metrics", timeout=1.5).json()
        except Exception as e:
            print(f"  [warn] {svc} unreachable ({str(e)[:50]}) — skipping this cycle")
            continue
        trows += [(ts, svc, "latency_ms", m["latency_p95_ms"]),
                  (ts, svc, "error_rate", m["error_rate_pct"]),
                  (ts, svc, "cpu_pct", m["cpu_pct"]),
                  (ts, svc, "req_per_sec", m["rps"])]
        try:
            for L in requests.get(url + "/logs?n=20", timeout=1.5).json().get("logs", []):
                key = (svc, L["ts"], L["msg"])
                if key not in _seen_logs:
                    _seen_logs.add(key)
                    lrows.append((L["ts"], svc, L["level"], L["msg"]))
        except Exception:
            pass
    if trows:
        dt = ",".join(f"datetime({t}),'{s}','{me}',real({v}),'prod'" for (t, s, me, v) in trows)
        kusto.command(".set-or-append Telemetry <| "
                      "datatable(Timestamp:datetime,Service:string,Metric:string,Value:real,Environment:string)"
                      f"[{dt}]")
    if lrows:
        dt = ",".join(f"datetime({t}),'{s}','{lv}',\"{_q(msg)}\"" for (t, s, lv, msg) in lrows)
        kusto.command(".set-or-append Logs <| "
                      "datatable(Timestamp:datetime,Service:string,Level:string,Message:string)"
                      f"[{dt}]")
    return len(trows), len(lrows)


def main() -> None:
    if SOURCE != "services":
        print(f"TELEMETRY_SOURCE={SOURCE!r} → collector idle (synthetic generator is the source).")
        print("Enable with:  TELEMETRY_SOURCE=services python collector/collector.py")
        return
    _ensure_tables()
    once = len(sys.argv) > 1 and sys.argv[1] == "once"
    n = int(sys.argv[2]) if once and len(sys.argv) > 2 else 0
    print(f"Collector: polling {len(SERVICES)} services every {INTERVAL}s → ADX. "
          + ("(once mode)" if once else "Ctrl-C to stop."))
    i = 0
    while True:
        try:
            t, l = poll_once()
            print(f"{_now()}  +{t} telemetry, +{l} logs")
        except Exception as e:
            print(f"  [error] {str(e)[:120]}")
        i += 1
        if once and i >= n:
            break
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
