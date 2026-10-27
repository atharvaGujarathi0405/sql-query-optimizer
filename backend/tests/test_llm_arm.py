import re

import pytest

from app.eval.llm_arm import build_messages, metric_table, option_order, parse_choice


def opts(n=3):
    out = []
    for i in range(n):
        out.append({"id": "original" if i == 0 else f"c{i}", "sql": f"SELECT {i} FROM t",
                    "metrics": {"outcome": "ok", "runtime_ms_median": 100.0 / (i + 1), "runtime_ms_iqr": 3.0,
                                "rows_examined": 1000 * (i + 1), "est_cost": 12.5 * (i + 1), "est_rows": 999.0}})
    return out


def test_repeat_zero_is_canonical_and_others_are_deterministic_permutations():
    assert option_order("q", 0, 4) == [0, 1, 2, 3]
    for r in range(1, 6):
        o = option_order("q", r, 4)
        assert sorted(o) == [0, 1, 2, 3] and o == option_order("q", r, 4)
    assert len({tuple(option_order("q", r, 5)) for r in range(1, 6)}) > 1  # repeats actually differ


def test_2a_prompt_has_sql_and_schema_but_no_measurements_or_hints():
    msgs, lm = build_messages("2a", "sakila", "CREATE TABLE t (...)", opts(), [0, 1, 2])
    u = msgs[1]["content"]
    assert "CREATE TABLE t" in u and "SELECT 1 FROM t" in u and "original query as written" in u
    for leaked in ("runtime", "rows examined", "EXPLAIN", "median", "1000", "12.5", "function-on-column", "hint"):
        assert leaked not in u, leaked
    assert lm == {"A": "original", "B": "c1", "C": "c2"}


def test_2b_prompt_adds_exactly_the_metric_table_the_or_engine_uses():
    o = opts()
    msgs, _ = build_messages("2b", "sakila", "DDL", o, [0, 1, 2])
    u = msgs[1]["content"]
    assert metric_table(o, [0, 1, 2]) in u
    assert "A | 100.00, 3.00 | 1000 | 12.5 | 999" in u and "C | 33.33, 3.00 | 3000 | 37.5 | 999" in u
    # same options + schema as 2a, nothing else added
    u2a = build_messages("2a", "sakila", "DDL", o, [0, 1, 2])[0][1]["content"]
    assert u.startswith(u2a.replace("\n\nWhich option is fastest? Answer with JSON only.", ""))


def test_labels_follow_the_shuffled_order_and_map_back_to_ids():
    msgs, lm = build_messages("2a", "sakila", "DDL", opts(), [2, 0, 1])
    assert lm == {"A": "c2", "B": "original", "C": "c1"}
    u = msgs[1]["content"]
    assert re.search(r"Option B  \[this is the original query as written\]:\nSELECT 0 FROM t", u)


def test_failed_options_are_shown_as_not_completed():
    o = opts(2)
    o[1]["metrics"] = {"outcome": "timeout"}
    assert "B | timeout (did not complete)" in metric_table(o, [0, 1])


LM = {"A": "original", "B": "c1", "C": "c2"}


@pytest.mark.parametrize("text,expected", [
    ('{"choice": "B", "reason": "uses the index"}', "c1"),
    ('{"choice": "option c"}', "c2"),
    ('```json\n{"choice": "A"}\n```', "original"),
    ('<think>hmm {"choice": "C"} maybe</think>{"choice": "B"}', "c1"),
    ('Sure! {"choice": "A", "reason": "x"} done', "original"),
])
def test_parse_choice_accepts(text, expected):
    assert parse_choice(text, LM) == (expected, "")


@pytest.mark.parametrize("text,prefix", [
    ("I think B is best", "parse_error"),
    ("", "parse_error"),
    ('{"answer": "B"}', "parse_error"),
    ('{"choice": "Z"}', "invalid_choice"),
    ('{"choice": "original"}', "invalid_choice"),  # ids are not labels
    ('{"choice": ""}', "invalid_choice"),
])
def test_parse_choice_rejects_and_says_why(text, prefix):
    alt, err = parse_choice(text, LM)
    assert alt is None and err.startswith(prefix)
