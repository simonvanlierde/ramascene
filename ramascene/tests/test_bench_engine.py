"""Benchmarks of the engine's scenario solve and analysis routes, on synthetic matrices.

CodSpeed measures them in CI (`pytest ramascene/tests/test_bench_engine.py
--codspeed`); a plain `pytest` runs each once as a test. Each asserts on its
result, so a benchmark that got faster by getting wrong fails.
"""

import json
import os

import numpy as np
import pytest
import scipy.sparse

from ramascene import modelling, querymanagement
from ramascene.analyze import Analyze
from ramascene.modelling import LeontiefSolve, LowRankLeontief, Modelling
from ramascene.productindexmanger import ProductIndexManager
from ramascene.tests.validation_data import VALIDATION_DIR, open_validation_file

# N x N, not 9800 x 9800: the simulation runs code many times slower than native,
# and full-size dense work timed out a 15-minute CI job.
N = 500

# The routes hard-code the EXIOBASE shapes, so Y and B get their real shapes.
COUNTRY_CNT = 49
PRODUCT_CNT = 200
MATRIX_ROWS = COUNTRY_CNT * PRODUCT_CNT
INDICATOR_ROWS = 60

LEGACY_VOCABULARY = {
    "Consumption": "Contribution",
    "Production": "Hotspot",
    "GeoMap": "Geographic",
    "TreeMap": "Sectoral",
}

# route -> (validation file giving the selection, dimType, vizType)
ROUTE_CASES = {
    "route_one": ("validation_analytical_4_v3.csv", "Contribution", "Sectoral"),
    "route_two": ("validation_analytical_1_v3.csv", "Contribution", "Geographic"),
    "route_three": ("validation_analytical_1_v3.csv", "Hotspot", "Geographic"),
    "route_four": ("validation_analytical_4_v3.csv", "Hotspot", "Sectoral"),
}


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


def read_query(filename):
    query, _, _ = open_validation_file(os.path.join(VALIDATION_DIR, filename))
    selection = query["querySelection"]
    selection["year"] = int(selection["year"][0])
    selection["dimType"] = LEGACY_VOCABULARY[selection["dimType"]]
    selection["vizType"] = LEGACY_VOCABULARY[selection["vizType"]]
    return selection


@pytest.fixture(scope="module")
def route_matrices():
    """Y and B at full size; L is the identity plus a few small coefficients.

    The routes only call L.dot and L.T.dot. A dense 9800 x 9800 L would dominate
    every measurement, and the dense solve is measured above.
    """
    rng = np.random.default_rng(20260926)
    Y = rng.random((MATRIX_ROWS, COUNTRY_CNT))
    B = rng.random((INDICATOR_ROWS, MATRIX_ROWS))
    off_diagonal = scipy.sparse.random(MATRIX_ROWS, MATRIX_ROWS, density=1e-4, format="csr", random_state=rng)
    L = (scipy.sparse.identity(MATRIX_ROWS, format="csr") + off_diagonal * 1e-3).tocsr()
    return Y, B, L


@pytest.mark.parametrize("route", sorted(ROUTE_CASES))
def test_analyze_route(benchmark, route, route_matrices):
    """One route end to end: index expansion, the products with L, aggregation, ORM labels, JSON."""
    filename, dim_type, viz_type = ROUTE_CASES[route]
    selection = read_query(filename)
    selection["dimType"] = dim_type
    selection["vizType"] = viz_type
    ready = querymanagement.calc_ready_selection(selection)
    Y, B, L = route_matrices
    analyze = Analyze(
        querymanagement.convert_to_numpy(ready["nodesSec"]),
        querymanagement.convert_to_numpy(ready["nodesReg"]),
        querymanagement.convert_to_numpy(ready["extn"]),
        selection,
        ready["idx_units"],
        "benchmark",
        0,
        np.arange(COUNTRY_CNT),
        Y,
        B,
        L,
    )

    result = benchmark(getattr(analyze, route))
    assert json.loads(result)["rawResultData"]


@pytest.mark.parametrize("filename", ["validation_analytical_1_v3.csv", "validation_analytical_4_v3.csv"])
def test_calc_ready_selection(benchmark, filename):
    """Expanding a front-end selection into calculation indices through the ORM."""
    ready = benchmark(querymanagement.calc_ready_selection, read_query(filename))
    assert ready["nodesSec"] and ready["nodesReg"]


def test_product_index_manager_full_selection(benchmark):
    """Index expansion for every product in every country."""
    products = np.arange(PRODUCT_CNT)
    countries = np.arange(COUNTRY_CNT)

    def expand():
        ids = ProductIndexManager(products, countries, products, countries)
        return ids.get_consumed_product_ids(), ids.get_produced_product_ids()

    consumed, produced = benchmark(expand)
    assert len(consumed) == len(produced) == MATRIX_ROWS
