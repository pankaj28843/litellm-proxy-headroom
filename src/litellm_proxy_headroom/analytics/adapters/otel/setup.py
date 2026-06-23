from __future__ import annotations

import os

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)


def _enabled(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _service_resource() -> Resource:
    return Resource.create(
        {
            "service.name": os.getenv(
                "OTEL_SERVICE_NAME", "headroom-analytics-backend"
            ),
            "service.namespace": "litellm-proxy-headroom",
        }
    )


def _http_endpoint(signal: str) -> str | None:
    specific = os.getenv(f"OTEL_EXPORTER_OTLP_{signal.upper()}_ENDPOINT", "").strip()
    if specific:
        return specific
    generic = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not generic:
        return None
    suffix = f"/v1/{signal}"
    return generic if generic.endswith(suffix) else generic.rstrip("/") + suffix


def configure_otel_from_env() -> None:
    if not _enabled("HEADROOM_ANALYTICS_OTEL_ENABLED", True):
        return

    resource = _service_resource()
    if trace.get_tracer_provider().__class__.__name__ == "ProxyTracerProvider":
        provider = TracerProvider(resource=resource)
        if _enabled("HEADROOM_ANALYTICS_OTEL_CONSOLE", False):
            provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        traces_endpoint = _http_endpoint("traces")
        if traces_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )

            provider.add_span_processor(
                BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint))
            )
        trace.set_tracer_provider(provider)

    if metrics.get_meter_provider().__class__.__name__ == "_ProxyMeterProvider":
        readers = []
        export_interval = int(
            os.getenv("HEADROOM_ANALYTICS_OTEL_METRIC_EXPORT_INTERVAL_MS", "60000")
        )
        if _enabled("HEADROOM_ANALYTICS_OTEL_CONSOLE", False):
            readers.append(
                PeriodicExportingMetricReader(
                    ConsoleMetricExporter(),
                    export_interval_millis=export_interval,
                )
            )
        metrics_endpoint = _http_endpoint("metrics")
        if metrics_endpoint:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter,
            )

            readers.append(
                PeriodicExportingMetricReader(
                    OTLPMetricExporter(endpoint=metrics_endpoint),
                    export_interval_millis=export_interval,
                )
            )
        if readers:
            metrics.set_meter_provider(
                MeterProvider(resource=resource, metric_readers=readers)
            )


def instrument_analytics_app(app: FastAPI) -> None:
    if not _enabled("HEADROOM_ANALYTICS_OTEL_ENABLED", True):
        return
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=trace.get_tracer_provider(),
        meter_provider=metrics.get_meter_provider(),
        exclude_spans=["receive", "send"],
    )
