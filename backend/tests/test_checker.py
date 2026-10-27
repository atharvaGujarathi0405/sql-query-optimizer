"""Correctness checker tests. Pure-logic cases need no database; the last group runs real
queries as the read-only user (needs the MySQL container)."""
from datetime import date
from decimal import Decimal

import pytest

from app.db import get_readonly_conn
from app.optimizer.checker import (
    check_candidate, compare_results, fetch_original, is_order_sensitive, read_only_select_problem,
)
from app.optimizer.execution import open_session


# ---- cases that must PASS -------------------------------------------------------------------
def test_identical_results_are_valid():
    assert compare_results([(1, "a"), (2, "b")], [(1, "a"), (2, "b")], False).valid


def test_row_order_is_ignored_without_order_by():
    r = compare_results([(1,), (2,), (3,)], [(3,), (1,), (2,)], order_sensitive=False)
    assert r.valid and r.reason == "ok"


def test_numeric_types_compare_by_value():
    assert compare_results([(Decimal("1.50"),), (2,)], [(1.5,), (Decimal("2.000"),)], False).valid


def test_empty_results_pass_but_are_a_weak_oracle():
    assert compare_results([], [], False).valid  # documented: why registry queries must not be empty


# ---- cases that must FAIL -------------------------------------------------------------------
def test_different_rows_same_count_fail():
    r = compare_results([(1,), (2,)], [(1,), (3,)], False)
    assert not r.valid and r.reason == "rows_mismatch"
    assert "(2,)" in r.detail and "(3,)" in r.detail


def test_row_count_is_checked_first():
    r = compare_results([(1,), (2,)], [(1,)], False)
    assert not r.valid and r.reason == "row_count_mismatch"


def test_duplicates_are_compared_as_a_multiset_not_a_set():
    # same distinct values, same length, different multiplicities
    r = compare_results([(1,), (1,), (2,)], [(1,), (2,), (2,)], False)
    assert not r.valid and r.reason == "rows_mismatch"


def test_different_order_fails_when_original_has_order_by():
    r = compare_results([(1,), (2,), (3,)], [(3,), (2,), (1,)], order_sensitive=True)
    assert not r.valid and r.reason == "order_mismatch"
    assert r.order_sensitive


def test_wrong_rows_under_order_by_report_rows_not_order():
    r = compare_results([(1,), (2,)], [(1,), (9,)], order_sensitive=True)
    assert not r.valid and r.reason == "rows_mismatch"


def test_null_is_not_empty_string_or_zero():
    assert not compare_results([(None,)], [("",)], False).valid
    assert not compare_results([(None,)], [(0,)], False).valid
    assert not compare_results([(None,)], [("NULL",)], False).valid  # the string 'NULL' is not NULL


def test_null_equals_null_and_position_matters():
    assert compare_results([(None, 1)], [(None, 1)], False).valid
    assert not compare_results([(None, 1)], [(1, None)], False).valid  # swapped columns


def test_string_never_equals_number():
    assert not compare_results([(1,)], [("1",)], False).valid


def test_column_count_mismatch_fails_before_row_comparison():
    r = compare_results([(1, 2)], [(1,)], False, orig_ncols=2, cand_ncols=1)
    assert not r.valid and r.reason == "col_count_mismatch"


def test_dates_compare_exactly():
    assert compare_results([(date(2005, 7, 8),)], [(date(2005, 7, 8),)], False).valid
    assert not compare_results([(date(2005, 7, 8),)], [(date(2005, 7, 9),)], False).valid


# ---- SQL shape helpers ------------------------------------------------------------------------
@pytest.mark.parametrize("sql,expected", [
    ("SELECT a FROM t ORDER BY a", True),
    ("SELECT a FROM t", False),
    ("SELECT * FROM (SELECT a FROM t ORDER BY a) x", False),  # inner ORDER BY does not count
    ("SELECT a FROM t UNION SELECT a FROM u ORDER BY a", True),
    ("WITH c AS (SELECT a FROM t) SELECT a FROM c ORDER BY a LIMIT 3", True),
])
def test_is_order_sensitive(sql, expected):
    assert is_order_sensitive(sql) is expected


@pytest.mark.parametrize("sql", [
    "SELECT 1", "SELECT a FROM t UNION ALL SELECT a FROM u", "WITH c AS (SELECT 1 a) SELECT a FROM c",
])
def test_accepts_single_selects(sql):
    assert read_only_select_problem(sql) is None


@pytest.mark.parametrize("sql", [
    "DELETE FROM t", "UPDATE t SET a = 1", "DROP TABLE t", "INSERT INTO t VALUES (1)",
    "SELECT 1; SELECT 2", "SELECT 1; DROP TABLE t", "SELECT a INTO OUTFILE '/tmp/x' FROM t", "SELEC nonsense (",
])
def test_rejects_anything_but_one_select(sql):
    assert read_only_select_problem(sql) is not None


# ---- against the real database, as the read-only user ---------------------------------------
@pytest.fixture
def cur():
    with get_readonly_conn() as conn:
        yield open_session(conn, "sakila", timeout_s=2)


ORIG = "SELECT film_id, title FROM film WHERE film_id < 5 ORDER BY film_id"


def test_db_equivalent_rewrite_is_valid(cur):
    o = fetch_original(cur, ORIG)
    r = check_candidate(cur, o, "SELECT film_id, title FROM film WHERE film_id <= 4 ORDER BY film_id")
    assert r.valid and r.original_rows == 4 and r.order_sensitive


def test_db_off_by_one_rewrite_fails_on_row_count(cur):
    r = check_candidate(cur, fetch_original(cur, ORIG), "SELECT film_id, title FROM film WHERE film_id < 6 ORDER BY film_id")
    assert not r.valid and r.reason == "row_count_mismatch"


def test_db_reversed_order_fails_under_order_by(cur):
    r = check_candidate(cur, fetch_original(cur, ORIG),
                        "SELECT film_id, title FROM film WHERE film_id < 5 ORDER BY film_id DESC")
    assert not r.valid and r.reason == "order_mismatch"


def test_db_same_rows_new_order_passes_without_order_by(cur):
    o = fetch_original(cur, "SELECT film_id FROM film WHERE film_id < 5")
    assert check_candidate(cur, o, "SELECT film_id FROM film WHERE film_id < 5 ORDER BY film_id DESC").valid


def test_db_wrong_values_fail(cur):
    r = check_candidate(cur, fetch_original(cur, ORIG), "SELECT film_id, title FROM film WHERE film_id BETWEEN 2 AND 5 ORDER BY film_id")
    assert not r.valid


def test_db_null_handling_not_null_vs_filtered(cur):
    # film.original_language_id is NULL for every film: IS NULL rows != '= NULL' (empty) rows
    o = fetch_original(cur, "SELECT film_id FROM film WHERE original_language_id IS NULL AND film_id < 4")
    r = check_candidate(cur, o, "SELECT film_id FROM film WHERE original_language_id = NULL AND film_id < 4")
    assert not r.valid and r.reason == "row_count_mismatch"


def test_db_write_statement_is_rejected_before_it_runs(cur):
    o = fetch_original(cur, "SELECT 1")
    r = check_candidate(cur, o, "DELETE FROM film")
    assert not r.valid and r.reason == "not_select"


def test_db_syntax_error_is_logged_not_raised(cur):
    r = check_candidate(cur, fetch_original(cur, "SELECT 1"), "SELECT nope FROM film")
    assert not r.valid and r.reason == "exec_error"


def test_db_timeout_is_its_own_reason(cur):
    # Not SLEEP(): MySQL returns 1 from an interrupted SLEEP() instead of raising, which would
    # look like a valid '1'. A runaway scan (16044^3 rows) does raise ER_QUERY_TIMEOUT at the 2 s cap.
    runaway = "SELECT SUM(a.amount * b.amount * c.amount) FROM payment a, payment b, payment c"  # COUNT(*) is shortcut
    r = check_candidate(cur, fetch_original(cur, "SELECT 1"), runaway)
    assert not r.valid and r.reason == "timeout"


def test_db_failed_original_fails_every_candidate_visibly(cur):
    o = fetch_original(cur, "SELECT nope FROM film")
    r = check_candidate(cur, o, "SELECT 1")
    assert not r.valid and r.reason == "original_failed" and "nope" in r.detail
