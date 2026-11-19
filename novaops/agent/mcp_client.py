"""MCP client adapter used by the shared LangGraph agent."""

from __future__ import annotations

from typing import Any, Protocol

from novaops.core.tracing import tool_observation


class ToolExecutor(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...


class MCPToolExecutor:
    def __init__(
        self,
        transport: Any = "http://127.0.0.1:9876/mcp",
        *,
        request_id: str | None = None,
    ) -> None:
        self.transport = transport
        self.request_id = request_id

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        from fastmcp import Client

        transport = self.transport
        if self.request_id and isinstance(transport, str):
            from fastmcp.client.transports import StreamableHttpTransport

            transport = StreamableHttpTransport(
                transport,
                headers={"X-Request-ID": self.request_id},
            )
        with tool_observation(name, arguments) as observation:
            async with Client(transport) as client:
                result = await client.call_tool(name, arguments)
            if result.is_error:
                raise RuntimeError(
                    f"MCP tool {name!r} returned an error: {result.content}"
                )
            if result.data is not None:
                value = result.data
            elif result.structured_content is not None:
                value = result.structured_content
            else:
                value = result.content
            observation.record_result(value)
            return value
