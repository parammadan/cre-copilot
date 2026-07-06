"""Microsoft Teams integration — post an Adaptive Card for an incident, with
Approve / Escalate actions. Outbound uses a Teams Incoming Webhook. The Approve
button is an Action.OpenUrl to the backend's /api/teams/approve (works when the
backend is publicly reachable — App Service or an az devtunnel; localhost can't
receive Teams callbacks)."""
import requests

try:
    from shared.settings import ACT_THRESHOLD
except Exception:
    ACT_THRESHOLD = 0.70


def build_incident_card(inc: dict, approve_url: str, console_url: str) -> dict:
    """Adaptive Card wrapped for a Teams Incoming Webhook."""
    rc = inc.get("rootCause", {})
    conf = float(rc.get("confidence", 0))
    auto = conf >= ACT_THRESHOLD
    sev = inc.get("severity", "Sev?")
    worst = ""
    deg = [x for x in inc.get("impact", []) if x.get("LatencyIncrease", 0) >= 1.3]
    if deg:
        w = max(deg, key=lambda x: x["LatencyIncrease"])
        worst = f"{w['AffectedService']} {w['LatencyIncrease']:.1f}x"
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": [
            {"type": "TextBlock", "size": "Large", "weight": "Bolder",
             "text": f"🚨 Incident — {inc.get('alertService','?')} ({sev})",
             "color": "Attention" if auto else "Warning"},
            {"type": "FactSet", "facts": [
                {"title": "Root cause", "value": f"{rc.get('service','?')} {rc.get('version','')}"},
                {"title": "Confidence", "value": f"{conf:.2f}  ({'≥' if auto else '<'} {ACT_THRESHOLD:.2f} gate)"},
                {"title": "Decision", "value": "🤖 auto-remediate" if auto else "🧑 escalate to human"},
                {"title": "Blast radius", "value": worst or "no downstream degraded"},
            ]},
            {"type": "TextBlock", "wrap": True, "isSubtle": True,
             "text": f"Recommended: rollback {rc.get('service','?')} to previous release." if auto
                     else "Confidence below gate — human review recommended."},
        ],
        "actions": [
            {"type": "Action.OpenUrl", "title": "✅ Approve & Remediate",
             "url": f"{approve_url}?service={rc.get('service','')}"},
            {"type": "Action.OpenUrl", "title": "🔎 Open Console", "url": console_url},
        ],
    }
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def build_escalation_card(inc: dict, incident_id: str, reason: str, evidence: list,
                          approve_url: str, reject_url: str, console_url: str) -> dict:
    """Auto-escalation card posted by the BACKEND when the deterministic gate escalates.
    Includes incident id, service, severity, root-cause candidate, confidence, reason, top
    evidence, and Approve / Reject actions."""
    rc = inc.get("rootCause", {})
    conf = float(rc.get("confidence", 0))
    sev = inc.get("severity", "Sev?")
    body = [
        {"type": "TextBlock", "size": "Large", "weight": "Bolder", "color": "Warning",
         "text": f"🧑 Escalation — {inc.get('alertService', '?')} ({sev})"},
        {"type": "TextBlock", "wrap": True, "isSubtle": True, "text": reason},
        {"type": "FactSet", "facts": [
            {"title": "Incident", "value": incident_id},
            {"title": "Affected service", "value": inc.get("alertService", "?")},
            {"title": "Severity", "value": sev},
            {"title": "Root cause candidate", "value": f"{rc.get('service', '?')} {rc.get('version', '')}"},
            {"title": "Confidence", "value": f"{conf:.2f}  (gate {ACT_THRESHOLD:.2f})"},
        ]},
    ]
    if evidence:
        body.append({"type": "TextBlock", "weight": "Bolder", "text": "Top evidence", "spacing": "Medium"})
        for e in evidence[:4]:
            body.append({"type": "TextBlock", "wrap": True, "size": "Small", "spacing": "None", "text": f"• {e}"})
    card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
        "body": body,
        "actions": [
            {"type": "Action.OpenUrl", "title": "✅ Approve remediation", "url": approve_url},
            {"type": "Action.OpenUrl", "title": "🔎 Reject / keep investigating", "url": reject_url},
            {"type": "Action.OpenUrl", "title": "Open Console", "url": console_url},
        ],
    }
    return {"type": "message",
            "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}


def post_card(webhook_url: str, payload: dict) -> dict:
    """POST the card to the Teams Incoming Webhook (dry-run if no URL configured)."""
    if not webhook_url:
        return {"posted": False, "reason": "TEAMS_WEBHOOK_URL not set", "preview": payload}
    # DATA EXFILTRATION GUARD — deny-by-default egress allowlist (defense in depth).
    from shared import security
    eg = security.egress_allowed(webhook_url)
    if not eg["allowed"]:
        from shared import obs
        obs.log("security.egress_blocked", host=eg["host"], reason=eg["reason"])
        return {"posted": False, "reason": f"egress blocked by allowlist: {eg['host']}"}
    r = requests.post(webhook_url, json=payload, timeout=10)
    return {"posted": r.status_code in (200, 202), "status": r.status_code}
