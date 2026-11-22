"""Workload Advisor: ILP known answers, candidate parsing, noise rule, dev-only guard, and 'indexes are always dropped'."""
import pytest

from app.advisor.advise import CleanupError, dev_workload, index_name, indexes_created, sweep_stale
from app.advisor.candidates import analyze
from app.advisor.ilp import predicted, solve_greedy, solve_ilp
from app.db import get_admin_conn


# ---- ILP / greedy: hand-computed answers -------------------------------------------------------------------

def test_ilp_beats_greedy_on_the_classic_knapsack_trap():
    # budget 10. A: 60 ms for 6 MB (best ratio 10/MB), B and C: 45 ms for 5 MB each (9/MB).
    # Greedy takes A, then nothing fits (4 MB left): 60. Optimum is B + C = 90.
    benefit = {("q1", "A"): 60, ("q2", "B"): 45, ("q3", "C"): 45}
    size = {"A": 6, "B": 5, "C": 5}
    ilp, greedy = solve_ilp(benefit, size, 10, 3), solve_greedy(benefit, size, 10, 3)
    assert ilp["chosen"] == ["B", "C"] and ilp["predicted_ms"] == 90
    assert greedy["chosen"] == ["A"] and greedy["predicted_ms"] == 60


def test_two_indexes_helping_the_same_query_do_not_add_up():
    benefit = {("q1", "A"): 10, ("q1", "B"): 12}
    assert predicted({"A", "B"}, benefit) == 12  # max per query, not the sum 22
    r = solve_ilp(benefit, {"A": 1, "B": 1}, 100, 5)
    assert r["chosen"] == ["B"] and r["predicted_ms"] == 12  # the redundant index is not bought


def test_cap_and_budget_are_respected_and_empty_is_fine():
    benefit = {("q1", "A"): 5, ("q2", "B"): 4, ("q3", "C"): 3}
    size = {"A": 1, "B": 1, "C": 1}
    assert solve_ilp(benefit, size, 100, 2)["chosen"] == ["A", "B"]      # cap 2
    assert solve_ilp(benefit, size, 1.5, 3)["chosen"] == ["A"]           # budget fits one
    assert solve_ilp({}, size, 10, 3)["chosen"] == [] and solve_greedy({}, size, 10, 3)["chosen"] == []


def test_a_beneficial_index_that_does_not_fit_is_never_chosen():
    r = solve_ilp({("q1", "A"): 1000}, {"A": 50}, 10, 3)
    assert r["chosen"] == [] and r["predicted_ms"] == 0


# ---- noise rule -------------------------------------------------------------------------------------------

def test_effect_counts_only_what_every_round_agrees_on():
    from app.advisor.advise import effect_ms
    assert effect_ms([100, 120], [50, 60], 15) == 55           # both rounds halve the time: mean saving
    assert effect_ms([100, 120], [50, 118], 15) == 0           # one round disagrees: drift, not an index
    assert effect_ms([100, 100], [95, 96], 15) == 0            # consistent but under 15%
    assert effect_ms([100, 120], [150, 190], 15) == -60        # consistent slowdown is a (negative) effect
    assert effect_ms([100, 120], [150, 100], 15) == 0
    assert effect_ms([], [], 15) == 0


# ---- candidate parsing (no database: a synthetic schema) ---------------------------------------------------

TABLES = {"orders": ("orders", {c: c for c in ("id", "cust", "status", "created", "total")}),
          "cust": ("cust", {c: c for c in ("id", "name", "region")})}


def test_analyze_classifies_predicates():
    u = analyze("SELECT o.id FROM orders o JOIN cust c ON c.id = o.cust "
                "WHERE o.status = 'x' AND o.created > '2020-01-01' AND c.name LIKE 'ab%' AND c.region LIKE '%z' "
                "AND YEAR(o.total) = 3 ORDER BY o.total", TABLES)
    assert u["orders"]["eq"] == ["cust", "status"] and u["orders"]["range"] == ["created"]
    assert u["orders"]["order"] == ["total"]                      # YEAR(total) is skipped, ORDER BY total kept
    assert u["cust"]["eq"] == ["id"] and u["cust"]["range"] == ["name"]   # prefix LIKE yes, leading wildcard no


def test_unqualified_ambiguous_columns_are_skipped():
    u = analyze("SELECT 1 FROM orders o JOIN cust c ON c.id = o.cust WHERE id = 5 AND status = 'x'", TABLES)
    assert u["orders"]["eq"] == ["cust", "status"] and "id" not in u["cust"]["eq"][1:]


# ---- safety: dev only, and indexes are always dropped ------------------------------------------------------

def test_workload_must_be_dev_split_of_that_dataset():
    import yaml
    from app.eval.runner import ROOT
    test_id = yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())["test"][0]
    with pytest.raises(ValueError, match="dev-split"):
        dev_workload("sakila", [test_id])
    with pytest.raises(ValueError):
        dev_workload("sakila", ["ap03_correlated_subquery", "emp_current_managers"])  # wrong dataset
    assert "ap03_correlated_subquery" in dev_workload("sakila", None)


def _adv_indexes(conn, dataset="sakila"):
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT index_name FROM information_schema.statistics WHERE table_schema=%s AND index_name LIKE 'adv\\_%%'", (dataset,))
    return [r[0] for r in cur.fetchall()]


@pytest.fixture
def admin():
    conn = get_admin_conn()
    conn.autocommit = True
    sweep_stale(conn, "sakila")
    yield conn
    sweep_stale(conn, "sakila")  # the test must never leave an index behind, even when it fails
    conn.close()


CAND = {"table": "payment", "cols": ["amount", "customer_id"], "name": index_name("payment", ["amount", "customer_id"])}


def test_index_exists_inside_and_is_dropped_after(admin):
    with indexes_created(admin, "sakila", [CAND]):
        assert _adv_indexes(admin) == [CAND["name"]]
    assert _adv_indexes(admin) == []


def test_index_is_dropped_when_the_experiment_fails(admin):
    with pytest.raises(RuntimeError, match="boom"):
        with indexes_created(admin, "sakila", [CAND]):
            assert _adv_indexes(admin) == [CAND["name"]]
            raise RuntimeError("boom")
    assert _adv_indexes(admin) == []


def test_a_failing_create_still_cleans_up_the_ones_before_it(admin):
    bad = {"table": "payment", "cols": ["no_such_column"], "name": index_name("payment", ["no_such_column"])}
    with pytest.raises(Exception):
        with indexes_created(admin, "sakila", [CAND, bad]):
            pass
    assert _adv_indexes(admin) == []


def test_sweep_removes_a_leftover_from_a_killed_run(admin):
    cur = admin.cursor()
    cur.execute(f"CREATE INDEX `{CAND['name']}` ON sakila.payment (amount)")
    assert _adv_indexes(admin) == [CAND["name"]]
    assert sweep_stale(admin, "sakila") == [f"payment.{CAND['name']}"]
    assert _adv_indexes(admin) == []


def test_cleanup_error_type_exists_for_the_unrecoverable_case():
    assert issubclass(CleanupError, Exception)


# ---- API --------------------------------------------------------------------------------------------------

def test_api_refuses_test_split_and_unknown_dataset_before_any_ddl():
    import yaml
    from fastapi.testclient import TestClient

    from app.eval.runner import ROOT
    from app.main import app
    c = TestClient(app)
    test_id = yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())["test"][0]
    assert c.post("/advisor/run", json={"dataset": "sakila", "query_ids": [test_id]}).status_code == 422
    assert c.post("/advisor/run", json={"dataset": "nope"}).status_code == 422
    assert c.post("/advisor/run", json={"dataset": "sakila", "budget_mb": -1}).status_code == 422
    assert c.get("/advisor/jobs/missing").status_code == 404


def test_a_candidate_that_absorbs_an_implicit_fk_index_fails_cleanly_and_the_schema_is_restored(admin):
    # The employees.dept_emp incident on a scratch table: the FK's implicit index `fk_probe_a` is dropped by MySQL when
    # a wider index leading with the same column is created, and the FK then latches onto the new index.
    cur = admin.cursor()
    cur.execute("DROP TABLE IF EXISTS sakila.zz_adv_probe")
    cur.execute("CREATE TABLE sakila.zz_adv_probe (id INT PRIMARY KEY, a TINYINT UNSIGNED NOT NULL, b INT, "
                "CONSTRAINT fk_probe_a FOREIGN KEY (a) REFERENCES sakila.language (language_id))")
    try:
        wide = {"table": "zz_adv_probe", "cols": ["a", "b"], "name": index_name("zz_adv_probe", ["a", "b"])}
        with pytest.raises(CleanupError, match="dropped existing"):
            with indexes_created(admin, "sakila", [wide]):
                raise AssertionError("must not be measured")
        assert _adv_indexes(admin) == []
        cur.execute("SELECT index_name FROM information_schema.statistics WHERE table_schema='sakila' AND table_name='zz_adv_probe' "
                    "AND column_name='a' AND seq_in_index=1")
        assert [r[0] for r in cur.fetchall()] == ["fk_probe_a"]  # the FK's own index is back, under its old name
    finally:
        cur.execute("DROP TABLE IF EXISTS sakila.zz_adv_probe")


def test_schema_fingerprint_guard_restores_an_index_lost_during_an_experiment(admin):
    cur = admin.cursor()
    cur.execute("CREATE INDEX `zz_guard_probe` ON sakila.payment (amount)")
    try:
        with pytest.raises(CleanupError, match="zz_guard_probe"):
            with indexes_created(admin, "sakila", [CAND]):
                cur.execute("DROP INDEX `zz_guard_probe` ON sakila.payment")  # what MySQL did to dept_emp
        cur.execute("SELECT COUNT(*) FROM information_schema.statistics WHERE table_schema='sakila' AND index_name='zz_guard_probe'")
        assert cur.fetchone()[0] == 1  # rebuilt
        assert _adv_indexes(admin) == []
    finally:
        cur.execute("DROP INDEX `zz_guard_probe` ON sakila.payment")


def test_an_index_that_breaks_another_query_is_charged_for_it():
    # A saves 100 ms on q1 but slows q2 by 150 ms. Gains alone would buy it; net it loses 50 ms. B is a clean 30 ms.
    benefit = {("q1", "A"): 100, ("q3", "B"): 30}
    size, penalty = {"A": 1, "B": 1}, {"A": 150}
    assert solve_ilp(benefit, size, 10, 3)["chosen"] == ["A", "B"]                      # blind to regressions
    r = solve_ilp(benefit, size, 10, 3, penalty)
    assert r["chosen"] == ["B"] and r["predicted_ms"] == 30
    assert solve_greedy(benefit, size, 10, 3, penalty)["chosen"] == ["B"]
    assert predicted({"A", "B"}, benefit, penalty) == 130 - 150


def test_experimental_index_is_invisible_while_it_is_toggled(admin):
    cur = admin.cursor()
    with indexes_created(admin, "sakila", [CAND], invisible=True):
        cur.execute("SELECT is_visible FROM information_schema.statistics WHERE table_schema='sakila' AND index_name=%s LIMIT 1", (CAND["name"],))
        assert cur.fetchone()[0] == "NO"
        cur.execute(f"ALTER TABLE sakila.payment ALTER INDEX `{CAND['name']}` VISIBLE")
        cur.execute("SELECT is_visible FROM information_schema.statistics WHERE table_schema='sakila' AND index_name=%s LIMIT 1", (CAND["name"],))
        assert cur.fetchone()[0] == "YES"
    assert _adv_indexes(admin) == []
