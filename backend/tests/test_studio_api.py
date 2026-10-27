"""POST /studio/optimize end to end: scripted LLM, real MySQL, real measurement and ranking."""
import pytest
from fastapi.testclient import TestClient

from app.api.studio import get_llm_client, get_pool_dir
from app.main import app

QID = "ap02_function_on_indexed_column"  # DATE(rental_date) = '2005-07-08' on sakila.rental
RANGE_REWRITE = ("SELECT rental_id, customer_id, inventory_id FROM rental "
                 "WHERE rental_date >= '2005-07-08' AND rental_date < '2005-07-09'")
WRONG_REWRITE = ("SELECT rental_id, customer_id, inventory_id FROM rental "
                 "WHERE rental_date >= '2005-07-08' AND rental_date < '2005-07-10'")


@pytest.fixture
def api(tmp_path, fake_llm):
    llm = fake_llm([RANGE_REWRITE, WRONG_REWRITE, "DROP TABLE rental"])
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_pool_dir] = lambda: tmp_path
    yield TestClient(app), llm, tmp_path
    app.dependency_overrides.clear()


def test_optimize_returns_candidates_validity_metrics_ranking_and_winner(api):
    client, llm, _ = api
    r = client.post("/studio/optimize", json={"query_id": QID})
    assert r.status_code == 200
    body = r.json()

    # validity flags: one valid, one wrong, one rejected as a write; none dropped
    st = {c["id"]: c["status"] for c in body["all_candidates"]}
    assert st == {"c1": "valid", "c2": "invalid", "c3": "invalid"}
    assert body["all_candidates"][2]["check"]["reason"] == "not_select"

    # only the original and the valid rewrite are measured and ranked
    alts = {a["id"]: a for a in body["alternatives"]}
    assert set(alts) == {"original", "c1"}
    for a in alts.values():
        m = a["metrics"]
        assert m["outcome"] == "ok" and len(m["runs_ms"]) == 5
        assert m["rows_examined"] and m["est_cost"] and m["est_rows"] and m["runtime_ms_iqr"] is not None
        assert 0 <= a["topsis_closeness"] <= 1

    # ranking is by closeness, winner is its head, weights are the fixed AHP ones
    assert sorted(body["ranking"]) == ["c1", "original"] and body["winner_raw"] == body["ranking"][0]
    assert body["winner"] in (body["winner_raw"], "original")  # the noise guard may only fall back to the original
    assert alts[body["ranking"][0]]["topsis_closeness"] >= alts[body["ranking"][1]]["topsis_closeness"]
    assert body["criteria"] == ["runtime_ms", "rows_examined", "est_cost", "est_rows"]
    assert sum(body["weights"]) == pytest.approx(1.0) and body["ahp"]["consistency_ratio"] < 0.1
    assert body["pool"]["generator"]["provider"] == "ollama" and body["pool"]["n_valid"] == 1

    # the range rewrite reads far fewer rows than the full scan: the work metric agrees with intuition
    assert alts["c1"]["metrics"]["rows_examined"] < alts["original"]["metrics"]["rows_examined"]


def test_second_call_uses_the_cached_pool(api):
    client, llm, _ = api
    client.post("/studio/optimize", json={"query_id": QID})
    again = client.post("/studio/optimize", json={"query_id": QID}).json()
    assert llm.calls == 1 and again["pool"]["cached"] is True


def test_adhoc_sql_is_supported_and_cached_separately(api):
    client, llm, tmp = api
    r = client.post("/studio/optimize", json={"dataset": "sakila",
                                              "sql": "SELECT film_id FROM film WHERE film_id + 0 < 4;"})
    assert r.status_code == 200 and r.json()["query_id"].startswith("adhoc-")
    assert list((tmp / "adhoc").glob("adhoc-*.json"))  # not in the registry's pool dir
    assert not list(tmp.glob("*.json"))


def test_llm_outage_returns_an_error_not_a_fake_result(tmp_path, fake_llm):
    app.dependency_overrides[get_llm_client] = lambda: fake_llm(fail=True)
    app.dependency_overrides[get_pool_dir] = lambda: tmp_path
    try:
        body = TestClient(app).post("/studio/optimize", json={"query_id": QID}).json()
    finally:
        app.dependency_overrides.clear()
    assert "LLM unavailable" in body["error"] and body["winner"] is None


@pytest.mark.parametrize("payload,code", [
    ({}, 422),
    ({"query_id": "no_such_query"}, 404),
    ({"dataset": "mysql", "sql": "SELECT 1"}, 422),                       # not one of the three datasets
    ({"dataset": "sakila", "sql": "DELETE FROM film"}, 422),               # never a write
    ({"dataset": "sakila", "sql": "SELECT 1; DROP TABLE film"}, 422),
])
def test_bad_requests_are_rejected_before_anything_runs(api, payload, code):
    client, llm, _ = api
    assert client.post("/studio/optimize", json=payload).status_code == code
    assert llm.calls == 0
