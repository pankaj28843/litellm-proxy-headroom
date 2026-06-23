import asyncio
import importlib.util
from datetime import UTC, datetime
from pathlib import Path


def load_shim_module():
    spec = importlib.util.spec_from_file_location(
        "headroom_litellm_callback",
        Path("config/headroom_litellm_callback.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_shim_callback() -> type:
    return load_shim_module().HeadroomCallback


def test_headroom_callback_shim_exposes_litellm_post_call_hooks() -> None:
    callback = load_shim_callback()(api_key=None)

    assert hasattr(callback, "async_pre_call_hook")
    assert hasattr(callback, "async_success_handler")
    assert hasattr(callback, "async_failure_handler")
    assert hasattr(callback, "async_post_call_success_hook")
    assert hasattr(callback, "async_post_call_failure_hook")


def test_headroom_callback_shim_post_call_hooks_are_noops() -> None:
    callback = load_shim_callback()(api_key=None)

    success_result = asyncio.run(
        callback.async_post_call_success_hook({}, None, {"choices": []})
    )
    failure_result = asyncio.run(
        callback.async_post_call_failure_hook({}, RuntimeError("boom"), None)
    )

    assert success_result is None
    assert failure_result is None


def test_headroom_callback_shim_exports_litellm_config_instance() -> None:
    module = load_shim_module()

    assert isinstance(module.headroom_callback, module.HeadroomCallback)
    assert hasattr(module.headroom_callback, "async_pre_call_hook")
    assert "headroom_callback" in module.__all__


def test_local_compression_uses_agent_90_profile(monkeypatch) -> None:
    from types import SimpleNamespace

    module = load_shim_module()

    captured = {}

    def fake_compress(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            messages=kwargs["messages"],
            tokens_before=1000,
            tokens_after=100,
            tokens_saved=900,
            compression_ratio=0.1,
            transforms_applied=["fake"],
        )

    from litellm_proxy_headroom.analytics.adapters.litellm import callback

    monkeypatch.setattr(callback, "compress", fake_compress)

    result = module.HeadroomCallback()._local_compress(
        [{"role": "user", "content": "large prompt"}],
        "chatgpt",
    )

    assert captured["config"].savings_profile == "agent-90"
    assert result["savings_profile"] == "agent-90"
    assert result["tokens_saved"] == 900


def test_success_handler_captures_responses_input_without_pre_call(monkeypatch) -> None:
    callback = load_shim_callback()(api_key=None)
    posted = {}

    async def fake_post_capture(capture, **kwargs):
        posted["capture"] = capture
        posted["kwargs"] = kwargs

    monkeypatch.setattr(callback, "_post_capture", fake_post_capture)

    asyncio.run(
        callback.async_success_handler(
            kwargs={
                "model": "gpt-5.4-mini",
                "input": "responses payload",
                "metadata": {"request_id": "responses-fallback"},
            },
            response={
                "id": "response-id",
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            start_time=datetime.now(UTC),
            end_time=datetime.now(UTC),
        )
    )

    capture = posted["capture"]
    assert capture.request_key == "responses-fallback"
    assert capture.model == "gpt-5.4-mini"
    assert capture.incoming_route == "/v1/responses"
    assert posted["kwargs"]["status"] == "succeeded"


def test_post_capture_bounds_long_responses_ids() -> None:
    callback = load_shim_callback()(api_key=None)
    submitted = {}

    class FakeBuffer:
        def submit_nowait(self, command):
            submitted["command"] = command
            return True

    callback._analytics_buffer = FakeBuffer()
    capture = callback._post_call_capture(
        {
            "model": "gpt-5.4-mini",
            "input": "responses payload",
            "metadata": {"request_id": "responses-long-id"},
        }
    )

    assert capture is not None

    asyncio.run(
        callback._post_capture(
            capture,
            response={
                "id": "resp_" + ("x" * 320),
                "usage": {
                    "input_tokens": 12,
                    "output_tokens": 3,
                    "total_tokens": 15,
                },
            },
            status="succeeded",
            duration_ms=10,
        )
    )

    command = submitted["command"]
    assert len(command.event.event_key) <= 255
    assert len(command.provider_calls[0].provider_call_key) <= 255
    assert len(command.provider_calls[0].provider_response_id) <= 255
    assert command.request.incoming_route == "/v1/responses"
