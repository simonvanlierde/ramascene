"""The correctness gate: real jobs through the API, on the real engine and data.

Deselected by default (`-m "not engine"` in pyproject.toml). Each job peaks near
1.65 GB and takes about a minute; run them with

    DATASETS_DIR=/path/to/datasets DATASETS_VERSION=v4 uv run pytest -m engine -s

EXPLORER_RESULTS points at the explorer's committed data/results.json and
EXPLORER_EMBODIED at its data/embodied.parquet (default: the sibling checkout).
"""

import json
import math
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np
import pyarrow.parquet as pq
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
EXPLORER_EMBODIED = Path(
    os.environ.get("EXPLORER_EMBODIED")
    or Path(__file__).parents[3] / "ramascene-explorer" / "data" / "embodied.parquet"
)
GATE = "steel-in-vehicles-europe"
REL = 1e-9
# The Parquet stores float32: about 6e-8 relative per cell.
FLOWS_REL = 1e-6
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


@pytest.fixture(scope="module")
def gate_job(client: TestClient, explorer: dict[str, Any]) -> dict[str, Any]:
    """The explorer's steel-into-vehicles -20% scenario, as it defines it, run once for the module."""
    [definition] = [s for s in explorer["scenarios"] if s["id"] == GATE]
    job = run_job(client, {"label": definition["label"], "model_details": definition["model_details"]})
    assert job["status"] == "succeeded", job["error"]
    return job


def test_steel_in_vehicles_reproduces_the_explorer(gate_job: dict[str, Any], explorer: dict[str, Any]) -> None:
    """The job's numbers against the explorer's committed results."""
    job = gate_job
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


def test_flows_reproduce_the_explorer_parquet(gate_job: dict[str, Any]) -> None:
    """Absolute baseline flows, and scenario minus baseline, against embodied.parquet (float32)."""
    if not EXPLORER_EMBODIED.is_file():
        pytest.skip(f"no explorer flows at {EXPLORER_EMBODIED}; set EXPLORER_EMBODIED")
    file = pq.ParquetFile(EXPLORER_EMBODIED)
    meta = json.loads(file.schema_arrow.metadata[b"ramascene"])
    assert {i["scale"] for i in meta["indicators"]} == {1}  # stored value = value x scale, so scale 1 is unscaled
    flows = gate_job["result"]["flows"]
    shape = tuple(flows["shape"])
    assert shape == (3, 49, 15, 49)
    baseline = np.array(flows["baseline"]).reshape(shape)
    scenario = np.array(flows["scenario"]).reshape(shape)
    ids = [i["id"] for i in gate_job["result"]["indicators"]]
    groups = {file.metadata.row_group(g).column(0).statistics.min: g for g in range(file.metadata.num_row_groups)}

    def cells(run: str, absolute: np.ndarray) -> np.ndarray:
        """The stored run as a dense array; omitted cells are 0."""
        table = file.read_row_group(groups[run]).to_pydict()
        want = np.zeros(shape)
        index = (
            [ids.index(i) for i in table["indicator"]],
            table["producer"],
            table["section"],
            table["consumer"],
        )
        want[index] = table["value"]
        assert want.shape == absolute.shape
        return want

    worst = 0.0
    for run, got, want in (
        ("baseline", baseline, cells("baseline", baseline)),
        (GATE, scenario - baseline, cells(GATE, scenario)),
    ):
        for k, indicator in enumerate(ids):
            floor = FLOWS_REL * np.abs(baseline[k]).max()
            diff = np.abs(got[k] - want[k])
            assert (diff <= FLOWS_REL * np.abs(want[k]) + floor).all(), (run, indicator, diff.max())
            total = abs(got[k].sum() - want[k].sum()) / max(abs(want[k].sum()), floor)
            assert total <= FLOWS_REL, (run, indicator, total)
            worst = max(worst, (diff / np.maximum(np.abs(want[k]), floor)).max())
    print(f"worst relative flows difference vs explorer parquet: {worst:.3e}")  # noqa: T201
