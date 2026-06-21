import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack, asynccontextmanager
from os import environ
from typing import Any

from headroom.ccr.mcp_server import HeadroomMCPServer
from headroom.integrations.asgi import CompressionMiddleware
from headroom.proxy.server import create_app_from_env as create_headroom_app
from litellm.proxy.proxy_server import app as litellm_proxy_app
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send


def _has_middleware(asgi_app: Any, middleware_cls: type[Any]) -> bool:
    return any(
        middleware.cls is middleware_cls for middleware in asgi_app.user_middleware
    )


def _has_route(asgi_app: Any, path: str) -> bool:
    return any(getattr(route, "path", None) == path for route in asgi_app.routes)


def _mount_before_dynamic_mcp(
    asgi_app: Any, path: str, mounted_app: Any, name: str
) -> None:
    if _has_route(asgi_app, path):
        return

    routes = asgi_app.router.routes
    insert_at = next(
        (
            index
            for index, route in enumerate(routes)
            if getattr(route, "path", None) == "/{mcp_server_name}/mcp"
        ),
        len(routes),
    )
    routes.insert(insert_at, Mount(path, app=mounted_app, name=name))


def _headroom_mcp_proxy_url() -> str:
    default_port = environ.get("LITELLM_HEADROOM_PORT", "4000")
    return environ.get(
        "HEADROOM_MCP_PROXY_URL",
        f"http://127.0.0.1:{default_port}/headroom",
    )


class _HeadroomMCPStreamableHTTPApp:
    def __init__(self, proxy_url_factory: Callable[[], str]) -> None:
        self.proxy_url_factory = proxy_url_factory
        self.session_manager: StreamableHTTPSessionManager | None = None
        self._stack: AsyncExitStack | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self.session_manager is not None:
            return

        async with self._lock:
            if self.session_manager is not None:
                return

            headroom_mcp_server = HeadroomMCPServer(
                proxy_url=self.proxy_url_factory(),
                check_proxy=False,
            )
            session_manager = StreamableHTTPSessionManager(
                headroom_mcp_server.server,
                session_idle_timeout=1800,
            )
            stack = AsyncExitStack()
            await stack.enter_async_context(session_manager.run())
            self._stack = stack
            self.session_manager = session_manager

    async def stop(self) -> None:
        async with self._lock:
            stack = self._stack
            self._stack = None
            self.session_manager = None

        if stack is not None:
            await stack.aclose()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.session_manager is None:
            response = JSONResponse(
                {"error": "Headroom MCP server is not started"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        await self.session_manager.handle_request(scope, receive, send)


app = litellm_proxy_app
_headroom_mcp_app = _HeadroomMCPStreamableHTTPApp(_headroom_mcp_proxy_url)

if not _has_middleware(app, CompressionMiddleware):
    app.add_middleware(CompressionMiddleware)

_mount_before_dynamic_mcp(app, "/headroom/mcp", _headroom_mcp_app, "headroom_mcp")
_mount_before_dynamic_mcp(app, "/headroom", create_headroom_app(), "headroom")
_litellm_lifespan_context = app.router.lifespan_context


@app.middleware("http")
async def _normalize_headroom_mcp_path(request: Any, call_next: Any) -> Any:
    if request.scope.get("path") == "/headroom/mcp":
        request.scope["path"] = "/headroom/mcp/"
        request.scope["raw_path"] = b"/headroom/mcp/"

    return await call_next(request)


@asynccontextmanager
async def _headroom_lifespan(asgi_app: Any) -> Any:
    async with _litellm_lifespan_context(asgi_app):
        await _headroom_mcp_app.start()
        try:
            yield
        finally:
            await _headroom_mcp_app.stop()


app.router.lifespan_context = _headroom_lifespan
