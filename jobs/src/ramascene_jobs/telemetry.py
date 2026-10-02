"""OpenTelemetry: request traces and a request-duration histogram, or nothing.

Strictly opt-in. Without OTEL_EXPORTER_OTLP_ENDPOINT no provider is built, no
middleware is added and nothing leaves the process. With it, spans and metrics go
to that collector over OTLP/HTTP; the SDK reads the other OTEL_* variables itself.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.trace import TracerProvider

    from ramascene_jobs.config import Settings


@dataclass
class Telemetry:
    """The providers the app reports to; both None means telemetry is off."""

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None

    @property
    def enabled(self) -> bool:
        """Whether anything is recorded at all."""
        return self.tracer_provider is not None or self.meter_provider is not None

    def instrument(self, app: FastAPI) -> None:
        """Add the request span and duration histogram middleware, if enabled."""
        if not self.enabled:
            return
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # noqa: PLC0415

        # Called before the app first serves, so the middleware enters the stack
        # Starlette builds on startup; instrumenting later needs a rebuild.
        FastAPIInstrumentor.instrument_app(
            app, tracer_provider=self.tracer_provider, meter_provider=self.meter_provider
        )

    def shutdown(self) -> None:
        """Flush and stop both providers."""
        for provider in (self.tracer_provider, self.meter_provider):
            if provider is not None:
                provider.shutdown()


def from_settings(settings: Settings) -> Telemetry:
    """OTLP/HTTP exporters when an endpoint is configured, otherwise a no-op."""
    if settings.otlp_endpoint is None:
        return Telemetry()
    # Imported here so the SDK and exporters are never loaded when telemetry is off.
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter  # noqa: PLC0415
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter  # noqa: PLC0415
    from opentelemetry.sdk.metrics import MeterProvider  # noqa: PLC0415
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader  # noqa: PLC0415
    from opentelemetry.sdk.resources import Resource  # noqa: PLC0415
    from opentelemetry.sdk.trace import TracerProvider  # noqa: PLC0415
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # noqa: PLC0415

    resource = Resource.create({"service.name": settings.service_name})
    tracer_provider = TracerProvider(resource=resource)
    # No endpoint= : the exporters read OTEL_EXPORTER_OTLP_ENDPOINT and append the
    # signal path. An explicit endpoint is used as-is and 404s at the collector.
    tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    reader = PeriodicExportingMetricReader(OTLPMetricExporter())
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    return Telemetry(tracer_provider, meter_provider)
