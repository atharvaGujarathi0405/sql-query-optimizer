import json

import pytest

from app.db import get_readonly_conn
from app.optimizer.execution import open_session
from app.optimizer.metrics import collect, parse_explain

SIMPLE = json.dumps({"query_block": {"select_id": 1, "cost_info": {"query_cost": "2.35"},
                                     "table": {"table_name": "t", "rows_examined_per_scan": 10}}})
JOIN = json.dumps({"query_block": {"select_id": 1, "cost_info": {"query_cost": "100.5"}, "nested_loop": [
    {"table": {"table_name": "a", "rows_examined_per_scan": 40}},
    {"table": {"table_name": "b", "rows_examined_per_scan": 2}}]}})
UNION = json.dumps({"query_block": {"union_result": {"query_specifications": [
    {"query_block": {"select_id": 1, "cost_info": {"query_cost": "1.5"}, "table": {"rows_examined_per_scan": 3}}},
    {"query_block": {"select_id": 2, "cost_info": {"query_cost": "2.5"}, "table": {"rows_examined_per_scan": 4}}}]}}})


def test_parse_explain_simple_join_and_union():
    assert parse_explain(SIMPLE) == (2.35, 10.0)
    assert parse_explain(JOIN) == (100.5, 42.0)  # rows: sum of per-scan estimates, no multiplication
    assert parse_explain(UNION) == (4.0, 7.0)  # cost: sum of members


def test_parse_explain_without_cost_is_none():
    assert parse_explain(json.dumps({"query_block": {"message": "No tables used"}})) == (None, None)


@pytest.fixture
def cur():
    with get_readonly_conn() as conn:
        yield open_session(conn, "sakila", timeout_s=2)


def test_collect_real_query(cur):
    m = collect(cur, "SELECT customer_id, SUM(amount) FROM payment GROUP BY customer_id", warmup=1, runs=5)
    assert m.outcome == "ok"
    assert len(m.runs_ms) == 5 and min(m.runs_ms) <= m.runtime_ms_median <= max(m.runs_ms)
    assert m.runtime_ms_iqr >= 0
    assert m.rows_examined and m.rows_examined >= 16000  # it must read the 16044 payments
    assert m.est_cost > 0 and m.est_rows > 0


def test_collect_union_has_a_cost(cur):
    m = collect(cur, "SELECT film_id FROM film WHERE film_id < 5 UNION SELECT film_id FROM film WHERE film_id > 995", 1, 2)
    assert m.outcome == "ok" and m.est_cost is not None


def test_collect_timeout_is_an_outcome_not_a_runtime(cur):
    m = collect(cur, "SELECT SUM(a.amount * b.amount * c.amount) FROM payment a, payment b, payment c", warmup=1, runs=5)
    assert m.outcome == "timeout"
    assert m.runtime_ms_median is None and m.runs_ms == []


def test_collect_error_is_recorded(cur):
    m = collect(cur, "SELECT nope FROM film", 1, 2)
    assert m.outcome == "error" and "nope" in m.error and m.runtime_ms_median is None
