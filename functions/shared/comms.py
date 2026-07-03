"""The Comms agent — turns a resolved/mitigating incident into a human-readable
status update (the kind you'd post to a status page or incident channel)."""
from __future__ import annotations
import pandas as pd


def status_update(*, severity: str, alert_service: str, root_service: str,
                  root_version: str, confidence: float, action: str,
                  auto: bool, impact: pd.DataFrame) -> str:
    headline = "RESOLVED" if auto else "INVESTIGATING"
    degraded = impact[impact["LatencyIncrease"] >= 1.3]
    if len(degraded):
        worst = degraded.sort_values("LatencyIncrease", ascending=False).iloc[0]
        impact_line = (f"{len(degraded)} service(s) degraded — worst: "
                       f"{worst.AffectedService} at {worst.LatencyIncrease:.1f}x normal latency")
    else:
        impact_line = "no downstream services materially degraded"

    if auto:
        action_line = f"Automated remediation applied: {action}."
        next_line = "Monitoring recovery; will confirm full resolution shortly."
    else:
        action_line = f"Escalated to on-call: {action}."
        next_line = "Awaiting human confirmation before remediating. Next update in 15 min."

    return (
        f"[{headline}] {severity} — degraded performance on {alert_service}\n"
        f"Root cause: {root_service} {root_version} "
        f"(identified with {confidence*100:.0f}% confidence).\n"
        f"Impact: {impact_line}.\n"
        f"{action_line}\n"
        f"{next_line}"
    )
