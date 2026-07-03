"""Smoke test: one HOSTED agent on Azure AI Foundry Agent Service calling a real KQL tool."""
import warnings
warnings.filterwarnings("ignore")
from azure.identity import AzureCliCredential
from azure.ai.projects import AIProjectClient
from azure.ai.agents.models import FunctionTool, ListSortOrder

from shared import kusto

ENDPOINT = "https://crecopilot-foundry.services.ai.azure.com/api/projects/creproj"


def detect_anomalies() -> str:
    """Detect anomalous services from live telemetry. Returns JSON list."""
    df = kusto.query("Detect()")
    return df.to_json(orient="records") if not df.empty else "[]"


project = AIProjectClient(endpoint=ENDPOINT, credential=AzureCliCredential())
agents = project.agents
functions = FunctionTool(functions={detect_anomalies})
agents.enable_auto_function_calls(functions)

agent = agents.create_agent(
    model="gpt-5-mini", name="CRE-Smoke",
    instructions="You are an SRE agent. Call detect_anomalies() and report which services are anomalous, highest score first. Be concise.",
    tools=functions.definitions,
)
print("created hosted agent:", agent.id)
thread = agents.threads.create()
agents.messages.create(thread_id=thread.id, role="user", content="Check the live site now.")
run = agents.runs.create_and_process(thread_id=thread.id, agent_id=agent.id)
print("run status:", run.status)
if run.status == "failed":
    print("error:", run.last_error)
for m in agents.messages.list(thread_id=thread.id, order=ListSortOrder.ASCENDING):
    if m.role == "assistant":
        for t in m.text_messages:
            print("ASSISTANT:", t.text.value)
agents.delete_agent(agent.id)
print("cleaned up agent")
