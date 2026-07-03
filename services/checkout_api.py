from common import make_app

# checkout-api really depends on the other three — its /health calls their /health,
# so a downstream failure produces real cascading degradation (blast radius).
app = make_app("checkout-api", deps={
    "payment-service":   "http://127.0.0.1:8102",
    "inventory-service": "http://127.0.0.1:8103",
    "auth-service":      "http://127.0.0.1:8104",
}, base_lat=130)
