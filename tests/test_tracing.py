import json
from contextlib import contextmanager, nullcontext
from dataclasses import replace

import langfuse
import langfuse.langchain
import pytest

from novaops.core.config import Settings
from novaops.core.tracing import (
    clear_last_trace_id,
    last_trace_id,
    new_request_id,
    trace_agent_run,
    trajectory_digest,
)


def test_new_request_id_preserves_valid_header_and_generates_when_missing() -> None:
    assert new_request_id("client-42:retry.1") == "client-42:retry.1"
    generated = new_request_id()
    assert len(generated) == 32
    int(generated, 16)


@pytest.mark.parametrize("value", ["", "has spaces", "x" * 129, "newline\nid"])
def test_new_request_id_rejects_malformed_header(value: str) -> None:
    with pytest.raises(ValueError, match="X-Request-ID"):
        new_request_id(value)


def test_trajectory_digest_is_valid_json_below_limit() -> None:
    digest = trajectory_digest(
        {
            "tool_history": [
                {
                    "name": f"tool-{index}",
                    "arguments": {"query": "q" * 500},
                    "result": {"text": "r" * 900},
                }
                for index in range(10)
            ],
            "access_decision": {"status": "needs_human", "facts": ["f" * 5000]},
            "status": "needs_human",
        }
    )

    encoded = json.dumps(digest, ensure_ascii=False, sort_keys=True)
    assert len(encoded) <= 1500
    assert json.loads(encoded)["status"] == "needs_human"
    assert digest["truncated"] is True


def test_trace_binding_is_disabled_without_credentials() -> None:
    settings = Settings.from_env(load_env_file=False)

    with trace_agent_run(
        settings,
        user_id="E001",
        session_id="thread-1",
        request_id="request-1",
        workflow_request_id="request-1",
    ) as tracing:
        assert tracing.callbacks == ()
        assert tracing.root_span is None


def test_trace_binding_propagates_attributes_and_records_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        Settings.from_env(load_env_file=False),
        langfuse_public_key="public",
        langfuse_secret_key="secret",
    )
    attributes: list[dict] = []
    updates: list[dict] = []
    flushes: list[bool] = []

    class FakeSpan:
        def update(self, **kwargs) -> None:
            updates.append(kwargs)

    class FakeClient:
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            yield FakeSpan()

        def flush(self) -> None:
            flushes.append(True)

        def get_current_trace_id(self) -> str:
            return "trace-abc"

    class FakeLangfuseClient(FakeClient):
        def __init__(self, *, public_key: str, secret_key: str, host: str) -> None:
            assert public_key == "public"
            assert secret_key == "secret"
            assert host == settings.langfuse_host

    class FakeCallbackHandler:
        def __init__(self, *, public_key: str) -> None:
            assert public_key == "public"

    client = FakeLangfuseClient(
        public_key="public", secret_key="secret", host=settings.langfuse_host
    )
    monkeypatch.setattr(langfuse, "Langfuse", lambda **_: client)
    monkeypatch.setattr(langfuse, "get_client", lambda **_: client)
    monkeypatch.setattr(
        langfuse,
        "propagate_attributes",
        lambda **kwargs: attributes.append(kwargs) or nullcontext(),
    )
    monkeypatch.setattr(
        langfuse.langchain, "CallbackHandler", FakeCallbackHandler
    )

    with trace_agent_run(
        settings,
        user_id="E001",
        session_id="thread-1",
        request_id="request-2",
        workflow_request_id="request-1",
    ) as tracing:
        assert len(tracing.callbacks) == 1
        tracing.record_result(
            {
                "scope": "webex",
                "status": "pending",
                "answer": "Request pending approval.",
                "tool_history": [
                    {
                        "name": "create_access_request",
                        "arguments": {"employee_id": "E001"},
                        "result": {"request_id": "AR005"},
                    }
                ],
                "access_decision": {"request_id": "AR005"},
            }
        )

    assert attributes[0]["user_id"] == "E001"
    assert attributes[0]["session_id"] == "thread-1"
    assert "shared-agent" in attributes[0]["tags"]
    assert updates[0]["metadata"]["request_id"] == "request-2"
    assert updates[0]["metadata"]["workflow_request_id"] == "request-1"
    assert updates[0]["metadata"]["status"] == "pending"
    assert updates[0]["metadata"]["trajectory_digest"]["tools"]
    assert flushes == [True]
    assert tracing.trace_id == "trace-abc"
    assert last_trace_id() == "trace-abc"

    clear_last_trace_id()
    with pytest.raises(RuntimeError):
        with trace_agent_run(
            settings,
            user_id="E001",
            session_id="thread-1",
            request_id="request-3",
            workflow_request_id="request-3",
        ):
            raise RuntimeError("turn failed")
    assert last_trace_id() == "trace-abc"  # still findable after a failed turn


def test_trace_configuration_requires_both_credentials() -> None:
    settings = replace(
        Settings.from_env(load_env_file=False),
        langfuse_public_key="public",
        langfuse_secret_key=None,
    )

    with pytest.raises(ValueError, match="must both be set"):
        with trace_agent_run(
            settings,
            user_id="E001",
            session_id="thread-1",
            request_id="request-1",
            workflow_request_id="request-1",
        ):
            pass

def test_tool_observation_records_arguments_and_result_when_tracing_is_active() -> None:
    from novaops.core import tracing

    spans: list[dict] = []

    class FakeSpan:
        def __init__(self, record: dict) -> None:
            self.record = record

        def update(self, **kwargs) -> None:
            self.record.update(kwargs)

    class FakeClient:
        @contextmanager
        def start_as_current_observation(self, **kwargs):
            record = dict(kwargs)
            spans.append(record)
            yield FakeSpan(record)

    with tracing.tool_observation("search_knowledge_base", {"query": "q"}) as disabled:
        disabled.record_result(["ignored"])
    assert spans == []

    token = tracing._ACTIVE_CLIENT.set(FakeClient())
    try:
        with tracing.tool_observation(
            "search_knowledge_base", {"query": "q", "caller_employee_id": "E001"}
        ) as observation:
            observation.record_result([])
    finally:
        tracing._ACTIVE_CLIENT.reset(token)

    assert spans == [
        {
            "name": "tool.search_knowledge_base",
            "as_type": "tool",
            "input": {"query": "q", "caller_employee_id": "E001"},
            "output": [],
        }
    ]
