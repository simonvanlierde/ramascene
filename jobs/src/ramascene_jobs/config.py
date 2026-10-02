"""Settings, read from the environment once at startup.

Plain environment variables, no settings framework. Put them in `.env` for
`docker compose` (never commit it) or export them.
"""

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

DEFAULT_PORT = 8020


def engine_db_default() -> Path | None:
    """The engine checkout's db.sqlite3, next to the `ramascene` package.

    Found without importing the package: importing it pulls in Django. Only an
    editable (path) install keeps the database beside the package; otherwise
    set RAMASCENE_DB.
    """
    spec = importlib.util.find_spec("ramascene")
    if spec is None or spec.origin is None:
        return None
    candidate = Path(spec.origin).resolve().parents[1] / "db.sqlite3"
    return candidate if candidate.is_file() else None


@dataclass(frozen=True)
class Settings:
    """What the service reads from its environment."""

    # Loopback by default, so a bare `python -m ramascene_jobs` is not reachable
    # from the network. The image sets HOST=0.0.0.0; compose then publishes the
    # port on 127.0.0.1 only.
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    # Telemetry is off unless this is set. OTLP over HTTP, e.g. http://otel-collector:4318;
    # the exporters append /v1/traces and /v1/metrics themselves.
    otlp_endpoint: str | None = None
    service_name: str = "ramascene-jobs"

    # --- the engine ----------------------------------------------------------
    # DATASETS_DIR/DATASETS_VERSION are the names RaMa-Scene's own settings read:
    # $DATASETS_DIR/2011/{A,L,Y,B}_$DATASETS_VERSION.npy.
    datasets_dir: Path | None = None
    datasets_version: str = "v4"
    # The engine's labels and id hierarchy. Copied per job, never opened for writing.
    engine_db: Path | None = field(default_factory=engine_db_default)
    # One job at a time; this many more may wait. A full queue answers 503.
    max_queued: int = 4
    job_timeout_s: float = 900.0
    # Optional: a lock file the worker holds (flock) around each engine run, to
    # serialise it with other memory-heavy processes on a shared host.
    job_lock: Path | None = None
    # Optional: write each finished job's record here as <id>.json.
    jobs_dir: Path | None = None
    # Browser origins allowed to call the API, besides any loopback origin
    # (http://127.0.0.1:<port>, http://localhost:<port>), which is always allowed.
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> Settings:
        """Build settings from `env`; an empty value counts as unset."""

        def path(name: str) -> Path | None:
            value = env.get(name)
            return Path(value).expanduser() if value else None

        return cls(
            host=env.get("HOST") or cls.host,
            port=int(env.get("PORT") or DEFAULT_PORT),
            otlp_endpoint=env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or None,
            service_name=env.get("OTEL_SERVICE_NAME") or cls.service_name,
            datasets_dir=path("DATASETS_DIR"),
            datasets_version=env.get("DATASETS_VERSION") or cls.datasets_version,
            engine_db=path("RAMASCENE_DB") or engine_db_default(),
            max_queued=int(env.get("MAX_QUEUED_JOBS") or cls.max_queued),
            job_timeout_s=float(env.get("JOB_TIMEOUT_S") or cls.job_timeout_s),
            job_lock=path("JOB_LOCK_FILE"),
            jobs_dir=path("JOBS_DIR"),
            cors_origins=tuple(o.strip() for o in (env.get("CORS_ORIGINS") or "").split(",") if o.strip()),
        )
