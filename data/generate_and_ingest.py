#!/usr/bin/env python3
"""
CRE Copilot — Phase 1: synthetic telemetry generator + ingestion.

Builds a realistic incident and loads it into ADX:
  - baseline metrics for 5 services over the last few hours
  - a planted deployment to `payment-service` that spikes its latency + errors
  - a cascade into `checkout-api` (its dependency)
  - an alert firing on checkout-api ~8 min after the deploy
  - decoy deploys to other services (so correlation isn't trivial)
  - a little incident history for dashboards

Auth: uses your `az login` (AzureCliCredential). You were granted ADX DB Admin
in the Bicep, so this can create tables and ingest.

Usage:
    pip install -r requirements.txt
    # set ADX_CLUSTER_URI + ADX_DATABASE (see .env.example), then:
    python generate_and_ingest.py
"""
from __future__ import annotations
import math
import os
import uuid
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.ingest import QueuedIngestClient, IngestionProperties
from azure.kusto.data.data_format import DataFormat

load_dotenv()

CLUSTER_URI = os.environ["ADX_CLUSTER_URI"].rstrip("/")
DATABASE = os.environ.get("ADX_DATABASE", "CopilotDb")
# Ingest endpoint = same host with an "ingest-" prefix.
INGEST_URI = CLUSTER_URI.replace("https://", "https://ingest-")

# ---- incident design (tweak these to re-shape the demo) --------------------
SERVICES = ["frontend", "checkout-api", "payment-service", "inventory", "auth"]
METRICS = ["latency_ms", "error_rate", "cpu_pct", "req_per_sec"]
ENV = "prod"
WINDOW_HOURS = 6
STEP_MIN = 1
NOW = datetime.now(timezone.utc).replace(second=0, microsecond=0)
INCIDENT_AT = NOW - timedelta(minutes=25)          # recent, sharp incident (good for anomaly detection)
CULPRIT = "payment-service"
VICTIM = "checkout-api"                              # cascaded dependency
CASCADE_DELAY_MIN = 2
ALERT_DELAY_MIN = 8
DECOY_DELAY_MIN = 5   # a *more recent* but innocent deploy (tests the correlator)

# A SECOND, concurrent, MILDER incident — auth-service. Lower anomaly => lower
# confidence => the gate should ESCALATE this one while auto-remediating payment.
SECOND_SVC = "auth"
SECOND_INCIDENT_AT = NOW - timedelta(minutes=22)
SECOND_ALERT_DELAY_MIN = 8
SECOND_SPIKE = {"latency_ms": 1.8, "error_rate": 4.0}  # moderate, not dramatic

# Service topology: "<Service> DependsOn <upstream>". checkout-api sits on payment/inventory/auth.
SERVICE_DEPS = [
    ("frontend", "checkout-api"),
    ("checkout-api", "payment-service"),
    ("checkout-api", "inventory"),
    ("checkout-api", "auth"),
]

# Per-service baseline profiles (mean, std) per metric — DIFFERENT noise per service,
# so detection can't rely on one global threshold.
SERVICE_PROFILES = {
    "frontend":        {"latency_ms": (110, 6),  "error_rate": (0.4, 0.12), "cpu_pct": (38, 5), "req_per_sec": (220, 18)},
    "checkout-api":    {"latency_ms": (130, 9),  "error_rate": (0.6, 0.18), "cpu_pct": (46, 7), "req_per_sec": (260, 22)},
    "payment-service": {"latency_ms": (120, 7),  "error_rate": (0.5, 0.15), "cpu_pct": (42, 6), "req_per_sec": (180, 16)},
    "inventory":       {"latency_ms": (100, 13), "error_rate": (0.5, 0.22), "cpu_pct": (35, 9), "req_per_sec": (150, 20)},  # noisiest
    "auth":            {"latency_ms": (115, 6),  "error_rate": (0.4, 0.12), "cpu_pct": (40, 5), "req_per_sec": (200, 18)},
}
# Gentle daily (diurnal) swing as a fraction of baseline — realistic seasonality.
DIURNAL_AMP = {"latency_ms": 0.10, "cpu_pct": 0.12, "req_per_sec": 0.18, "error_rate": 0.0}

SPIKE = {  # multiplicative/absolute bump during the incident
    "latency_ms":   6.5,   # 120 -> ~800ms
    "error_rate":   24.0,  # 0.5 -> ~12%
    "cpu_pct":      2.1,   # 40 -> ~85%
    "req_per_sec":  0.7,   # traffic dips as errors climb
}


SCENARIO = os.environ.get("SCENARIO", "classic")


def _scenario_spec():
    """Incidents/deploys/alerts for the chosen SCENARIO. 'start'/'at'/'end' = minutes before NOW."""
    if SCENARIO == "proactive":   # rising trend caught BEFORE it breaches — no alert yet
        return {
            "incidents": [dict(svc="checkout-api", start=40, lat_mult=1.95, ramp=True)],
            "deploys": [dict(svc="checkout-api", at=40, ver="v5.1.0", who="cai")],
            "alerts": [],
        }
    if SCENARIO == "ambiguous":   # two close deploys, neither dominates -> escalate
        return {
            "incidents": [dict(svc="checkout-api", start=20, lat_mult=1.9, err_mult=3.0),
                          dict(svc="payment-service", start=20, lat_mult=2.0, err_mult=3.0),
                          dict(svc="inventory", start=20, lat_mult=1.9, err_mult=3.0)],
            "deploys": [dict(svc="payment-service", at=18, ver="v3.5.0", who="deep"),
                        dict(svc="inventory", at=17, ver="v7.4.0", who="ana")],
            "alerts": [dict(svc="checkout-api", at=12, sev="Sev2", thr=300.0, obs=250.0,
                            desc="checkout-api p95 latency breached 300ms threshold")],
        }
    if SCENARIO == "falsealarm":  # alert fired but it was a transient blip, already recovered
        return {
            "incidents": [dict(svc="checkout-api", start=15, end=12, lat_mult=3.0)],
            "deploys": [],
            "alerts": [dict(svc="checkout-api", at=14, sev="Sev2", thr=300.0, obs=360.0,
                            desc="checkout-api p95 latency spike (transient)")],
        }
    # classic (default): severe payment (auto-remediate) + mild auth (escalate)
    return {
        "incidents": [dict(svc="payment-service", start=25, lat_mult=6.5, err_mult=24.0),
                      dict(svc="checkout-api", start=23, lat_mult=2.9, err_mult=10.0),
                      dict(svc="auth", start=22, lat_mult=1.8, err_mult=4.0)],
        "deploys": [dict(svc="payment-service", at=25, ver="v3.4.0", who="deep"),
                    dict(svc="inventory", at=20, ver="v7.3.0", who="ana"),
                    dict(svc="auth", at=22, ver="v8.0.0", who="bo")],
        "alerts": [dict(svc="checkout-api", at=17, sev="Sev2", thr=300.0, obs=540.0,
                        desc="checkout-api p95 latency breached 300ms threshold"),
                   dict(svc="auth", at=14, sev="Sev3", thr=180.0, obs=216.0,
                        desc="auth p95 latency elevated above 180ms threshold")],
    }


SPEC = _scenario_spec()


def _kcsb(uri: str) -> KustoConnectionStringBuilder:
    return KustoConnectionStringBuilder.with_az_cli_authentication(uri)


def create_schema(client: KustoClient) -> None:
    with open(os.path.join(os.path.dirname(__file__), "kql", "01_tables.kql")) as f:
        raw = f.read()
    # Drop // comment lines, then split the file into individual control commands.
    no_comments = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))
    cmds = [c.strip() for c in no_comments.split("\n\n") if c.strip().startswith(".create")]
    for cmd in cmds:
        client.execute_mgmt(DATABASE, cmd)
    print(f"✓ {len(cmds)} tables created")
    # Clear data so re-running this script is idempotent (no duplicate rows).
    for t in ["Telemetry", "Deployments", "Alerts", "Incidents", "ServiceDependencies", "Runbooks", "Postmortems"]:
        client.execute_mgmt(DATABASE, f".clear table {t} data")
    print("✓ tables cleared for clean reload")
    # Make queued ingestion flush fast so the demo shows data in seconds, not minutes.
    client.execute_mgmt(
        DATABASE,
        '.alter database ' + DATABASE + ' policy ingestionbatching '
        '```{"MaximumBatchingTimeSpan":"00:00:20","MaximumNumberOfItems":500,'
        '"MaximumRawDataSizeMB":100}```',
    )
    print("✓ fast-ingest batching policy set")


def _diurnal(t: datetime, metric: str) -> float:
    """Gentle daily pattern (peaks mid-afternoon), as a multiplier around 1.0."""
    frac = (t.hour * 60 + t.minute) / 1440.0
    return 1.0 + DIURNAL_AMP.get(metric, 0.0) * math.sin(2 * math.pi * (frac - 0.25))


def build_telemetry() -> pd.DataFrame:
    steps = int(WINDOW_HOURS * 60 / STEP_MIN)
    times = [NOW - timedelta(minutes=STEP_MIN * i) for i in range(steps)][::-1]
    rng = np.random.default_rng(42)

    # Pre-pick harmless TRANSIENT BLIPS (1-2 min latency bumps) well before the incident
    # window — these must NOT trigger incidents (they test the detector's noise rejection).
    incident_zone = len(times) - 30
    blip_idx: dict[str, set] = {}
    for svc in SERVICES:
        for _ in range(int(rng.integers(2, 4))):          # 2-3 blips per service
            start = int(rng.integers(20, max(21, incident_zone - 5)))
            for k in range(int(rng.integers(1, 3))):       # 1-2 minutes long
                blip_idx.setdefault(svc, set()).add(start + k)

    rows = []
    for svc in SERVICES:
        prof = SERVICE_PROFILES[svc]
        for metric in METRICS:
            mean, std = prof[metric]
            for i, t in enumerate(times):
                base = mean * _diurnal(t, metric)
                val = rng.normal(base, std)                # noisy baseline + seasonality
                # harmless transient blip (short — should be rejected as noise)
                if metric == "latency_ms" and i in blip_idx.get(svc, ()):
                    val = base * rng.uniform(2.0, 2.8)
                # REAL incidents / trends from the scenario spec (override any blip)
                for inc in SPEC["incidents"]:
                    if svc != inc["svc"]:
                        continue
                    start_t = NOW - timedelta(minutes=inc["start"])
                    end_t = NOW - timedelta(minutes=inc["end"]) if inc.get("end") else None
                    if t < start_t or (end_t is not None and t > end_t):
                        continue
                    if metric == "latency_ms":
                        m = inc["lat_mult"]
                        if inc.get("ramp"):
                            span = (NOW - start_t).total_seconds() or 1
                            frac = max(0.0, min(1.0, (t - start_t).total_seconds() / span))
                            m = 1.0 + (inc["lat_mult"] - 1.0) * frac
                        val = mean * m + rng.normal(0, std)
                    elif metric == "error_rate" and inc.get("err_mult"):
                        val = mean * inc["err_mult"] + rng.normal(0, std)
                rows.append((t, svc, metric, float(max(val, 0)), ENV))
    return pd.DataFrame(rows, columns=["Timestamp", "Service", "Metric", "Value", "Environment"])


def build_deployments() -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    # innocent decoys scattered far back in the window (noise for the correlator)
    for svc in ["frontend", "inventory", "auth", "frontend"]:
        t = NOW - timedelta(minutes=int(rng.integers(150, 340)))
        rows.append((t, svc, f"v{rng.integers(1,9)}.{rng.integers(0,9)}.{rng.integers(0,9)}",
                     uuid.uuid4().hex[:8], rng.choice(["ana", "bo", "cai", "deep"]),
                     "azure-pipelines-ci", ENV))
    # scenario deploys
    for d in SPEC["deploys"]:
        rows.append((NOW - timedelta(minutes=d["at"]), d["svc"], d["ver"], uuid.uuid4().hex[:8],
                     d["who"], "azure-pipelines-ci", ENV))
    return pd.DataFrame(rows, columns=["Timestamp", "Service", "Version", "CommitId",
                                       "Author", "Pipeline", "Environment"])


def build_dependencies() -> pd.DataFrame:
    return pd.DataFrame(SERVICE_DEPS, columns=["Service", "DependsOn"])


def build_runbooks() -> pd.DataFrame:
    # Seed ONE premade runbook (payment-service) so classic incidents MATCH; other
    # services have none -> the Runbook agent authors + stores one on resolution.
    rows = [("RB-1001", "payment-service:bad_deploy", "payment-service", "bad_deploy",
             "1) Roll back payment-service to the previous release. "
             "2) Verify checkout-api p95 latency returns < 300ms. 3) Confirm error rate < 1%.",
             "seed", NOW - timedelta(days=20), 3)]
    return pd.DataFrame(rows, columns=["RunbookId", "Signature", "Service", "FailureType",
                                       "Steps", "CreatedBy", "CreatedAt", "TimesUsed"])


ALERT_COLS = ["Timestamp", "AlertId", "Service", "Metric", "Severity",
              "Threshold", "ObservedValue", "Description"]


def build_alerts() -> pd.DataFrame:
    rows = [(NOW - timedelta(minutes=a["at"]), f"ALT-{uuid.uuid4().hex[:6]}", a["svc"], "latency_ms",
             a["sev"], a["thr"], a["obs"], a["desc"]) for a in SPEC["alerts"]]
    return pd.DataFrame(rows, columns=ALERT_COLS)


def build_incident_history() -> pd.DataFrame:
    rng = np.random.default_rng(11)
    rows = []
    for i in range(30):
        start = NOW - timedelta(days=int(rng.integers(1, 30)), minutes=int(rng.integers(0, 1400)))
        conf = round(float(rng.uniform(0.45, 0.98)), 2)
        # The confidence gate outcome: high confidence -> auto-resolved fast; low -> escalated, slower.
        auto = conf >= 0.70
        dur = int(rng.integers(4, 20)) if auto else int(rng.integers(30, 120))
        rows.append((f"INC-{1000+i}", start, start + timedelta(minutes=dur),
                     rng.choice(SERVICES), rng.choice(["Sev1", "Sev2", "Sev2", "Sev3"]),
                     rng.choice(["bad deploy", "config drift", "dependency outage", "capacity"]),
                     "auto-resolved" if auto else "escalated",
                     "auto" if auto else rng.choice(["ana", "bo", "cai"]), conf))
    return pd.DataFrame(rows, columns=["IncidentId","StartTime","EndTime","Service","Severity",
                                       "RootCause","Status","ResolvedBy","Confidence"])


def ingest(ic: QueuedIngestClient, df: pd.DataFrame, table: str) -> None:
    if df.empty:
        print(f"✓ (no rows for {table} — expected for this scenario)")
        return
    props = IngestionProperties(database=DATABASE, table=table, data_format=DataFormat.CSV)
    ic.ingest_from_dataframe(df, ingestion_properties=props)
    print(f"✓ queued {len(df):>5} rows -> {table}")


def main() -> None:
    print(f"Cluster: {CLUSTER_URI}\nDatabase: {DATABASE}\nScenario: {SCENARIO.upper()}")
    admin = KustoClient(_kcsb(CLUSTER_URI))
    create_schema(admin)

    ic = QueuedIngestClient(_kcsb(INGEST_URI))
    ingest(ic, build_telemetry(), "Telemetry")
    ingest(ic, build_deployments(), "Deployments")
    ingest(ic, build_alerts(), "Alerts")
    ingest(ic, build_incident_history(), "Incidents")
    ingest(ic, build_dependencies(), "ServiceDependencies")
    ingest(ic, build_runbooks(), "Runbooks")
    print("\nAll queued. Data lands in ~20-40s (fast batching policy). "
          "Verify with:  Telemetry | count")


if __name__ == "__main__":
    main()
