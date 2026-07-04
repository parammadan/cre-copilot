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
import queue
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
try:
    from shared.settings import (AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_RESOURCE_GROUP,
                                 PORTAL_USER_DATA_DIR, PORTAL_SERVICE_APPS, AZURE_APPINSIGHTS_NAME)
except Exception:  # allow standalone use
    AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "rg-cre-copilot")
    PORTAL_USER_DATA_DIR = os.environ.get("PORTAL_USER_DATA_DIR", os.path.expanduser("~/.cre-portal-profile"))
    PORTAL_SERVICE_APPS = {}
    AZURE_APPINSIGHTS_NAME = os.environ.get("AZURE_APPINSIGHTS_NAME", "")

try:
    from shared import obs as _obs
except Exception:
    _obs = None


def _log(name, **k):
    """Structured log to stdout (server logs) so browser launch/skip is always diagnosable."""
    if _obs:
        _obs.log(name, **k)
    else:
        print(f"[portal] {name} {k}", flush=True)

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


def _ai_link(blade: str) -> str:
    """App Insights blade deep-link (Failures/Performance). Falls back to the Container App log
    stream if App Insights isn't configured — graceful, still relevant to errors."""
    if not AZURE_APPINSIGHTS_NAME:
        return ""
    rid = (f"/subscriptions/{AZURE_SUBSCRIPTION_ID}/resourceGroups/{AZURE_RESOURCE_GROUP}"
           f"/providers/microsoft.insights/components/{AZURE_APPINSIGHTS_NAME}")
    return f"https://portal.azure.com/#@{AZURE_TENANT_ID}/resource{rid}/{blade}"


def _plan_for(event: str, app: str) -> list:
    """Contextual navigation plan (url, label, dwell_ms) chosen by the sandbox event type.
    Each investigation stage advances one step; the browser stays open throughout."""
    dl = lambda b="": _deeplink(app, b)
    ai = _ai_link("failures") or dl("logstream")
    plans = {
        "bad_deploy": [(dl(), "Container App overview", 2500), (dl("revisionManagement"), "Revisions", 3000),
                       (dl("revisionManagement"), "Current revision", 3000), (dl("revisionManagement"), "Deployment history", 3000)],
        "kill":       [(dl(), "Container App overview", 2500), (dl(), "Health / status", 3000),
                       (dl("logstream"), "Log stream", 3500)],
        "errors":     [(ai, "App Insights · Failures", 3500), (ai, "Exceptions", 3000),
                       (dl("logstream"), "Log stream", 3000)],
        "traffic":    [(dl("metrics"), "Metrics", 3000), (dl("metrics"), "Live metrics", 3000),
                       (dl("metrics"), "Performance", 3000)],
    }
    return plans.get(event, [(dl(), "Overview", 2500), (dl("revisionManagement"), "Revisions", 3000),
                             (dl("logstream"), "Log stream", 3000), (dl("metrics"), "Metrics", 3000)])


class PortalSession:
    """A long-lived, headed, READ-ONLY portal session that reacts to investigation stages.
    Owns Playwright on ONE dedicated thread (thread-safe); receives stage commands via a queue;
    stays open for the whole investigation and lingers briefly before closing."""

    IDLE_TIMEOUT = 300   # close if no stage command for 5 min (safety)

    def __init__(self, event: str, service: str):
        self.event = (event or "generic").lower()
        self.service = service
        self.app = PORTAL_SERVICE_APPS.get(service, service)
        self.plan = _plan_for(self.event, self.app)
        self.idx = 0
        self.done = False
        self._cmd = queue.Queue()
        self._ev = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    # --- public API (called from the web thread) ---
    def advance(self, stage: str):
        self._cmd.put(("nav", stage))

    def stop(self):
        self._cmd.put(("stop", None))

    def drain(self):
        """Generator of events for SSE; ends on the None sentinel."""
        while True:
            ev = self._ev.get()
            if ev is None:
                break
            yield ev

    # --- internals (dedicated thread) ---
    def _emit(self, t, **k):
        d = {"type": t}
        d.update(k)
        self._ev.put(d)

    def _next_step(self, stage: str):
        s = (stage or "").lower()
        if s.startswith("verif"):                    # Verifier → always re-check Health
            return (_deeplink(self.app, ""), "refreshed Health", 3500)
        if s.startswith("impact"):                   # Impact → dependency/resource view
            return (_deeplink(self.app, ""), "resource / dependency view", 3000)
        if self.idx < len(self.plan):
            step = self.plan[self.idx]
            self.idx += 1
            return step
        return self.plan[-1] if self.plan else (_deeplink(self.app, ""), "Overview", 2500)

    def _run(self):
        self._emit("portal_status", status="Opening browser")
        _log("portal.browser_launching", sim_event=self.event, service=self.service, app=self.app)
        try:
            from playwright.sync_api import sync_playwright
        except Exception:
            _log("portal.browser_skipped", reason="playwright_not_installed")
            self._emit("portal_status", status="Failed")
            self._emit("portal_evidence", message="Portal Agent unavailable: Playwright not installed")
            self.done = True
            self._ev.put(None)
            return
        pw = ctx = None
        try:
            pw = sync_playwright().start()
            ctx = pw.chromium.launch_persistent_context(
                PORTAL_USER_DATA_DIR, headless=False, slow_mo=300, viewport=None,
                args=["--start-maximized", "--window-position=760,0"])
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _log("portal.browser_launched", app=self.app, profile=PORTAL_USER_DATA_DIR)
            self._emit("portal_status", status="Navigating")
            page.goto("https://portal.azure.com/", wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1200)
            if "login.microsoftonline.com" in (page.url or "") or "/oauth2" in (page.url or ""):
                self._emit("portal_evidence", message="Portal Agent: not signed in — run the login step (see README)")
            self._emit("portal_evidence", message=f"Portal Agent opened Azure Portal · focus: {self.event}")
            while True:
                try:
                    cmd, arg = self._cmd.get(timeout=self.IDLE_TIMEOUT)
                except queue.Empty:
                    break                             # idle timeout → close
                if cmd == "stop":
                    self._emit("portal_status", status="Evidence captured")
                    self._emit("portal_evidence", message=f"Portal Agent captured portal evidence for {self.app}")
                    page.wait_for_timeout(4000)       # linger so the last page is observable
                    self._emit("portal_status", status="Complete")
                    break
                if cmd == "nav":
                    url, label, dwell = self._next_step(arg)
                    self._emit("portal_status", status="Inspecting")
                    try:
                        if url:
                            page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(dwell)   # pause on the page so the user can observe it
                        self._emit("portal_evidence", message=f"Portal Agent → {label} ({self.app})")
                    except Exception as e:
                        self._emit("portal_evidence", message=f"Portal Agent skipped {label} ({str(e)[:50]})")
        except Exception as e:
            _log("portal.browser_skipped", reason=str(e)[:160])
            self._emit("portal_status", status="Failed")
            self._emit("portal_evidence", message=f"Portal Agent failed: {str(e)[:120]} — continuing normal flow")
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
            self.done = True
            _log("portal.browser_exited", app=self.app)
            self._ev.put(None)


_SESSION: PortalSession | None = None


def start_session(event: str, service: str) -> PortalSession:
    global _SESSION
    if _SESSION is not None and not _SESSION.done:
        try:
            _SESSION.stop()
        except Exception:
            pass
    _SESSION = PortalSession(event, service)
    _log("portal.session_created", sim_event=event, service=service)
    return _SESSION


def session() -> PortalSession | None:
    return _SESSION


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


def _logged_in(page) -> bool:
    u = page.url or ""
    return ("portal.azure.com" in u) and ("login.microsoftonline" not in u) and ("/oauth2" not in u)


def _login(max_wait_sec: int = 420):
    """One-time: open the persistent profile at the portal so you can sign in (incl. MFA).
    Polls until you're on the portal (no stdin needed, so it works when launched for you), then
    saves the session to PORTAL_USER_DATA_DIR (reused by investigate())."""
    from playwright.sync_api import sync_playwright
    print(f"[login] opening Azure Portal — sign in (incl. MFA) in the window; waiting up to {max_wait_sec}s…", flush=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(PORTAL_USER_DATA_DIR, headless=False, viewport=None,
                                                   args=["--start-maximized"])
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://portal.azure.com/")
        ok = 0
        for _ in range(max_wait_sec // 3):
            try:
                if _logged_in(page):
                    ok += 1
                    if ok >= 2:      # stable for two consecutive checks → logged in
                        page.wait_for_timeout(4000)   # let cookies flush to the profile
                        print("[login] signed in — session saved.", flush=True)
                        break
                else:
                    ok = 0
                page.wait_for_timeout(3000)
            except Exception:
                break
        ctx.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["login"]:
        _login()
    else:
        for e in investigate(sys.argv[1] if len(sys.argv) > 1 else "auth-service"):
            print(e)
