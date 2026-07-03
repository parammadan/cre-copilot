"""Unit tests for the confidence gate — the system's most consequential decision."""
from shared.confidence import decide, remediation_for, ACT_THRESHOLD, GateDecision


def test_acts_above_threshold():
    assert decide(0.80, "payment-service", "v3.4.0").action == "auto_remediate"


def test_escalates_below_threshold():
    assert decide(0.57, "auth", "v8.0.0").action == "escalate"


def test_boundary_is_inclusive():
    # exactly at the threshold should ACT (>=)
    assert decide(ACT_THRESHOLD, "svc", "v1").action == "auto_remediate"


def test_just_below_boundary_escalates():
    assert decide(ACT_THRESHOLD - 0.001, "svc", "v1").action == "escalate"


def test_custom_threshold_can_force_escalation():
    # the same strong signal escalates under a stricter policy
    assert decide(0.80, "payment-service", "v3.4.0", threshold=0.85).action == "escalate"


def test_remediation_names_the_culprit():
    r = remediation_for("payment-service", "v3.4.0")
    assert "payment-service" in r and "v3.4.0" in r


def test_auto_decision_carries_rollback_action():
    d = decide(0.95, "payment-service", "v3.4.0")
    assert isinstance(d, GateDecision)
    assert "rollback" in d.remediation
    assert d.confidence == 0.95 and d.threshold == ACT_THRESHOLD
