from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

import httpx
from e2e_analytics_smoke import _payload
from fastmcp import Client

DEFAULT_BACKEND_URL = "http://127.0.0.1:8010"


async def _stats(client: httpx.AsyncClient, backend_url: str) -> dict[str, Any]:
    response = await client.get(f"{backend_url}/stats")
    response.raise_for_status()
    return response.json()


def _tool_data(result: Any) -> dict[str, Any]:
    data = getattr(result, "data", None)
    if isinstance(data, dict):
        return data
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    if hasattr(data, "model_dump"):
        return data.model_dump()
    raise RuntimeError(f"tool result did not contain structured data: {result!r}")


async def main() -> int:
    backend_url = os.environ.get("ANALYTICS_BACKEND_URL", DEFAULT_BACKEND_URL).rstrip(
        "/"
    )
    marker = os.environ.get(
        "MCP_OTEL_SMOKE_MARKER", f"mcp-otel-smoke-{int(time.time())}"
    )
    ccr_hash = f"{marker}-ccr"
    payload = _payload(marker)

    async with httpx.AsyncClient(timeout=20.0) as http:
        before = await _stats(http, backend_url)
        ingest = await http.post(f"{backend_url}/ingest/compression", json=payload)
        ingest.raise_for_status()

        async with Client(f"{backend_url}/mcp/") as mcp:
            tools = await mcp.list_tools()
            tool_names = {tool.name for tool in tools}
            if "litellm_proxy_analytics_retrieve_chunk" not in tool_names:
                raise RuntimeError(
                    "litellm_proxy_analytics_retrieve_chunk missing from MCP tools"
                )
            result = await mcp.call_tool(
                "litellm_proxy_analytics_retrieve_chunk",
                {"ccr_hash": ccr_hash, "source": "mcp-smoke"},
                timeout=10.0,
            )
            tool_data = _tool_data(result)

        after = await _stats(http, backend_url)
        metrics = await http.get(f"{backend_url}/metrics")
        metrics.raise_for_status()

    if not tool_data.get("found"):
        print("mcp_otel_smoke=failed chunk_not_found", file=sys.stderr)
        return 1
    if tool_data.get("ccr_hash") != ccr_hash:
        print("mcp_otel_smoke=failed ccr_hash_mismatch", file=sys.stderr)
        return 1
    if after.get("retrievals", 0) <= before.get("retrievals", 0):
        print("mcp_otel_smoke=failed retrieval_count_did_not_increase", file=sys.stderr)
        return 1
    if "litellm_proxy_analytics_retrievals_total" not in metrics.text:
        print("mcp_otel_smoke=failed metrics_missing_retrievals", file=sys.stderr)
        return 1

    print(
        "mcp_otel_smoke=ok "
        f"marker={marker} "
        f"ccr_hash={ccr_hash} "
        f"event_id={ingest.json().get('event_id')} "
        f"mcp_event_id={tool_data.get('retrieval_event_id')} "
        f"retrievals_before={before.get('retrievals')} "
        f"retrievals_after={after.get('retrievals')} "
        f"otel_console_enabled="
        f"{os.environ.get('HEADROOM_ANALYTICS_OTEL_CONSOLE', 'false')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
