"""The FastAPI application: submit a scenario, poll for its result."""

from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ramascene_jobs import telemetry as otel
from ramascene_jobs.config import Settings
from ramascene_jobs.jobs import JobRunner, QueueFullError
from ramascene_jobs.scenario import Catalog, ScenarioRequest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

# Any port on loopback: the explorer served locally, or through an SSH tunnel.
LOOPBACK_ORIGINS = r"^http://(127\.0\.0\.1|localhost)(:\d+)?$"

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness: the process answers. The image's HEALTHCHECK probes this."""
    # Unauthenticated: no version, config or environment in the body.
    return {"status": "ok"}


def ready_catalog(request: Request) -> Catalog:
    """The catalog, or 503 when the engine is not configured."""
    catalog, settings = request.app.state.catalog, request.app.state.settings
    if catalog is None or settings.datasets_dir is None:
        raise HTTPException(503, "engine not configured: set DATASETS_DIR and RAMASCENE_DB")
    return catalog


@router.get("/catalog")
def get_catalog(request: Request) -> dict[str, Any]:
    """The ids a scenario may use, with names."""
    return ready_catalog(request).as_json()


@router.post("/jobs", status_code=202)
def post_job(scenario: ScenarioRequest, request: Request, response: Response) -> dict[str, Any]:
    """Validate and queue a scenario; 202 with the job, Location names it."""
    problems = ready_catalog(request).problems(scenario)
    if problems:
        raise HTTPException(422, problems)
    try:
        job = request.app.state.runner.submit(scenario)
    except QueueFullError:
        raise HTTPException(503, "job queue is full; retry later") from None
    response.headers["Location"] = f"/jobs/{job.id}"
    return asdict(job)


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    """The job's status, and its result once it has succeeded."""
    job = request.app.state.runner.get(job_id)
    if job is None:
        raise HTTPException(404, "no such job")
    return asdict(job)


async def invalid_request(_: Request, exc: Exception) -> JSONResponse:
    """422 without the inputs echoed back.

    FastAPI's default handler echoes each bad input, and a NaN or Infinity
    literal (which the JSON parser accepts) then fails to serialise: a 500
    instead of a 422. Location and message are enough.
    """
    assert isinstance(exc, RequestValidationError)  # noqa: S101 - registered for this type only
    errors = [{k: e[k] for k in ("loc", "msg", "type")} for e in exc.errors()]
    return JSONResponse({"detail": errors}, status_code=422)


def create_app(
    settings: Settings | None = None,
    telemetry: otel.Telemetry | None = None,
    runner: JobRunner | None = None,
    catalog: Catalog | None = None,
) -> FastAPI:
    """Build the app; tests pass their own settings, telemetry, runner and catalog."""
    settings = settings or Settings.from_env()
    telemetry = telemetry or otel.from_settings(settings)
    runner = runner or JobRunner(settings)
    runner.use(telemetry.tracer_provider, telemetry.meter_provider)
    if catalog is None and settings.engine_db is not None:
        catalog = Catalog.from_db(settings.engine_db)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        runner.start()
        yield
        runner.stop()
        telemetry.shutdown()

    app = FastAPI(title="ramascene-jobs", lifespan=lifespan)
    app.state.settings, app.state.runner, app.state.catalog = settings, runner, catalog
    app.include_router(router)
    app.add_exception_handler(RequestValidationError, invalid_request)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=LOOPBACK_ORIGINS,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        expose_headers=["Location"],
    )
    telemetry.instrument(app)
    return app
