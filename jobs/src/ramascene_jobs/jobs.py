"""The job queue: one worker thread, one engine subprocess at a time.

Jobs live in memory; a restart forgets them. The parent measures every run itself: wall-clock
duration, and the child's peak RSS from wait4(2), which holds even when the
child is killed before it can report anything.
"""

import datetime
import json
import logging
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from opentelemetry import metrics, trace

if TYPE_CHECKING:
    from collections.abc import Sequence

    from ramascene_jobs.config import Settings
    from ramascene_jobs.scenario import ScenarioRequest

Status = Literal["queued", "running", "succeeded", "failed"]
QUEUED: Status = "queued"
FAILED: Status = "failed"
WORKER = [sys.executable, "-m", "ramascene_jobs.worker"]
logger = logging.getLogger(__name__)


def now() -> str:
    """UTC, to the second."""
    return datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds")


@dataclass
class Job:
    """What GET /jobs/{id} returns."""

    id: str
    scenario: dict[str, Any]
    status: Status = "queued"
    submitted_at: str = field(default_factory=now)
    started_at: str | None = None
    finished_at: str | None = None
    duration_s: float | None = None
    # The worker process's peak resident set, measured by the parent (wait4).
    peak_rss_bytes: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class Outcome:
    """One finished subprocess."""

    returncode: int
    duration_s: float
    peak_rss_bytes: int
    output: dict[str, Any] | None


class QueueFullError(Exception):
    """More jobs are waiting than the service accepts."""


def run_process(command: Sequence[str], job: dict[str, Any], timeout_s: float) -> Outcome:
    """Run the worker on `job`; kill it after `timeout_s`. Peak RSS via wait4."""
    with tempfile.TemporaryDirectory(prefix="ramascene-job-") as tmp:
        in_path, out_path = Path(tmp) / "in.json", Path(tmp) / "out.json"
        in_path.write_text(json.dumps(job))
        started = time.perf_counter()
        proc = subprocess.Popen([*command, str(in_path), str(out_path)])  # noqa: S603
        timer = threading.Timer(timeout_s, os.kill, (proc.pid, signal.SIGKILL))
        timer.start()
        try:
            # wait4, not proc.wait(): it also returns the child's rusage.
            _, status, usage = os.wait4(proc.pid, 0)
        finally:
            timer.cancel()
        duration = time.perf_counter() - started
        proc.returncode = os.waitstatus_to_exitcode(status)  # reaped here, so Popen must not wait again
        output = json.loads(out_path.read_text()) if out_path.exists() else None
    # ru_maxrss is in KiB on Linux but in bytes on macOS.
    return Outcome(
        proc.returncode, duration, usage.ru_maxrss * (1 if sys.platform.startswith("darwin") else 1024), output
    )


class JobRunner:
    """Accepts jobs, runs them one at a time on a daemon thread."""

    def __init__(self, settings: Settings, command: Sequence[str] = WORKER) -> None:
        self.settings = settings
        self.command = list(command)
        self.jobs: dict[str, Job] = {}
        self.pending: queue.Queue[str | None] = queue.Queue()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        tracer = trace.get_tracer(__name__)
        meter = metrics.get_meter(__name__)
        self.tracer, self.meter = tracer, meter
        self._instruments()

    def use(self, tracer_provider: Any, meter_provider: Any) -> None:  # noqa: ANN401
        """Report to these providers instead of the global (no-op) ones."""
        if tracer_provider is not None:
            self.tracer = tracer_provider.get_tracer(__name__)
        if meter_provider is not None:
            self.meter = meter_provider.get_meter(__name__)
        self._instruments()

    def _instruments(self) -> None:
        self.duration = self.meter.create_histogram(
            "ramascene_jobs.job.duration", unit="s", description="Wall-clock time of one engine job"
        )
        self.peak_rss = self.meter.create_histogram(
            "ramascene_jobs.job.peak_rss", unit="By", description="Peak resident set of one engine job's process"
        )
        self.queued = self.meter.create_up_down_counter(
            "ramascene_jobs.jobs.queued", unit="{job}", description="Jobs waiting for the worker"
        )

    def start(self) -> None:
        """Start the worker thread."""
        self.thread.start()

    def stop(self) -> None:
        """Let the current job finish, then end the thread; queued jobs are dropped."""
        self.pending.put(None)
        self.thread.join(timeout=self.settings.job_timeout_s + 5)

    def submit(self, scenario: ScenarioRequest) -> Job:
        """Queue a job, or raise QueueFullError."""
        with self.lock:
            waiting = sum(j.status == QUEUED for j in self.jobs.values())
            if waiting >= self.settings.max_queued:
                raise QueueFullError
            job = Job(id=uuid.uuid4().hex, scenario=scenario.model_dump())
            self.jobs[job.id] = job
        self.queued.add(1)
        self.pending.put(job.id)
        return job

    def get(self, job_id: str) -> Job | None:
        """The job, or None."""
        return self.jobs.get(job_id)

    def _loop(self) -> None:
        while (job_id := self.pending.get()) is not None:
            self.queued.add(-1)
            job = self.jobs[job_id]
            try:
                self._run(job)
            except Exception as e:  # an unexpected error fails the job, not the thread
                logger.exception("job %s", job.id)
                if job.finished_at is None:
                    job.status, job.error, job.finished_at = FAILED, f"{type(e).__name__}: {e}", now()

    def _run(self, job: Job) -> None:
        settings = self.settings
        with self.tracer.start_as_current_span("ramascene_jobs.job") as span:
            span.set_attribute("job.id", job.id)
            span.set_attribute("job.steps", len(job.scenario["model_details"]))
            job.status, job.started_at = "running", now()
            payload = {
                "scenario": job.scenario,
                "datasets_dir": str(settings.datasets_dir),
                "datasets_version": settings.datasets_version,
                "engine_db": str(settings.engine_db),
            }
            try:
                outcome = run_process(self.command, payload, settings.job_timeout_s)
            except Exception as e:  # noqa: BLE001 - the job fails, the runner goes on
                outcome = Outcome(-1, 0.0, 0, {"error": f"{type(e).__name__}: {e}"})
            job.duration_s, job.peak_rss_bytes = outcome.duration_s, outcome.peak_rss_bytes
            output = outcome.output or {}
            if outcome.returncode == 0 and output.get("results") is not None:
                job.status, job.result = "succeeded", output
            else:
                job.status = FAILED
                job.error = output.get("error") or f"worker exited with {outcome.returncode} (killed or out of memory?)"
            job.finished_at = now()
            span.set_attribute("job.status", job.status)
            span.set_attribute("job.duration_s", job.duration_s)
            span.set_attribute("job.peak_rss_bytes", job.peak_rss_bytes)
            if job.status == FAILED:
                span.set_status(trace.StatusCode.ERROR, job.error)
        attributes = {"job.status": job.status}
        self.duration.record(job.duration_s, attributes)
        self.peak_rss.record(job.peak_rss_bytes, attributes)
