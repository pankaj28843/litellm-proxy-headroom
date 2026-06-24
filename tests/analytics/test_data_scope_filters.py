from __future__ import annotations

from sqlalchemy.dialects import postgresql

from litellm_proxy_headroom.analytics.adapters.postgres.query_filters import (
    matching_execution_rows,
)
from litellm_proxy_headroom.analytics.adapters.postgres.simulation_selection import (
    filters_from_selection,
)
from litellm_proxy_headroom.analytics.application.query_filters import AnalyticsFilters


def test_default_data_scope_excludes_smoke_and_demo_rows() -> None:
    sql = _compiled_sql(AnalyticsFilters())

    assert "NOT (" in sql
    assert "request_metadata ->> 'smoke'" in sql
    assert "request_metadata ->> 'analytics_data_scope'" in sql
    assert "'test'" in sql


def test_test_data_scope_selects_only_smoke_and_demo_rows() -> None:
    sql = _compiled_sql(AnalyticsFilters(data_scope="test"))

    assert "NOT (" not in sql
    assert "request_metadata ->> 'smoke'" in sql
    assert "request_metadata ->> 'analytics_data_scope'" in sql


def test_all_data_scope_does_not_apply_test_data_filter() -> None:
    sql = _compiled_sql(AnalyticsFilters(data_scope="all"))

    assert "request_metadata ->>" not in sql


def test_simulation_selection_preserves_explicit_data_scope() -> None:
    assert filters_from_selection({"data_scope": "test"}).data_scope == "test"
    assert filters_from_selection({"data_scope": "all"}).data_scope == "all"
    assert filters_from_selection({"data_scope": "unknown"}).data_scope == "real"


def _compiled_sql(filters: AnalyticsFilters) -> str:
    return str(
        matching_execution_rows(filters).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
