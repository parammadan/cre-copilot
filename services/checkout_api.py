import os

from common import make_app

# checkout-api really depends on the other three — its /health calls their /health,
# so a downstream failure produces real cascading degradation (blast radius).
# Dependency URLs are env-driven (localhost defaults for local dev; internal DNS in Azure).
app = make_app("checkout-api", deps={
    "payment-service":   os.environ.get("PAYMENT_URL",   "http://127.0.0.1:8102"),
    "inventory-service": os.environ.get("INVENTORY_URL", "http://127.0.0.1:8103"),
    "auth-service":      os.environ.get("AUTH_URL",      "http://127.0.0.1:8104"),
}, base_lat=130)
