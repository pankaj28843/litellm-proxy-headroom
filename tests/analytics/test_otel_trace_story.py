from __future__ import annotations

import json
from decimal import Decimal

from litellm_proxy_headroom.analytics.adapters.otel.trace_story import (
    compression_trace_story,
    record_current_compression_story,
)
from litellm_proxy_headroom.analytics.application.commands import (
    CacheActivityCommand,
    CompressionActivityIngestCommand,
    CompressionChunkCommand,
    CompressionConfigCommand,
    CompressionExecutionCommand,
    CompressionRequestCommand,
    CostCalculationCommand,
    IngestionEventCommand,
    ProviderCallCommand,
    TokenUsageBreakdownCommand,
    TraceContextCommand,
)


def test_compression_trace_story_maps_safe_metadata_and_ordered_events() -> None:
    command = _representative_command()

    story = compression_trace_story(command, success=True, latency_ms=17)

    assert story.attributes["session.id"] == "codex-run-123"
    assert story.attributes["gen_ai.conversation.id"] == "codex-run-123"
    assert story.attributes["litellm.proxy.session.client"] == "codex"
    assert (
        story.attributes["litellm.proxy.headroom.request.metadata.integration"]
        == "litellm-responses"
    )
    assert (
        story.attributes[
            "litellm.proxy.headroom.request.metadata.redacted_values.count"
        ]
        == 1
    )
    assert story.attributes[
        "litellm.proxy.headroom.execution.transforms.deployment_payload"
        ".mutable_output.hash"
    ]
    assert (
        story.attributes[
            "litellm.proxy.provider.response.metadata.hidden_params"
            ".redacted_values.count"
        ]
        == 1
    )
    assert story.attributes["litellm.proxy.compression.strategy"] == "agent-90"
    assert story.attributes["litellm.proxy.compression.tokens_saved"] == 450
    assert story.attributes["litellm.proxy.compression.transforms.applied"] == (
        "smart-crusher",
        "cache-aligner",
    )
    assert story.attributes["litellm.proxy.compression.ccr.hashes"] == (
        "headroom-ccr-1",
        "stored-ccr-1",
    )
    assert story.attributes["litellm.proxy.provider.cost.total"] == 0.0123
    assert story.attributes["gen_ai.provider.name"] == "openai"
    assert story.attributes["gen_ai.request.model"] == "gpt-5.4-mini"
    assert story.attributes["gen_ai.response.id"] == "resp_123"
    assert story.attributes["llm.token_count.prompt"] == 1000
    assert story.attributes["llm.token_count.completion"] == 150
    assert story.attributes["llm.token_count.total"] == 1150
    assert story.attributes["llm.token_count.prompt_details.cache_read"] == 250
    assert story.attributes["llm.token_count.prompt_details.cache_write"] == 75
    assert story.attributes["llm.token_count.completion_details.reasoning"] == 40
    assert story.attributes["litellm.proxy.cache.provider.hit"] is True
    assert story.attributes["litellm.proxy.cache.headroom_ccr.tokens_written"] == 750

    assert [event.name for event in story.events] == [
        "compression.request.captured",
        "headroom.metadata.captured",
        "compression.transforms.applied",
        "compression.ccr.stored",
        "provider.usage.reported",
        "provider.cache.reported",
        "economics.verdict",
    ]
    assert story.events[1].attributes["sections"] == (
        "request",
        "transforms",
        "chunks",
        "provider_response",
        "provider_usage",
    )
    economics = story.events[-1].attributes
    assert economics["usefulness_verdict"] == (
        "unproven_without_direct_vs_proxy_aggregate"
    )
    assert economics["measured_cost_available"] is True
    assert economics["savings_claim_allowed"] is False


def test_compression_trace_story_does_not_leak_raw_content_or_secrets() -> None:
    story = compression_trace_story(_representative_command())

    serialized = json.dumps(
        {
            "attributes": story.attributes,
            "events": [
                {"name": event.name, "attributes": event.attributes}
                for event in story.events
            ],
        },
        sort_keys=True,
    )

    assert "raw prompt content" not in serialized
    assert "raw compressed content" not in serialized
    assert "raw original content" not in serialized
    assert "secret-api-key" not in serialized
    assert "secret-token" not in serialized
    assert "authorization" not in serialized.lower()
    assert "raw_usage.secret" not in serialized


def test_record_current_compression_story_applies_attributes_and_events(
    monkeypatch,
) -> None:
    span = _RecordingSpan()
    monkeypatch.setattr(
        "litellm_proxy_headroom.analytics.adapters.otel.trace_story.trace.get_current_span",
        lambda: span,
    )

    record_current_compression_story(
        _representative_command(),
        success=True,
        latency_ms=9,
    )

    assert span.attributes["litellm.proxy.analytics.success"] is True
    assert span.attributes["litellm.proxy.analytics.persistence.latency_ms"] == 9
    assert "provider.usage.reported" in [name for name, _ in span.events]
    assert "economics.verdict" in [name for name, _ in span.events]
    assert span.attribute_calls[-2:] == [
        ("session.id", "codex-run-123"),
        ("gen_ai.conversation.id", "codex-run-123"),
    ]


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes = {}
        self.attribute_calls = []
        self.events = []

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, key, value) -> None:
        self.attributes[key] = value
        self.attribute_calls.append((key, value))

    def add_event(self, name, attributes=None) -> None:
        self.events.append((name, attributes or {}))


def _representative_command() -> CompressionActivityIngestCommand:
    trace = TraceContextCommand(
        trace_id="0123456789abcdef0123456789abcdef",
        span_id="0123456789abcdef",
        traceparent="00-0123456789abcdef0123456789abcdef-0123456789abcdef-01",
    )
    return CompressionActivityIngestCommand(
        event=IngestionEventCommand(
            source="litellm-headroom-callback",
            event_type="compression_result",
            event_key="request-1:resp_123:succeeded",
            raw_payload={
                "prompt": "raw prompt content",
                "api_key": "secret-api-key",
            },
            trace=trace,
        ),
        request=CompressionRequestCommand(
            request_key="request-1",
            source_system="litellm-proxy",
            incoming_route="/v1/responses",
            provider_hint="openai",
            model_hint="gpt-5.4-mini",
            metadata={
                "integration": "litellm-responses",
                "litellm_proxy_run_marker": "codex-run-123",
                "litellm_proxy_project": "project-a",
                "litellm_proxy_client": "codex",
                "litellm_proxy_compression_mode": "on",
                "provider_session_affinity_hash": "affinity-hash",
                "provider_session_affinity_source": "prompt_cache_key",
                "authorization": "secret-token",
            },
            trace=trace,
        ),
        config=CompressionConfigCommand(
            config_hash="config-1",
            strategy_name="agent-90",
            strategy_version="1",
            target_model="gpt-5.4-mini",
            raw_config={"raw_prompt": "raw prompt content"},
        ),
        execution=CompressionExecutionCommand(
            attempt_number=1,
            status="succeeded",
            original_tokens=1200,
            compressed_tokens=750,
            tokens_saved=450,
            compression_ratio=Decimal("0.625"),
            transforms={
                "applied": ["smart-crusher", "cache-aligner"],
                "attempted_input_tokens": 1200,
                "ccr_hashes": ["headroom-ccr-1"],
                "cache_hot_zone": {
                    "stable_prefix_hash": "stable-prefix-hash",
                    "stable_prefix_without_prompt_cache_key_hash": (
                        "stable-prefix-no-cache-key"
                    ),
                    "stable_prefix_bytes": 1234,
                    "input_type": "list",
                    "input_item_count": 8,
                    "stable_input_item_count": 6,
                    "mutable_boundary": {
                        "input_index": 6,
                        "item_type": "function_call_output",
                    },
                },
                "deployment_payload": {
                    "cache_hot_zone": {
                        "stable_prefix_hash": "deployment-stable-prefix-hash",
                        "input_type": "list",
                    },
                    "mutable_output": {
                        "output_item_count": 2,
                        "text_output_item_count": 1,
                        "output_bytes": 512,
                        "output_tokens_estimate": 128,
                        "output_hash": "output-hash",
                        "output_item_types": ["function_call_output"],
                    },
                },
            },
            trace=trace,
        ),
        chunks=[
            CompressionChunkCommand(
                ordinal=0,
                ccr_hash="stored-ccr-1",
                content_hash="content-hash",
                original_tokens=1200,
                compressed_tokens=750,
                original_content="raw original content",
                compressed_content="raw compressed content",
                storage_policy="plaintext",
                metadata={"api_key": "secret-api-key"},
            )
        ],
        provider_calls=[
            ProviderCallCommand(
                provider_call_key="provider-call-1",
                provider="openai",
                model="gpt-5.4-mini",
                litellm_call_id="litellm-call-1",
                provider_response_id="resp_123",
                status="succeeded",
                cost_total=Decimal("0.0123"),
                currency="USD",
                raw_response_metadata={"_hidden_params": {"api_key": "secret-api-key"}},
                trace=trace,
                token_usage=[
                    TokenUsageBreakdownCommand(
                        measurement_source="provider_reported",
                        input_tokens=1000,
                        cached_input_tokens=250,
                        newly_processed_input_tokens=750,
                        cache_write_tokens=75,
                        output_tokens=150,
                        reasoning_tokens=40,
                        total_tokens=1150,
                        raw_usage={"secret": "secret-token"},
                    )
                ],
                cost_calculations=[
                    CostCalculationCommand(
                        calculation_kind="measured",
                        total_cost=Decimal("0.0123"),
                    )
                ],
            )
        ],
        cache_activities=[
            CacheActivityCommand(
                cache_system="provider",
                operation="read",
                hit=True,
                provider_call_key="provider-call-1",
                tokens_read=250,
            ),
            CacheActivityCommand(
                cache_system="headroom_ccr",
                operation="write",
                hit=None,
                ccr_hash="stored-ccr-1",
                tokens_written=750,
            ),
        ],
    )
