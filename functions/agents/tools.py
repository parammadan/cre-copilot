"""The agents' TOOLS — thin wrappers over the EXISTING KQL functions and the
DETERMINISTIC confidence gate. The LLM decides which to call; the numbers come
only from here (Kusto / gate), never invented by the model.

Inputs are sanitized (LLMs pass messy strings) and failures are returned to the
agent as JSON errors rather than raised, so one bad arg can't crash the run."""
import json
import pandas as pd
from semantic_kernel.functions import kernel_function

from shared import kusto
from shared.confidence import decide


_KNOWN: set | None = None


def _known() -> set:
    global _KNOWN
    if _KNOWN is None:
        _KNOWN = set(kusto.query("Telemetry | distinct Service")["Service"].tolist())
    return _KNOWN


def _svc(s: str) -> str:
    """Snap a messy LLM arg (e.g. 'payment-service (v3.4.0)') to a REAL service name."""
    s = str(s)
    for k in _known():
        if k in s:
            return k
    return "".join(c for c in s if c.isalnum() or c in "-_")[:40]


def _ver(s: str) -> str:
    """Clean a version string; cap length so junk can't pollute messages."""
    return "".join(c for c in str(s) if c.isalnum() or c in ".-_")[:20] or "unknown"


def _ts(s: str) -> str:
    """Keep only datetime-literal chars."""
    return "".join(c for c in str(s) if c.isalnum() or c in "-:T.Z")


class IncidentTools:
    @kernel_function(description="Detect services with anomalies in raw telemetry right now (rejects noise/blips). Returns JSON list.")
    def detect(self) -> str:
        try:
            df = kusto.query("Detect()")
            return df.to_json(orient="records") if not df.empty else "[]"
        except Exception as e:
            return json.dumps({"error": str(e)[:200]})

    @kernel_function(description="Get currently firing alerts. Returns JSON list of {service, time_iso, severity, description}.")
    def get_alerts(self) -> str:
        try:
            df = kusto.query(
                "let amax=toscalar(Alerts|summarize max(Timestamp));"
                "Alerts | where Timestamp>amax-60m | project Service, Timestamp, Severity, Description "
                "| order by Timestamp asc"
            )
            out = [{"service": r.Service,
                    "time_iso": pd.Timestamp(r.Timestamp).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "severity": r.Severity, "description": r.Description}
                   for r in df.itertuples(index=False)]
            return json.dumps(out)
        except Exception as e:
            return json.dumps({"error": str(e)[:200]})

    @kernel_function(description="Rank root-cause deploy candidates for ONE alert. alert_time_iso like 2026-07-03T16:11:00Z. Returns JSON list ordered by confidence.")
    def correlate(self, alert_service: str, alert_time_iso: str) -> str:
        try:
            df = kusto.query(f"Correlate('{_svc(alert_service)}', datetime({_ts(alert_time_iso)}))")
            return df.to_json(orient="records")
        except Exception as e:
            return json.dumps({"error": str(e)[:200], "hint": "pass one service name and one ISO time"})

    @kernel_function(description="Downstream blast radius for ONE service. Returns JSON list of affected services + latency multiple.")
    def assess_impact(self, service_name: str) -> str:
        try:
            df = kusto.query(f"ImpactAssessment('{_svc(service_name)}')")
            return df.to_json(orient="records")
        except Exception as e:
            return json.dumps({"error": str(e)[:200], "hint": "pass exactly one service name"})

    @kernel_function(description="Apply the DETERMINISTIC confidence gate (not an LLM decision). Returns action=auto_remediate|escalate with reason.")
    def apply_gate(self, confidence: float, service_name: str, version: str) -> str:
        try:
            d = decide(float(confidence), _svc(service_name), _ver(version))
            return json.dumps({"action": d.action, "confidence": d.confidence,
                               "threshold": d.threshold, "remediation": d.remediation, "reason": d.reason})
        except Exception as e:
            return json.dumps({"error": str(e)[:200]})
