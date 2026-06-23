from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ..otel.setup import configure_otel_from_env, instrument_analytics_app
from ..postgres.session import create_analytics_engine, create_session_factory
from .mcp import create_analytics_mcp_server
from .routes import router

DASHBOARD_STATIC_DIR = Path(__file__).with_name("static").joinpath("dashboard")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    engine = create_analytics_engine()
    app.state.analytics_engine = engine
    app.state.analytics_session_factory = create_session_factory(engine)
    try:
        async with AsyncExitStack() as stack:
            mcp_app = getattr(app.state, "analytics_mcp_app", None)
            if mcp_app is not None:
                await stack.enter_async_context(
                    mcp_app.router.lifespan_context(mcp_app)
                )
            yield
    finally:
        await engine.dispose()


def create_app() -> FastAPI:
    configure_otel_from_env()
    app = FastAPI(
        title="LiteLLM Compression Analytics",
        version="0.1.0",
        lifespan=lifespan,
    )
    mcp = create_analytics_mcp_server(lambda: app.state.analytics_session_factory)
    mcp_app = mcp.http_app(path="/")
    app.state.analytics_mcp_server = mcp
    app.state.analytics_mcp_app = mcp_app
    app.include_router(router)
    app.mount(
        "/dashboard/static",
        StaticFiles(directory=DASHBOARD_STATIC_DIR),
        name="dashboard_static",
    )
    app.mount("/mcp", mcp_app)
    instrument_analytics_app(app)
    return app


app = create_app()
