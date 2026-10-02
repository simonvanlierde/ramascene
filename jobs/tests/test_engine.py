"""The correctness gate: real jobs through the API, on the real engine and data.

Deselected by default (`-m "not engine"` in pyproject.toml). Each job peaks near
1.65 GB and takes about a minute; run them with

    DATASETS_DIR=/path/to/datasets DATASETS_VERSION=v4 uv run pytest -m engine -s

EXPLORER_RESULTS points at the explorer's committed data/results.json (default:
the sibling checkout).
"""

import json
import math
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from fastapi.testclient import TestClient

from ramascene_jobs.app import create_app
from ramascene_jobs.config import Settings

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.engine

EXPLORER_RESULTS = Path(
    os.environ.get("EXPLORER_RESULTS") or Path(__file__).parents[3] / "ramascene-explorer" / "data" / "results.json"
)
GATE = "steel-in-vehicles-europe"
REL = 1e-9
# The explorer's absolute floor (precompute.ABS_FLOOR), for values that should be 0.
ABS_FLOOR = 1e-9
# PR 2 (solve instead of invert) measured 1.65-1.69 GB; the inverting engine 3.97 GB.
PEAK_RSS_CEILING = 2 * 2**30


@pytest.fixture(scope="module")
def settings() -> Settings:
    """From the environment; skip unless the dataset is there."""
    settings = Settings.from_env()
    names = [f"{n}_{settings.datasets_version}.npy" for n in "ALYB"]
    if settings.datasets_dir is None or not all((settings.datasets_dir / "2011" / n).is_file() for n in names):
        pytest.skip("set DATASETS_DIR (and DATASETS_VERSION) to the 2011 EXIOBASE matrices")
    return settings


@pytest.fixture(scope="module")
def client(settings: Settings) -> Iterator[TestClient]:
    """The app with the real worker."""
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture(scope="module")
def explorer() -> dict[str, Any]:
    """The explorer's committed, precomputed results."""
    if not EXPLORER_RESULTS.is_file():
        pytest.skip(f"no explorer results at {EXPLORER_RESULTS}; set EXPLORER_RESULTS")
    return json.loads(EXPLORER_RESULTS.read_text())


def run_job(client: TestClient, body: dict[str, Any], timeout: float = 3600) -> dict[str, Any]:
    """Submit, then poll until done."""
    response = client.post("/jobs", json=body)
    assert response.status_code == 202, response.text
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = client.get(f"/jobs/{response.json()['id']}").json()
        if job["status"] in {"succeeded", "failed"}:
            print(  # noqa: T201 - the evidence, with -s
                f"\njob {job['id']} {body['label']!r}: {job['status']}, "
                f"{job['duration_s']:.1f} s, peak RSS {job['peak_rss_bytes'] / 2**30:.2f} GiB "
                f"({job['peak_rss_bytes']} bytes)"
            )
            return job
        time.sleep(1)
    pytest.fail("job did not finish")


def max_relative_difference(got: dict[str, dict[str, float]], want: dict[str, dict[str, float]]) -> float:
    """Over every indicator and region; both must name the same cells."""
    assert got.keys() == want.keys()
    worst = 0.0
    for indicator, regions in want.items():
        assert got[indicator].keys() == regions.keys()
        for region, value in regions.items():
            worst = max(worst, abs(got[indicator][region] - value) / max(abs(value), ABS_FLOOR))
    return worst


def test_steel_in_vehicles_reproduces_the_explorer(client: TestClient, explorer: dict[str, Any]) -> None:
    """The explorer's steel-into-vehicles -20% scenario, as it defines it, through the API."""
    [definition] = [s for s in explorer["scenarios"] if s["id"] == GATE]
    job = run_job(client, {"label": definition["label"], "model_details": definition["model_details"]})
    assert job["status"] == "succeeded", job["error"]
    result = job["result"]
    assert result["regions"] == explorer["regions"]
    assert [i["id"] for i in result["indicators"]] == [i["id"] for i in explorer["indicators"]]
    scenario = max_relative_difference(result["results"]["scenario"], explorer["results"][GATE])
    baseline = max_relative_difference(result["results"]["baseline"], explorer["results"]["baseline"])
    print(f"max relative difference vs explorer: scenario {scenario:.3e}, baseline {baseline:.3e}")  # noqa: T201
    assert scenario <= REL
    assert baseline <= REL
    # The same matrices the explorer's numbers came from.
    assert result["provenance"]["dataset"]["sha256"] == explorer["provenance"]["dataset"]["sha256"]
    assert 0 < job["peak_rss_bytes"] < PEAK_RSS_CEILING


def test_zero_change_reproduces_the_octave_references(client: TestClient, explorer: dict[str, Any]) -> None:
    """A 0% change on the same cells, through (I - A) x = y: all 122 Octave values."""
    [definition] = [s for s in explorer["scenarios"] if s["id"] == GATE]
    details = [{**step, "techChange": [0]} for step in definition["model_details"]]
    job = run_job(client, {"label": "zero change", "model_details": details, "reference_check": True})
    assert job["status"] == "succeeded", job["error"]
    validation = job["result"]["reference_validation"]
    print(  # noqa: T201
        f"Octave references: {validation['values']} values, passed={validation['passed']}, "
        f"max relative difference {validation['max_relative_difference']:.3e}"
    )
    assert validation["passed"], validation
    assert validation["values"] == 122  # 50 + 50 + 3 + 19
    assert all(f["mismatches"] == 0 for f in validation["files"].values())
    # And a 0% change leaves every result where the baseline is.
    scenario, baseline = job["result"]["results"]["scenario"], job["result"]["results"]["baseline"]
    assert max_relative_difference(scenario, baseline) <= 1e-9
    assert all(math.isfinite(v) for regions in scenario.values() for v in regions.values())
