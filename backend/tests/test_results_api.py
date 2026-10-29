"""/queries offers only the dev split; /results reads a run and refuses path tricks."""
import yaml
from fastapi.testclient import TestClient

from app.eval.runner import ROOT
from app.main import app

client = TestClient(app)


def test_queries_are_dev_only():
    test_ids = set(yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())["test"])
    ids = {q["id"] for q in client.get("/queries").json()}
    assert ids and not ids & test_ids


def test_run_detail_matches_the_committed_dev_run():
    runs = {r["run_id"]: r for r in client.get("/results").json()}
    assert runs["dev-006"]["complete"] and "llm_calls" not in runs
    d = client.get("/results/dev-006").json()
    arms = {a["arm"]: a for a in d["arms"]}
    assert arms["3"]["units"] == 15 and arms["2a"]["units"] == 75
    assert arms["1"]["win"] == arms["1"]["loss"] == 0  # baseline is all ties
    assert "PER-ARM RESULTS" in d["report"]


def test_unknown_or_traversing_run_is_404():
    for rid in ("nope", "..%2Fconfig%2Fahp", "llm_calls.jsonl"):
        assert client.get(f"/results/{rid}").status_code == 404


def test_every_recorded_run_renders_including_those_before_3_measured_existed():
    for r in client.get("/results").json():
        assert client.get(f"/results/{r['run_id']}").status_code == 200, r["run_id"]
