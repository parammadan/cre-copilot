"""Structured observability — one JSON line per event, so every agent decision,
tool call, gate outcome, and failure is traceable (grep-able locally; ships straight
to App Insights / Log Analytics when deployed, since those index stdout JSON)."""
import json
import logging
import sys
import time
import uuid

_logger = logging.getLogger("cre")
if not _logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_h)
    _logger.setLevel(logging.INFO)


def log(event: str, **fields) -> None:
    """Emit one structured JSON event."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event}
    rec.update(fields)
    _logger.info(json.dumps(rec, default=str))


def new_trace() -> str:
    """A correlation id to tie all events of one incident run together."""
    return "trace-" + uuid.uuid4().hex[:10]


def log_tool(trace: str, agent: str, tool: str, args=None) -> None:
    log("agent.tool_call", trace=trace, agent=agent, tool=tool, args=args or {})


def log_decision(trace: str, service: str, confidence: float, action: str, threshold: float) -> None:
    log("gate.decision", trace=trace, service=service, confidence=confidence,
        action=action, threshold=threshold)
