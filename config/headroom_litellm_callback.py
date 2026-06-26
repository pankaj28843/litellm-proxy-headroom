from __future__ import annotations

from typing import Any

from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
    HeadroomAnalyticsCallback,
)


class HeadroomCallback(HeadroomAnalyticsCallback):
    """Named class for direct imports and local smoke scripts."""

    async def async_pre_call_hook(
        self,
        user_api_key: Any = None,
        data: dict[str, Any] | None = None,
        call_type: str = "",
        *,
        user_api_key_dict: Any = None,
        cache: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        return await super().async_pre_call_hook(
            user_api_key=user_api_key,
            data=data,
            call_type=call_type,
            user_api_key_dict=user_api_key_dict,
            cache=cache,
            **kwargs,
        )


headroom_callback = HeadroomCallback()


__all__ = ["HeadroomCallback", "headroom_callback"]
