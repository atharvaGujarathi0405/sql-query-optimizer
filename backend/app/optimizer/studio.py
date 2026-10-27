"""Query Studio: pool -> metrics -> TOPSIS ranking -> winner.

The OR engine sees exactly the metric table returned here (the same table arm 2b gets).
Only alternatives that ran to completion (outcome ok, all four criteria present) are
ranked; the rest are listed with the reason. The original is always alternative 0, so it
wins ties: a rewrite must be strictly better to be chosen.
"""
import random
from pathlib import Path

import numpy as np

from app.config import RUN_CONFIG
from app.optimizer.candidates import POOL_DIR, generate_pool, valid_candidates
from app.optimizer.execution import open_session
from app.optimizer.mcdm import load_ahp, rank, topsis
from app.optimizer.metrics import collect

AHP_PATH = Path(__file__).resolve().parents[3] / "config" / "ahp.yaml"
ORDER_SEED = 20261027  # measurement order is shuffled with this seed, to spread order effects


def optimize(conn, client, dataset: str, sql: str, query_id: str, *, provider: str | None = None,
             pool_dir: Path = POOL_DIR, force_pool: bool = False) -> dict:
    pool = generate_pool(conn, client, query_id, dataset, sql, provider=provider, pool_dir=pool_dir, force=force_pool)
    if pool.get("error"):
        return {"query_id": query_id, "dataset": dataset, "error": pool["error"], "pool": pool, "winner": None}

    criteria, benefit, ahp_res = load_ahp(AHP_PATH)  # raises if CR >= 0.1
    cur = open_session(conn, dataset, RUN_CONFIG["query_timeout_s"])
    alts = [{"id": "original", "sql": sql, "rationale": "the query as written", "status": "original"}]
    alts += [{**c} for c in valid_candidates(pool)]

    order = list(range(len(alts)))
    random.Random(ORDER_SEED).shuffle(order)
    for i in order:
        alts[i]["metrics"] = collect(cur, alts[i]["sql"]).to_dict()

    rankable = [a for a in alts if a["metrics"]["outcome"] == "ok"
                and all(a["metrics"].get(_field(c)) is not None for c in criteria)]
    for a in alts:
        a["ranked"] = a in rankable
        if not a["ranked"]:
            a["excluded_reason"] = (a["metrics"]["outcome"] if a["metrics"]["outcome"] != "ok" else "missing metric")
    ranking, winner = [], None
    if rankable:
        X = np.array([[a["metrics"][_field(c)] for c in criteria] for a in rankable], dtype=float)
        closeness = topsis(X, ahp_res.weights, benefit)
        for a, c in zip(rankable, closeness):
            a["topsis_closeness"] = round(float(c), 6)
        ranking = [rankable[i]["id"] for i in rank(closeness)]
        winner = ranking[0]
    return {
        "query_id": query_id, "dataset": dataset, "original_sql": sql,
        "pool": {"cached": pool["cached"], "generator": pool["generator"], "n_candidates": len(pool["candidates"]),
                 "n_valid": len(valid_candidates(pool))},
        "all_candidates": pool["candidates"],  # incl. invalid + duplicates, with the check result
        "alternatives": alts,                  # original + valid candidates, with metrics
        "criteria": criteria, "weights": [round(float(w), 6) for w in ahp_res.weights],
        "ahp": {"lambda_max": round(ahp_res.lambda_max, 6), "consistency_ratio": round(ahp_res.cr, 6)},
        "ranking": ranking, "winner": winner,
        "measurement_order": [alts[i]["id"] for i in order],
    }


def _field(criterion: str) -> str:
    """AHP criterion name -> Metrics field."""
    return {"runtime_ms": "runtime_ms_median"}.get(criterion, criterion)
