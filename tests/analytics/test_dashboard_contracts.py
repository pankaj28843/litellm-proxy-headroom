from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from litellm_proxy_headroom.analytics.adapters.api import routes_dashboard
from litellm_proxy_headroom.analytics.adapters.api.dashboard_query import (
    DashboardQuery,
    _date_window,
    get_dashboard_query,
)
from litellm_proxy_headroom.analytics.adapters.api.dashboard_view import (
    BREAKDOWN_GROUPS,
    build_dashboard_context,
)
from litellm_proxy_headroom.analytics.adapters.api.deps import get_session
from litellm_proxy_headroom.analytics.application.dashboard_schemas import (
    CacheDashboardStats,
    CostDashboardStats,
    DashboardStats,
    LatencyDistribution,
    ProviderCacheDashboardStats,
    ProviderEstimateDelta,
    SavingsDistribution,
    UsefulnessStatus,
)
from litellm_proxy_headroom.analytics.application.query_filters import (
    AnalyticsFilters,
    DataScope,
)
from litellm_proxy_headroom.analytics.application.read_models import (
    CompressionRecordPage,
    CompressionRecordSummary,
    StatsBreakdown,
    StatsBreakdownRow,
)
from litellm_proxy_headroom.analytics.application.simulation_schemas import (
    SimulationRunPage,
    SimulationRunSummary,
)


def test_dashboard_date_presets_include_15m_window() -> None:
    generated_at = datetime(2026, 6, 23, 12, 0, tzinfo=UTC)

    started_from, started_to = _date_window(
        "15m",
        generated_at=generated_at,
        started_from=None,
        started_to=None,
    )

    assert started_from == generated_at - timedelta(minutes=15)
    assert started_to == generated_at


def test_dashboard_query_custom_range_and_filters_are_preserved() -> None:
    query = asyncio.run(
        get_dashboard_query(
            preset="24h",
            started_from="2026-06-23T10:00:00+00:00",
            started_to="2026-06-23T11:00:00+00:00",
            provider="  ",
            model="gpt-test",
            strategy="compact",
            tenant_id="tenant-a",
            team_id="team-b",
            status="succeeded",
            negative_savings="false",
            data_scope="test",
            live=False,
            paused=True,
        )
    )

    query_values = parse_qs(query.query_string(), keep_blank_values=True)

    assert query.preset == "custom"
    assert query.filters.started_from == datetime(2026, 6, 23, 10, tzinfo=UTC)
    assert query.filters.started_to == datetime(2026, 6, 23, 11, tzinfo=UTC)
    assert query.filters.provider is None
    assert query.filters.model == "gpt-test"
    assert query.filters.strategy == "compact"
    assert query.filters.tenant_id == "tenant-a"
    assert query.filters.team_id == "team-b"
    assert query.filters.status == "succeeded"
    assert query.filters.negative_savings is False
    assert query.filters.data_scope == "test"
    assert query_values["preset"] == ["custom"]
    assert query_values["from"] == ["2026-06-23T10:00:00+00:00"]
    assert query_values["to"] == ["2026-06-23T11:00:00+00:00"]
    assert query_values["model"] == ["gpt-test"]
    assert query_values["negative_savings"] == ["false"]
    assert query_values["data_scope"] == ["test"]
    assert query_values["live"] == ["false"]
    assert query_values["paused"] == ["true"]


def test_dashboard_context_contains_template_contract() -> None:
    query = _query(provider="provider-a", model="model-a", negative_savings=True)

    context = _dashboard_context(query)

    assert context["has_data"] is True
    assert context["backend_status"] == "ready"
    assert context["database_ready"] is True
    assert context["compression_ratio"] == 0.5
    assert context["stats"].usefulness.status == "unproven"
    assert (
        context["stats"].usefulness.cache_evidence_scope
        == "whole Codex turn/provider-call sequence"
    )
    assert [metric.label for metric in context["summary_metrics"]] == [
        "Combined saving",
        "Provider cache hit",
        "Raw tokens saved",
        "Estimated dollars",
        "Billing capacity",
        "Success rate",
    ]
    assert context["summary_metrics"][0].value == "62.0%"
    assert context["summary_metrics"][1].value == "30.0%"
    assert [metric.label for metric in context["activity_metrics"]] == [
        "Retrievals",
        "Cache events",
        "Provider cached",
    ]
    assert all(
        metric.label != "Provider delta" for metric in context["activity_metrics"]
    )
    assert [(chip.label, chip.value) for chip in context["filter_chips"]] == [
        ("Data", "Operational"),
        ("Provider", "provider-a"),
        ("Model", "model-a"),
        ("Savings", "Negative only"),
    ]


def test_dashboard_route_preserves_filters_in_template_context(
    dashboard_client: TestClient,
) -> None:
    response = dashboard_client.get(
        "/dashboard",
        params={
            "preset": "15m",
            "provider": "provider-a",
            "model": "model-a",
            "strategy": "compact",
            "tenant_id": "tenant-a",
            "team_id": "team-b",
            "status": "succeeded",
            "negative_savings": "true",
            "data_scope": "test",
            "paused": "true",
        },
    )

    assert response.status_code == 200
    assert response.template.name == "dashboard/index.html"
    assert response.context["query"].preset == "15m"
    assert response.context["query"].filters.provider == "provider-a"
    assert response.context["query"].filters.negative_savings is True
    assert response.context["query"].filters.data_scope == "test"
    assert "Data: Test/demo" in response.text
    assert 'name="data_scope"' in response.text
    assert "Provider: provider-a" in response.text
    assert "Primary usefulness unproven" in response.text
    assert "whole Codex turn/provider-call sequence" in response.text
    assert 'name="provider" value="provider-a"' in response.text
    assert "Resume" in response.text
    assert 'hx-trigger="every 15s"' not in response.text
    assert "SENSITIVE_RAW_PROMPT" not in response.text
    assert "SENSITIVE_RAW_RESPONSE" not in response.text
    assert "Provider delta" not in response.text
    assert "Estimated before delta" not in response.text


@pytest.mark.parametrize(
    ("path", "template_name"),
    (
        ("/dashboard/partials/live", "dashboard/partials/live_region.html"),
        ("/dashboard/partials/controls", "dashboard/partials/controls.html"),
        ("/dashboard/partials/summary", "dashboard/partials/summary.html"),
        ("/dashboard/partials/activity", "dashboard/partials/activity.html"),
        ("/dashboard/partials/breakdowns", "dashboard/partials/breakdowns.html"),
        ("/dashboard/partials/records", "dashboard/partials/records.html"),
        ("/dashboard/partials/simulations", "dashboard/partials/simulations.html"),
    ),
)
def test_dashboard_partial_routes_return_html_status(
    dashboard_client: TestClient,
    path: str,
    template_name: str,
) -> None:
    response = dashboard_client.get(path, params={"provider": "provider-a"})

    assert response.status_code == 200
    assert response.template.name == template_name
    assert response.headers["content-type"].startswith("text/html")
    assert "<!doctype html>" not in response.text


def test_dashboard_templates_do_not_reference_raw_content_fields() -> None:
    template_dir = Path(routes_dashboard.__file__).with_name("templates") / "dashboard"
    forbidden_tokens = (
        "prompt_text",
        "response_text",
        "raw_prompt",
        "raw_response",
        "original_content",
        "compressed_content",
        "original_chunk",
        "compressed_chunk",
    )

    for template_path in template_dir.rglob("*.html"):
        source = template_path.read_text()
        for token in forbidden_tokens:
            assert token not in source, f"{token} rendered by {template_path}"


@pytest.fixture
def dashboard_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(routes_dashboard.router)
    app.mount(
        "/dashboard/static",
        StaticFiles(
            directory=Path(routes_dashboard.__file__)
            .with_name("static")
            .joinpath("dashboard")
        ),
        name="dashboard_static",
    )

    async def fake_session():
        yield object()

    async def fake_dashboard_context(session, query):
        context = _dashboard_context(query)
        context["raw_prompt"] = "SENSITIVE_RAW_PROMPT"
        context["raw_response"] = "SENSITIVE_RAW_RESPONSE"
        context["original_content"] = "SENSITIVE_ORIGINAL_CONTENT"
        context["compressed_content"] = "SENSITIVE_COMPRESSED_CONTENT"
        return context

    app.dependency_overrides[get_session] = fake_session
    monkeypatch.setattr(routes_dashboard, "_dashboard_context", fake_dashboard_context)

    with TestClient(app) as client:
        yield client

    app.dependency_overrides.clear()


def _dashboard_context(query: DashboardQuery) -> dict[str, object]:
    return build_dashboard_context(
        query=query,
        stats=_stats(),
        breakdowns={
            group.key: StatsBreakdown(
                group_by=group.key,
                rows=[
                    StatsBreakdownRow(
                        value=f"{group.key}-a",
                        requests=2,
                        executions=3,
                        original_tokens=1000,
                        compressed_tokens=500,
                        tokens_saved=500,
                        negative_savings_executions=0,
                    )
                ],
            )
            for group in BREAKDOWN_GROUPS
        },
        records=CompressionRecordPage(
            total=1,
            limit=8,
            offset=0,
            items=[
                CompressionRecordSummary(
                    request_id="request-1",
                    request_key="request-key-1",
                    execution_id="execution-1",
                    attempt_number=1,
                    status="succeeded",
                    is_simulated=False,
                    started_at=datetime(2026, 6, 23, 11, 30, tzinfo=UTC),
                    tenant_id="tenant-a",
                    team_id="team-b",
                    provider="provider-a",
                    model="model-a",
                    strategy_name="compact",
                    strategy_version="v1",
                    original_tokens=1000,
                    compressed_tokens=500,
                    tokens_saved=500,
                    compression_ratio=0.5,
                    duration_ms=125,
                    provider_calls=1,
                    chunks=2,
                    retrievals=4,
                )
            ],
        ),
        simulations=SimulationRunPage(
            total=1,
            limit=6,
            offset=0,
            items=[
                SimulationRunSummary(
                    simulation_id="simulation-1",
                    simulation_key="simulation-key-1",
                    name="Replay compact",
                    status="succeeded",
                    strategy_name="compact",
                    selected_filter={"provider": "provider-a"},
                    result_count=2,
                    total_simulated_tokens_saved=250,
                    total_baseline_cost="0.02000000",
                    total_simulated_cost="0.01000000",
                    duration_ms=80,
                    started_at=datetime(2026, 6, 23, 11, 0, tzinfo=UTC),
                    ended_at=datetime(2026, 6, 23, 11, 1, tzinfo=UTC),
                    created_at=datetime(2026, 6, 23, 11, 0, tzinfo=UTC),
                )
            ],
        ),
    )


def _query(
    *,
    provider: str | None = None,
    model: str | None = None,
    strategy: str | None = None,
    tenant_id: str | None = None,
    team_id: str | None = None,
    status: str | None = None,
    negative_savings: bool | None = None,
    data_scope: DataScope = "real",
) -> DashboardQuery:
    return DashboardQuery(
        filters=AnalyticsFilters(
            started_from=datetime(2026, 6, 23, 11, 45, tzinfo=UTC),
            started_to=datetime(2026, 6, 23, 12, 0, tzinfo=UTC),
            provider=provider,
            model=model,
            strategy=strategy,
            tenant_id=tenant_id,
            team_id=team_id,
            status=status,
            negative_savings=negative_savings,
            data_scope=data_scope,
        ),
        preset="15m",
        explicit_from=None,
        explicit_to=None,
        live=True,
        paused=False,
        generated_at=datetime(2026, 6, 23, 12, 0, tzinfo=UTC),
    )


def _stats() -> DashboardStats:
    return DashboardStats(
        requests=2,
        executions=3,
        provider_calls=3,
        chunks=6,
        retrievals=12,
        retrievals_per_chunk=2.0,
        original_tokens=1000,
        compressed_tokens=500,
        tokens_saved=500,
        savings_percent=50.0,
        negative_savings_executions=0,
        failed_executions=0,
        success_rate=100.0,
        savings_distribution=SavingsDistribution(
            min_tokens_saved=50,
            p50_tokens_saved=150.0,
            p90_tokens_saved=300.0,
            max_tokens_saved=500,
            min_compression_ratio=0.45,
            p50_compression_ratio=0.5,
            p90_compression_ratio=0.6,
            max_compression_ratio=0.75,
        ),
        latency_distribution=LatencyDistribution(
            avg_compression_duration_ms=120.0,
            p50_compression_duration_ms=110.0,
            p90_compression_duration_ms=170.0,
            avg_end_to_end_request_latency_ms=320.0,
            p50_end_to_end_request_latency_ms=300.0,
            p90_end_to_end_request_latency_ms=400.0,
        ),
        provider_estimate_delta=ProviderEstimateDelta(
            provider_reported_input_tokens=520,
            provider_reported_total_tokens=640,
            estimated_before_input_tokens=1000,
            estimated_after_input_tokens=500,
            estimated_before_provider_input_delta=480,
            estimated_after_provider_input_delta=-20,
        ),
        provider_cache=ProviderCacheDashboardStats(
            provider_reported_input_tokens=520,
            provider_reported_cached_input_tokens=156,
            provider_reported_uncached_input_tokens=364,
            provider_cache_hit_percent=30.0,
            cached_input_cost_multiplier="0.10",
            billing_equivalent_input_tokens=379.6,
            billing_equivalent_tokens_saved=620.4,
            billing_equivalent_savings_percent=62.04,
            raw_token_capacity_multiplier=1.923077,
            billing_equivalent_capacity_multiplier=2.634352,
        ),
        cost=CostDashboardStats(
            measured_provider_cost_total="0.01000000",
            estimated_baseline_cost_total="0.02000000",
            estimated_cost_savings="0.01000000",
            cost_increase_provider_calls=0,
        ),
        cache=CacheDashboardStats(
            cache_read_events=4,
            cache_write_events=2,
            cache_hit_events=3,
            cache_tokens_read=200,
            cache_tokens_written=100,
        ),
        usefulness=UsefulnessStatus(
            status="unproven",
            label="Primary usefulness unproven",
            detail=(
                "These are one-sided operational aggregates. Primary usefulness "
                "requires a passed direct-vs-proxy Codex CLI proof with provider "
                "usage/cost and cache hit measured across the whole turn sequence."
            ),
            cache_evidence_scope="whole Codex turn/provider-call sequence",
        ),
    )
