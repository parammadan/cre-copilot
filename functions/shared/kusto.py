"""Thin ADX helper — query + control command, authenticated via `az login`
(DefaultAzureCredential's CLI path). Same code runs locally or in the Function App
(where it would use the managed identity instead)."""
from __future__ import annotations
import os
import warnings

warnings.filterwarnings("ignore")
from datetime import timedelta
import pandas as pd
from azure.identity import DefaultAzureCredential
from azure.kusto.data import KustoClient, KustoConnectionStringBuilder, ClientRequestProperties
from azure.kusto.data.helpers import dataframe_from_result_table

try:
    from shared.settings import KUSTO_TIMEOUT_SEC
except Exception:
    KUSTO_TIMEOUT_SEC = 30

_CLUSTER = os.environ["ADX_CLUSTER_URI"].rstrip("/")
_DB = os.environ.get("ADX_DATABASE", "CopilotDb")
# Portable: managed identity in the cloud, `az login` locally — same code both places.
_CRED = DefaultAzureCredential()


def _props() -> ClientRequestProperties:
    p = ClientRequestProperties()
    p.set_option(ClientRequestProperties.request_timeout_option_name, timedelta(seconds=KUSTO_TIMEOUT_SEC))
    return p


def _client() -> KustoClient:
    return KustoClient(KustoConnectionStringBuilder.with_azure_token_credential(_CLUSTER, credential=_CRED))


def query(kql: str):
    """Run a query; return a pandas DataFrame (raises on failure — caller decides)."""
    resp = _client().execute(_DB, kql, _props())
    return dataframe_from_result_table(resp.primary_results[0])


def query_safe(kql: str) -> pd.DataFrame:
    """Graceful-degradation query: on timeout/error return an EMPTY frame instead of raising,
    so one slow/failing panel doesn't take down the whole console."""
    try:
        return query(kql)
    except Exception as e:
        from shared.obs import log
        log("kusto.query_failed", error=str(e)[:200], kql=kql[:80])
        return pd.DataFrame()


def command(kql: str) -> None:
    """Run a control command (e.g. write a row)."""
    _client().execute_mgmt(_DB, kql, _props())
