from typing import Any

from headroom.integrations.asgi import CompressionMiddleware
from litellm.proxy.proxy_server import app as litellm_proxy_app


def _has_middleware(asgi_app: Any, middleware_cls: type[Any]) -> bool:
    return any(
        middleware.cls is middleware_cls for middleware in asgi_app.user_middleware
    )


app = litellm_proxy_app

if not _has_middleware(app, CompressionMiddleware):
    app.add_middleware(CompressionMiddleware)
