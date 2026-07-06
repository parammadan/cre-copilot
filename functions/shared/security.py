"""Enterprise AI-safety layer — deterministic, defense-in-depth controls.

Everything here is DETERMINISTIC (no LLM) and composable into the one real write path (remediation).
Follows Azure SRE principles: least privilege, deterministic authorization, defense in depth,
human-in-the-loop. Nothing fabricates capability — where a control needs infrastructure we don't
have locally (VNet, a signing KMS, live Azure RBAC reads), the code says so and the dashboard
marks it 'planned' rather than pretending.

Pieces:
  * PROMPT-INJECTION scanner  — flags instruction-override / role-injection / exfil verbs in
    UNTRUSTED evidence (logs, alerts, tickets) before it influences the model.
  * BLAST-RADIUS guard        — computes downstream impact from ServiceDependencies; caps it.
  * DETERMINISTIC POLICY ENGINE — declarative rules → allow / require_approval / block.
  * EGRESS allowlist          — outbound destination allowlist (VNet enforcement = planned).
  * APPROVAL integrity        — binds an approval to (incident_id + action hash). (signing = planned)
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from shared import settings

# --------------------------------------------------------------------------- prompt injection
# Deterministic signatures for content that tries to hijack the agent. Applied to UNTRUSTED text
# (log lines, alert descriptions, ticket bodies) — never to our own prompts.
_INJECTION_PATTERNS = [
    (r"ignore\s+(all\s+)?(previous|prior|above|earlier)\s+instructions", "instruction-override"),
    (r"disregard\s+(the\s+)?(previous|prior|above|system)", "instruction-override"),
    (r"you\s+are\s+now\s+", "role-injection"),
    (r"\bsystem\s*prompt\b|reveal\s+(your\s+)?(system\s+)?prompt", "prompt-exfiltration"),
    (r"<\|?(im_start|im_end|system|assistant)\|?>", "role-marker-injection"),
    (r"\b(exfiltrate|leak|send)\b.{0,30}\b(to|http)", "data-exfil-verb"),
    (r"(curl|wget|Invoke-WebRequest)\s+https?://", "outbound-command"),
    (r"BEGIN\s+(SYSTEM|INSTRUCTIONS)", "instruction-override"),
    (r"override\s+(the\s+)?(gate|policy|approval|guardrail)", "control-override"),
]
_COMPILED = [(re.compile(p, re.I | re.S), tag) for p, tag in _INJECTION_PATTERNS]


def scan_untrusted(text: str) -> dict:
    """Scan untrusted text for prompt-injection signatures. Returns
    {clean: bool, matches: [{tag, snippet}]}. Deterministic; no model involved."""
    s = str(text or "")
    hits = []
    for rx, tag in _COMPILED:
        m = rx.search(s)
        if m:
            i = max(0, m.start() - 15)
            hits.append({"tag": tag, "snippet": s[i:m.end() + 15][:80]})
    return {"clean": not hits, "matches": hits}


# --------------------------------------------------------------------------- blast radius
def blast_radius(service: str, deps_edges: list[tuple[str, str]] | None = None) -> dict:
    """Downstream services that depend (transitively) on `service`. `deps_edges` is a list of
    (Service, DependsOn) — pass the ServiceDependencies rows; if None, returns an empty radius
    (caller supplies data so this module needs no ADX import). Deterministic BFS."""
    from shared.services import canonical
    svc = canonical(service)
    edges = deps_edges or []
    # who depends on X → services S where (S, DependsOn=X)
    dependents: dict[str, list[str]] = {}
    for s, dep in edges:
        dependents.setdefault(dep, []).append(s)
    seen, frontier = set(), [svc]
    while frontier:
        cur = frontier.pop()
        for up in dependents.get(cur, []):
            if up not in seen:
                seen.add(up)
                frontier.append(up)
    affected = sorted(seen)
    tier0_hit = sorted(set([svc] + affected) & set(settings.TIER0_SERVICES))
    return {"service": svc, "affected": affected, "count": len(affected),
            "tier0_impacted": tier0_hit, "cap": settings.BLAST_RADIUS_CAP,
            "exceeds_cap": len(affected) > settings.BLAST_RADIUS_CAP}


# --------------------------------------------------------------------------- egress allowlist
# Outbound is allowed ONLY to these host suffixes (Azure data/control planes + the Teams webhook +
# localhost). VNet-level ENFORCEMENT is planned; this is the app-level deny-by-default check.
EGRESS_ALLOWLIST = [
    ".kusto.windows.net", ".openai.azure.com", ".cognitiveservices.azure.com",
    ".azurecontainerapps.io", ".vault.azure.net", ".applicationinsights.azure.com",
    "localhost", "127.0.0.1",
]


def egress_allowed(url: str) -> dict:
    """Deterministic outbound-destination check (deny by default). Teams webhook host is allowed
    if configured. Returns {allowed, host, reason}."""
    host = (urlparse(url).hostname or "").lower() if url else ""
    if not host:
        return {"allowed": False, "host": "", "reason": "no host"}
    allow = list(EGRESS_ALLOWLIST)
    wh = urlparse(settings.TEAMS_WEBHOOK_URL).hostname if settings.TEAMS_WEBHOOK_URL else ""
    if wh:
        allow.append(wh.lower())
    ok = any(host == a or host.endswith(a) for a in allow)
    return {"allowed": ok, "host": host, "reason": "allowlisted" if ok else "not on egress allowlist"}


# --------------------------------------------------------------------------- approval integrity
def action_hash(incident_id: str, service: str, version: str, action: str) -> str:
    """Bind an approval to a SPECIFIC action. SHA-256 over the tuple → an approval for one
    incident/action can't be replayed onto another. (Cryptographic signing + expiry = planned.)"""
    raw = f"{incident_id}|{service}|{version}|{action}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def verify_action_hash(incident_id: str, service: str, version: str, action: str, provided: str) -> bool:
    return bool(provided) and action_hash(incident_id, service, version, action) == provided


# --------------------------------------------------------------------------- deterministic policy engine
@dataclass
class PolicyResult:
    decision: str            # "allow" | "require_approval" | "block"
    rules: list = field(default_factory=list)   # [{rule, effect, detail}]

    @property
    def allow(self) -> bool:
        return self.decision == "allow"


def policy_rules() -> list[dict]:
    """The declarative policy, surfaced for the dashboard (single source of truth)."""
    return [
        {"rule": "kill_switch", "effect": "block", "detail": "emergency stop halts all remediation"},
        {"rule": "rate_limit", "effect": "block", "detail": f"max {settings.SELF_HEAL_MAX_PER_HOUR} remediations/hour"},
        {"rule": "concurrency", "effect": "block", "detail": f"max {settings.SELF_HEAL_MAX_CONCURRENT} concurrent"},
        {"rule": "blast_radius_cap", "effect": "require_approval", "detail": f"> {settings.BLAST_RADIUS_CAP} downstream → human"},
        {"rule": "tier0_autonomous", "effect": "require_approval", "detail": f"tier-0 {sorted(settings.TIER0_SERVICES)} autonomous → human"},
        {"rule": "llm_no_write", "effect": "block", "detail": "the model has no remediation tool; write path is code-only"},
    ]


def authorize(service: str, action: str, autonomous: bool, deps_edges=None) -> PolicyResult:
    """Compose the guardrails into ONE deterministic authorization decision. This is the policy
    engine's verdict; the caller still enforces human approval on 'require_approval'."""
    from shared import safety
    fired = []
    # hard blocks first (reuse the permission-gate guardrails)
    gate = safety.evaluate(service, autonomous=autonomous)
    if gate.verdict == "block":
        fired.append({"rule": "permission_gate", "effect": "block", "detail": "; ".join(gate.reasons)})
        return PolicyResult("block", fired)
    # blast radius
    br = blast_radius(service, deps_edges)
    if br["exceeds_cap"]:
        fired.append({"rule": "blast_radius_cap", "effect": "require_approval",
                      "detail": f"{br['count']} downstream > cap {br['cap']}"})
    if br["tier0_impacted"] and autonomous:
        fired.append({"rule": "tier0_autonomous", "effect": "require_approval",
                      "detail": f"tier-0 impacted: {br['tier0_impacted']}"})
    if gate.verdict == "require_approval":
        fired.append({"rule": "permission_gate", "effect": "require_approval", "detail": "; ".join(gate.reasons)})
    decision = "require_approval" if any(f["effect"] == "require_approval" for f in fired) else "allow"
    if decision == "allow":
        fired.append({"rule": "within_policy", "effect": "allow", "detail": "all rules passed"})
    return PolicyResult(decision, fired)
