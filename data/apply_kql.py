#!/usr/bin/env python3
"""Apply a .kql file of control commands (e.g. function definitions) to ADX.
Usage: python apply_kql.py kql/02_functions.kql"""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder

CLUSTER = os.environ["ADX_CLUSTER_URI"].rstrip("/")
DB = os.environ.get("ADX_DATABASE", "CopilotDb")

raw = open(sys.argv[1]).read()
# Drop // comment lines; run the remaining control command(s).
body = "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//")).strip()
client = KustoClient(KustoConnectionStringBuilder.with_az_cli_authentication(CLUSTER))
client.execute_mgmt(DB, body)
print(f"✓ applied {sys.argv[1]}")
