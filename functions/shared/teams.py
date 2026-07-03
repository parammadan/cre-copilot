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


def post_card(webhook_url: str, payload: dict) -> dict:
    """POST the card to the Teams Incoming Webhook (dry-run if no URL configured)."""
    if not webhook_url:
        return {"posted": False, "reason": "TEAMS_WEBHOOK_URL not set", "preview": payload}
    r = requests.post(webhook_url, json=payload, timeout=10)
    return {"posted": r.status_code in (200, 202), "status": r.status_code}
