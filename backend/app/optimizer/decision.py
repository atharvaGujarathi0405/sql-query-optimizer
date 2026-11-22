"""The OR engine's decision: TOPSIS over the metric table, plus an explicit noise guard.

One implementation for the Studio endpoint and the evaluation runner (arm 3), so what
users see is what is evaluated. Pure function of the metric table: no I/O.

Noise guard (config/run.yaml `or_noise_guard_iqr_multiple`, default 1.0; 0 disables):
TOPSIS min/max-normalises per query, so when plans tie it turns runtime noise into a
large closeness gap (docs/threats_to_validity.md item 14). The guard keeps the original
unless the TOPSIS winner's median runtime beats the original's by MORE than k x the
original's IQR. It was set before any evaluation result existed. `winner_raw` is always
returned too, so the unguarded engine can be scored from the same data.
"""
import numpy as np

from app.config import RUN_CONFIG
from app.optimizer.mcdm import load_ahp, rank, topsis
from app.optimizer.studio_paths import AHP_PATH

NOISE_K = float(RUN_CONFIG.get("or_noise_guard_iqr_multiple", 1.0))


def _field(criterion: str) -> str:
    """AHP criterion name -> Metrics field."""
    return {"runtime_ms": "runtime_ms_median"}.get(criterion, criterion)


def or_decision(alts: list[dict], noise_k: float | None = None, variant: str | None = None) -> dict:
    """alts: [{'id', 'metrics': Metrics.to_dict()}, ...] with the ORIGINAL FIRST (it wins ties).

    Only alternatives that ran to completion with all criteria present are ranked; the rest get
    `excluded_reason`. -> dict with ranking, closeness, winner (guarded), winner_raw, guard.
    `variant` selects a declared criteria subset from config/ahp.yaml (e.g. "measured"); None = all criteria.
    """
    k = NOISE_K if noise_k is None else noise_k
    criteria, benefit, ahp_res = load_ahp(AHP_PATH, variant)  # raises if CR >= 0.1
    rankable = [a for a in alts if a["metrics"]["outcome"] == "ok"
                and all(a["metrics"].get(_field(c)) is not None for c in criteria)]
    ids = {a["id"] for a in rankable}
    excluded = {a["id"]: (a["metrics"]["outcome"] if a["metrics"]["outcome"] != "ok" else "missing metric")
                for a in alts if a["id"] not in ids}
    out = {"criteria": criteria, "weights": [round(float(w), 6) for w in ahp_res.weights],
           "ahp": {"lambda_max": round(ahp_res.lambda_max, 6), "consistency_ratio": round(ahp_res.cr, 6)},
           "excluded": excluded, "closeness": {}, "ranking": [], "winner_raw": None, "winner": None,
           "guard": {"k": k, "applied": False, "reason": ""}}
    if not rankable:
        return out
    X = np.array([[a["metrics"][_field(c)] for c in criteria] for a in rankable], dtype=float)
    closeness = topsis(X, ahp_res.weights, benefit)
    out["closeness"] = {a["id"]: round(float(c), 6) for a, c in zip(rankable, closeness)}
    out["ranking"] = [rankable[i]["id"] for i in rank(closeness)]
    out["winner_raw"] = out["winner"] = out["ranking"][0]

    out["winner"], out["guard"] = _guard(rankable, out["winner_raw"], k)
    return out


def _guard(rankable: list[dict], raw: str, k: float) -> tuple[str, dict]:
    """Noise guard: keep the original unless `raw` beats it by more than k x the original's IQR."""
    guard = {"k": k, "applied": False, "reason": ""}
    orig = next((a for a in rankable if a["id"] == "original"), None)
    if k > 0 and orig is not None and raw != "original":
        win = next(a for a in rankable if a["id"] == raw)
        gain = orig["metrics"]["runtime_ms_median"] - win["metrics"]["runtime_ms_median"]
        need = k * orig["metrics"]["runtime_ms_iqr"]
        if not gain > need:
            guard.update(applied=True, reason=f"runtime gain {gain:.2f} ms <= {k} x original IQR ({need:.2f} ms)")
            return "original", guard
    return raw, guard


def runtime_argmin_decision(alts: list[dict], noise_k: float | None = None) -> dict:
    """Declared arm "3-argmin" (before the test run): no AHP, no TOPSIS. Pick the alternative with the lowest
    measured median runtime, then apply the SAME noise guard. If this makes the same picks as arms 3 and
    3-measured, the multi-criteria machinery adds nothing beyond "fastest measured candidate" (threats 35c, 40).
    Exact runtime ties go to the earlier alternative (the original is first)."""
    k = NOISE_K if noise_k is None else noise_k
    rankable = [a for a in alts if a["metrics"]["outcome"] == "ok" and a["metrics"].get("runtime_ms_median") is not None]
    excluded = {a["id"]: a["metrics"]["outcome"] for a in alts if a not in rankable}
    out = {"criteria": ["runtime_ms"], "excluded": excluded, "closeness": {}, "ranking": [], "winner_raw": None,
           "winner": None, "guard": {"k": k, "applied": False, "reason": ""}}
    if not rankable:
        return out
    out["ranking"] = [a["id"] for a in sorted(rankable, key=lambda a: a["metrics"]["runtime_ms_median"])]  # stable
    out["winner_raw"] = out["ranking"][0]
    out["winner"], out["guard"] = _guard(rankable, out["winner_raw"], k)
    return out
