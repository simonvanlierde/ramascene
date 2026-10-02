"""Shared fixtures: a small catalog and a runner on the fake worker."""

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from fastapi.testclient import TestClient

from ramascene_jobs.app import create_app
from ramascene_jobs.config import Settings
from ramascene_jobs.jobs import JobRunner
from ramascene_jobs.scenario import Catalog

if TYPE_CHECKING:
    from collections.abc import Iterator

FAKE_WORKER = [sys.executable, str(Path(__file__).with_name("fake_worker.py"))]


@pytest.fixture
def catalog() -> Catalog:
    """A few of the engine's ids: 276 is final consumption, 198 an industry."""
    return Catalog(
        products={1: "Total", 180: "Basic iron and steel", 199: "Motor vehicles"},
        regions={1: "Total", 2: "Europe"},
        consumers={198: "S: Motor vehicles", 276: "Y: Final Consumption"},
        final_consumption=frozenset({276}),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Engine configured (paths the fake worker never opens)."""
    return Settings(
        datasets_dir=tmp_path / "data",
        engine_db=tmp_path / "db.sqlite3",
        job_timeout_s=10,
        max_queued=2,
    )


@pytest.fixture
def client(settings: Settings, catalog: Catalog) -> Iterator[TestClient]:
    """The app on the fake worker."""
    app = create_app(settings, runner=JobRunner(settings, FAKE_WORKER), catalog=catalog)
    with TestClient(app) as c:
        yield c
