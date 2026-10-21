"""Fixtures shared by the regression harness and the benchmarks."""

import numpy as np
import pytest

COUNTRY_CNT = 49
MATRIX_ROWS = COUNTRY_CNT * 200  # products
INDICATOR_ROWS = 60  # enough for every indicator global id in the fixtures


@pytest.fixture(scope="session")
def synthetic_matrices():
    """Stand-ins with the real shapes and dtype. L is dense, non-symmetric and
    diagonally dominant, so a slip that swaps rows for columns, L for L.T or
    the order of a product changes the result; an identity L would hide all
    three. Built without an inversion, so it costs one 9800x9800 allocation.
    """
    rng = np.random.default_rng(20260926)
    Y = rng.random((MATRIX_ROWS, COUNTRY_CNT))
    B = rng.random((INDICATOR_ROWS, MATRIX_ROWS))
    L = rng.random((MATRIX_ROWS, MATRIX_ROWS))
    L *= 1e-3
    L[np.diag_indices(MATRIX_ROWS)] += 1.0
    return Y, B, L
