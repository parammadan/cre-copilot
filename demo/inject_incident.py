#!/usr/bin/env python3
"""One-command live trigger — plant a scenario into ADX and wait for ingestion.

Usage:
    python demo/inject_incident.py [classic|proactive|ambiguous|falsealarm]

Scenarios:
    classic     severe payment-service incident (auto-remediate) + mild auth (escalate)
    proactive   rising checkout-api trend caught BEFORE it breaches — no alert yet
    ambiguous   two close deploys, neither clears the gate -> escalate with candidates
    falsealarm  an alert fired but it was a transient blip, already recovered -> no action
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, "data", ".venv", "bin", "python")

scenario = sys.argv[1] if len(sys.argv) > 1 else "classic"
if scenario not in ("classic", "proactive", "ambiguous", "falsealarm"):
    sys.exit(f"unknown scenario '{scenario}' (classic|proactive|ambiguous|falsealarm)")

env = dict(os.environ)
env["PATH"] = "/opt/homebrew/bin:" + env.get("PATH", "")
env["SCENARIO"] = scenario
env.setdefault("ADX_CLUSTER_URI", "https://crecopilotadxvxxmsm.eastus.kusto.windows.net")
env.setdefault("ADX_DATABASE", "CopilotDb")

print(f">> Injecting scenario: {scenario}")
subprocess.run([PY, os.path.join(ROOT, "data", "generate_and_ingest.py")],
               env=env, cwd=os.path.join(ROOT, "data"))

print(">> Waiting for ingestion...")
for _ in range(15):
    out = subprocess.run([PY, os.path.join(ROOT, "data", "q.py"),
                          "Telemetry | count | project Count"],
                         env=env, cwd=os.path.join(ROOT, "data"),
                         capture_output=True, text=True).stdout
    digits = "".join(c for c in out.split("\n")[-2] if c.isdigit()) if out.strip() else ""
    if digits and int(digits) >= 7200:
        print(f">> Ready — scenario '{scenario}' is live ({digits} telemetry rows).")
        break
    __import__("time").sleep(6)
else:
    print(">> (still ingesting — give it a few more seconds)")
