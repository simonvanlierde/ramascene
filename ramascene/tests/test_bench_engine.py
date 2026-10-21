"""Benchmarks of the engine's scenario solve, on synthetic matrices.

CodSpeed measures them in CI (`pytest ramascene/tests/test_bench_engine.py
--codspeed`); a plain `pytest` runs each once as a test. Each asserts on its
result, so a benchmark that got faster by getting wrong fails.
"""

import numpy as np
import pytest

from ramascene import modelling, querymanagement
from ramascene.modelling import LeontiefSolve, LowRankLeontief, Modelling

# N x N, not 9800 x 9800: the simulation runs code many times slower than native,
# and full-size work timed out a 15-minute CI job. The routes are left out for
# the same reason; the regression harness checks them at full size.
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
