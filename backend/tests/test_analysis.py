"""The detector must find the pattern each hand-written anti-pattern query is labelled with,
and must not cry wolf on the control / TPC-H queries. Labels come from the query files' tags;
the detector itself never reads them."""
from pathlib import Path

import pytest
import yaml

from app.db import get_readonly_conn
from app.optimizer.analysis import detect, load_schema, schema_text, tables_in

QUERIES = yaml.safe_load((Path(__file__).resolve().parents[2] / "data" / "queries.yaml").read_text())
# tag in the query file -> detector pattern name (None = not detectable from syntax alone)
TAG_TO_PATTERN = {
    "select-star": "select-star", "function-on-column": "function-on-column",
    "correlated-subquery": "correlated-subquery", "not-in": "not-in",
    "leading-wildcard-like": "leading-wildcard-like", "or-across-columns": "or-across-columns",
    "implicit-cast": "implicit-cast", "redundant-distinct": "distinct",
    "having-instead-of-where": "having-instead-of-where", "deep-offset": "deep-offset",
    "subquery-in-select": "subquery-in-select", "union-vs-union-all": "union-vs-union-all",
}


@pytest.fixture(scope="module")
def schemas():
    with get_readonly_conn() as conn:
        return {d: load_schema(conn, d) for d in ("employees", "sakila", "tpch")}


@pytest.mark.parametrize("q", [q for q in QUERIES if "antipattern" in q["tags"]], ids=lambda q: q["id"])
def test_detector_finds_the_labelled_pattern(q, schemas):
    found = {f.pattern for f in detect(q["sql"], schemas[q["dataset"]])}
    expected = {TAG_TO_PATTERN[t] for t in q["tags"] if t in TAG_TO_PATTERN}
    assert expected, f"{q['id']} has no detectable tag"
    assert expected <= found, f"missing {expected - found}; found {found}"


# Real TPC-H Q4 and Q17 are correlated subqueries; that finding is correct there.
ALLOWED_ON_CONTROLS = {"tpch_q04": {"correlated-subquery"}, "tpch_q17": {"correlated-subquery"}}


@pytest.mark.parametrize("q", [q for q in QUERIES if "antipattern" not in q["tags"]], ids=lambda q: q["id"])
def test_detector_does_not_flag_clean_queries(q, schemas):
    found = {f.pattern for f in detect(q["sql"], schemas[q["dataset"]])}
    assert found <= ALLOWED_ON_CONTROLS.get(q["id"], set()), f"false positives: {found}"


def test_count_star_is_not_select_star():
    assert not [f for f in detect("SELECT COUNT(*) FROM t") if f.pattern == "select-star"]


def test_constant_side_function_is_sargable():
    assert not detect("SELECT a FROM t WHERE d >= DATE_SUB('1998-12-01', INTERVAL 3 DAY)")


def test_union_all_is_not_flagged():
    assert not [f for f in detect("SELECT a FROM t UNION ALL SELECT a FROM u") if f.pattern == "union-vs-union-all"]


def test_tables_and_schema_text_from_the_database():
    assert tables_in("SELECT * FROM rental r JOIN customer c ON c.customer_id = r.customer_id") == ["rental", "customer"]
    with get_readonly_conn() as conn:
        ddl = schema_text(conn, "sakila", ["film"])
    assert "CREATE TABLE `film`" in ddl and "idx_title" in ddl  # indexes are part of what the LLM sees
