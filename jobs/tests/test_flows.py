"""embodied_flows on a hand-built case: no engine."""

import numpy as np
import pytest

from ramascene_jobs.worker import embodied_flows


def test_a_flow_is_b_times_x_summed_over_the_products_of_a_section() -> None:
    """2 countries x 3 products; sections {0, 2} and {1}."""
    x = np.arange(1.0, 13.0).reshape(6, 2)  # rows country-major, one column per consumer
    b = np.array([[1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]])
    out = embodied_flows(b, x, [2, 1], [[0, 2], [1]])  # indicator ids are 1-based rows of b
    assert out.shape == (2, 2, 2, 2)
    # indicator 2 (row 1), producer 1, section {1}, consumer 0: product 1 of country 1 is row 4.
    assert out[0, 1, 1, 0] == b[1, 4] * x[4, 0]
    # indicator 1 (row 0), producer 0, section {0, 2}, consumer 1: rows 0 and 2.
    assert out[1, 0, 0, 1] == b[0, 0] * x[0, 1] + b[0, 2] * x[2, 1]
    # Summed over everything, the flows are B . X.
    assert out[1].sum() == pytest.approx((b[0][:, None] * x).sum())


@pytest.mark.parametrize("leaves", [[[0], [1]], [[0, 1], [1, 2]], [[0, 1, 2, 3]]])
def test_sections_that_do_not_partition_the_products_raise(leaves: list[list[int]]) -> None:
    """A missing, doubled or unknown product is an error."""
    with pytest.raises(ValueError, match="partition"):
        embodied_flows(np.ones((1, 6)), np.ones((6, 2)), [1], leaves)
