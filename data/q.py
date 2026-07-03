#!/usr/bin/env python3
"""Tiny KQL runner: `python q.py "<kusto query>"` -> prints results as a table.
Reads ADX_CLUSTER_URI + ADX_DATABASE from the environment; auth via `az login`."""
import os
import sys
import warnings

warnings.filterwarnings("ignore")
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.helpers import dataframe_from_result_table

CLUSTER = os.environ["ADX_CLUSTER_URI"].rstrip("/")
DB = os.environ.get("ADX_DATABASE", "CopilotDb")


def run(query: str):
    client = KustoClient(KustoConnectionStringBuilder.with_az_cli_authentication(CLUSTER))
    resp = client.execute(DB, query)
    df = dataframe_from_result_table(resp.primary_results[0])
    if df.empty:
        print("(no rows)")
    else:
        print(df.to_string(index=False))


if __name__ == "__main__":
    run(sys.argv[1])
