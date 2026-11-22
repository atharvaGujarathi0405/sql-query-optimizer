"""GET /overview: what the project is made of, for the UI landing page.

Counts only. Nothing here exposes test-split timings, pools or picks (fairness rule 5);
pool statistics are for the dev split.
"""
import json
from collections import Counter

import yaml
from fastapi import APIRouter

from app.config import RUN_CONFIG
from app.eval.runner import ROOT
from app.optimizer.candidates import POOL_DIR, valid_candidates
from app.optimizer.mcdm import load_ahp
from app.optimizer.studio_paths import AHP_PATH

router = APIRouter(tags=["overview"])


@router.get("/overview")
def overview() -> dict:
    reg = yaml.safe_load((ROOT / "data" / "queries.yaml").read_text())
    splits = yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())
    dev = set(splits["dev"])
    pools = []
    for qid in dev:
        p = POOL_DIR / f"{qid}.json"
        if p.exists():
            pools.append(len(valid_candidates(json.loads(p.read_text(encoding="utf-8")))))
    out = {}
    for variant in (None, "measured"):
        criteria, _, res = load_ahp(AHP_PATH, variant)
        out["ahp_measured" if variant else "ahp"] = {
            "criteria": criteria, "weights": [round(float(w), 3) for w in res.weights], "cr": round(float(res.cr), 3)}
    return {
        "queries": {"total": len(reg), "dev": len(dev), "test": len(splits["test"]),
                    "by_dataset": dict(Counter(q["dataset"] for q in reg))},
        "dev_pools": {"generated": len(pools), "with_a_real_choice": sum(n > 0 for n in pools)},
        "run_config": {k: RUN_CONFIG[k] for k in ("warmup_runs", "timed_runs", "llm_repeats", "tie_band_pct", "query_timeout_s", "candidates_per_query")},
        **out,
    }
