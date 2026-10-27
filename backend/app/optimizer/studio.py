"""Query Studio: pool -> metrics -> TOPSIS ranking -> winner.

The OR engine sees exactly the metric table returned here (the same table arm 2b gets).
Only alternatives that ran to completion (outcome ok, all four criteria present) are
ranked; the rest are listed with the reason. The original is always alternative 0, so it
wins ties: a rewrite must be strictly better to be chosen.
"""
import random
from pathlib import Path

from app.config import RUN_CONFIG
from app.optimizer.candidates import POOL_DIR, generate_pool, valid_candidates
from app.optimizer.execution import open_session
from app.optimizer.decision import or_decision
from app.optimizer.metrics import collect

ORDER_SEED = 20261027  # measurement order is shuffled with this seed, to spread order effects


def optimize(conn, client, dataset: str, sql: str, query_id: str, *, provider: str | None = None,
             pool_dir: Path = POOL_DIR, force_pool: bool = False) -> dict:
    pool = generate_pool(conn, client, query_id, dataset, sql, provider=provider, pool_dir=pool_dir, force=force_pool)
    if pool.get("error"):
        return {"query_id": query_id, "dataset": dataset, "error": pool["error"], "pool": pool, "winner": None}

    cur = open_session(conn, dataset, RUN_CONFIG["query_timeout_s"])
    alts = [{"id": "original", "sql": sql, "rationale": "the query as written", "status": "original"}]
    alts += [{**c} for c in valid_candidates(pool)]

    order = list(range(len(alts)))
    random.Random(ORDER_SEED).shuffle(order)
    for i in order:
        alts[i]["metrics"] = collect(cur, alts[i]["sql"]).to_dict()

    d = or_decision(alts)  # the same function the evaluation runner uses for arm 3
    for a in alts:
        a["ranked"] = a["id"] in d["closeness"]
        if a["ranked"]:
            a["topsis_closeness"] = d["closeness"][a["id"]]
        else:
            a["excluded_reason"] = d["excluded"][a["id"]]
    return {
        "query_id": query_id, "dataset": dataset, "original_sql": sql,
        "pool": {"cached": pool["cached"], "generator": pool["generator"], "n_candidates": len(pool["candidates"]),
                 "n_valid": len(valid_candidates(pool))},
        "all_candidates": pool["candidates"],  # incl. invalid + duplicates, with the check result
        "alternatives": alts,                  # original + valid candidates, with metrics
        "criteria": d["criteria"], "weights": d["weights"], "ahp": d["ahp"],
        "ranking": d["ranking"], "winner": d["winner"], "winner_raw": d["winner_raw"], "noise_guard": d["guard"],
        "measurement_order": [alts[i]["id"] for i in order],
    }
