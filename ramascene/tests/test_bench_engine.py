"""Benchmarks of the engine's scenario solve and its four routes, on synthetic matrices.

CodSpeed measures them in CI (`pytest ramascene/tests/test_bench_engine.py
--codspeed`); a plain `pytest` runs each once as a test. Each asserts on its
result, so a benchmark that got faster by getting wrong fails.
"""

import json

import numpy as np
import pytest

from ramascene import modelling, querymanagement
from ramascene.modelling import LeontiefSolve, LowRankLeontief, Modelling
from ramascene.tests.test_engine_regression import GOLDEN_PATH, SYNTHETIC_CASES, read_query, run_route

# The scenario matrices are N x N, not 9800 x 9800: a full factorization takes
# about 2 s natively and far longer under CodSpeed's CPU simulation.
N = 500


@pytest.fixture
def published(monkeypatch):
    """A and its L, served the way get_numpy_objects serves the dataset."""
    A = np.random.default_rng(5).random((N, N)) * 1e-3
    matrices = {"A": A, "L": np.linalg.inv(np.eye(N) - A)}
    monkeypatch.setattr(querymanagement, "get_numpy_objects", lambda year, name, mmap_mode=None: matrices[name].copy())
    return matrices


@pytest.mark.parametrize(
    ("low_rank_max", "kind"),
    [(modelling.LOW_RANK_MAX, LowRankLeontief), (0, LeontiefSolve)],
    ids=["woodbury", "leontief_solve"],
)
def test_apply_model(benchmark, monkeypatch, published, low_rank_max, kind):
    """A -20% intermediate change of product 0 into itself, then L.dot(y), as a job does."""
    monkeypatch.setattr(modelling, "LOW_RANK_MAX", low_rank_max)
    y = np.random.default_rng(6).random((N, 2))
    one = [[0]]
    details = {
        "product": one,
        "consumedBy": one,
        "originReg": one,
        "consumedReg": one,
        "techChange": [["-20"]],
        "identifiers": ["INTERMEDIATE"],
    }

    def scenario():
        _, L = Modelling(details, np.ones((N, 1)), [True], 2011, None).apply_model()
        assert isinstance(L, kind)
        return L.dot(y)

    x = benchmark(scenario)
    changed = published["A"].copy()
    changed[0, 0] *= 0.8
    np.testing.assert_allclose(x, np.linalg.solve(np.eye(N) - changed, y), rtol=1e-12)


@pytest.mark.parametrize("case", sorted(SYNTHETIC_CASES))
def test_route(benchmark, case, synthetic_matrices):
    """One Analyze route on the 9800 x 9800 synthetic L, against the golden output."""
    filename, dim_type, viz_type, extn = SYNTHETIC_CASES[case]
    query_selection, _, _ = read_query(filename)
    query_selection["dimType"] = dim_type
    query_selection["vizType"] = viz_type
    if extn:
        query_selection["extn"] = extn
    values, unit = benchmark(run_route, query_selection, synthetic_matrices)
    with open(GOLDEN_PATH) as golden_file:
        golden = json.load(golden_file)[case]
    assert unit == golden["unit"]
    assert values == pytest.approx(golden["values"], rel=1e-12)
