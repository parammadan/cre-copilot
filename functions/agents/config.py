"""Azure OpenAI connection for the agents — keyless (Entra ID) auth via `az login`.
Same identity model as the rest of the system; no API key in code."""
import os
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from semantic_kernel.connectors.ai.open_ai import AzureChatCompletion

ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT", "https://crecopilot-aoai-vxxmsm.openai.azure.com/")
DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")


def chat_service(service_id: str) -> AzureChatCompletion:
    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default"
    )
    return AzureChatCompletion(
        service_id=service_id,
        deployment_name=DEPLOYMENT,
        endpoint=ENDPOINT,
        api_version=API_VERSION,
        ad_token_provider=token_provider,
    )
