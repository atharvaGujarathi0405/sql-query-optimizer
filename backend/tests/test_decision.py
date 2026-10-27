"""OR-engine decision on hand-built metric tables: no database, no timing noise."""
from app.optimizer.decision import or_decision


def alt(id_, runtime, iqr, rows=1000, cost=100.0, est=1000.0, outcome="ok"):
    return {"id": id_, "metrics": {"outcome": outcome, "runtime_ms_median": runtime if outcome == "ok" else None,
                                   "runtime_ms_iqr": iqr if outcome == "ok" else None, "rows_examined": rows,
                                   "est_cost": cost, "est_rows": est}}


ORIG = alt("original", 1000.0, 50.0, rows=2_000_000, cost=5000.0, est=2_000_000)
BIG_WIN = alt("c1", 2.0, 0.1, rows=17, cost=2.0, est=17)


def test_clear_win_is_chosen_and_guard_does_not_interfere():
    d = or_decision([ORIG, BIG_WIN], noise_k=1.0)
    assert d["winner"] == d["winner_raw"] == "c1" and d["ranking"] == ["c1", "original"]
    assert d["guard"]["applied"] is False
    assert d["closeness"]["c1"] == 1.0 and d["closeness"]["original"] == 0.0


def test_identical_plans_with_noise_gap_raw_topsis_picks_noise_guard_keeps_original():
    # The ap19 situation: same plan, same cost, same rows; runtimes differ by less than the noise.
    slower = alt("c1", 453.0, 41.0)
    fast_orig = alt("original", 417.0, 25.0)
    d = or_decision([fast_orig, slower], noise_k=1.0)
    assert d["winner"] == "original"
    # reverse the roles: the rewrite is 36 ms faster, but the original's IQR is 41 ms -> still not enough
    d = or_decision([alt("original", 453.0, 41.0), alt("c1", 417.0, 25.0)], noise_k=1.0)
    assert d["winner_raw"] == "c1"  # raw TOPSIS would switch on a 36 ms (8%) difference...
    assert d["winner"] == "original" and d["guard"]["applied"] is True  # ...the guard does not
    assert "<=" in d["guard"]["reason"]


def test_guard_lets_a_gain_beyond_the_iqr_through():
    d = or_decision([alt("original", 500.0, 20.0), alt("c1", 400.0, 10.0)], noise_k=1.0)  # gain 100 > 20
    assert d["winner"] == "c1" and not d["guard"]["applied"]


def test_guard_boundary_gain_equal_to_threshold_is_not_enough():
    d = or_decision([alt("original", 500.0, 20.0), alt("c1", 480.0, 5.0)], noise_k=1.0)  # gain == 20 == 1 x IQR
    assert d["winner_raw"] == "c1" and d["winner"] == "original"


def test_k_zero_disables_the_guard():
    d = or_decision([alt("original", 453.0, 41.0), alt("c1", 417.0, 25.0)], noise_k=0)
    assert d["winner"] == d["winner_raw"] == "c1"


def test_exact_tie_goes_to_the_original():
    d = or_decision([alt("original", 10.0, 1.0), alt("c1", 10.0, 1.0)], noise_k=0)
    assert d["winner"] == "original" and d["ranking"][0] == "original"


def test_timed_out_candidate_is_excluded_not_ranked():
    d = or_decision([ORIG, alt("c1", 0, 0, outcome="timeout"), BIG_WIN.copy() | {"id": "c2"}], noise_k=1.0)
    assert d["excluded"] == {"c1": "timeout"} and "c1" not in d["ranking"] and d["winner"] == "c2"


def test_original_that_cannot_run_means_no_guard_and_a_candidate_may_win():
    d = or_decision([alt("original", 0, 0, outcome="timeout"), alt("c1", 100.0, 5.0)], noise_k=1.0)
    assert d["winner"] == "c1" and d["excluded"] == {"original": "timeout"}


def test_nothing_rankable_gives_no_winner():
    d = or_decision([alt("original", 0, 0, outcome="timeout"), alt("c1", 0, 0, outcome="error")], noise_k=1.0)
    assert d["winner"] is None and d["ranking"] == []


def test_missing_metric_excludes_the_alternative():
    broken = alt("c1", 5.0, 1.0)
    broken["metrics"]["est_cost"] = None
    d = or_decision([ORIG, broken], noise_k=1.0)
    assert d["excluded"] == {"c1": "missing metric"} and d["winner"] == "original"


def test_weights_come_from_the_fixed_config_with_cr_below_limit():
    d = or_decision([ORIG, BIG_WIN])
    assert d["criteria"] == ["runtime_ms", "rows_examined", "est_cost", "est_rows"]
    assert abs(sum(d["weights"]) - 1) < 1e-5 and d["ahp"]["consistency_ratio"] < 0.1
