import io
import json

import pytest
from botocore.exceptions import NoCredentialsError, PartialCredentialsError

from novaops.core.config import Settings
from novaops.core.model import BedrockModel
from novaops.retrieval.embeddings import BedrockEmbedder


class FakeBedrockClient:
    def __init__(self) -> None:
        self.converse_request = None
        self.embedding_request = None

    def converse(self, **request):
        self.converse_request = request
        return {
            "output": {
                "message": {"content": [{"text": "Use the managed laptop policy."}]}
            }
        }

    def invoke_model(self, **request):
        self.embedding_request = request
        return {"body": io.BytesIO(b'{"embedding":[0.25,0.5]}')}


def test_model_call_is_behind_the_bedrock_boundary() -> None:
    client = FakeBedrockClient()
    model = BedrockModel(Settings.from_env(load_env_file=False), client=client)

    answer = model.generate("Question", system="Use sources only.")

    assert answer == "Use the managed laptop policy."
    assert client.converse_request["modelId"] == "us.amazon.nova-2-lite-v1:0"
    assert client.converse_request["system"] == [{"text": "Use sources only."}]


def test_embedder_uses_the_configured_bedrock_model() -> None:
    client = FakeBedrockClient()
    embedder = BedrockEmbedder("test-embedding-model", "us-east-1", client=client)

    assert embedder.embed("example") == [0.25, 0.5]
    assert client.embedding_request["modelId"] == "test-embedding-model"
    assert json.loads(client.embedding_request["body"])["inputText"] == "example"


@pytest.mark.parametrize(
    "error",
    [NoCredentialsError(), PartialCredentialsError(provider="env", cred_var="AWS_SECRET_ACCESS_KEY")],
)
def test_embedder_explains_missing_credentials_for_mcp_process(error: Exception) -> None:
    class MissingCredentialsClient:
        def invoke_model(self, **request):
            raise error

    embedder = BedrockEmbedder(
        "test-embedding-model",
        "us-east-1",
        client=MissingCredentialsClient(),
    )

    with pytest.raises(RuntimeError, match="process running the NovaOps MCP server") as exc:
        embedder.embed("example")

    assert isinstance(exc.value.__cause__, type(error))
