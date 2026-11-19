"""Langfuse binding, request correlation, and bounded run summaries."""

from __future__ import annotations

import json
import re
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from novaops.core.config import Settings

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_MAX_DIGEST_CHARS = 1500
_ACTIVE_CLIENT: ContextVar[Any | None] = ContextVar("novaops_langfuse_client", default=None)
_LAST_TRACE_ID: ContextVar[str | None] = ContextVar("novaops_last_trace_id", default=None)


def last_trace_id() -> str | None:
    """Return the Langfuse trace ID of the latest run in this context, even if it failed."""
    return _LAST_TRACE_ID.get()


def clear_last_trace_id() -> None:
    _LAST_TRACE_ID.set(None)


class ToolObservation:
    """Records a tool result on its trace span; a no-op when tracing is disabled."""

    def __init__(self, span: Any | None = None) -> None:
        self._span = span

    def record_result(self, result: Any) -> None:
        if self._span is not None:
            self._span.update(output=_json_safe(result))


@contextmanager
def tool_observation(name: str, arguments: dict[str, Any]) -> Iterator[ToolObservation]:
    """Open a `tool.<name>` span carrying the tool's arguments and result."""
    client = _ACTIVE_CLIENT.get()
    if client is None:
        yield ToolObservation()
        return
    with client.start_as_current_observation(
        name=f"tool.{name}",
        as_type="tool",
        input=_json_safe(arguments),
    ) as span:
        try:
            yield ToolObservation(span)
        except BaseException as exc:
            span.update(level="ERROR", status_message=str(exc)[:500])
            raise


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


class StepObservation:
    """A named workflow step span; every method is a no-op when tracing is disabled."""

    def __init__(self, span: Any | None = None) -> None:
        self._span = span

    def update(self, **fields: Any) -> None:
        if self._span is None:
            return
        for key in ("input", "output", "metadata"):
            if key in fields:
                fields[key] = _json_safe(fields[key])
        self._span.update(**fields)


@contextmanager
def workflow_step(
    name: str,
    *,
    input: Any = None,
    as_type: str = "span",
    model: str | None = None,
) -> Iterator[StepObservation]:
    """Open a child span (or a `generation` for model calls) under the active trace."""
    client = _ACTIVE_CLIENT.get()
    if client is None:
        yield StepObservation()
        return
    options: dict[str, Any] = {"name": name, "as_type": as_type, "input": _json_safe(input)}
    if model is not None:
        options["model"] = model
    with client.start_as_current_observation(**options) as span:
        try:
            yield StepObservation(span)
        except BaseException as exc:
            span.update(level="ERROR", status_message=str(exc)[:500])
            raise


@contextmanager
def trace_workflow_run(
    settings: Settings,
    *,
    name: str,
    session_id: str,
    request_id: str,
    tags: list[str],
    input: Any = None,
    user_id: str | None = None,
) -> Iterator[StepObservation]:
    """One Langfuse trace for a standalone (non-agent) workflow run.

    Vendor and Renewal never touch the agent graph, so they get their own root span;
    a renewal run that pauses and resumes inside one evaluation case stays one trace.
    """
    has_public_key = bool(settings.langfuse_public_key)
    if has_public_key != bool(settings.langfuse_secret_key):
        raise ValueError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must both be set or both be empty"
        )
    if not has_public_key:
        _LAST_TRACE_ID.set(None)
        yield StepObservation()
        return

    from langfuse import Langfuse, propagate_attributes

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    attributes: dict[str, Any] = {
        "session_id": session_id,
        "tags": ["novaops", *tags],
        "metadata": {"request_id": request_id},
    }
    if user_id:
        attributes["user_id"] = user_id
    with propagate_attributes(**attributes):
        with client.start_as_current_observation(
            name=name, as_type="chain", input=_json_safe(input)
        ) as root_span:
            client_token = _ACTIVE_CLIENT.set(client)
            _LAST_TRACE_ID.set(client.get_current_trace_id())
            try:
                yield StepObservation(root_span)
            except BaseException as exc:
                root_span.update(level="ERROR", status_message=str(exc)[:500])
                raise
            finally:
                _ACTIVE_CLIENT.reset(client_token)
                client.flush()


def new_request_id(x_request_id: str | None = None) -> str:
    """Preserve a valid inbound request ID or create a UUID-based one."""
    if x_request_id is None:
        return uuid.uuid4().hex
    if not isinstance(x_request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(
        x_request_id
    ):
        raise ValueError("X-Request-ID must be 1-128 safe ASCII characters")
    return x_request_id


def trajectory_digest(state: dict[str, Any]) -> dict[str, Any]:
    """Return a compact, JSON-safe summary of tool activity and terminal outcome."""
    events = state.get("tool_history", [])
    if not isinstance(events, list):
        events = []
    compact_events = [
        {
            "tool": _bounded_json(event.get("name"), 64),
            "args": _bounded_json(event.get("arguments", {}), 160),
            "result": _bounded_json(event.get("result"), 160),
        }
        for event in events
        if isinstance(event, dict)
    ]
    decision = state.get("access_decision")
    payload: dict[str, Any] = {
        "tools": compact_events,
        "decision": _bounded_json(decision, 360) if decision else None,
        "status": state.get("status", "unknown"),
        "truncated": False,
    }

    while _json_size(payload) > _MAX_DIGEST_CHARS and compact_events:
        compact_events.pop()
        payload["truncated"] = True
    if _json_size(payload) > _MAX_DIGEST_CHARS:
        payload["decision"] = _bounded_json(decision, 120) if decision else None
        payload["truncated"] = True
    if _json_size(payload) > _MAX_DIGEST_CHARS:
        payload["decision"] = None
    return payload


def _bounded_json(value: Any, limit: int) -> str:
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) <= limit:
        return serialized
    return serialized[: max(0, limit - 16)] + "...[truncated]"


def _json_size(value: dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False, sort_keys=True))


@dataclass(slots=True)
class TraceSession:
    request_id: str
    workflow_request_id: str
    callbacks: tuple[Any, ...] = ()
    root_span: Any | None = None
    client: Any | None = None
    trace_id: str | None = None

    def record_result(self, state: dict[str, Any]) -> None:
        if self.root_span is None:
            return
        status = state.get("status", "unknown")
        self.root_span.update(
            output={
                "status": status,
                "answer": state.get("answer", ""),
            },
            metadata={
                "request_id": self.request_id,
                "workflow_request_id": self.workflow_request_id,
                "scope": state.get("scope"),
                "status": status,
                "trajectory_digest": trajectory_digest(state),
            },
        )


@contextmanager
def trace_agent_run(
    settings: Settings,
    *,
    user_id: str,
    session_id: str,
    request_id: str,
    workflow_request_id: str,
) -> Iterator[TraceSession]:
    """Bind graph callbacks and identity attributes when Langfuse is configured."""
    has_public_key = bool(settings.langfuse_public_key)
    has_secret_key = bool(settings.langfuse_secret_key)
    if has_public_key != has_secret_key:
        raise ValueError(
            "LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY must both be set or both be empty"
        )

    if not has_public_key:
        _LAST_TRACE_ID.set(None)
        yield TraceSession(
            request_id=request_id,
            workflow_request_id=workflow_request_id,
        )
        return

    from langfuse import Langfuse, propagate_attributes
    from langfuse.langchain import CallbackHandler

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        host=settings.langfuse_host,
    )
    handler = CallbackHandler(public_key=settings.langfuse_public_key)
    with propagate_attributes(
        user_id=user_id,
        session_id=session_id,
        tags=["novaops", "shared-agent"],
        metadata={
            "request_id": request_id,
            "workflow_request_id": workflow_request_id,
        },
    ):
        with client.start_as_current_observation(
            name="novaops.agent",
            as_type="chain",
            input={"request_id": request_id, "workflow_request_id": workflow_request_id},
        ) as root_span:
            client_token = _ACTIVE_CLIENT.set(client)
            trace_id = client.get_current_trace_id()
            _LAST_TRACE_ID.set(trace_id)
            try:
                yield TraceSession(
                    request_id=request_id,
                    workflow_request_id=workflow_request_id,
                    callbacks=(handler,),
                    root_span=root_span,
                    client=client,
                    trace_id=trace_id,
                )
            except BaseException as exc:
                root_span.update(
                    level="ERROR",
                    status_message=str(exc)[:500],
                    metadata={
                        "request_id": request_id,
                        "workflow_request_id": workflow_request_id,
                        "status": "error",
                    },
                )
                raise
            finally:
                _ACTIVE_CLIENT.reset(client_token)
                client.flush()
