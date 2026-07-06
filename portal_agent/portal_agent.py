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
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "functions"))
try:
    from shared.settings import (AZURE_SUBSCRIPTION_ID, AZURE_TENANT_ID, AZURE_RESOURCE_GROUP,
                                 PORTAL_USER_DATA_DIR, AZURE_APPINSIGHTS_NAME,
                                 PORTAL_AGENT_NOVNC, PORTAL_CDP_URL)
    from shared.services import canonical, REAL_SERVICES     # one source of truth for names
except Exception:  # allow standalone use
    AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
    AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
    AZURE_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "rg-cre-copilot")
    PORTAL_USER_DATA_DIR = os.environ.get("PORTAL_USER_DATA_DIR", os.path.expanduser("~/.cre-portal-profile"))
    AZURE_APPINSIGHTS_NAME = os.environ.get("AZURE_APPINSIGHTS_NAME", "")
    PORTAL_AGENT_NOVNC = os.environ.get("PORTAL_AGENT_NOVNC", "false").lower() == "true"
    PORTAL_CDP_URL = os.environ.get("PORTAL_CDP_URL", "http://localhost:9222")
    REAL_SERVICES = {"checkout-api", "payment-service", "inventory-service", "auth-service"}
    def canonical(n): return {"auth": "auth-service", "inventory": "inventory-service"}.get((n or "").strip(), n)

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
    """Contextual plan (url, label, dwell_ms) by sandbox event. First the event-specific page,
    then LOG STREAM as the resting page (where we hold to watch live updates)."""
    dl = lambda b="": _deeplink(app, b)
    ls = dl("logstream")
    first = {
        "bad_deploy": (dl("revisionManagement"), "Revisions"),
        "kill":       (dl(), "Container App overview"),
        "errors":     (ls, "Log stream (live)"),     # real live logs — App Insights isn't instrumented (would be empty)
        "traffic":    (dl("metrics"), "Metrics"),
    }.get(event, (dl(), "Overview"))
    return [(first[0], first[1], 5000), (ls, "Log stream (live)", 8000)]


# Stage → blade the Portal Agent shows, so it MIRRORS what each agent is doing on the SAME
# target. blade=None means "don't navigate" (Gate just evaluates confidence, stay on the page).
# Clean stage script → one blade per stage. blade=None means DO NOT navigate (timeline only).
# The story reads: Overview → Metrics → Revisions → Log Stream → Verify (no jitter).
_STAGE_BLADE = {
    "commander":   ("",                   "Overview"),                 # open the resource once
    "detector":    ("metrics",            "Metrics"),                  # first detect() → Metrics once
    "correlator":  ("revisionManagement", "Revisions / deployment"),  # Revisions once
    "switch":      ("logstream",          "Log stream (root cause)"),  # root cause changed → new service
    "impact":      ("logstream",          "Log stream (blast radius)"),# Log Stream once
    "gate":        (None,                 "Confidence decision"),      # do NOT navigate — timeline only
    "runbook":     ("logstream",          "Log stream (runbook)"),     # stay stable on Log Stream
    "verifier":    ("",                   "Overview (recovery)"),      # refresh Overview once
    "verifier_ls": ("logstream",          "Log stream (recovery)"),    # then Log Stream once
}


class PortalSession:
    """A long-lived, headed, READ-ONLY portal session that reacts to investigation stages.
    Owns Playwright on ONE dedicated thread (thread-safe); receives stage commands via a queue;
    stays open for the whole investigation and lingers briefly before closing."""

    IDLE_TIMEOUT = 300   # close if no stage command for 5 min (safety)

    LOG_STREAM_MIN_SEC = 60   # hold on Log Stream at least this long (watch live updates)
    POST_COMPLETE_SEC = 15    # keep the browser open this long after the investigation completes

    def __init__(self, event: str, service: str):
        self.event = (event or "generic").lower()
        self.service = service
        app = canonical(service)                              # e.g. 'auth' → 'auth-service'
        self.app = app if app in REAL_SERVICES else "checkout-api"   # frontend/unknown → a real CA to display
        self.plan = _plan_for(self.event, self.app)
        self.idx = 0
        self.done = False
        self._ls_url = _deeplink(self.app, "logstream")
        self._reached_ls = False
        self._ls_since = None
        self._cur_blade = None   # the blade currently shown (for de-dup — no re-nav for animation)
        self._blade_ts = {}      # blade → last navigation time (30s cooldown, no re-nav within window)
        self._novnc = False   # set true if we connect to the container's Chromium over CDP
        self._cmd = queue.Queue()
        self._ev = queue.Queue()
        threading.Thread(target=self._run, daemon=True).start()

    # --- public API (called from the web thread) ---
    def advance(self, stage: str, target: str | None = None):
        # target = the service the CURRENT stage is analyzing → the Portal Agent mirrors THAT
        # service (never independently picks a different one).
        self._cmd.put(("nav", (stage, target)))

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

    def _retarget(self, target: str | None):
        """Point the session at the service the CURRENT stage is analyzing (canonicalized).
        Only ever moves to a REAL Container App — never independently invents a target."""
        if not target:
            return
        app = canonical(target)
        if app in REAL_SERVICES and app != self.app:
            self.app = app
            self._ls_url = _deeplink(self.app, "logstream")
            self._reached_ls = False        # a fresh service → the log-stream watch resets
            self._ls_since = None
            self._cur_blade = None           # force a nav on the new service
            self._blade_ts = {}              # reset the per-blade cooldown for the new service

    def _next_step(self, stage: str):
        """Clean stage script: one blade per stage, no jitter. Returns (url, label, dwell).
        label=None means SILENT no-navigation (the browser stays stable; no timeline spam):
          * Gate (blade None) never navigates.
          * If already on the blade, or we navigated to it within 30s, we don't reload.
        switch (real root-cause change) and the Verifier refresh always navigate."""
        key = (stage or "").strip().lower()
        blade, label = _STAGE_BLADE.get(key, ("logstream", "Log stream (live)"))
        if blade is None:                    # Gate etc. — never navigate; the timeline explains it
            return (None, None, 0)
        force = key in ("switch", "verifier", "verifier_ls")   # real transitions always refresh
        recent = (time.time() - self._blade_ts.get(blade, 0)) < 30
        if (blade == self._cur_blade or recent) and not force:
            return (None, None, 0)           # already shown / within 30s → keep the browser stable
        self._cur_blade = blade
        self._blade_ts[blade] = time.time()
        return (_deeplink(self.app, blade), f"{label} · {self.app}", 2500)   # ≤3s so the browser keeps pace

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
            use_novnc = PORTAL_AGENT_NOVNC
            if use_novnc:
                # EMBEDDED mode: drive the container's Chromium over CDP (streamed via noVNC).
                try:
                    browser = pw.chromium.connect_over_cdp(PORTAL_CDP_URL, timeout=8000)
                    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                    page = ctx.pages[0] if ctx.pages else ctx.new_page()
                    self._novnc = True
                    _log("portal.novnc_connected", cdp=PORTAL_CDP_URL)
                except Exception as e:
                    _log("portal.novnc_connect_failed", reason=str(e)[:140])
                    self._emit("portal_evidence", message="Portal Agent: noVNC container not reachable — falling back to external browser")
                    use_novnc = False
            if not use_novnc:
                # FALLBACK / default: external headed Chromium on the host, right side of the screen.
                win_pos = os.environ.get("PORTAL_WINDOW_POS", "1000,0")
                win_size = os.environ.get("PORTAL_WINDOW_SIZE", "1000,1300")
                ctx = pw.chromium.launch_persistent_context(
                    PORTAL_USER_DATA_DIR, headless=False, slow_mo=300, viewport=None,
                    args=[f"--window-position={win_pos}", f"--window-size={win_size}"])
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
            _log("portal.browser_launched", mode=("novnc" if self._novnc else "external"), app=self.app)
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
                    # keep watching Log Stream for at least LOG_STREAM_MIN_SEC total
                    if self._reached_ls and self._ls_since:
                        remain = self.LOG_STREAM_MIN_SEC - (time.time() - self._ls_since)
                        if remain > 0:
                            self._emit("portal_evidence", message=f"Portal Agent holding on Log stream ~{int(remain)}s more (live)")
                            page.wait_for_timeout(int(remain * 1000))
                    self._emit("portal_status", status="Evidence captured")
                    self._emit("portal_evidence", message=f"Portal Agent captured portal evidence for {self.app}")
                    page.wait_for_timeout(self.POST_COMPLETE_SEC * 1000)   # stay open 15s after completion
                    self._emit("portal_status", status="Complete")
                    break
                if cmd == "nav":
                    # CATCH UP: if newer stages are already queued, jump to the LATEST nav so the
                    # browser never lags behind the investigation (skip the intermediate pages).
                    while not self._cmd.empty():
                        try:
                            c2, a2 = self._cmd.get_nowait()
                        except queue.Empty:
                            break
                        if c2 == "nav":
                            arg = a2                    # a newer stage supersedes this one
                        else:                           # 'stop' — put it back, handle after this nav
                            self._cmd.put((c2, a2))
                            break
                    stage, target = arg if isinstance(arg, tuple) else (arg, None)
                    self._retarget(target)              # mirror the current stage's target
                    url, label, dwell = self._next_step(stage)
                    if label is None:
                        continue                        # silent no-nav → browser stays stable, no spam
                    self._emit("portal_status", status="Inspecting")
                    try:
                        if url:
                            # wait_until="commit" returns as soon as navigation starts — we DON'T block
                            # on the portal's full (slow) load, so the browser keeps pace with the agents.
                            page.goto(url, wait_until="commit", timeout=12000)
                            if url == self._ls_url and not self._reached_ls:
                                self._reached_ls = True
                                self._ls_since = time.time()   # start the Log Stream watch clock
                        page.wait_for_timeout(dwell)   # brief pause so the page is observable (≤3s)
                        self._emit("portal_evidence", message=f"Portal Agent → {label} ({self.app})")
                    except Exception as e:
                        self._emit("portal_evidence", message=f"Portal Agent skipped {label} ({str(e)[:50]})")
        except Exception as e:
            _log("portal.browser_skipped", reason=str(e)[:160])
            self._emit("portal_status", status="Failed")
            self._emit("portal_evidence", message=f"Portal Agent failed: {str(e)[:120]} — continuing normal flow")
        finally:
            # In noVNC mode DON'T close the container's Chromium (keep the profile/login + the
            # embedded view alive for the next run) — only external browsers are closed here.
            if not self._novnc:
                try:
                    if ctx:
                        ctx.close()
                except Exception:
                    pass
            try:
                if pw:
                    pw.stop()      # disconnects the CDP session; container browser stays up
            except Exception:
                pass
            self.done = True
            _log("portal.browser_exited", app=self.app, mode=("novnc" if self._novnc else "external"))
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
    app = canonical(service) if canonical(service) in REAL_SERVICES else "checkout-api"
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
