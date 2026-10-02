"""The app, its settings and its telemetry, all in process."""

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ramascene_jobs import telemetry as otel
from ramascene_jobs.app import create_app
from ramascene_jobs.config import DEFAULT_PORT, Settings


def test_health() -> None:
    """/health answers 200 with a fixed body."""
    with TestClient(create_app(Settings())) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_path_is_404() -> None:
    """Anything else is a 404."""
    with TestClient(create_app(Settings())) as client:
        assert client.get("/nope").status_code == 404


def test_settings_from_env() -> None:
    """Each setting comes from its variable."""
    settings = Settings.from_env({"HOST": "0.0.0.0", "PORT": "9000", "OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318"})  # noqa: S104
    assert settings == Settings(host="0.0.0.0", port=9000, otlp_endpoint="http://c:4318")  # noqa: S104


@pytest.mark.parametrize("env", [{}, {"OTEL_EXPORTER_OTLP_ENDPOINT": ""}])
def test_telemetry_is_off_without_an_endpoint(env: dict[str, str]) -> None:
    """No endpoint, or an empty one: no providers and no middleware."""
    settings = Settings.from_env(env)
    assert settings.port == DEFAULT_PORT
    assert settings.otlp_endpoint is None
    telemetry = otel.from_settings(settings)
    assert not telemetry.enabled
    app = create_app(settings, telemetry)
    # No OTel middleware in the stack: nothing is recorded, not merely nothing sent.
    assert [m.cls for m in app.user_middleware] == [CORSMiddleware]


def test_telemetry_records_a_span_and_the_request_duration() -> None:
    """With providers, a request leaves a span and a duration histogram point."""
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    telemetry = otel.Telemetry(tracer_provider, MeterProvider(metric_readers=[reader]))

    with TestClient(create_app(Settings(), telemetry)) as client:
        assert client.get("/health").status_code == 200

    assert "GET /health" in [span.name for span in spans.get_finished_spans()]
    data = reader.get_metrics_data()
    assert data is not None
    names = {m.name for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    # The stable semantic-convention name, or the older one, depending on
    # OTEL_SEMCONV_STABILITY_OPT_IN.
    assert names & {"http.server.request.duration", "http.server.duration"}
