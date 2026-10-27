"""Candidate pool: generation, checking, caching. Needs the MySQL container; the LLM is scripted."""
import json

import pytest

from app.db import get_readonly_conn
from app.optimizer.candidates import generate_pool, load_pool, parse_candidates, pool_path, valid_candidates

QID, DS = "t_film_low_ids", "sakila"
ORIG = "SELECT film_id, title FROM film WHERE film_id + 0 < 5 ORDER BY film_id"
GOOD = "SELECT film_id, title FROM film WHERE film_id < 5 ORDER BY film_id"
WRONG_COUNT = "SELECT film_id, title FROM film WHERE film_id < 6 ORDER BY film_id"
WRONG_ORDER = "SELECT film_id, title FROM film WHERE film_id < 5 ORDER BY film_id DESC"
SAME_AS_ORIG = "select film_id,  title\nfrom film where film_id + 0 < 5 order by film_id"  # formatting only
DELETE = "DELETE FROM film"
BROKEN = "SELECT nope FROM film"


@pytest.fixture
def conn():
    with get_readonly_conn() as c:
        yield c


def test_parse_candidates_variants():
    assert parse_candidates('{"candidates":[{"sql":"SELECT 1;","rationale":"r"}]}') == [{"sql": "SELECT 1", "rationale": "r"}]
    assert parse_candidates('```json\n{"candidates":["SELECT 2"]}\n```')[0]["sql"] == "SELECT 2"
    assert parse_candidates('Sure! Here you go: [{"sql": "SELECT 3"}] hope it helps')[0]["sql"] == "SELECT 3"
    for bad in ("no json here", '{"other": 1}', '{"candidates":[{"sql":"  "}]}'):
        with pytest.raises(ValueError):
            parse_candidates(bad)
    assert parse_candidates('{"candidates": []}') == []  # "nothing to improve" is an answer, not a failure


def test_empty_answer_is_cached_as_an_original_only_pool(conn, tmp_path, fake_llm):
    llm = fake_llm(text='{"candidates": []}')
    pool = generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path)
    assert "error" not in pool and pool["candidates"] == [] and pool["n_proposed"] == 0
    assert load_pool(QID, tmp_path) is not None  # cached: arms see the original only, not a missing pool
    assert generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path)["cached"] is True and llm.calls == 1


def test_every_candidate_is_checked_and_nothing_is_dropped(conn, tmp_path, fake_llm):
    llm = fake_llm([GOOD, WRONG_COUNT, WRONG_ORDER, SAME_AS_ORIG, DELETE, BROKEN])
    pool = generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path)

    by_id = {c["id"]: c for c in pool["candidates"]}
    assert len(by_id) == 5  # candidates_per_query = 5 caps what is kept (c6, BROKEN, was beyond the cap)
    assert pool["n_proposed"] == 6 and pool["n_truncated"] == 1  # the cap is recorded, not silent
    assert by_id["c1"]["status"] == "valid"
    assert by_id["c2"]["status"] == "invalid" and by_id["c2"]["check"]["reason"] == "row_count_mismatch"
    assert by_id["c3"]["status"] == "invalid" and by_id["c3"]["check"]["reason"] == "order_mismatch"
    assert by_id["c4"]["status"] == "duplicate" and by_id["c4"]["duplicate_of"] == "original"
    assert by_id["c5"]["status"] == "invalid" and by_id["c5"]["check"]["reason"] == "not_select"
    assert [c["id"] for c in valid_candidates(pool)] == ["c1"]


def test_broken_sql_is_logged_as_exec_error(conn, tmp_path, fake_llm):
    pool = generate_pool(conn, fake_llm([BROKEN]), QID, DS, ORIG, pool_dir=tmp_path)
    c = pool["candidates"][0]
    assert c["status"] == "invalid" and c["check"]["reason"] == "exec_error" and "nope" in c["check"]["detail"]


def test_pool_file_records_provenance(conn, tmp_path, fake_llm):
    generate_pool(conn, fake_llm([GOOD], provider="groq"), QID, DS, ORIG, pool_dir=tmp_path)
    on_disk = json.loads(pool_path(QID, tmp_path).read_text())
    g = on_disk["generator"]
    assert (g["provider"], g["model"], g["temperature"], g["seed"]) == ("groq", "fake-model", 0.0, 42)
    assert len(g["prompt_sha256"]) == 64 and "function-on-column" in g["detected_patterns"]
    assert on_disk["original_sql"] == ORIG and on_disk["original_check"]["rows"] == 4


def test_pool_is_generated_once_then_served_from_cache(conn, tmp_path, fake_llm):
    llm = fake_llm([GOOD])
    first = generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path)
    second = generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path)
    assert llm.calls == 1 and first["cached"] is False and second["cached"] is True
    assert second["candidates"] == first["candidates"]  # the same pool for whoever asks (fairness rule 2)
    generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path, force=True)
    assert llm.calls == 2


def test_pinned_provider_is_never_served_another_providers_pool(conn, tmp_path, fake_llm):
    generate_pool(conn, fake_llm([GOOD], provider="groq"), QID, DS, ORIG, pool_dir=tmp_path)
    llm = fake_llm([GOOD], provider="ollama")
    r = generate_pool(conn, llm, QID, DS, ORIG, provider="ollama", pool_dir=tmp_path)
    assert "'groq'" in r["error"] and "'ollama'" in r["error"] and llm.calls == 0
    assert load_pool(QID, tmp_path)["generator"]["provider"] == "groq"  # untouched
    # same provider is fine, and so is an unpinned request (development use)
    assert generate_pool(conn, llm, QID, DS, ORIG, provider="groq", pool_dir=tmp_path)["cached"] is True
    assert generate_pool(conn, llm, QID, DS, ORIG, pool_dir=tmp_path)["cached"] is True


def test_llm_outage_is_reported_and_not_cached(conn, tmp_path, fake_llm):
    r = generate_pool(conn, fake_llm(fail=True), QID, DS, ORIG, pool_dir=tmp_path)
    assert "LLM unavailable" in r["error"] and r["generator"]["llm_attempts"][0]["ok"] is False
    assert load_pool(QID, tmp_path) is None  # a failure must not masquerade as "no good rewrite"


def test_unparseable_answer_is_reported_and_not_cached(conn, tmp_path, fake_llm):
    r = generate_pool(conn, fake_llm(text="I cannot help with that."), QID, DS, ORIG, pool_dir=tmp_path)
    assert "unparseable" in r["error"] and "cannot help" in r["raw_answer"]
    assert load_pool(QID, tmp_path) is None


def test_all_candidates_invalid_is_a_cached_legitimate_outcome(conn, tmp_path, fake_llm):
    pool = generate_pool(conn, fake_llm([WRONG_COUNT]), QID, DS, ORIG, pool_dir=tmp_path)
    assert valid_candidates(pool) == [] and load_pool(QID, tmp_path) is not None


def test_query_id_cannot_escape_the_pool_dir(tmp_path):
    with pytest.raises(ValueError):
        pool_path("../evil", tmp_path)
