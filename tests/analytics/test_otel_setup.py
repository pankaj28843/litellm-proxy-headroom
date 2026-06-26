from __future__ import annotations

import re
from typing import Any

from fastapi import FastAPI

from litellm_proxy_headroom.analytics.adapters.otel import setup


def test_instrument_analytics_app_excludes_owned_probe_and_static_routes(
    monkeypatch,
) -> None:
    for name in (
        "HEADROOM_ANALYTICS_OTEL_ENABLED",
        "LITELLM_PROXY_ANALYTICS_OTEL_EXCLUDED_URLS",
        "OTEL_PYTHON_FASTAPI_EXCLUDED_URLS",
        "OTEL_PYTHON_EXCLUDED_URLS",
    ):
        monkeypatch.delenv(name, raising=False)
    calls: list[dict[str, Any]] = []

    def fake_instrument_app(app: FastAPI, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        setup.FastAPIInstrumentor,
        "instrument_app",
        staticmethod(fake_instrument_app),
    )

    setup.instrument_analytics_app(FastAPI())

    assert len(calls) == 1
    excluded_urls = calls[0]["excluded_urls"]
    excluded = re.compile("|".join(excluded_urls.split(",")))

    assert calls[0]["exclude_spans"] == ["receive", "send"]
    for url in (
        "http://testserver/health",
        "http://testserver/ready",
        "http://testserver/live",
        "http://testserver/liveness",
        "http://testserver/readiness",
        "http://testserver/metrics",
        "http://testserver/favicon.ico",
        "http://testserver/docs",
        "http://testserver/redoc",
        "http://testserver/openapi.json",
        "http://testserver/dashboard/static/dashboard.css",
        "http://testserver/dashboard/partials/live",
        "http://testserver/dashboard/partials/live?paused=false&live=true",
    ):
        assert excluded.search(url), url

    for url in (
        "http://testserver/ingest/compression",
        "http://testserver/chunks/abc123",
        "http://testserver/headroom/ccr/store",
        "http://testserver/mcp/",
        "http://testserver/dashboard",
        "http://testserver/dashboard/partials/summary",
    ):
        assert not excluded.search(url), url


def test_instrument_analytics_app_uses_standard_fastapi_exclusion_override(
    monkeypatch,
) -> None:
    monkeypatch.delenv("HEADROOM_ANALYTICS_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("LITELLM_PROXY_ANALYTICS_OTEL_EXCLUDED_URLS", raising=False)
    monkeypatch.delenv("OTEL_PYTHON_EXCLUDED_URLS", raising=False)
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/internal,/probe")
    calls: list[dict[str, Any]] = []

    def fake_instrument_app(app: FastAPI, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        setup.FastAPIInstrumentor,
        "instrument_app",
        staticmethod(fake_instrument_app),
    )

    setup.instrument_analytics_app(FastAPI())

    assert calls[0]["excluded_urls"] == "/internal,/probe"


def test_instrument_analytics_app_local_exclusion_override_wins(monkeypatch) -> None:
    monkeypatch.delenv("HEADROOM_ANALYTICS_OTEL_ENABLED", raising=False)
    monkeypatch.setenv("LITELLM_PROXY_ANALYTICS_OTEL_EXCLUDED_URLS", "/only-local")
    monkeypatch.setenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "/standard")
    calls: list[dict[str, Any]] = []

    def fake_instrument_app(app: FastAPI, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        setup.FastAPIInstrumentor,
        "instrument_app",
        staticmethod(fake_instrument_app),
    )

    setup.instrument_analytics_app(FastAPI())

    assert calls[0]["excluded_urls"] == "/only-local"


def test_blank_local_exclusion_override_keeps_default_exclusions(monkeypatch) -> None:
    monkeypatch.delenv("HEADROOM_ANALYTICS_OTEL_ENABLED", raising=False)
    monkeypatch.setenv("LITELLM_PROXY_ANALYTICS_OTEL_EXCLUDED_URLS", "")
    monkeypatch.delenv("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", raising=False)
    monkeypatch.delenv("OTEL_PYTHON_EXCLUDED_URLS", raising=False)
    calls: list[dict[str, Any]] = []

    def fake_instrument_app(app: FastAPI, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        setup.FastAPIInstrumentor,
        "instrument_app",
        staticmethod(fake_instrument_app),
    )

    setup.instrument_analytics_app(FastAPI())

    assert calls[0]["excluded_urls"] == setup.DEFAULT_ANALYTICS_OTEL_EXCLUDED_URLS


def test_instrument_analytics_app_respects_disabled_otel(monkeypatch) -> None:
    monkeypatch.setenv("HEADROOM_ANALYTICS_OTEL_ENABLED", "false")
    calls: list[dict[str, Any]] = []

    def fake_instrument_app(app: FastAPI, **kwargs: Any) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(
        setup.FastAPIInstrumentor,
        "instrument_app",
        staticmethod(fake_instrument_app),
    )

    setup.instrument_analytics_app(FastAPI())

    assert calls == []
