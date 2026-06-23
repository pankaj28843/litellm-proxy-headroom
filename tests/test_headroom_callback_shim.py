import asyncio
import importlib.util
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
