"""The API and the job runner, on a fake worker: no engine, no dataset."""

import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from ramascene_jobs import telemetry as otel
from ramascene_jobs.app import create_app
from ramascene_jobs.config import Settings
from ramascene_jobs.jobs import JobRunner, run_process

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from ramascene_jobs.scenario import Catalog

FAKE_WORKER = [sys.executable, str(Path(__file__).with_name("fake_worker.py"))]
STEEL = {"product": [180], "originReg": [1], "consumedBy": [198], "consumedReg": [2], "techChange": [-20]}


def scenario(label: str = "steel", **step: Any) -> dict[str, Any]:  # noqa: ANN401
    """A one-step request body, STEEL unless overridden."""
    return {"label": label, "model_details": [{**STEEL, **step}]}


def wait(client: TestClient, job_id: str, timeout: float = 20) -> dict[str, Any]:
    """Poll until the job leaves queued/running."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] in {"succeeded", "failed"}:
            return job
        time.sleep(0.05)
    pytest.fail(f"job {job_id} still {job['status']}")


def test_a_job_runs_and_records_duration_and_peak_rss(client: TestClient) -> None:
    """202 + Location, then succeeded, with the parent's own measurements."""
    response = client.post("/jobs", json=scenario())
    assert response.status_code == 202
    job_id = response.json()["id"]
    assert response.headers["location"] == f"/jobs/{job_id}"
    job = wait(client, job_id)
    assert job["status"] == "succeeded", job
    assert job["duration_s"] > 0
    # The fake worker holds 200 MiB; the reading is the child's, not the API's.
    assert job["peak_rss_bytes"] > 200 * 2**20
    # The worker got the validated scenario and the engine settings.
    echo = job["result"]["echo"]
    assert echo["scenario"]["model_details"][0]["techChange"] == [-20.0]
    assert echo["datasets_version"] == "v4"


@pytest.mark.parametrize(
    ("label", "error"),
    [("fail", "ValueError: boom"), ("crash", "worker exited with 3 (killed or out of memory?)")],
)
def test_a_failing_worker_fails_the_job_not_the_api(client: TestClient, label: str, error: str) -> None:
    """The error is reported; the next job still runs."""
    job = wait(client, client.post("/jobs", json=scenario(label)).json()["id"])
    assert (job["status"], job["error"]) == ("failed", error)
    assert job["peak_rss_bytes"] > 0
    assert wait(client, client.post("/jobs", json=scenario()).json()["id"])["status"] == "succeeded"


def test_timeout_kills_the_worker() -> None:
    """SIGKILL after the timeout; wait4 still reaps it and reports its usage."""
    job = {"scenario": {"label": "sleep"}}
    started = time.monotonic()
    outcome = run_process(FAKE_WORKER, job, timeout_s=0.5)
    assert time.monotonic() - started < 10
    assert outcome.returncode == -9
    assert outcome.output is None
    assert outcome.peak_rss_bytes > 0


def test_full_queue_is_503(settings: Settings, catalog: Catalog) -> None:
    """max_queued waiting jobs, then 503; the runner is not started here."""
    runner = JobRunner(settings, FAKE_WORKER)
    app = create_app(settings, runner=runner, catalog=catalog)
    runner.start = lambda: None  # ty: ignore[invalid-assignment]
    runner.stop = lambda: None  # ty: ignore[invalid-assignment]
    from fastapi.testclient import TestClient  # noqa: PLC0415

    with TestClient(app) as client:
        codes = [client.post("/jobs", json=scenario()).status_code for _ in range(3)]
    assert codes == [202, 202, 503]


def test_unknown_job_is_404(client: TestClient) -> None:
    """Ids are opaque; a wrong one is simply not found."""
    assert client.get("/jobs/nope").status_code == 404


@pytest.mark.parametrize(
    ("body", "fragment"),
    [
        (scenario(techChange=["nan"]), "techChange"),
        (scenario(techChange=["1e400"]), "techChange"),
        (scenario(techChange=[True]), "techChange"),
        (scenario(techChange=[-150]), "greater than or equal"),
        (scenario(techChange=[5000]), "less than or equal"),
        (scenario(techChange=[]), "at least 1"),
        (scenario(techChange=[-20, 10]), "at most 1"),
        (scenario(product=[180.5]), "valid integer"),
        (scenario(product=["180"]), "valid integer"),
        (scenario(product=[]), "at least 1"),
        ({"model_details": []}, "at least 1"),
        ({"model_details": [STEEL], "extra": 1}, "Extra inputs"),
        ({"model_details": [{**STEEL, "cheat": 1}]}, "Extra inputs"),
    ],
)
def test_malformed_scenarios_are_422(client: TestClient, body: dict[str, Any], fragment: str) -> None:
    """Schema errors: numbers only, finite, in range, the right shapes."""
    response = client.post("/jobs", json=body)
    assert response.status_code == 422
    assert fragment in json.dumps(response.json())


def test_non_finite_json_literals_are_422(client: TestClient) -> None:
    """NaN and Infinity, which Python's json accepts, are rejected too."""
    for literal in ("NaN", "Infinity", "-Infinity"):
        body = json.dumps(scenario()).replace("-20", literal)
        response = client.post("/jobs", content=body, headers={"content-type": "application/json"})
        assert response.status_code == 422, literal


@pytest.mark.parametrize(
    ("step", "problem"),
    [
        ({"product": [180, 9999]}, "model_details[0].product: unknown ids [9999]"),
        ({"originReg": [0]}, "model_details[0].originReg: unknown ids [0]"),
        ({"consumedReg": [99]}, "model_details[0].consumedReg: unknown ids [99]"),
        ({"consumedBy": [42]}, "model_details[0].consumedBy: unknown ids [42]"),
        ({"consumedBy": [276, 198]}, "mixes final consumption with intermediate consumers"),
    ],
)
def test_unknown_ids_are_422(client: TestClient, step: dict[str, Any], problem: str) -> None:
    """Every id is checked against the engine database's catalog."""
    response = client.post("/jobs", json=scenario(**step))
    assert response.status_code == 422
    assert any(problem in p for p in response.json()["detail"])


def test_catalog_lists_the_ids(client: TestClient) -> None:
    """What a client needs to build a valid scenario."""
    body = client.get("/catalog").json()
    assert {"id": 180, "name": "Basic iron and steel"} in body["products"]
    assert body["final_consumption"] == [276]


def test_unconfigured_engine_is_503(catalog: Catalog) -> None:
    """No DATASETS_DIR: the API is up, jobs are refused with a reason."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    settings = Settings(datasets_dir=None)
    with TestClient(create_app(settings, runner=JobRunner(settings, FAKE_WORKER), catalog=catalog)) as client:
        assert client.get("/health").status_code == 200
        response = client.post("/jobs", json=scenario())
    assert response.status_code == 503
    assert "DATASETS_DIR" in response.json()["detail"]


def test_cors_allows_loopback_origins_only(client: TestClient) -> None:
    """The explorer on any loopback port; any other origin gets no CORS header."""

    def allowed(origin: str) -> str | None:
        return client.get("/health", headers={"Origin": origin}).headers.get("access-control-allow-origin")

    assert allowed("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert allowed("http://localhost:5173") == "http://localhost:5173"
    assert allowed("https://example.org") is None
    assert allowed("http://127.0.0.1.evil.example") is None


def test_job_span_and_metrics(settings: Settings, catalog: Catalog) -> None:
    """One span per job, and duration and peak RSS histograms."""
    from fastapi.testclient import TestClient  # noqa: PLC0415

    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    telemetry = otel.Telemetry(tracer_provider, MeterProvider(metric_readers=[reader]))
    app = create_app(settings, telemetry, JobRunner(settings, FAKE_WORKER), catalog)
    with TestClient(app) as client:
        job = wait(client, client.post("/jobs", json=scenario()).json()["id"])
        data = reader.get_metrics_data()

    [span] = [s for s in spans.get_finished_spans() if s.name == "ramascene_jobs.job"]
    assert span.attributes is not None
    assert span.attributes["job.id"] == job["id"]
    assert span.attributes["job.peak_rss_bytes"] == job["peak_rss_bytes"]
    assert data is not None
    points = {m.name: m.data.data_points for rm in data.resource_metrics for sm in rm.scope_metrics for m in sm.metrics}
    [rss] = points["ramascene_jobs.job.peak_rss"]
    assert rss.max == job["peak_rss_bytes"]  # ty: ignore[unresolved-attribute]
    assert "ramascene_jobs.job.duration" in points
