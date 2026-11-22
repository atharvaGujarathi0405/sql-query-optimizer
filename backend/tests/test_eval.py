"""Evaluation harness: scoring rules, statistics (hand-checked), and an end-to-end runner smoke test
with a scripted LLM against the real database."""
import json

import pytest

from app.db import get_readonly_conn
from app.eval import report as R
from app.eval.runner import run, select_queries
from app.optimizer.candidates import generate_pool


# ---- scoring -------------------------------------------------------------------------------------
def m(median, iqr, outcome="ok"):
    return {"outcome": outcome, "runtime_ms_median": median, "runtime_ms_iqr": iqr}


@pytest.mark.parametrize("pick_ms,expected", [
    (85, "win"), (89, "win"), (91, "tie"), (95, "tie"), (109, "tie"), (111, "loss"), (150, "loss"),
])
def test_win_tie_loss_uses_the_wider_of_iqr_and_percentage_band(pick_ms, expected):
    # baseline 100 ms, IQR 10 ms, 5% = 5 ms -> band 10 ms
    assert R.classify(m(100, 10), m(pick_ms, 1), False, 5)[0] == expected


def test_percentage_band_applies_when_iqr_is_tiny():
    assert R.classify(m(100, 1), m(94, 1), False, 5)[0] == "win"   # 6 ms > max(1, 5)
    assert R.classify(m(100, 1), m(96, 1), False, 5)[0] == "tie"   # 4 ms < 5


def test_picking_the_original_is_the_baseline_time_and_a_tie():
    assert R.classify(m(100, 10), m(100, 10), True, 5) == ("tie", 1.0)


def test_timeouts_errors_and_failed_baselines_are_their_own_outcomes_not_runtimes():
    assert R.classify(m(100, 1), m(0, 0, "timeout"), False, 5) == ("timeout", None)
    assert R.classify(m(100, 1), m(0, 0, "error"), False, 5) == ("error", None)
    assert R.classify(m(0, 0, "timeout"), m(5, 1), False, 5) == ("baseline_failed", None)


# ---- statistics: expected values worked out by hand ---------------------------------------------
def test_wilcoxon_all_one_direction_n6():
    # d = -1..-6: W+ = 0, so r = -1; exact two-sided p = 2 * (1/2**6) = 0.03125
    r = R.wilcoxon_paired([-1, -2, -3, -4, -5, -6], [0] * 6)
    assert r["r"] == pytest.approx(-1.0) and r["p"] == pytest.approx(0.03125)
    assert r["n"] == 6 and r["n_zero"] == 0


def test_wilcoxon_mixed_signs_n3():
    # d = +3, -1, +2: ranks 3,1,2; W+ = 5, W- = 1 -> r = 4/6; P(W<=1) = 2/8 -> two-sided p = 0.5
    r = R.wilcoxon_paired([3, -1, 2], [0, 0, 0])
    assert r["r"] == pytest.approx(2 / 3) and r["p"] == pytest.approx(0.5)


def test_wilcoxon_drops_and_counts_identical_pairs():
    r = R.wilcoxon_paired([1, 2, 3, 4], [1, 2, 3, 3])
    assert r["n"] == 4 and r["n_zero"] == 3 and r["r"] == pytest.approx(1.0)


def test_wilcoxon_with_nothing_to_test_says_so_instead_of_inventing_a_p():
    r = R.wilcoxon_paired([1, 2], [1, 2])
    assert r["p"] is None and r["r"] is None and r["n_zero"] == 2


# ---- query selection -----------------------------------------------------------------------------
def test_limit_takes_queries_round_robin_across_datasets():
    qs = select_queries("dev", limit=6)
    assert len(qs) == 6 and {q["dataset"] for q in qs} == {"employees", "sakila", "tpch"}
    assert [q["dataset"] for q in qs[:3]] == ["employees", "sakila", "tpch"]
    assert select_queries("dev", limit=6) == qs  # deterministic


def test_explicit_ids_outside_the_split_are_refused():
    with pytest.raises(SystemExit):
        select_queries("dev", ids=[select_queries("test", limit=1)[0]["id"]])


# ---- end to end: scripted LLM, real MySQL ---------------------------------------------------------
Q_GOOD, Q_ALSO, Q_NOPOOL = "ap02_function_on_indexed_column", "ap05_leading_wildcard_like", "sak_films_per_category"
REWRITE = ("SELECT rental_id, customer_id, inventory_id FROM rental "
           "WHERE rental_date >= '2005-07-08' AND rental_date < '2005-07-09'")
SAME_RESULT = "SELECT film_id, title FROM film WHERE title LIKE '%LOVE%' AND film_id > 0"  # a valid same-result rewrite


@pytest.fixture
def pools(tmp_path, fake_llm):
    pool_dir = tmp_path / "pools"
    qs = {q["id"]: q for q in select_queries("all", ids=[Q_GOOD, Q_ALSO, Q_NOPOOL])}
    with get_readonly_conn() as conn:
        generate_pool(conn, fake_llm([REWRITE]), Q_GOOD, "sakila", qs[Q_GOOD]["sql"], pool_dir=pool_dir)
        generate_pool(conn, fake_llm([SAME_RESULT]), Q_ALSO, "sakila", qs[Q_ALSO]["sql"], pool_dir=pool_dir)
    return pool_dir, list(qs.values()), tmp_path / "results"


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_runner_writes_every_query_every_repeat_and_every_failure(pools, fake_llm):
    pool_dir, qs, out = pools
    llm = fake_llm(text='{"choice": "B", "reason": "scripted"}')
    path = run("t1", "ollama", qs, client=llm, split="all", pool_dir=pool_dir, out_dir=out, repeats=2)
    recs = records(path)
    meta = recs[0]
    assert meta["type"] == "run_meta" and meta["provider"] == "ollama" and meta["model"] == "fake-model"
    env = meta["environment"]  # rule 7: version, live buffer pool, collation, hardware in every results file
    assert env["mysql"]["version"] == "8.4.13" and env["mysql"]["innodb_buffer_pool_size"] == 3 * 1024**3
    assert env["mysql"]["collation_server"] == "utf8mb4_0900_ai_ci" and env["hardware"]["cpu"]
    assert meta["config_sha256"]["ahp.yaml"] and meta["data_sha256"]["splits.yaml"]
    assert recs[-1]["type"] == "run_end"

    for qid in (Q_GOOD, Q_ALSO):
        arms = [r for r in recs if r["type"] == "arm" and r["query_id"] == qid]
        by = {a: [r for r in arms if r["arm"] == a] for a in ("1", "2a", "2b", "3", "3-measured", "3-raw")}
        assert [len(by[a]) for a in ("1", "2a", "2b", "3", "3-measured", "3-raw")] == [1, 2, 2, 1, 1, 1]
        assert by["3-measured"][0]["criteria"] == ["runtime_ms", "rows_examined"]
        assert by["3"][0]["criteria"] == ["runtime_ms", "rows_examined", "est_cost", "est_rows"]
        phases = {(r["phase"], r["alt_id"]) for r in recs if r["type"] == "measurement" and r["query_id"] == qid}
        assert {("selection", "original"), ("evaluation", "original"), ("selection", "c1"), ("evaluation", "c1")} <= phases
        assert all(r["prompt_sha256"] and r["llm_provider"] == "ollama" for r in by["2a"] + by["2b"])

    nopool = [r for r in recs if r["type"] == "failure" and r["query_id"] == Q_NOPOOL]
    assert [f["kind"] for f in nopool] == ["pool_missing"]
    assert {r["pick"] for r in recs if r["type"] == "arm" and r["query_id"] == Q_NOPOOL} == {"original"}  # scored as baseline


def test_2a_and_2b_prompts_differ_only_by_the_metric_table(pools, fake_llm):
    pool_dir, qs, out = pools
    seen = []
    llm = fake_llm(text='{"choice": "A"}')
    orig_chat = llm.chat
    llm.chat = lambda msgs, **kw: (seen.append(msgs[1]["content"]), orig_chat(msgs, **kw))[1]
    run("t2", "ollama", qs[:1], client=llm, split="all", pool_dir=pool_dir, out_dir=out, repeats=1)
    assert len(seen) == 2 and "Measurements for each option" not in seen[0] and "Measurements for each option" in seen[1]
    assert seen[1].startswith(seen[0].replace("\n\nWhich option is fastest? Answer with JSON only.", ""))


def test_llm_failures_are_logged_and_scored_as_the_original(pools, fake_llm):
    pool_dir, qs, out = pools
    path = run("t3", "ollama", qs[:1], client=fake_llm(fail=True), split="all", pool_dir=pool_dir, out_dir=out, repeats=2)
    recs = records(path)
    fails = [r for r in recs if r["type"] == "failure"]
    assert [f["kind"] for f in fails] == ["llm_failure"] * 4 and all(f["stage"].startswith("arm_2") for f in fails)
    llm_arms = [r for r in recs if r["type"] == "arm" and r["arm"] in ("2a", "2b")]
    assert len(llm_arms) == 4 and all(a["pick"] == "original" and a["fallback"] for a in llm_arms)


@pytest.mark.parametrize("answer,kind", [("I would pick the second one.", "parse_error"), ('{"choice": "Q"}', "invalid_choice")])
def test_unusable_answers_are_logged_by_kind(pools, fake_llm, answer, kind):
    pool_dir, qs, out = pools
    path = run("t4", "ollama", qs[:1], client=fake_llm(text=answer), split="all", pool_dir=pool_dir, out_dir=out, repeats=1)
    assert {r["kind"] for r in records(path) if r["type"] == "failure"} == {kind}


def test_a_pool_from_another_provider_is_refused_not_used(tmp_path, fake_llm):
    pool_dir, qs = tmp_path / "p", select_queries("all", ids=[Q_GOOD])
    with get_readonly_conn() as conn:
        generate_pool(conn, fake_llm([REWRITE], provider="groq"), Q_GOOD, "sakila", qs[0]["sql"], pool_dir=pool_dir)
    path = run("t5", "ollama", qs, client=fake_llm(text='{"choice":"B"}'), split="all", pool_dir=pool_dir,
               out_dir=tmp_path / "r", repeats=1)
    recs = records(path)
    assert [r["kind"] for r in recs if r["type"] == "failure"] == ["pool_provider_mismatch"]
    assert next(r for r in recs if r["type"] == "query")["n_valid"] == 0


def test_a_run_never_overwrites_a_previous_results_file(pools, fake_llm):
    pool_dir, qs, out = pools
    run("t6", "ollama", qs[:1], client=fake_llm(text='{"choice":"A"}'), split="all", pool_dir=pool_dir, out_dir=out, repeats=1)
    with pytest.raises(FileExistsError):
        run("t6", "ollama", qs[:1], client=fake_llm(text='{"choice":"A"}'), split="all", pool_dir=pool_dir, out_dir=out, repeats=1)


def test_report_covers_every_section_and_counts_failures(pools, fake_llm):
    pool_dir, qs, out = pools
    path = run("t7", "ollama", qs, client=fake_llm(text='{"choice": "B"}'), split="all", pool_dir=pool_dir, out_dir=out, repeats=2)
    text = R.report(path)
    for section in ("PER-ARM RESULTS", "LLM VARIANCE", "PAIRED COMPARISONS", "FAILURES", "MySQL 8.4.13", "buffer_pool=3.0 GiB",
                    "3  OR engine", "3m OR, measured only", "2a LLM", "pool_missing", "Wilcoxon"):
        assert section in text, section
    assert "complete=True" in text


def test_an_unreachable_provider_is_a_separate_failure_kind_with_the_same_scoring(pools, fake_llm):
    pool_dir, qs, out = pools
    path = run("t8", "ollama", qs[:1], client=fake_llm(fail="ConnectError: [Errno 11001] getaddrinfo failed"),
               split="all", pool_dir=pool_dir, out_dir=out, repeats=1)
    recs = records(path)
    assert {r["kind"] for r in recs if r["type"] == "failure"} == {"llm_unreachable"}
    assert all(a["pick"] == "original" and a["fallback"] for a in recs if a["type"] == "arm" and a["arm"] in ("2a", "2b"))


def test_bootstrap_ci_is_seeded_and_brackets_the_mean():
    from app.eval.report import bootstrap_ci
    d = [-0.5, -0.4, -0.6, -0.45, -0.55]
    lo, hi = bootstrap_ci(d)
    assert (lo, hi) == bootstrap_ci(d) and lo <= sum(d) / len(d) <= hi and hi < 0
    assert bootstrap_ci([0.0, 0.0, 0.0]) == (0.0, 0.0) and bootstrap_ci([0.1]) is None
