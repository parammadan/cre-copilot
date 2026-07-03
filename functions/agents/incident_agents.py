"""Multi-agent incident response in Semantic Kernel.

Four LLM agents on Azure OpenAI hand off on a shared conversation:
  Commander -> Correlator -> Impact -> Gate
Each reasons and calls the EXISTING KQL / gate as function-calling tools.
The Gate calls DETERMINISTIC code — the LLM never decides to act on its own.

Phase 2: plain output (verify the logic + numbers). Streaming/UI comes later.
Run:  cd functions && PYTHONPATH=. ../data/.venv/bin/python -m agents.incident_agents
"""
import asyncio
import warnings

warnings.filterwarnings("ignore")
from semantic_kernel.agents import ChatCompletionAgent
from semantic_kernel.connectors.ai.function_choice_behavior import FunctionChoiceBehavior
from semantic_kernel.contents import ChatMessageContent, AuthorRole

from agents.config import chat_service
from agents.tools import IncidentTools

TOOLS = IncidentTools()

INSTRUCTIONS = {
    "Commander": (
        "You are the Incident Commander for a live-site incident. Call detect() and get_alerts() "
        "to see what is happening. In 2-3 factual sentences, state which services are anomalous and "
        "which alerts fired, then hand off to the Correlator. Be concise; no fluff."),
    "Correlator": (
        "You are the Correlator. For EACH active alert, call correlate(alert_service, alert_time_iso) "
        "to rank root-cause deploy candidates. Report the top candidate, its confidence to 3 decimals, "
        "and WHY (time proximity, anomaly ratio, dependency position). Use ONLY numbers the tool returns. "
        "Then hand off to Impact."),
    "Impact": (
        "You are the Impact analyst. For each root-cause service the Correlator named, call "
        "assess_impact(service). Report which downstream services are degraded and by how much "
        "(latency multiple). Then hand off to the Gate."),
    "Gate": (
        "You are the Gate. You do NOT decide yourself. For each root cause, call "
        "apply_gate(confidence, service, version) and report EXACTLY what it returns: auto-remediate or "
        "escalate, with the reason. State clearly that the decision is deterministic code, not your opinion."),
}


def make(name: str, sid: str) -> ChatCompletionAgent:
    return ChatCompletionAgent(
        service=chat_service(sid), name=name, instructions=INSTRUCTIONS[name],
        plugins=[TOOLS], function_choice_behavior=FunctionChoiceBehavior.Auto(),
    )


def _agents():
    return [make("Commander", "s_cmd"), make("Correlator", "s_cor"),
            make("Impact", "s_imp"), make("Gate", "s_gate")]

KICKOFF = "A live-site incident was triggered. Run the incident response end to end."


async def run() -> None:
    agents = _agents()
    history = [ChatMessageContent(role=AuthorRole.USER, content=KICKOFF)]
    for agent in agents:
        resp = await agent.get_response(messages=history)
        print(f"\n{'='*68}\n  {agent.name}\n{'='*68}\n{resp.message.content}")
        history.append(resp.message)


async def run_stream():
    """Async generator of events for the live UI: agent_start, token, tool_call,
    tool_result, agent_end, done. Each agent hands off on the shared history."""
    agents = _agents()
    history = [ChatMessageContent(role=AuthorRole.USER, content=KICKOFF)]
    yield {"type": "incident_start"}
    for agent in agents:
        yield {"type": "agent_start", "agent": agent.name}
        buf = ""
        async for chunk in agent.invoke_stream(messages=history):
            msg = getattr(chunk, "message", chunk)
            text = getattr(msg, "content", "") or ""
            if text:
                buf += text
                yield {"type": "token", "agent": agent.name, "text": text}
            for item in getattr(msg, "items", []) or []:
                kind = type(item).__name__
                if kind == "FunctionCallContent":
                    name = getattr(item, "function_name", None) or getattr(item, "name", "") or ""
                    if name:
                        yield {"type": "tool_call", "agent": agent.name, "tool": str(name).split("-")[-1]}
                elif kind == "FunctionResultContent":
                    yield {"type": "tool_result", "agent": agent.name}
        history.append(ChatMessageContent(role=AuthorRole.ASSISTANT, name=agent.name, content=buf))
        yield {"type": "agent_end", "agent": agent.name, "content": buf}
    yield {"type": "done"}


async def _test_stream():
    async for ev in run_stream():
        t = ev["type"]
        if t == "token":
            print(ev["text"], end="", flush=True)
        elif t == "agent_start":
            print(f"\n\n===== {ev['agent']} =====")
        elif t == "tool_call":
            print(f"\n  [tool → {ev['tool']}]")
        elif t == "done":
            print("\n\n[done]")


if __name__ == "__main__":
    import sys
    asyncio.run(_test_stream() if "stream" in sys.argv else run())
