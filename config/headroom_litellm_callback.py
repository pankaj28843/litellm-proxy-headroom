from typing import Any

from headroom.integrations.litellm_callback import HeadroomCallback as _HeadroomCallback

_callback: _HeadroomCallback | None = None


def _callback_instance() -> _HeadroomCallback:
    global _callback
    if _callback is None:
        _callback = _HeadroomCallback()
    return _callback


class HeadroomCallback(_HeadroomCallback):
    """Compatibility shim for LiteLLM proxy callback loading.

    LiteLLM proxy loads config-local callback paths as class objects in this
    setup. Headroom's callback implements older instance handler names, so the
    class-callable methods below keep LiteLLM's proxy hook surface from failing
    while still delegating Headroom's original handlers through a lazy instance.
    """

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
