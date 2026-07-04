"""Single source of truth for service naming + reachability.

Topology/telemetry names (auth, inventory, frontend, checkout-api, payment-service) map to the
real backend service names — the microservice-lab modules, local ports, and Azure Container Apps:
    auth       → auth-service
    inventory  → inventory-service
    checkout-api / payment-service → unchanged (already canonical)
    frontend   → (telemetry-only; no backend) stays 'frontend'
Everything that touches a service — get_service_health, the Portal Agent, remediation
reachability, workspace status, and any future Azure op — resolves names through here so naming
never diverges across the codebase.
"""
from __future__ import annotations

import os

# Real, reachable services (local microservice ports = also the Container App names).
SERVICE_PORTS = {"checkout-api": 8101, "payment-service": 8102,
                 "inventory-service": 8103, "auth-service": 8104}
REAL_SERVICES = set(SERVICE_PORTS)

# Env var per service → override base URL (Azure internal DNS in the cloud; localhost port locally).
SERVICE_ENV = {"checkout-api": "CHECKOUT_URL", "payment-service": "PAYMENT_URL",
               "inventory-service": "INVENTORY_URL", "auth-service": "AUTH_URL"}

# Topology/short aliases → canonical real service name.
_ALIASES = {"auth": "auth-service", "inventory": "inventory-service"}


def canonical(name: str) -> str:
    """Topology/short name → real backend service name. Idempotent; unknown names unchanged.
    (frontend has no backend, so it stays 'frontend' → callers treat it as no real resource.)"""
    n = (name or "").strip()
    return _ALIASES.get(n, n)


def is_real(name: str) -> bool:
    """True if the (canonicalized) name is a deployable/reachable service."""
    return canonical(name) in REAL_SERVICES


def service_base(name: str) -> str | None:
    """Base URL for a service — env override (Azure internal DNS) else local port. Canonicalizes
    the name first, so 'auth' and 'auth-service' both resolve. None if there's no real service."""
    svc = canonical(name)
    override = os.environ.get(SERVICE_ENV.get(svc, ""))
    if override:
        return override.rstrip("/")
    port = SERVICE_PORTS.get(svc)
    return f"http://127.0.0.1:{port}" if port else None
