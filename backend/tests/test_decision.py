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


# ---- real selection tables from dev-003 (the evidence behind the declared "measured" variant) --------
def real(id_, ms, iqr, rows, cost, est):
    return alt(id_, ms, iqr, rows=rows, cost=cost, est=est)


AP18 = [real("original", 576, 26, 1_800_003, 15_304, 148_488), real("c1", 1424, 54, 3_199_995, 673_229, 1_489_214),
        real("c2", 618, 39, 1_800_003, 15_304, 148_488), real("c3", 404, 18, 1_822_075, 536_529, 1_637_696)]
Q18 = [real("original", 2780, 10, 9_002_190, 1_649_814, 7_425_664), real("c1", 2716, 77, 9_002_190, 1_649_814, 7_425_664),
       real("c2", 6412, 23, 9_002_188, 1_649_814, 1_489_222), real("c3", 2192, 72, 6_002_302, 8_716_321, 11_872_896)]
AP17 = [real("original", 478, 7, 1_728_646, 1_641_217, 1_489_213), real("c1", 571, 14, 1_728_646, 317_439, 1_489_213)]


def test_primary_arm_keeps_the_original_on_ap18_because_estimates_outvote_runtime():
    # c3 is 30% faster but EXPLAIN prices it 35x higher: the four-criteria engine stays put (documented weakness)
    assert or_decision(AP18)["winner"] == "original"


def test_measured_variant_finds_the_faster_candidate_on_ap18_and_q18():
    d = or_decision(AP18, variant="measured")
    assert d["criteria"] == ["runtime_ms", "rows_examined"] and d["weights"] == [0.75, 0.25]
    assert d["winner"] == "c3"
    assert or_decision(Q18, variant="measured")["winner"] == "c3"
    assert or_decision(Q18)["winner"] != "c3"  # the primary arm picks c1 here (no gain)


def test_noise_guard_still_protects_the_measured_variant():
    # ap17: c1 is SLOWER; with the guard the original stays, whichever criteria are used
    assert or_decision(AP17, variant="measured")["winner"] == "original"


# --- declared arm "3-argmin": fastest measured runtime + the same noise guard (no AHP/TOPSIS)
from app.optimizer.decision import runtime_argmin_decision  # noqa: E402


def test_argmin_picks_the_fastest_and_guard_applies():
    d = runtime_argmin_decision([ORIG, alt("c0", 600.0, 5.0), BIG_WIN], noise_k=1.0)
    assert d["ranking"] == ["c1", "c0", "original"] and d["winner"] == "c1"
    # a 36 ms gain does not clear the original's 41 ms IQR; a 100 ms gain clears 20 ms
    assert runtime_argmin_decision([alt("original", 453.0, 41.0), alt("c1", 417.0, 25.0)], noise_k=1.0)["winner"] == "original"
    assert runtime_argmin_decision([alt("original", 500.0, 20.0), alt("c1", 400.0, 10.0)], noise_k=1.0)["winner"] == "c1"


def test_argmin_ignores_estimates_that_mislead_topsis():
    # the dev-003 ap18 situation: the faster candidate has a far worse EXPLAIN cost
    o, c = alt("original", 600.0, 5.0, cost=15_304.0), alt("c3", 400.0, 5.0, cost=536_529.0)
    assert runtime_argmin_decision([o, c])["winner"] == "c3"


def test_argmin_ties_go_to_the_original_and_failures_are_excluded():
    assert runtime_argmin_decision([alt("original", 10.0, 1.0), alt("c1", 10.0, 1.0)], noise_k=0)["winner"] == "original"
    d = runtime_argmin_decision([ORIG, alt("c1", 0, 0, outcome="timeout")])
    assert d["winner"] == "original" and d["excluded"] == {"c1": "timeout"}
    assert runtime_argmin_decision([alt("original", 0, 0, outcome="error")])["winner"] is None
