import math

import pytest

from evals.metrics import Comparison, compare, pass_at_k, pass_hat_k, standard_error


def test_pass_at_k_examples():
    assert pass_at_k(3, 1, 1) == pytest.approx(1 / 3)
    assert pass_at_k(3, 0, 3) == 0
    assert pass_at_k(3, 1, 3) == 1
    assert pass_at_k(5, 2, 2) == pytest.approx(1 - 3 / 10)


def test_pass_hat_k_examples():
    assert pass_hat_k(3, 3, 3) == 1
    assert pass_hat_k(3, 2, 3) == 0
    assert pass_hat_k(4, 3, 2) == pytest.approx(3 / 6)


@pytest.mark.parametrize("fn", [pass_at_k, pass_hat_k])
def test_k_greater_than_n_raises(fn):
    with pytest.raises(ValueError):
        fn(3, 1, 4)


@pytest.mark.parametrize("fn", [pass_at_k, pass_hat_k])
@pytest.mark.parametrize("args", [(0, 0, 1), (3, 4, 1), (3, -1, 1), (3, 1, 0)])
def test_invalid_counts_raise(fn, args):
    with pytest.raises(ValueError):
        fn(*args)


def test_standard_error():
    assert standard_error([]) == 0.0
    assert standard_error([1.0]) == 0.0
    xs = [1, 1, 0, 0]
    sd = math.sqrt(sum((x - 0.5) ** 2 for x in xs) / 3)
    assert standard_error(xs) == pytest.approx(sd / 2)


def test_compare_flags_big_drop_and_ignores_small_one():
    base = [1.0] * 85 + [0.0] * 15  # 0.85
    big_drop = [1.0] * 70 + [0.0] * 30  # 0.70
    c = compare(base, big_drop)
    assert isinstance(c, Comparison)
    assert c.regressed is True
    assert c.pass_delta == pytest.approx(-0.15)
    # 0.03 drop at the same variance
    cur = [1.0] * 82 + [0.0] * 18
    assert compare(base, cur).regressed is False


def test_compare_threshold_is_max_of_floor_and_two_se():
    # tiny sample => huge standard error => a large drop is not flagged
    c = compare([1.0, 0.0], [0.0, 0.0])
    assert c.threshold > 0.05
    assert c.regressed is False


def test_compare_cost_regression():
    ok = [1.0] * 10
    assert compare(ok, ok, baseline_cost_per_pass=1.0, current_cost_per_pass=1.2).cost_regressed
    assert not compare(ok, ok, baseline_cost_per_pass=1.0, current_cost_per_pass=1.1).cost_regressed
    assert not compare(ok, ok).cost_regressed
    assert compare(ok, ok, baseline_cost_per_pass=1.0, current_cost_per_pass=1.2).regressed


def test_compare_empty_raises():
    with pytest.raises(ValueError):
        compare([], [1.0])
