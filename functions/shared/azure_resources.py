"""READ-ONLY live Azure Container Apps investigation, via the `az` CLI.

The agents use this to pull REAL resource state during an incident — running revision,
replica count, CPU/memory limits, restart signals, and recent console/system logs — so a
diagnosis cites what Azure actually reports, not just telemetry.

Hard guarantees:
  * READ-ONLY. Only `az containerapp show | revision list | logs show` are ever run — no
    create/update/delete/restart/scale. No write path exists in this module by design.
  * GRACEFUL. If the `az` CLI is missing, not logged in, times out, or the service isn't a
    real Container App, every function returns {"available": false, "note": ...} instead of
    raising — a cloud dependency must never crash an investigation.
  * Names resolve through shared/services.py (canonical) — 'auth' → 'auth-service' etc.
"""
from __future__ import annotations

import json
import shutil
import subprocess

from shared import settings
from shared.services import canonical, is_real

_SHOW_TIMEOUT = 20      # `show` / `revision list` are quick
_LOGS_TIMEOUT = 35      # `logs show` has to connect to the log stream first


def _unavailable(service: str, note: str) -> dict:
    return {"available": False, "service": service, "note": note}


def _resolve(service: str):
    """(canonical_name, error_dict|None). Only real Container Apps are investigable."""
    svc = canonical(service)
    if not is_real(svc):
        return svc, _unavailable(svc, "not a real Azure Container App (telemetry-only) — rely on ADX evidence")
    return svc, None


def _az(args: list[str], timeout: int):
    """Run a read-only `az` command → (ok, parsed_json_or_text, error_str).
    Never raises: missing CLI / auth / timeout all come back as ok=False with a note."""
    if not shutil.which("az"):
        return False, None, "azure CLI (az) not installed"
    cmd = ["az", *args, "--subscription", settings.AZURE_SUBSCRIPTION_ID,
           "--only-show-errors", "-o", "json"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, None, f"az timed out after {timeout}s"
    except Exception as e:  # pragma: no cover - defensive
        return False, None, str(e)[:160]
    if p.returncode != 0:
        err = (p.stderr or "").strip().splitlines()
        msg = err[-1] if err else f"az exited {p.returncode}"
        low = msg.lower()
        if "az login" in low or "credential" in low or "not logged in" in low:
            msg = "az not logged in (run `az login`) — using telemetry evidence instead"
        return False, None, msg[:200]
    try:
        return True, json.loads(p.stdout or "null"), None
    except json.JSONDecodeError:
        return True, (p.stdout or "").strip(), None


def _ndjson(text) -> list[dict]:
    """`az containerapp logs show -o json` emits newline-delimited objects, not an array."""
    if isinstance(text, list):
        return text
    out = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"Log": line})
    return out


# ---------------------------------------------------------------- public tools

def get_container_app_status(service: str) -> dict:
    """Live status: provisioning/running state, active revision, replica count, CPU/mem, health."""
    svc, err = _resolve(service)
    if err:
        return err
    ok, data, e = _az(["containerapp", "show", "-g", settings.AZURE_RESOURCE_GROUP, "-n", svc], _SHOW_TIMEOUT)
    if not ok:
        return _unavailable(svc, e)
    props = (data or {}).get("properties", {}) or {}
    tmpl = props.get("template", {}) or {}
    containers = tmpl.get("containers") or [{}]
    res = (containers[0] or {}).get("resources", {}) or {}
    scale = tmpl.get("scale", {}) or {}
    return {
        "available": True,
        "service": svc,
        "provisioningState": props.get("provisioningState"),
        "runningStatus": props.get("runningStatus"),
        "activeRevision": props.get("latestRevisionName"),
        "cpu": res.get("cpu"),
        "memory": res.get("memory"),
        "minReplicas": scale.get("minReplicas"),
        "maxReplicas": scale.get("maxReplicas"),
    }


def get_container_app_revisions(service: str) -> dict:
    """Revision history: name, active flag, traffic %, replica count, health, created time."""
    svc, err = _resolve(service)
    if err:
        return err
    ok, data, e = _az(["containerapp", "revision", "list", "-g", settings.AZURE_RESOURCE_GROUP, "-n", svc], _SHOW_TIMEOUT)
    if not ok:
        return _unavailable(svc, e)
    revs = []
    for r in (data or []):
        p = r.get("properties", {}) or {}
        revs.append({
            "name": r.get("name"),
            "active": p.get("active"),
            "trafficWeight": p.get("trafficWeight"),
            "replicas": p.get("replicas"),
            "health": p.get("healthState"),
            "created": p.get("createdTime"),
        })
    revs.sort(key=lambda x: x.get("created") or "", reverse=True)
    return {"available": True, "service": svc, "count": len(revs), "revisions": revs[:6]}


def get_container_app_resource_limits(service: str) -> dict:
    """CPU/memory limits per container + the scale (min/max replica) bounds."""
    svc, err = _resolve(service)
    if err:
        return err
    st = get_container_app_status(svc)
    if not st.get("available"):
        return st
    return {"available": True, "service": svc, "cpu": st.get("cpu"), "memory": st.get("memory"),
            "minReplicas": st.get("minReplicas"), "maxReplicas": st.get("maxReplicas")}


def get_container_app_logs(service: str, tail: int = 20) -> dict:
    """Recent CONSOLE logs (the app's stdout/stderr) from the active replica."""
    svc, err = _resolve(service)
    if err:
        return err
    ok, data, e = _az(["containerapp", "logs", "show", "-g", settings.AZURE_RESOURCE_GROUP,
                       "-n", svc, "--type", "console", "--tail", str(int(tail))], _LOGS_TIMEOUT)
    if not ok:
        return _unavailable(svc, e)
    rows = _ndjson(data)
    lines = [{"time": r.get("TimeStamp"), "log": r.get("Log")} for r in rows if r.get("Log")]
    return {"available": True, "service": svc, "type": "console", "count": len(lines), "logs": lines[-tail:]}


def get_container_app_system_logs(service: str, tail: int = 20) -> dict:
    """Recent SYSTEM logs (platform events: scaling, health, restarts) + a restart-signal count."""
    svc, err = _resolve(service)
    if err:
        return err
    ok, data, e = _az(["containerapp", "logs", "show", "-g", settings.AZURE_RESOURCE_GROUP,
                       "-n", svc, "--type", "system", "--tail", str(int(tail))], _LOGS_TIMEOUT)
    if not ok:
        return _unavailable(svc, e)
    rows = _ndjson(data)
    events = [{"time": r.get("TimeStamp"), "type": r.get("Type"),
               "reason": r.get("Reason"), "msg": r.get("Msg")} for r in rows if r.get("Msg")]
    # Container Apps doesn't expose a numeric restart count; approximate it from platform events.
    restart_reasons = ("Killing", "BackOff", "Unhealthy", "Restart", "Recreat", "Fail")
    restarts = sum(1 for ev in events if any(k in str(ev.get("reason") or "") for k in restart_reasons))
    return {"available": True, "service": svc, "type": "system", "count": len(events),
            "restartSignals": restarts, "events": events[-tail:]}


if __name__ == "__main__":  # quick manual check: python -m shared.azure_resources auth
    import sys
    s = sys.argv[1] if len(sys.argv) > 1 else "auth-service"
    for fn in (get_container_app_status, get_container_app_revisions,
               get_container_app_resource_limits, get_container_app_system_logs, get_container_app_logs):
        print(f"\n== {fn.__name__}({s}) ==")
        print(json.dumps(fn(s), indent=2)[:1200])
