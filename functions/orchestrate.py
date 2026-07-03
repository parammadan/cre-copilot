#!/usr/bin/env python3
"""CRE Copilot — orchestrator (the glue between the agents + the confidence gate).

Runs the full live-site incident-response flow against live ADX, handling ALL
concurrent incidents:
  1. Detector  -> anomalies in raw telemetry
  2. For each firing alert:
       Correlator -> root cause + confidence
       Gate       -> act autonomously (>= threshold) or escalate (< threshold)
       Impact     -> blast radius
       Comms      -> status update
       Persist    -> write outcome to Incidents
  3. Summary across all incidents.

Runs locally against the cloud cluster (Functions tier is quota-gated); the same
code would run in the Function App via managed identity.

Usage:
    export ADX_CLUSTER_URI=... ADX_DATABASE=CopilotDb
    python orchestrate.py [gate_threshold]
"""
from __future__ import annotations
import sys
import time

import pandas as pd

from shared import kusto
from shared.confidence import decide, ACT_THRESHOLD
from shared.comms import status_update

RULE = "─" * 68


def _section(title: str) -> None:
    print(f"\n{RULE}\n {title}\n{RULE}")


def _persist(inc_service: str, severity: str, top, decision) -> None:
    inc_id = f"INC-{int(time.time() * 1000) % 100000000}"
    start_iso = pd.Timestamp(top.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")
    auto = decision.action == "auto_remediate"
    end_expr = "now()" if auto else "datetime(null)"
    root_cause = f"{top.Service} {top.Version}: {decision.remediation}"
    kusto.command(
        f'.set-or-append Incidents <| print '
        f'IncidentId="{inc_id}", StartTime=datetime({start_iso}), EndTime={end_expr}, '
        f'Service="{inc_service}", Severity="{severity}", '
        f'RootCause="{root_cause}", Status="{"auto-resolved" if auto else "escalated"}", '
        f'ResolvedBy="{"auto" if auto else "pending-human"}", '
        f'Confidence={float(top.confidence)}'
    )


def process(alert, threshold: float) -> dict | None:
    alert_iso = pd.Timestamp(alert.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")
    candidates = kusto.query(f"Correlate('{alert.Service}', datetime({alert_iso}))")
    if candidates.empty:
        return None
    top = candidates.iloc[0]
    decision = decide(float(top.confidence), top.Service, top.Version, threshold)
    impact = kusto.query(f"ImpactAssessment('{top.Service}')")
    message = status_update(
        severity=alert.Severity, alert_service=alert.Service,
        root_service=top.Service, root_version=top.Version,
        confidence=float(top.confidence), action=decision.remediation,
        auto=(decision.action == "auto_remediate"), impact=impact,
    )
    _persist(alert.Service, alert.Severity, top, decision)
    return {
        "alert_service": alert.Service, "severity": alert.Severity,
        "root": f"{top.Service} {top.Version}", "confidence": float(top.confidence),
        "action": decision.action, "remediation": decision.remediation, "message": message,
    }


def main() -> None:
    threshold = float(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else ACT_THRESHOLD
    print(f"CRE Copilot — incident response run  (gate threshold = {threshold:.2f})")

    # 1. DETECTOR ----------------------------------------------------------
    _section("DETECTOR — anomalies in raw telemetry")
    anomalies = kusto.query("Detect()")
    if anomalies.empty:
        print("All services healthy. No incident. ✅")
        return
    print(anomalies.to_string(index=False))

    # 2. ALL CONCURRENT ALERTS --------------------------------------------
    alerts = kusto.query(
        "let amax = toscalar(Alerts | summarize max(Timestamp)); "
        "Alerts | where Timestamp > amax - 60m "
        "| project Service, Timestamp, Severity, Description | order by Timestamp asc"
    )
    _section(f"{len(alerts)} CONCURRENT INCIDENT(S) — correlate + gate each")
    results = []
    for i, alert in enumerate(alerts.itertuples(index=False), 1):
        r = process(alert, threshold)
        if not r:
            continue
        results.append(r)
        icon = "🤖 AUTO-REMEDIATE" if r["action"] == "auto_remediate" else "🧑 ESCALATE TO HUMAN"
        print(f"\n[{i}/{len(alerts)}] {r['severity']} on {r['alert_service']}")
        print(f"      root cause : {r['root']}  (confidence {r['confidence']:.2f})")
        print(f"      gate       : {icon}")
        print(f"      action     : {r['remediation']}")

    # 3. SUMMARY -----------------------------------------------------------
    autos = sum(1 for r in results if r["action"] == "auto_remediate")
    _section("SUMMARY")
    print(f"{len(results)} incident(s): {autos} auto-remediated, {len(results) - autos} escalated.")
    example = next((r for r in results if r["action"] == "auto_remediate"), None)
    if example:
        print("\nAuto-generated status update (Comms agent):\n" + example["message"])


if __name__ == "__main__":
    main()
