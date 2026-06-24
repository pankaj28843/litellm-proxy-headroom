import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from litellm_proxy_headroom.analytics.adapters.api.mcp import (
    create_analytics_mcp_server,
)


def test_analytics_mcp_retrieval_tool_resolves_compression_markers() -> None:
    async def list_tools():
        server = create_analytics_mcp_server(lambda: async_sessionmaker())
        return await server.list_tools()

    tools = {tool.name: tool for tool in asyncio.run(list_tools())}

    assert set(tools) == {
        "litellm_proxy_analytics_retrieve_chunk",
        "litellm_proxy_analytics_stats",
    }
    assert "headroom_retrieve" not in tools

    retrieve = tools["litellm_proxy_analytics_retrieve_chunk"]
    assert retrieve.description is not None
    assert "Retrieve more: hash=..." in retrieve.description
    assert "<<ccr:...>>" in retrieve.description
    assert "CCR hash" in retrieve.description
