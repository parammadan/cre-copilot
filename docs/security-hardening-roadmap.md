# CRE Copilot — Security Hardening Roadmap

A one-page maturity map for the **act path** (detect → decide → remediate → verify). The
*topology* is already production-grade: read-only visualization and mutation run on **separate
identities**, mutation is gated by **deterministic logic + human approval**, and an
**independent verifier** confirms recovery. The gaps below are the steps from *demo-safe* to
*safe to remediate real production*.

Guiding principles: **least privilege by function**, **separation of decide/act/verify**,
**every mutation is authenticated, bound, and auditable**.

---

## 1. Demo-safe today ✅
What is implemented now and safe for the local/interview demo.

- **Portal Agent is read-only visualization only.** It navigates Azure Portal blades and reads;
  it never clicks Save/Delete/Create/Restart/Rollback. It is *not* a control path and must never
  become one. Local-only, feature-flagged off by default.
- **Reader-scoped identity for the "eyes."** The visualization/investigation path uses a
  Reader-only Azure account — a compromised view path cannot mutate anything.
- **Backend Managed Identity performs approved actions** (keyless auth via
  `DefaultAzureCredential`) — the "hands" are a *separate* identity from the "eyes."
- **Deterministic gate before the LLM can act.** The confidence gate is code, not a prompt; the
  model recommends, math + a human decide.
- **Human approval required** before any remediation (unless explicitly autonomous), with
  idempotent approvals.
- **Independent Verifier** re-reads live state after remediation instead of trusting the fix's
  own success signal.
- **No secrets in code** — Azure is keyless; the only secret (Teams webhook) lives in gitignored
  `demo/.env` locally and Key Vault in cloud.

---

## 2. Production-required before real remediation 🔒
Must-have before this identity is allowed to mutate real production resources.

- **Custom least-privilege remediation role.** Not Contributor. A custom RBAC role enumerating
  the exact actions (e.g. `Microsoft.App/containerApps/revisions/activate`, restart) and nothing
  else — no `*/write`. **Resource- or RG-scoped**, never subscription-wide.
- **Signed / expiring approval tokens.** The approval callback must verify a signed, short-TTL
  token and authenticate the caller (Teams/webhook). No unauthenticated or replayable approvals.
- **Approval bound to `incident_id` + remediation hash.** An approval authorizes *one specific
  action on one specific incident* — it cannot be replayed onto a different remediation. Require
  approver ≠ incident trigger.
- **Immutable audit trail.** Azure **Activity Log + diagnostic settings → immutable store.**
  Every mutation reconciles to `(incident_id, gate decision, approver, timestamp)`. Any change in
  Activity Log with no matching approved incident is an alert.
- **Guardrails on the act path.** **Kill-switch / global freeze**; **rate limits** (max
  remediations/hour, one service at a time); **blast-radius caps** (refuse if it touches a tier-0
  dependency); **auto rollback-of-the-rollback** if the Verifier fails. The gate decides
  *whether*; these decide *how much*.

---

## 3. Future enterprise hardening 🏢
Deeper controls for scale, multi-tenant, and audit-heavy environments.

- **JIT / PIM-elevated remediation identity** — the powerful identity is not standing-privileged
  24/7; it is activated per-incident and bound to the approval.
- **Independent Verifier signals.** The Verifier reads from a *different* source than what
  triggered the incident (synthetic probe / external health check, not the same ADX telemetry),
  so a poisoned signal cannot both trigger *and* falsely confirm recovery.
- **Portal Agent principal isolation.** Reader session as its own principal with no data-plane
  secrets, session-scoped, browser surface never network-exposed.
- **Policy-as-code enforcement** (Azure Policy / deny assignments) so guardrails hold even if the
  app is bypassed.
- **Per-tenant / per-environment identity + role separation**; break-glass procedures and
  periodic access reviews (PIM recertification).
- **Full chain-of-custody signing** — sign the incident → decision → approval → action → verify
  chain end to end for tamper-evident audit.

---

**Verdict:** topology is production-grade; the maturity gaps are all in the act path. Ship §2
before any real remediation; §3 is the enterprise glide path.
