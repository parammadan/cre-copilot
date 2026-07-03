"""Shared microservice framework for the CRE Copilot workload.

Each service is a tiny FastAPI app with no business logic — its job is to produce
realistic OPERATIONAL behaviour (health, metrics, logs) and to actually misbehave
when a failure is injected, so the agents investigate real evidence.

Endpoints (per service):
    GET  /health          status + dependency checks (real downstream calls)
    GET  /metrics         latency_p95_ms, error_rate_pct, cpu_pct, mem_pct, rps
    GET  /logs?n=50       recent structured log lines
    POST /injectFailure   {mode}: crash|latency_spike|memory_leak|dependency_timeout|
                                   bad_deploy|auth_failure|intermittent
    POST /recover         clear the failure, restore healthy
    POST /deploy          {version, config} record a deployment (evidence)
"""
from __future__ import annotations
import random
import time
from collections import deque
from datetime import datetime, timezone

import requests
from fastapi import FastAPI

MODES = {"crash", "latency_spike", "memory_leak", "dependency_timeout",
         "bad_deploy", "auth_failure", "intermittent"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Service:
    def __init__(self, name: str, deps: dict[str, str], base_lat: float):
        self.name = name
        self.deps = deps                       # {dep_name: base_url}
        self.base_lat = base_lat
        self.mode: str | None = None
        self.version = "v1.0.0"
        self.config = {"TIMEOUT_MS": 800, "MAX_CONN": 100}
        self.mem = 35.0
        self.logs: deque = deque(maxlen=300)
        self.log("INFO", f"{name} started {self.version}")

    def log(self, level: str, msg: str) -> None:
        self.logs.appendleft({"ts": _now(), "level": level, "service": self.name, "msg": msg})

    # ---- control ----
    def inject(self, mode: str) -> None:
        self.mode = mode
        if mode == "bad_deploy":
            self.version = "v9.9.9"
            self.config["TIMEOUT_MS"] = 200
            self.log("ERROR", f"deploy {self.version} changed config PAYMENT_TIMEOUT_MS 800 -> 200")
        elif mode == "memory_leak":
            self.log("WARN", "heap growth detected; GC pauses increasing")
        elif mode == "dependency_timeout":
            self.log("ERROR", "upstream dependency call exceeded timeout")
        elif mode == "auth_failure":
            self.log("ERROR", "token validation failing: 401 rate rising")
        elif mode == "crash":
            self.log("FATAL", "process crashed; health check failing (503)")
        else:
            self.log("WARN", f"failure injected: {mode}")

    def recover(self) -> None:
        self.log("INFO", f"recovery applied: cleared '{self.mode}', restored healthy")
        self.mode = None
        self.mem = 35.0
        self.config["TIMEOUT_MS"] = 800

    # ---- observability ----
    def metrics(self) -> dict:
        lat = self.base_lat * (1 + random.uniform(-0.05, 0.05))
        err = 0.4 + random.uniform(0.0, 0.3)
        cpu = 38 + random.uniform(-5, 5)
        rps = random.uniform(80, 260)
        m = self.mode
        if m == "latency_spike":
            lat *= 6; err += 2
        elif m == "memory_leak":
            self.mem = min(98.0, self.mem + random.uniform(1.5, 3.5))
            lat *= 1 + (self.mem - 35) / 90.0
        elif m == "bad_deploy":
            lat *= 4; err += 8
        elif m == "dependency_timeout":
            lat *= 3; err += 6
        elif m == "auth_failure":
            err += 25
        elif m == "crash":
            lat *= 0.1; err = 60 + random.uniform(0, 20); cpu = random.uniform(0, 3); rps *= 0.1
        elif m == "intermittent" and random.random() < 0.4:
            lat *= 5; err += 10
        if m in ("latency_spike", "bad_deploy", "memory_leak"):
            self.log("WARN", f"p95 latency {lat:.0f}ms exceeds SLO")
        if err > 5:
            self.log("ERROR", f"error rate {err:.1f}% ({int(err*3)} 5xx in last min)")
        return {"latency_p95_ms": round(lat, 1), "error_rate_pct": round(min(err, 100), 2),
                "cpu_pct": round(cpu, 1), "mem_pct": round(self.mem, 1), "rps": round(rps)}

    def _dep_health(self) -> dict:
        out = {}
        for dn, url in self.deps.items():
            try:
                r = requests.get(url + "/health", timeout=1.0)
                out[dn] = r.json().get("status", "unknown") if r.ok else "unhealthy"
            except Exception:
                out[dn] = "unreachable"
        return out

    def health(self) -> dict:
        deps = self._dep_health()
        if self.mode == "crash":
            own = "unhealthy"
        elif self.mode in ("latency_spike", "memory_leak", "bad_deploy", "dependency_timeout", "auth_failure"):
            own = "degraded"
        elif self.mode == "intermittent":
            own = "degraded" if random.random() < 0.4 else "healthy"
        else:
            own = "healthy"
        # REAL blast radius: an unhealthy/degraded dependency drags us down
        bad = [d for d, s in deps.items() if s not in ("healthy",)]
        status = own
        if own == "healthy" and bad:
            status = "degraded"
        checks = {"self": own}
        checks.update({f"dep:{d}": s for d, s in deps.items()})
        return {"service": self.name, "status": status, "version": self.version,
                "mode": self.mode, "deps": deps, "checks": checks}


def make_app(name: str, deps: dict[str, str], base_lat: float):
    svc = Service(name, deps, base_lat)
    app = FastAPI(title=name)

    @app.get("/health")
    def health():
        return svc.health()

    @app.get("/metrics")
    def metrics():
        return {"service": name, **svc.metrics()}

    @app.get("/logs")
    def logs(n: int = 50):
        return {"service": name, "logs": list(svc.logs)[:n]}

    @app.post("/injectFailure")
    def inject_failure(body: dict):
        mode = str(body.get("mode", ""))
        if mode not in MODES:
            return {"error": "unknown mode", "modes": sorted(MODES)}
        svc.inject(mode)
        return {"service": name, "mode": mode, "status": "injected"}

    @app.post("/recover")
    def recover():
        svc.recover()
        return {"service": name, "status": "recovered"}

    @app.post("/deploy")
    def deploy(body: dict):
        svc.version = str(body.get("version", svc.version))
        if body.get("config"):
            svc.config.update(body["config"])
        svc.log("INFO", f"deployed {svc.version}")
        return {"service": name, "version": svc.version, "config": svc.config}

    return app
