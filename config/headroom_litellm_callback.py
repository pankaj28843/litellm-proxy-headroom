from __future__ import annotations

from litellm_proxy_headroom.analytics.adapters.litellm.callback import (
    HeadroomAnalyticsCallback,
)


class HeadroomCallback(HeadroomAnalyticsCallback):
    """Named class for direct imports and local smoke scripts."""


headroom_callback = HeadroomCallback()


__all__ = ["HeadroomCallback", "headroom_callback"]
