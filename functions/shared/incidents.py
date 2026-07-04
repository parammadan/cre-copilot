"""Durable incident state — ADX as the system of record.

Why this exists (see docs/incident-state.md): incident + approval state used to live in
in-memory dicts, which vanish on restart and diverge across replicas — so a Teams approval
callback could hit a replica that never saw the incident, and a double-click could remediate
twice. Here every state transition is APPENDED to ADX; the current state is
`arg_max(UpdatedAt, *) by IncidentId`. That gives restart safety, multi-replica correctness,
correct Teams callbacks, and a real audit trail.
"""
from __future__ import annotations

import hashlib
import time

try:
    from shared import kusto
except Exception:  # pragma: no cover
    import kusto  # type: ignore

TABLE = "IncidentRecords"
STATUSES = ("OPEN", "INVESTIGATING", "AWAITING_APPROVAL", "REMEDIATING",
            "VERIFYING", "RESOLVED", "CLOSED", "FAILED")
ACTIVE = ("OPEN", "INVESTIGATING", "AWAITING_APPROVAL", "REMEDIATING", "VERIFYING")

# Column order MUST match the datatable schema in _append().
_COLS = ["IncidentId", "Service", "Severity", "Status", "RootCauseService", "RootCauseVersion",
         "Confidence", "GateDecision", "TeamsPosted", "ApprovalStatus", "RemediationStatus",
         "VerifierStatus", "TraceId", "Metric", "Value", "Threshold", "Description",
         "IdempotencyKeys", "CreatedAt", "UpdatedAt"]
_SCHEMA = ("IncidentId:string,Service:string,Severity:string,Status:string,"
           "RootCauseService:string,RootCauseVersion:string,Confidence:real,"
           "GateDecision:string,TeamsPosted:bool,ApprovalStatus:string,"
           "RemediationStatus:string,VerifierStatus:string,TraceId:string,"
           "Metric:string,Value:real,Threshold:real,Description:string,"
           "IdempotencyKeys:string,CreatedAt:datetime,UpdatedAt:datetime")

_ensured = False


def ensure_table() -> None:
    global _ensured
    if _ensured:
        return
    kusto.command(f".create-merge table {TABLE} ({_SCHEMA})")
    _ensured = True


def make_id(service: str) -> str:
    """Stable id per service → repeated alerts for the same service map to one open incident (dedup)."""
    return "INC-" + hashlib.sha1(str(service).encode()).hexdigest()[:8].upper()


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _q(s) -> str:
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ")[:400]


def _dt(v) -> str:
    if v is None or v == "":
        return _now()
    try:
        import pandas as pd
        return pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return _now()


def _append(rec: dict) -> None:
    rec = dict(rec)
    rec["CreatedAt"] = _dt(rec.get("CreatedAt"))
    rec["UpdatedAt"] = _now()
    vals = []
    for c in _COLS:
        v = rec.get(c)
        if c in ("Confidence", "Value", "Threshold"):
            try:
                vals.append(f"real({float(v)})")
            except (TypeError, ValueError):
                vals.append("real(0)")
        elif c == "TeamsPosted":
            vals.append("true" if v else "false")
        elif c in ("CreatedAt", "UpdatedAt"):
            vals.append(f"datetime({rec[c]})")
        else:
            vals.append(f"'{_q('' if v is None else v)}'")
    kusto.command(f".set-or-append {TABLE} <| datatable({_SCHEMA})[{','.join(vals)}]")


def _to_dict(row) -> dict:
    d = {}
    for k, v in row.items():
        try:
            import pandas as pd
            if isinstance(v, pd.Timestamp):
                d[k] = pd.Timestamp(v).strftime("%Y-%m-%dT%H:%M:%SZ")
                continue
        except Exception:
            pass
        d[k] = v.item() if hasattr(v, "item") else v
    return d


def get(iid: str) -> dict | None:
    ensure_table()
    df = kusto.query_safe(f"{TABLE} | where IncidentId == '{_q(iid)}' | summarize arg_max(UpdatedAt, *) by IncidentId")
    if df is None or df.empty:
        return None
    return _to_dict(df.iloc[0])


def list_active() -> list:
    ensure_table()
    active = ",".join(f"'{s}'" for s in ACTIVE)
    df = kusto.query_safe(f"{TABLE} | summarize arg_max(UpdatedAt, *) by IncidentId "
                          f"| where Status in ({active}) | order by UpdatedAt desc")
    if df is None or df.empty:
        return []
    return [_to_dict(r) for _, r in df.iterrows()]


def create_or_get(service: str, severity: str = "Sev3", metric: str = "",
                  value: float = 0, threshold: float = 0, description: str = "") -> dict:
    """Return the active incident for a service, or create a new OPEN one (dedup by service)."""
    ensure_table()
    iid = make_id(service)
    cur = get(iid)
    if cur and cur.get("Status") in ACTIVE:
        return cur
    rec = {"IncidentId": iid, "Service": service, "Severity": severity, "Status": "OPEN",
           "RootCauseService": "", "RootCauseVersion": "", "Confidence": 0, "GateDecision": "",
           "TeamsPosted": False, "ApprovalStatus": "none", "RemediationStatus": "none",
           "VerifierStatus": "none", "TraceId": "", "Metric": metric, "Value": value,
           "Threshold": threshold, "Description": description, "IdempotencyKeys": "", "CreatedAt": _now()}
    _append(rec)
    return rec


def transition(iid: str, status: str | None = None, **fields) -> dict:
    """Append a new state row for the incident (merging over current state). Returns the merged record."""
    cur = get(iid) or {"IncidentId": iid, "CreatedAt": _now()}
    merged = {**cur, **fields}
    if status:
        merged["Status"] = status
    _append(merged)
    return merged


def mark_action(iid: str, key: str) -> bool:
    """Idempotency: record `key` for the incident. Returns True if newly recorded, False if already seen."""
    cur = get(iid) or {}
    seen = str(cur.get("IdempotencyKeys", "") or "").split(",")
    if key in seen:
        return False
    seen = [s for s in seen if s] + [key]
    transition(iid, IdempotencyKeys=",".join(seen))
    return True
