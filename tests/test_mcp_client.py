import asyncio

from fastmcp import FastMCP

from novaops.agent.mcp_client import MCPToolExecutor


def test_tool_executor_calls_through_the_mcp_protocol() -> None:
    async def run() -> None:
        server = FastMCP("MCP Boundary Test")

        @server.tool
        def echo(value: str) -> dict[str, str]:
            return {"echo": value}

        executor = MCPToolExecutor(server)
        result = await executor.call_tool("echo", {"value": "crossed MCP"})

        assert result == {"echo": "crossed MCP"}

    asyncio.run(run())


def test_tool_executor_propagates_request_id_header(monkeypatch) -> None:
    import fastmcp
    import fastmcp.client.transports

    captured: dict[str, object] = {}

    class FakeTransport:
        def __init__(self, url: str, *, headers: dict[str, str]) -> None:
            captured["url"] = url
            captured["headers"] = headers

    class FakeResult:
        is_error = False
        data = {"ok": True}
        structured_content = None
        content = None

    class FakeClient:
        def __init__(self, transport) -> None:
            assert isinstance(transport, FakeTransport)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def call_tool(self, name: str, arguments: dict):
            return FakeResult()

    monkeypatch.setattr(
        fastmcp.client.transports, "StreamableHttpTransport", FakeTransport
    )
    monkeypatch.setattr(fastmcp, "Client", FakeClient)

    result = asyncio.run(
        MCPToolExecutor(
            "http://127.0.0.1:9876/mcp", request_id="request-42"
        ).call_tool("echo", {})
    )

    assert result == {"ok": True}
    assert captured == {
        "url": "http://127.0.0.1:9876/mcp",
        "headers": {"X-Request-ID": "request-42"},
    }
