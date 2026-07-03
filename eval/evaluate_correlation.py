#!/usr/bin/env python3
"""Correlation evaluation harness — precision/recall on LABELED synthetic incidents.

For each labeled scenario we know the ground-truth root cause (or that there is none /
it's ambiguous). We inject it, run the real Correlator + gate, and score:
  - accuracy   : did the top candidate match the labeled root cause?
  - precision  : of the incidents it acted on (conf >= threshold), how many were correct?
  - recall     : of the incidents it SHOULD have acted on, how many it caught.

Run:  python eval/evaluate_correlation.py     (takes a few min — it re-plants each scenario)
"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "functions"))
os.environ.setdefault("ADX_CLUSTER_URI", "https://crecopilotadxvxxmsm.eastus.kusto.windows.net")
os.environ.setdefault("ADX_DATABASE", "CopilotDb")

from shared import kusto            # noqa: E402
from shared.settings import ACT_THRESHOLD  # noqa: E402
PY = os.path.join(ROOT, "data", ".venv", "bin", "python")

# (scenario, alert_service, expected_root_service | None, should_act)
CASES = [
    ("classic",    "checkout-api",   "payment-service", True),   # clear culprit -> act
    ("classic",    "auth",           "auth",            False),  # mild -> correct root but escalate
    ("ambiguous",  "checkout-api",   None,              False),  # two close deploys -> escalate, don't guess
    ("falsealarm", "checkout-api",   None,              False),  # transient, no cause -> no action
]


def inject(scenario: str):
    env = dict(os.environ, SCENARIO=scenario, PATH="/opt/homebrew/bin:" + os.environ.get("PATH", ""))
    subprocess.run([PY, os.path.join(ROOT, "data", "generate_and_ingest.py")],
                   env=env, cwd=os.path.join(ROOT, "data"), capture_output=True)
    for _ in range(15):
        n = kusto.query("Telemetry | count | project Count").iloc[0].Count
        if int(n) >= 7200:
            return
        time.sleep(5)


def top_candidate(alert_service: str):
    a = kusto.query(f"Alerts | where Service=='{alert_service}' | summarize m=max(Timestamp)").iloc[0].m
    if a is None:
        return None, 0.0
    import pandas as pd
    aiso = pd.Timestamp(a).strftime("%Y-%m-%dT%H:%M:%SZ")
    df = kusto.query(f"Correlate('{alert_service}', datetime({aiso}))")
    if df.empty:
        return None, 0.0
    return df.iloc[0].Service, float(df.iloc[0].confidence)


def main():
    print(f"Correlation eval · gate threshold = {ACT_THRESHOLD}\n" + "-" * 74)
    rows, injected = [], None
    for scenario, alert_svc, expected, should_act in CASES:
        if scenario != injected:
            print(f"injecting '{scenario}' …")
            inject(scenario); injected = scenario
        pred, conf = top_candidate(alert_svc)
        acted = conf >= ACT_THRESHOLD
        correct_root = (pred == expected) if expected else (not acted)  # None => correct means it didn't act
        rows.append((scenario, alert_svc, expected, pred, round(conf, 3), should_act, acted, correct_root))

    print(f"\n{'scenario':<11}{'alert':<14}{'expected':<16}{'predicted':<16}{'conf':<7}{'act?':<6}{'ok?'}")
    for s, al, exp, pr, cf, sa, ac, ok in rows:
        print(f"{s:<11}{al:<14}{str(exp):<16}{str(pr):<16}{cf:<7}{str(ac):<6}{'✓' if ok else '✗'}")

    tp = sum(1 for *_ , sa, ac, ok in rows if sa and ac and ok)
    acted = sum(1 for *_, ac, ok in [(r[6], r[7]) for r in rows] if ac)
    should = sum(1 for r in rows if r[5])
    acc = sum(1 for r in rows if r[7]) / len(rows)
    precision = tp / acted if acted else 1.0
    recall = tp / should if should else 1.0
    print("-" * 74)
    print(f"accuracy={acc:.0%}  precision={precision:.0%}  recall={recall:.0%}  "
          f"(acted on {acted}, should-act {should}, correct {tp})")


if __name__ == "__main__":
    main()
