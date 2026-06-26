from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from headroom.hooks import CompressContext, CompressEvent, CompressionHooks
from headroom.pipeline import PipelineEvent


@dataclass(frozen=True, slots=True)
class HeadroomCompressionObservation:
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    compression_ratio: float
    transforms_applied: tuple[str, ...] = field(default_factory=tuple)
    ccr_hashes: tuple[str, ...] = field(default_factory=tuple)
    model: str = ""
    user_query: str = ""
    provider: str = ""

    @classmethod
    def from_event(cls, event: CompressEvent) -> HeadroomCompressionObservation:
        return cls(
            tokens_before=event.tokens_before,
            tokens_after=event.tokens_after,
            tokens_saved=event.tokens_saved,
            compression_ratio=event.compression_ratio,
            transforms_applied=tuple(event.transforms_applied),
            ccr_hashes=tuple(event.ccr_hashes),
            model=event.model,
            user_query=event.user_query,
            provider=event.provider,
        )

    def as_raw_metadata(self) -> dict[str, Any]:
        return {
            "tokens_before": self.tokens_before,
            "tokens_after": self.tokens_after,
            "tokens_saved": self.tokens_saved,
            "compression_ratio": self.compression_ratio,
            "transforms_applied": list(self.transforms_applied),
            "ccr_hashes": list(self.ccr_hashes),
            "model": self.model,
            "provider": self.provider,
        }


class AnalyticsCompressionHooks(CompressionHooks):
    """Observes Headroom compression without replacing Headroom behavior."""

    def __init__(self, delegate: CompressionHooks | None = None) -> None:
        self._delegate = delegate
        self._observations: list[HeadroomCompressionObservation] = []

    @property
    def observations(self) -> tuple[HeadroomCompressionObservation, ...]:
        return tuple(self._observations)

    def consume_last_observation(self) -> HeadroomCompressionObservation | None:
        if not self._observations:
            return None
        return self._observations.pop()

    def pre_compress(
        self,
        messages: list[dict[str, Any]],
        ctx: CompressContext,
    ) -> list[dict[str, Any]]:
        if self._delegate is None:
            return messages
        return self._delegate.pre_compress(messages, ctx)

    def compute_biases(
        self,
        messages: list[dict[str, Any]],
        ctx: CompressContext,
    ) -> dict[int, float]:
        if self._delegate is None:
            return {}
        return self._delegate.compute_biases(messages, ctx)

    def post_compress(self, event: CompressEvent) -> None:
        if self._delegate is not None:
            self._delegate.post_compress(event)
        self._observations.append(HeadroomCompressionObservation.from_event(event))

    def on_pipeline_event(self, event: PipelineEvent) -> PipelineEvent | None:
        if self._delegate is None:
            return None
        return self._delegate.on_pipeline_event(event)
