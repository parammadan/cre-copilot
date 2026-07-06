"""Permission gate + guardrails — the PRE-EXECUTION safety layer for self-healing.

Every remediation (human-approved OR autonomous) is evaluated here BEFORE it runs. This mirrors
the Azure SRE Agent permission-gate model: allow / require-human-approval / block, with the
decision audited. It is DETERMINISTIC (no LLM) and cannot be bypassed by the model — the model
has no remediation tool at all; this gate governs the one real write path.

Guardrails:
  * kill switch  — an emergency stop that blocks ALL remediation, everywhere, immediately.
  * rate limit   — no more than N remediations per hour.
  * concurrency  — at most N remediations in flight at once.
  * blast radius — an AUTONOMOUS heal of a tier-0 service is downgraded to human approval.

Nothing here performs or fakes an action; it only decides whether one may proceed, and records
that a real one happened (for the rate/concurrency windows).
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass

from shared import settings
from shared.services import canonical

_recent: deque[float] = deque(maxlen=500)   # timestamps of APPLIED remediations (rate window)
_active: set[str] = set()                    # services with an in-flight remediation
_kill_switch: bool = settings.SELF_HEAL_KILL_SWITCH   # runtime-togglable (starts from settings)


@dataclass
class Decision:
    verdict: str          # "allow" | "require_approval" | "block"
    allow: bool           # True only when verdict == "allow"
    reasons: list
    policy: dict


def set_kill_switch(on: bool) -> bool:
    """Engage/release the emergency stop (demo/operator control). Returns the new state."""
    global _kill_switch
    _kill_switch = bool(on)
    return _kill_switch


def kill_switch_on() -> bool:
    return _kill_switch


def _rate_count(window_sec: int = 3600) -> int:
    cut = time.time() - window_sec
    while _recent and _recent[0] < cut:
        _recent.popleft()
    return len(_recent)


def policy() -> dict:
    return {
        "kill_switch": _kill_switch,
        "max_per_hour": settings.SELF_HEAL_MAX_PER_HOUR,
        "used_this_hour": _rate_count(),
        "max_concurrent": settings.SELF_HEAL_MAX_CONCURRENT,
        "active": sorted(_active),
        "tier0": sorted(settings.TIER0_SERVICES),
    }


def evaluate(service: str, autonomous: bool = False) -> Decision:
    """Decide whether a remediation of `service` may proceed. `autonomous`=True applies the
    stricter blast-radius rule (tier-0 → human). Order matters: hard stops first."""
    svc = canonical(service)
    p = policy()
    if _kill_switch:
        return Decision("block", False, ["kill switch engaged — all self-healing halted"], p)
    if _rate_count() >= settings.SELF_HEAL_MAX_PER_HOUR:
        return Decision("block", False,
                        [f"rate limit reached ({settings.SELF_HEAL_MAX_PER_HOUR}/hour) — cooling down"], p)
    if len(_active) >= settings.SELF_HEAL_MAX_CONCURRENT and svc not in _active:
        return Decision("block", False,
                        [f"another remediation in progress (max {settings.SELF_HEAL_MAX_CONCURRENT} concurrent)"], p)
    if autonomous and svc in settings.TIER0_SERVICES:
        return Decision("require_approval", False,
                        [f"{svc} is a tier-0 service — autonomous heal downgraded to human approval"], p)
    return Decision("allow", True, ["within policy" + (" (human-approved)" if not autonomous else " (autonomous)")], p)


def begin(service: str) -> None:
    """Mark a remediation in flight (concurrency guard)."""
    _active.add(canonical(service))


def record(service: str) -> None:
    """Record that a real remediation was APPLIED (feeds the rate window) and clear it as active."""
    _recent.append(time.time())
    _active.discard(canonical(service))
