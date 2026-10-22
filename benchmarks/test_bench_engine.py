"""CodSpeed benchmarks for the calculation engine.

Run with ``pytest benchmarks/ --codspeed`` (see the README). They use
the checked-in db.sqlite3 through the root conftest.py and synthetic matrices,
so no EXIOBASE dataset, Celery, broker or channels layer is needed.

The routes hard-code the EXIOBASE shapes (49 countries x 200 products), so Y
and B have their real shapes. L is a sparse stand-in (identity plus a few
off-diagonal coefficients): the routes only call ``L.dot`` and ``L.T.dot``,
and a dense 9800 x 9800 product would dominate every measurement while the
point here is the selection, indexing, aggregation and labelling around it.
The dense linear algebra is measured separately on smaller matrices.
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

COUNTRY_CNT = 49
PRODUCT_CNT = 200
MATRIX_ROWS = COUNTRY_CNT * PRODUCT_CNT
INDICATOR_ROWS = 60
YEAR = 2011

LEGACY_VOCABULARY = {
    "Consumption": "Contribution",
    "Production": "Hotspot",
    "GeoMap": "Geographic",
    "TreeMap": "Sectoral",
}

# route -> (validation file providing the selection, dimType, vizType)
ROUTE_CASES = {
    "route_one": ("validation_analytical_4_v3.csv", "Contribution", "Sectoral"),
    "route_two": ("validation_analytical_1_v3.csv", "Contribution", "Geographic"),
    "route_three": ("validation_analytical_1_v3.csv", "Hotspot", "Geographic"),
    "route_four": ("validation_analytical_4_v3.csv", "Hotspot", "Sectoral"),
}


def read_query(filename):
    query, _, _ = open_validation_file(os.path.join(VALIDATION_DIR, filename))
    query_selection = query["querySelection"]
    query_selection["year"] = int(query_selection["year"][0])
    query_selection["dimType"] = LEGACY_VOCABULARY[query_selection["dimType"]]
    query_selection["vizType"] = LEGACY_VOCABULARY[query_selection["vizType"]]
    return query_selection


@pytest.fixture(scope="module")
def synthetic_matrices():
    rng = np.random.default_rng(20260926)
    Y = rng.random((MATRIX_ROWS, COUNTRY_CNT))
    B = rng.random((INDICATOR_ROWS, MATRIX_ROWS))
    off_diagonal = scipy.sparse.random(MATRIX_ROWS, MATRIX_ROWS, density=1e-4, format="csr", random_state=rng)
    L = (scipy.sparse.identity(MATRIX_ROWS, format="csr") + off_diagonal * 1e-3).tocsr()
    return Y, B, L


def make_analyze(query_selection, matrices):
    Y, B, L = matrices
    ready = querymanagement.calc_ready_selection(query_selection)
    return Analyze(
        querymanagement.convert_to_numpy(ready["nodesSec"]),
        querymanagement.convert_to_numpy(ready["nodesReg"]),
        querymanagement.convert_to_numpy(ready["extn"]),
        query_selection,
        ready["idx_units"],
        "benchmark",
        0,
        np.arange(0, COUNTRY_CNT),
        Y,
        B,
        L,
    )


@pytest.mark.parametrize("route", sorted(ROUTE_CASES))
def test_analyze_route(benchmark, route, synthetic_matrices):
    """One analysis route end to end: index expansion, the products with L,
    aggregation, ORM labelling and the JSON result."""
    filename, dim_type, viz_type = ROUTE_CASES[route]
    query_selection = read_query(filename)
    query_selection["dimType"] = dim_type
    query_selection["vizType"] = viz_type
    analyze = make_analyze(query_selection, synthetic_matrices)

    result = benchmark(getattr(analyze, route))
    assert json.loads(result)["rawResultData"]


@pytest.mark.parametrize(
    "filename", ["validation_analytical_1_v3.csv", "validation_analytical_4_v3.csv"]
)
def test_calc_ready_selection(benchmark, filename):
    """Expanding a front-end selection into calculation indices via the ORM."""
    query_selection = read_query(filename)
    ready = benchmark(querymanagement.calc_ready_selection, query_selection)
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


def leontief_system(n, seed):
    rng = np.random.default_rng(seed)
    A = rng.random((n, n)) * (0.5 / n)
    return A


def test_leontief_solve(benchmark):
    """Full factorization of I - A and both solves the routes use."""
    n = 300
    A = leontief_system(n, 1)
    y = np.random.default_rng(2).random((n, 3))

    def solve():
        M = np.eye(n) - A
        L = LeontiefSolve(M)
        return L.dot(y), L.T.dot(y)

    x, xt = benchmark(solve)
    assert np.isfinite(x).all() and np.isfinite(xt).all()


def test_low_rank_leontief_update(benchmark):
    """Woodbury update of a published L for a changed block of A."""
    n = 300
    A = leontief_system(n, 3)
    L = np.linalg.inv(np.eye(n) - A)
    rows = np.arange(0, n, 15)
    columns = np.arange(5, n, 20)
    change = A[np.ix_(rows, columns)] * -0.2
    y = np.random.default_rng(4).random((n, 3))

    def update():
        low_rank = LowRankLeontief(L, rows, columns, change)
        return low_rank.dot(y), low_rank.T.dot(y)

    x, xt = benchmark(update)
    assert np.isfinite(x).all() and np.isfinite(xt).all()


@pytest.mark.parametrize("identifier", ["INTERMEDIATE", "FINALCONSUMPTION"])
def test_modelling_apply_model(benchmark, monkeypatch, identifier):
    """A scenario with one intervention: final-demand changes on the full Y,
    intermediate changes through the low-rank update of L."""
    n = 400
    A = leontief_system(n, 5)
    published = {"A": A, "L": np.linalg.inv(np.eye(n) - A)}
    monkeypatch.setattr(querymanagement, "get_numpy_objects",
                        lambda year, name, mmap_mode=None: published[name].copy())
    rows = MATRIX_ROWS if identifier == "FINALCONSUMPTION" else n
    Y = np.random.default_rng(6).random((rows, COUNTRY_CNT))
    selection = [[0, 1, 2]]
    one = [[0]]
    model = Modelling(
        {"product": selection, "consumedBy": selection, "originReg": one, "consumedReg": one,
         "techChange": [["-20"]], "identifiers": [identifier]},
        Y, [identifier == "INTERMEDIATE"], YEAR, None,
    )

    Y_out, L_out = benchmark(model.apply_model)
    assert Y_out.shape == Y.shape
    if identifier == "INTERMEDIATE":
        assert isinstance(L_out, LowRankLeontief)
    assert modelling.LOW_RANK_MAX > 0
