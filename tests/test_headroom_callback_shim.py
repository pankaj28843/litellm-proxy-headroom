import asyncio
import importlib.util
from pathlib import Path


def load_shim_callback() -> type:
    spec = importlib.util.spec_from_file_location(
        "headroom_litellm_callback",
        Path("config/headroom_litellm_callback.py"),
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.HeadroomCallback


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


def test_headroom_callback_shim_post_call_hooks_work_when_loaded_as_class() -> None:
    callback_cls = load_shim_callback()

    success_result = asyncio.run(
        callback_cls.async_post_call_success_hook({}, None, {"choices": []})
    )
    failure_result = asyncio.run(
        callback_cls.async_post_call_failure_hook({}, RuntimeError("boom"), None)
    )

    assert success_result is None
    assert failure_result is None
