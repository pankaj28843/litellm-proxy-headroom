from __future__ import annotations

from typing import Any

from headroom.compress import CompressConfig, compress
from headroom.integrations.litellm_callback import (
    HeadroomCallback as _HeadroomLiteLLMCallback,
)

_SAVINGS_PROFILE = "agent-90"
_DEFAULT_MODEL = "claude-sonnet-4-5-20250929"
_callback: HeadroomCallback | None = None


def _callback_instance() -> HeadroomCallback:
    global _callback
    if _callback is None:
        _callback = HeadroomCallback()
    return _callback


class HeadroomCallback(_HeadroomLiteLLMCallback):
    """LiteLLM class-loading shim for Headroom's v0.27.0 callback.

    LiteLLM proxy loads config-local callbacks as class objects in this setup.
    Headroom's callback is an instance-based CustomLogger, so the static hook
    methods below delegate to one lazy instance. The only local behavior change
    is selecting Headroom's built-in agent-90 savings profile for local
    compression when no Headroom Cloud API key is configured.
    """

    async def _local_compress(
        self,
        messages: list[dict[str, Any]],
        model: str,
    ) -> dict[str, Any]:
        result = compress(
            messages=messages,
            model=model or _DEFAULT_MODEL,
            model_limit=self._model_limit,
            hooks=self._hooks,
            config=CompressConfig(savings_profile=_SAVINGS_PROFILE),
        )
        return {
            "messages": result.messages,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "tokens_saved": result.tokens_saved,
            "compression_ratio": result.compression_ratio,
            "transforms_applied": result.transforms_applied,
            "savings_profile": _SAVINGS_PROFILE,
        }

    @staticmethod
    async def async_pre_call_hook(
        user_api_key: str,
        data: dict[str, Any],
        call_type: str,
    ) -> dict[str, Any]:
        return await _callback_instance().async_pre_call_hook(
            user_api_key=user_api_key,
            data=data,
            call_type=call_type,
        )

    @staticmethod
    async def async_success_handler(
        kwargs: dict[str, Any],
        response: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        await _callback_instance().async_success_handler(
            kwargs=kwargs,
            response=response,
            start_time=start_time,
            end_time=end_time,
        )

    @staticmethod
    async def async_failure_handler(
        kwargs: dict[str, Any],
        response: Any,
        start_time: Any,
        end_time: Any,
    ) -> None:
        await _callback_instance().async_failure_handler(
            kwargs=kwargs,
            response=response,
            start_time=start_time,
            end_time=end_time,
        )

    @staticmethod
    async def async_post_call_success_hook(
        data: dict[str, Any],
        user_api_key_dict: Any,
        response: Any,
    ) -> None:
        return None

    @staticmethod
    async def async_post_call_failure_hook(
        request_data: dict[str, Any],
        original_exception: Exception,
        user_api_key_dict: Any,
        traceback_str: str | None = None,
    ) -> None:
        return None


__all__ = ["HeadroomCallback"]
