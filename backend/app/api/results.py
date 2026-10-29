"""Read-only endpoints for the UI: the dev queries, and the evaluation runs in results/*.jsonl.

/queries lists the DEV split only. The test split is never offered for interactive use, so
nobody sees its timings or pools before the single frozen test run (fairness rule 5).
"""
import re
from collections import Counter

import yaml
from fastapi import APIRouter, HTTPException

from app.eval import report as rep
from app.eval.runner import RESULTS_DIR, ROOT

router = APIRouter(tags=["results"])
RUN_ID = re.compile(r"^[A-Za-z0-9_-]+$")  # run ids become file names: no path separators
# Why a recorded run must not be read as a result (from project_progress.txt sections 15-16 and the dev-004 commit).
INVALID = {
    "dev-001": "invalid: rows_examined came from performance_schema, which undercounts subqueries (both 2b and 3 saw it)",
    "dev-002": "invalid for the LLM arms: a network outage failed 21 Groq calls, scored as the original",
    "dev-004": "aborted: Groq 429 rate limits; no run_end",
    "dev-005": "aborted: Groq 429 rate limits; no run_end",
    "dry-001": "pipeline check only (5 queries, 2 with a choice), not a result",
}


@router.get("/queries")
def dev_queries() -> list[dict]:
    dev = set(yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())["dev"])
    reg = yaml.safe_load((ROOT / "data" / "queries.yaml").read_text())
    return [{"id": q["id"], "dataset": q["dataset"], "tags": q["tags"], "sql": q["sql"].strip()} for q in reg if q["id"] in dev]


@router.get("/results")
def list_runs() -> list[dict]:
    out = []
    for p in sorted(RESULTS_DIR.glob("*.jsonl"), key=lambda p: p.stat().st_mtime):  # oldest first
        if p.name == "llm_calls.jsonl":
            continue
        try:
            d = rep.load(p)
        except StopIteration:  # a file with no run_meta (not a run)
            continue
        m = d["meta"]
        out.append({"run_id": m["run_id"], "split": m["split"], "provider": m["provider"], "model": m["model"],
                    "queries": len(m["query_ids"]), "complete": d["complete"], "note": INVALID.get(m["run_id"])})
    return out


@router.get("/results/{run_id}")
def run_detail(run_id: str) -> dict:
    path = RESULTS_DIR / f"{run_id}.jsonl"
    if not RUN_ID.match(run_id) or not path.exists():
        raise HTTPException(404, f"unknown run {run_id!r}")
    data = rep.load(path)
    sc = rep.score(data)
    arms = []
    for arm in rep.ARMS:
        u = [s for s in sc if s["arm"] == arm]
        if u:
            c = Counter(s["outcome"] for s in u)
            arms.append({"arm": arm, "name": rep.NAMES[arm], "units": len(u), "win": c["win"], "tie": c["tie"], "loss": c["loss"],
                         "other": len(u) - c["win"] - c["tie"] - c["loss"], "fallback": sum(bool(s["fallback"]) for s in u)})
    queries = []
    for q in (r for r in data["recs"] if r["type"] == "query"):
        base = data["ev"].get((q["query_id"], "original"))
        cells = {}
        for arm in rep.ARMS:
            u = [s for s in sc if s["query_id"] == q["query_id"] and s["arm"] == arm]
            if u:
                rs = [s["ratio"] for s in u if s["ratio"] is not None]
                picks = Counter(s["pick"] for s in u)
                cells[arm] = {"picks": dict(picks), "ratio": sum(rs) / len(rs) if rs else None,
                              "outcomes": dict(Counter(s["outcome"] for s in u))}
        queries.append({"query_id": q["query_id"], "dataset": q["dataset"], "n_valid": q["n_valid"],
                        "base_ms": base["runtime_ms_median"] if base else None, "arms": cells})
    return {"meta": data["meta"], "complete": data["complete"], "arms": arms, "queries": queries,
            "failures": len(data["failures"]), "note": INVALID.get(run_id), "report": rep.report(path)}
