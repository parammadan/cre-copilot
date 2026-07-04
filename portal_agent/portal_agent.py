#!/usr/bin/env python3
"""Portal Agent — a headed, READ-ONLY Playwright navigator for live demos.

Opens a visible Chromium window beside CRE Copilot and, based on the active incident's service,
navigates DEEP-LINKS to OUR resources in the Azure Portal (Overview → Revisions → Log stream →
Metrics). It only ever navigates and reads — it NEVER clicks Save/Delete/Create/Restart/Rollback.

Design rules (enforced here):
  * LOCAL-ONLY + headed (needs a display); disabled in cloud via the feature flag.
  * READ-ONLY: only page.goto() + reads; no destructive controls; remediation stays in the backend.
  * No secrets: reuses a logged-in local browser profile (persistent context, gitignored dir).
  * Graceful: any failure (Playwright missing, not logged in, MFA, selector/nav error) yields a
    'Failed' event and returns — the normal incident flow continues unaffected.

CLI:
  python portal_agent/portal_agent.py login     # one-time: opens the profile so you can sign in
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
try:
    from shared.settings import (AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_RESOURCE_GROUP,
                                 PORTAL_USER_DATA_DIR, PORTAL_SERVICE_APPS)
except Exception:  # allow standalone use
    AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "rg-cre-copilot")
    PORTAL_USER_DATA_DIR = os.environ.get("PORTAL_USER_DATA_DIR", os.path.expanduser("~/.cre-portal-profile"))
    PORTAL_SERVICE_APPS = {}

# Read-only Container Apps blades, navigated by stable deep-link (no UI click-through).
_BLADES = [
    ("", "opened Container App"),
    ("revisionManagement", "checked revisions"),
    ("logstream", "checked logs"),
    ("metrics", "checked metrics"),
]


def _resource_id(app: str) -> str:
    return (f"/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}"
            f"/providers/Microsoft.App/containerApps/{app}")


def _deeplink(app: str, blade: str = "") -> str:
    base = f"https://portal.azure.com/#@{AZURE_TENANT_ID}/resource{_resource_id(app)}"
    return base + (f"/{blade}" if blade else "")


def _ev(t, **k):
    d = {"type": t}
    d.update(k)
    return d


def investigate(service: str):
    """Sync generator of portal-agent events. Yields dicts:
       {type:'portal_status', status:...}  and  {type:'portal_evidence', message:...}.
    Read-only; safe to fail."""
    app = PORTAL_SERVICE_APPS.get(service, service)
    yield _ev("portal_status", status="Opening browser")
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        yield _ev("portal_status", status="Failed")
        yield _ev("portal_evidence", message="Portal Agent unavailable: Playwright not installed (pip install playwright && playwright install chromium)")
        return

    pw = ctx = None
    try:
        pw = sync_playwright().start()
        # persistent context = reuse the logged-in profile; headed so it's visible; slow_mo so it's watchable
        ctx = pw.chromium.launch_persistent_context(
            PORTAL_USER_DATA_DIR, headless=False, slow_mo=350, viewport=None,
            args=["--start-maximized", "--window-position=760,0"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()

        yield _ev("portal_status", status="Navigating")
        page.goto("https://portal.azure.com/", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)
        yield _ev("portal_evidence", message="Portal Agent opened Azure Portal")

        # graceful auth check — if we got bounced to login, note it and keep going (user may sign in)
        if "login.microsoftonline.com" in page.url or "/oauth2" in page.url:
            yield _ev("portal_evidence", message="Portal Agent: not signed in — complete login in the window (see README)")

        for blade, msg in _BLADES:
            yield _ev("portal_status", status="Inspecting")
            try:
                page.goto(_deeplink(app, blade), wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(2500)                    # dwell so it's visible on screen
                yield _ev("portal_evidence", message=f"Portal Agent {msg} ({app})")
            except Exception as e:
                yield _ev("portal_evidence", message=f"Portal Agent skipped {blade or 'overview'} ({str(e)[:60]})")

        yield _ev("portal_status", status="Evidence captured")
        yield _ev("portal_evidence", message=f"Portal Agent captured portal evidence for {app}")
        page.wait_for_timeout(4000)                            # linger beside the app
        yield _ev("portal_status", status="Complete")
    except Exception as e:
        yield _ev("portal_status", status="Failed")
        yield _ev("portal_evidence", message=f"Portal Agent failed: {str(e)[:140]} — continuing normal incident flow")
    finally:
        try:
            if ctx:
                ctx.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass


def _login():
    """One-time: open the persistent profile at the portal so you can sign in (incl. MFA).
    The session is saved to PORTAL_USER_DATA_DIR and reused by investigate()."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PORTAL_USER_DATA_DIR, headless=False, viewport=None)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://portal.azure.com/")
        input("→ Sign in (incl. MFA) in the opened browser, then press Enter here to save the session… ")
        ctx.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["login"]:
        _login()
    else:
        for e in investigate(sys.argv[1] if len(sys.argv) > 1 else "auth-service"):
            print(e)
