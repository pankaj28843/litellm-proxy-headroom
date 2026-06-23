from __future__ import annotations

from functools import lru_cache

from opentelemetry import metrics


class SimulationTelemetry:
    def __init__(self) -> None:
        meter = metrics.get_meter("litellm_proxy_headroom.analytics.simulation")
        self._duration = meter.create_histogram(
            "headroom.analytics.simulation.duration",
            unit="ms",
            description="Historical simulation run duration.",
        )
        self._result_count = meter.create_histogram(
            "headroom.analytics.simulation.results",
            unit="{result}",
            description="Historical simulation result count.",
        )
        self._failures = meter.create_counter(
            "headroom.analytics.simulation.failures",
            unit="{failure}",
            description="Historical simulation failures.",
        )

    def record_run(
        self,
        *,
        duration_ms: int,
        result_count: int,
        status: str,
    ) -> None:
        attrs = {"headroom.analytics.simulation.status": status}
        self._duration.record(duration_ms, attrs)
        self._result_count.record(result_count, attrs)
        if status != "succeeded":
            self._failures.add(1, attrs)


@lru_cache(maxsize=1)
def get_simulation_telemetry() -> SimulationTelemetry:
    return SimulationTelemetry()
