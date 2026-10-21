"""Benchmarks of the worker's own numpy work, on seeded synthetic inputs of the real shapes.

CodSpeed measures them in CI (`pytest --codspeed`); a plain `pytest` runs each once
as a test. No engine and no dataset: EXIOBASE is 49 countries x 200 products, B has
one row per extension, and the flows use 15 sections.
"""

import hashlib
from typing import TYPE_CHECKING, Any

import numpy as np
import pytest

from ramascene_jobs.worker import INDICATORS, embodied_flows, flows_of, sha256

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_codspeed import BenchmarkFixture

COUNTRIES, PRODUCTS, SECTIONS, EXTENSIONS = 49, 200, 15, 58


@pytest.fixture(scope="module")
def inputs() -> tuple[Any, Any, list[list[int]]]:
    """B (extensions x 9800), Y (9800 x 49) and 15 sections that partition the 200 products."""
    rng = np.random.default_rng(2011)
    b = rng.random((EXTENSIONS, COUNTRIES * PRODUCTS))
    y = rng.random((COUNTRIES * PRODUCTS, COUNTRIES))
    owner = rng.permutation(np.arange(PRODUCTS) % SECTIONS)  # every section gets at least one product
    leaves = [np.flatnonzero(owner == s).tolist() for s in range(SECTIONS)]
    return b, y, leaves


class Diagonal:
    """A stand-in for L with L.dot, so flows_of runs without a 0.77 GB matrix."""

    def __init__(self, d: Any) -> None:  # noqa: ANN401
        self.d = d

    def dot(self, y: Any) -> Any:  # noqa: ANN401
        """D . y."""
        return self.d[:, None] * y


def test_embodied_flows(benchmark: BenchmarkFixture, inputs: tuple[Any, Any, list[list[int]]]) -> None:
    """Three indicators, full size; the flows sum to B_i . X."""
    b, x, leaves = inputs
    out = benchmark(embodied_flows, b, x, INDICATORS, leaves)
    assert out.shape == (3, COUNTRIES, SECTIONS, COUNTRIES)
    assert out[1].sum() == pytest.approx((b[INDICATORS[1] - 1] @ x).sum())


def test_flows_of(benchmark: BenchmarkFixture, inputs: tuple[Any, Any, list[list[int]]]) -> None:
    """L . Y, then the flows and the finiteness check."""
    b, y, leaves = inputs
    leontief = Diagonal(np.linspace(1.0, 2.0, COUNTRIES * PRODUCTS))
    out = benchmark(flows_of, y, b, leontief, leaves)
    assert out[0].sum() == pytest.approx((b[INDICATORS[0] - 1] @ leontief.dot(y)).sum())


def test_sha256(benchmark: BenchmarkFixture, tmp_path: Path) -> None:
    """One dataset file's hash; 16 MiB here, the real A and L are 733 MB each."""
    data = np.random.default_rng(0).bytes(16 * 2**20)
    path = tmp_path / "A_v4.npy"
    path.write_bytes(data)
    assert benchmark(sha256, path) == hashlib.sha256(data).hexdigest()
