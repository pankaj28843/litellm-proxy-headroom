import httpx
import pytest

from litellm_proxy_headroom.analytics.adapters.litellm import http_client
from litellm_proxy_headroom.analytics.adapters.litellm.http_client import (
    AnalyticsHttpClient,
    AnalyticsHttpClientConfig,
)
from litellm_proxy_headroom.analytics.application.commands import (
    CompressionActivityIngestCommand,
    CompressionConfigCommand,
    CompressionExecutionCommand,
    CompressionRequestCommand,
    IngestionEventCommand,
    TraceContextCommand,
)


def _command() -> CompressionActivityIngestCommand:
    return CompressionActivityIngestCommand(
        event=IngestionEventCommand(
            source="pytest",
            event_type="compression_result",
            event_key="http-client-failure",
        ),
        request=CompressionRequestCommand(
            request_key="http-client-failure-request",
            source_system="pytest",
        ),
        config=CompressionConfigCommand(
            config_hash="http-client-failure-config",
            strategy_name="pytest-strategy",
        ),
        execution=CompressionExecutionCommand(
            attempt_number=1,
            status="succeeded",
        ),
    )


@pytest.mark.anyio
async def test_http_client_returns_false_on_transport_failure(monkeypatch) -> None:
    class RaisingAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "http://analytics.invalid")
            raise httpx.ConnectError("backend down", request=request)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(http_client.httpx, "AsyncClient", RaisingAsyncClient)
    client = AnalyticsHttpClient(
        AnalyticsHttpClientConfig(base_url="http://analytics.invalid")
    )

    assert not await client.post_compression_activity(_command())
    await client.aclose()


@pytest.mark.anyio
async def test_http_client_returns_false_on_server_error(monkeypatch) -> None:
    class ErrorAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def post(self, *args, **kwargs):
            request = httpx.Request("POST", "http://analytics.invalid")
            return httpx.Response(503, request=request)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(http_client.httpx, "AsyncClient", ErrorAsyncClient)
    client = AnalyticsHttpClient(
        AnalyticsHttpClientConfig(base_url="http://analytics.invalid")
    )

    assert not await client.post_compression_activity(_command())
    await client.aclose()


def test_http_client_config_is_disabled_without_backend_url(monkeypatch) -> None:
    monkeypatch.delenv("HEADROOM_ANALYTICS_URL", raising=False)

    assert AnalyticsHttpClientConfig.from_env() is None


@pytest.mark.anyio
async def test_http_client_propagates_w3c_trace_headers(monkeypatch) -> None:
    captured = {}

    class CapturingAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def post(self, *args, **kwargs):
            captured.update(kwargs)
            request = httpx.Request("POST", "http://analytics.local")
            return httpx.Response(200, request=request, json={"ok": True})

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(http_client.httpx, "AsyncClient", CapturingAsyncClient)
    client = AnalyticsHttpClient(
        AnalyticsHttpClientConfig(base_url="http://analytics.local")
    )
    command = _command().model_copy(
        update={
            "event": IngestionEventCommand(
                source="pytest",
                event_type="compression_result",
                event_key="http-client-trace",
                trace=TraceContextCommand(
                    traceparent=(
                        "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01"
                    ),
                    tracestate="vendor=value",
                ),
            )
        }
    )

    assert await client.post_compression_activity(command)
    await client.aclose()

    assert captured["headers"] == {
        "traceparent": "00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
        "tracestate": "vendor=value",
    }
