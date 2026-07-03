"""The confidence gate — the KEY FEATURE.

Pure, dependency-free, unit-testable decision logic: given a correlation
confidence, decide whether the system acts autonomously or escalates to a human.
Kept out of the agents (and out of any GUI) so it can be reviewed and tuned in one place.
"""
from __future__ import annotations
from dataclasses import dataclass

# Act autonomously at/above this confidence; escalate below it.
ACT_THRESHOLD = 0.70


@dataclass
class GateDecision:
    action: str          # "auto_remediate" | "escalate"
    confidence: float
    threshold: float
    remediation: str     # the action to take (or the reason for escalation)
    reason: str


def remediation_for(service: str, version: str) -> str:
    """Map a root cause to a self-heal action. In production this hits a runbook /
    deployment API; here it's the rollback we'd trigger."""
    return f"rollback {service} to previous release (culprit: {version})"


def decide(confidence: float, service: str, version: str,
           threshold: float = ACT_THRESHOLD) -> GateDecision:
    if confidence >= threshold:
        return GateDecision(
            action="auto_remediate",
            confidence=confidence,
            threshold=threshold,
            remediation=remediation_for(service, version),
            reason=(f"confidence {confidence:.2f} >= {threshold:.2f} — high-certainty root "
                    f"cause ({service} {version}); safe to self-heal without a human."),
        )
    return GateDecision(
        action="escalate",
        confidence=confidence,
        threshold=threshold,
        remediation="page on-call engineer with ranked candidates for review",
        reason=(f"confidence {confidence:.2f} < {threshold:.2f} — insufficient certainty; "
                f"escalating to a human rather than acting blindly."),
    )


if __name__ == "__main__":
    # quick self-check of the gate at a few confidence levels
    for c in (0.774, 0.70, 0.60, 0.42):
        d = decide(c, "payment-service", "v3.4.0")
        print(f"conf={c:.2f} -> {d.action:14s} | {d.reason}")
