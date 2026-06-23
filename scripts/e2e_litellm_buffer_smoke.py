from __future__ import annotations

import asyncio
import importlib.util
import os
import time
from pathlib import Path
from typing import Any

import httpx

DEFAULT_ANALYTICS_URL = "http://127.0.0.1:8010"


def _load_callback_class() -> type:
    spec = importlib.util.spec_from_file_location(
        "headroom_litellm_callback",
        Path("config/headroom_litellm_callback.py"),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("failed to load config/headroom_litellm_callback.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HeadroomCallback


async def _stats(client: httpx.AsyncClient, analytics_url: str) -> dict[str, Any]:
    response = await client.get(f"{analytics_url.rstrip('/')}/stats")
    response.raise_for_status()
    return response.json()


async def main() -> int:
    analytics_url = os.environ.get("HEADROOM_ANALYTICS_URL", DEFAULT_ANALYTICS_URL)
    os.environ["HEADROOM_ANALYTICS_URL"] = analytics_url
    marker = f"litellm-buffer-smoke-{int(time.time())}"

    callback_cls = _load_callback_class()
    callback = callback_cls(api_key=None)
    async with httpx.AsyncClient(timeout=5.0) as client:
        before = await _stats(client, analytics_url)
        await callback.async_post_call_success_hook(
            data={
                "model": "chatgpt/gpt-5.4-mini",
                "input": f"buffer smoke marker {marker}",
                "metadata": {
                    "request_id": marker,
                    "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
                },
            },
            user_api_key_dict=None,
            response={
                "id": marker,
                "usage": {
                    "prompt_tokens": 111,
                    "completion_tokens": 22,
                    "total_tokens": 133,
                    "prompt_tokens_details": {"cached_tokens": 11},
                    "completion_tokens_details": {"reasoning_tokens": 3},
                },
                "_hidden_params": {"response_cost": "0.00123000"},
            },
        )
        flushed = await callback.flush_analytics(timeout_seconds=5.0)
        after = await _stats(client, analytics_url)
        detail_response = await client.get(
            f"{analytics_url.rstrip('/')}/records/compression/{marker}"
        )

    stats = callback.analytics_buffer_stats()
    await callback.aclose()

    if not flushed:
        raise SystemExit("analytics buffer did not flush")
    if after["requests"] <= before["requests"]:
        raise SystemExit("analytics backend request count did not increase")
    if stats["delivered"] < 1:
        raise SystemExit(f"analytics buffer did not report delivery: {stats}")
    detail_response.raise_for_status()
    detail = detail_response.json()
    provider_calls = detail.get("provider_calls") or []
    if not provider_calls or provider_calls[0].get("cost_total") != "0.00123000":
        raise SystemExit(
            "analytics callback did not store normalized response cost: "
            f"{provider_calls}"
        )

    print(
        "litellm_buffer_smoke=ok "
        f"marker={marker} "
        f"requests_before={before['requests']} "
        f"requests_after={after['requests']} "
        f"submitted={stats['submitted']} "
        f"delivered={stats['delivered']} "
        f"cost_total={provider_calls[0].get('cost_total')} "
        f"dropped_full={stats['dropped_full']} "
        f"failed={stats['failed']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
