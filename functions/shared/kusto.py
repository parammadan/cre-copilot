"""Thin ADX helper — query + control command, authenticated via `az login`
(DefaultAzureCredential's CLI path). Same code runs locally or in the Function App
(where it would use the managed identity instead)."""
from __future__ import annotations
import os
import warnings

warnings.filterwarnings("ignore")
from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder
from azure.kusto.data.helpers import dataframe_from_result_table

_CLUSTER = os.environ["ADX_CLUSTER_URI"].rstrip("/")
_DB = os.environ.get("ADX_DATABASE", "CopilotDb")
# Portable: managed identity in the cloud, `az login` locally — same code both places.
_CRED = DefaultAzureCredential()


def _client() -> KustoClient:
    return KustoClient(KustoConnectionStringBuilder.with_azure_token_credential(_CLUSTER, credential=_CRED))


def query(kql: str):
    """Run a query; return a pandas DataFrame."""
    resp = _client().execute(_DB, kql)
    return dataframe_from_result_table(resp.primary_results[0])


def command(kql: str) -> None:
    """Run a control command (e.g. write a row)."""
    _client().execute_mgmt(_DB, kql)
