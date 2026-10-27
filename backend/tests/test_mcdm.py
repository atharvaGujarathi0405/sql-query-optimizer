"""Known-answer tests for AHP/TOPSIS. Expected numbers are derived by hand (see comments),
not produced by running the implementation."""
from pathlib import Path

import numpy as np
import pytest

from app.optimizer.mcdm import ahp, load_ahp, rank, topsis

AHP_YAML = Path(__file__).resolve().parents[2] / "config" / "ahp.yaml"


def test_ahp_perfectly_consistent_matrix_has_exact_weights():
    # a12=2, a13=4, a23=2 is exactly consistent -> weights proportional to (4, 2, 1)
    r = ahp([[1, 2, 4], [1 / 2, 1, 2], [1 / 4, 1 / 2, 1]])
    np.testing.assert_allclose(r.weights, [4 / 7, 2 / 7, 1 / 7], atol=1e-9)
    assert r.lambda_max == pytest.approx(3.0, abs=1e-9)
    assert r.cr == pytest.approx(0.0, abs=1e-9)


def test_ahp_slightly_inconsistent_matrix():
    # Hand check via row geometric means: (15)^(1/3), 1, (1/15)^(1/3) = 2.466, 1.000, 0.4055
    # -> weights 0.6370, 0.2583, 0.1047; A.w / w ~ 3.0385 => lambda_max 3.0385,
    # CI = 0.01925, CR = CI / 0.58 = 0.0332.
    r = ahp([[1, 3, 5], [1 / 3, 1, 3], [1 / 5, 1 / 3, 1]])
    np.testing.assert_allclose(r.weights, [0.6370, 0.2583, 0.1047], atol=2e-3)
    assert r.lambda_max == pytest.approx(3.0385, abs=1e-3)
    assert r.cr == pytest.approx(0.0332, abs=1e-3)


def test_ahp_cyclic_judgments_are_flagged_inconsistent():
    # A>B 5x, B>C 5x, C>A 5x: a preference cycle, CR far above 0.1
    r = ahp([[1, 5, 1 / 5], [1 / 5, 1, 5], [5, 1 / 5, 1]])
    assert r.cr > 0.1


def test_ahp_rejects_non_reciprocal_matrix():
    with pytest.raises(ValueError, match="reciprocal"):
        ahp([[1, 2], [2, 1]])


def test_shipped_ahp_config_is_consistent():
    criteria, benefit, res = load_ahp(AHP_YAML)
    assert criteria == ["runtime_ms", "rows_examined", "est_cost", "est_rows"]
    assert benefit == [False] * 4
    assert res.cr < 0.1  # the check the CLAUDE.md rule asks for, on the real file
    assert res.weights.sum() == pytest.approx(1.0)
    assert res.weights[0] == res.weights.max()  # runtime carries the most weight


def test_load_ahp_raises_on_inconsistent_file(tmp_path):
    bad = tmp_path / "ahp.yaml"
    bad.write_text("criteria: [a, b, c]\ndirection: {a: cost, b: cost, c: cost}\n"
                   "pairwise: [[a, b, 5], [b, c, 5], [c, a, 5]]\n")
    with pytest.raises(ValueError, match="inconsistent"):
        load_ahp(bad)


def test_topsis_benefit_criteria_known_answer():
    # A=(3,4), B=(4,3), weights (0.7,0.3), both benefit. Column norms are 5 and 5.
    # Weighted: A=(0.42,0.24), B=(0.56,0.18). Ideal=(0.56,0.24), anti=(0.42,0.18).
    # A: d+=0.14, d-=0.06 -> C=0.3.  B: d+=0.06, d-=0.14 -> C=0.7.
    c = topsis([[3, 4], [4, 3]], [0.7, 0.3], [True, True])
    np.testing.assert_allclose(c, [0.3, 0.7], atol=1e-12)
    assert rank(c) == [1, 0]


def test_topsis_cost_criteria_flip_the_answer():
    # Same numbers but both are costs: smaller is better, so A wins with 0.7.
    c = topsis([[3, 4], [4, 3]], [0.7, 0.3], [False, False])
    np.testing.assert_allclose(c, [0.7, 0.3], atol=1e-12)
    assert rank(c) == [0, 1]


def test_topsis_dominant_alternative_scores_one_and_dominated_zero():
    # Row 0 is best on both cost criteria, row 2 worst on both -> C=1 and C=0 exactly.
    c = topsis([[1, 1], [2, 2], [3, 3]], [0.5, 0.5], [False, False])
    assert c[0] == pytest.approx(1.0) and c[2] == pytest.approx(0.0)
    assert rank(c) == [0, 1, 2]


def test_topsis_identical_alternatives_tie_and_original_wins():
    c = topsis([[5, 5], [5, 5], [5, 5]], [0.5, 0.5], [False, False])
    assert list(c) == [0.5, 0.5, 0.5]
    assert rank(c)[0] == 0  # index 0 (the original) wins a tie


def test_topsis_constant_column_is_ignored():
    with_const = topsis([[1, 7], [2, 7]], [0.5, 0.5], [False, False])
    only_first = topsis([[1], [2]], [0.5], [False])
    np.testing.assert_allclose(with_const, only_first, atol=1e-12)
