"""Report for a results/<run_id>.jsonl: per-arm medians, win/tie/loss vs baseline, paired OR-vs-LLM
comparisons (Wilcoxon signed-rank + effect size), and a failure table.

Reads only the JSONL, so any run can be re-analysed. Usage: python -m app.eval.report results/<run>.jsonl

Scoring: every arm is scored from the EVALUATION-pass measurement of the alternative it picked.
A pick of the original is the baseline time exactly (rule 8) and counts as a tie. A rewrite is a
win/loss only if its median differs from the baseline's by more than
max(baseline IQR, tie_band_pct % of baseline median); otherwise a tie. Timeouts and errors are their
own outcomes, never runtimes, and are excluded from ratios and paired tests (with the counts shown).
"""
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from scipy import stats

ARMS = ["1", "2a", "2b", "3", "3-measured", "3-argmin", "3-raw"]
NAMES = {"1": "1  baseline", "2a": "2a LLM (SQL+schema)", "2b": "2b LLM (+metrics)", "3": "3  OR engine", "3-measured": "3m OR, measured only", "3-argmin": "3a fastest measured", "3-raw": "3-raw OR, no guard"}
PAIRS = [("3", "2b"), ("3", "2a"), ("2b", "2a"), ("3-measured", "2b"), ("3-measured", "3"), ("3-argmin", "3"), ("3-argmin", "2b"), ("3-raw", "2b")]
LLM_ARMS = ("2a", "2b")


def load(path: Path) -> dict:
    recs = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    meta = next(r for r in recs if r["type"] == "run_meta")
    ev = {(r["query_id"], r["alt_id"]): r["metrics"] for r in recs if r["type"] == "measurement" and r["phase"] == "evaluation"}
    return {"meta": meta, "recs": recs, "ev": ev, "complete": any(r["type"] == "run_end" for r in recs),
            "arms": [r for r in recs if r["type"] == "arm"], "failures": [r for r in recs if r["type"] == "failure"]}


def classify(base: dict, pick: dict, same_query: bool, tie_band_pct: float) -> tuple[str, float | None]:
    """-> (outcome, ratio). outcome: win | tie | loss | timeout | error | baseline_failed."""
    if base["outcome"] != "ok":
        return "baseline_failed", None
    if same_query:
        return "tie", 1.0  # the original, scored as the baseline time (rule 8)
    if pick["outcome"] != "ok":
        return pick["outcome"], None
    band = max(base["runtime_ms_iqr"], tie_band_pct / 100 * base["runtime_ms_median"])
    d = pick["runtime_ms_median"] - base["runtime_ms_median"]
    ratio = pick["runtime_ms_median"] / base["runtime_ms_median"]
    return ("win" if d < -band else "loss" if d > band else "tie"), ratio


def score(data: dict) -> list[dict]:
    band = data["meta"]["tie_band_pct"]
    out = []
    for a in data["arms"]:
        base, pick = data["ev"].get((a["query_id"], "original")), data["ev"].get((a["query_id"], a["pick"]))
        outcome, ratio = classify(base, pick, a["pick"] == "original", band)
        out.append({**a, "outcome": outcome, "ratio": ratio,
                    "ms": None if ratio is None else (base["runtime_ms_median"] if a["pick"] == "original" else pick["runtime_ms_median"])})
    return out


def wilcoxon_paired(x, y) -> dict:
    """Paired comparison of x vs y (lower is better). d = x - y.
    effect = matched-pairs rank-biserial r = (W+ - W-) / (W+ + W-) in [-1, 1]; negative = x lower (better).
    Zero differences are dropped from the test (Wilcoxon's method), and counted."""
    d = np.asarray(x, float) - np.asarray(y, float)
    nz = d[d != 0]
    res = {"n": len(d), "n_zero": int((d == 0).sum()), "median_diff": float(np.median(d)) if len(d) else None,
           "p": None, "r": None, "W": None}
    if len(nz) == 0:
        return res
    ranks = stats.rankdata(np.abs(nz))
    w_plus, w_minus = ranks[nz > 0].sum(), ranks[nz < 0].sum()
    res["r"] = float((w_plus - w_minus) / (w_plus + w_minus))
    if len(nz) >= 1:
        t = stats.wilcoxon(nz, alternative="two-sided")
        res["p"], res["W"] = float(t.pvalue), float(t.statistic)
    return res


def per_query_log_ratio(scored: list[dict], arm: str) -> dict[str, float]:
    """Mean ln(ratio) over the arm's numeric units for each query (negative = faster than baseline)."""
    acc = defaultdict(list)
    for s in scored:
        if s["arm"] == arm and s["ratio"] is not None:
            acc[s["query_id"]].append(math.log(s["ratio"]))
    return {q: float(np.mean(v)) for q, v in acc.items()}


def bootstrap_ci(d, n_boot: int = 10_000, seed: int = 20261122) -> tuple[float, float] | None:
    """95% percentile bootstrap CI of the mean paired difference (resampling queries). Fixed seed."""
    d = np.asarray(d, float)
    if len(d) < 2 or not d.any():
        return None if len(d) < 2 else (0.0, 0.0)
    rng = np.random.default_rng(seed)
    means = d[rng.integers(0, len(d), size=(n_boot, len(d)))].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def fmt_p(p):
    return "n/a" if p is None else f"{p:.4f}"


def _fmt_arm(units: list[dict]) -> str:
    """'c4  x0.08 win' for one pick; 'c1x4,original x1  x0.09 win' when repeats disagree."""
    if not units:  # a run recorded before this arm existed (e.g. 3-measured in dev-001..003)
        return "-"
    picks = Counter(u["pick"] for u in units)
    p = ",".join(f"{k}x{n}" for k, n in picks.most_common()) if len(picks) > 1 else next(iter(picks))
    rs = [u["ratio"] for u in units if u["ratio"] is not None]
    oc = Counter(u["outcome"] for u in units)
    o = next(iter(oc)) if len(oc) == 1 else ",".join(f"{k}x{n}" for k, n in oc.most_common())
    return f"{p} x{np.mean(rs):.2f} {o}" if rs else f"{p} {o}"


def per_query_section(data: dict, sc: list[dict], contested: list[dict]) -> list[str]:
    """One row per contested query: what each arm picked, mean ratio to baseline (x0.10 = 10x faster), outcome."""
    L = ["CONTESTED QUERIES (pool has >= 1 valid candidate); ratio = pick time / baseline time, lower is better",
         f"{'query':32}{'valid':>6}{'base ms':>9}   {'3 (OR)':24}{'3-measured':24}{'3-argmin':24}{'3-raw':22}{'2b':22}2a"]
    for q in contested:
        qid = q["query_id"]
        base = data["ev"].get((qid, "original"))
        cells = {a: _fmt_arm([s for s in sc if s["query_id"] == qid and s["arm"] == a]) for a in ("3", "3-measured", "3-argmin", "3-raw", "2b", "2a")}
        L.append(f"{qid[:31]:32}{q['n_valid']:>6}{base['runtime_ms_median']:>9.1f}   {cells['3']:24}{cells['3-measured']:24}{cells['3-argmin']:24}{cells['3-raw']:22}{cells['2b']:22}{cells['2a']}")
    L.append("")
    return L


def per_category_section(data: dict, sc: list[dict], queries: list[dict]) -> list[str]:
    """Win/tie/loss per arm within each registry category (first tag: antipattern | tpch-standard | natural)."""
    cat = {q["query_id"]: q["tags"][0] for q in queries}
    L = ["PER CATEGORY (units as in the per-arm table; cells are small: anecdotal, not tests)",
         f"{'category':16}{'queries':>8}  " + "  ".join(f"{a:>12}" for a in ARMS if a != "1")]
    for c in sorted(set(cat.values())):
        row = []
        for a in ARMS:
            if a == "1":
                continue
            u = [s for s in sc if s["arm"] == a and cat.get(s["query_id"]) == c]
            k = Counter(s["outcome"] for s in u)
            row.append(f"{k['win']}w/{k['tie']}t/{k['loss']}l" if u else "-")
        L.append(f"{c:16}{sum(1 for v in cat.values() if v == c):>8}  " + "  ".join(f"{x:>12}" for x in row))
    L.append("")
    return L


def report(path: Path) -> str:
    data = load(path)
    m, sc = data["meta"], score(data)
    env, mysql, hw = m["environment"], m["environment"]["mysql"], m["environment"]["hardware"]
    L = []
    L += [f"RUN {m['run_id']}   split={m['split']}   queries={len(m['query_ids'])}   complete={data['complete']}",
          f"provider={m['provider']} model={m['model']} temp={m['temperature']} seed={m['seed']} repeats(2a/2b)={m['repeats']}",
          f"MySQL {mysql['version']}  buffer_pool={mysql['innodb_buffer_pool_size'] / 2**30:.1f} GiB  collation={mysql['collation_server']}  "
          f"timeout={m['query_timeout_s']}s  protocol={m['warmup_runs']} warm-up + {m['timed_runs']} timed runs",
          f"hardware: {hw['cpu']}, {hw['logical_cores']} cores, {hw['host_ram_bytes'] / 2**30:.1f} GiB RAM, "
          f"docker VM {(hw['docker_vm_ram_bytes'] or 0) / 2**30:.1f} GiB   git={str(env['software']['git_commit'])[:8]} dirty={env['software']['git_dirty']}",
          f"win/loss band: > max(baseline IQR, {m['tie_band_pct']}% of baseline median); OR noise guard = {m['noise_guard_iqr_multiple']} x IQR", ""]
    queries = [r for r in data["recs"] if r["type"] == "query"]
    contested = [r for r in queries if r["n_valid"] > 0]
    L += [f"queries with a real choice (pool has >= 1 valid candidate): {len(contested)} of {len(queries)}. The other "
          f"{len(queries) - len(contested)} have original-only pools, so every arm ties there by construction.", ""]
    if not data["complete"]:
        L += ["!! run_end record missing: this run did not finish. Numbers below cover only what was written.", ""]

    L.append("PER-ARM RESULTS (scored from the evaluation-pass measurement of each arm's pick)")
    L.append(f"{'arm':22}{'units':>6}{'median ms':>11}{'median ratio':>14}{'win':>5}{'tie':>5}{'loss':>6}{'timeout':>9}{'error':>7}{'fallback':>10}")
    for arm in ARMS:
        u = [s for s in sc if s["arm"] == arm]
        if not u:
            continue
        c = Counter(s["outcome"] for s in u)
        ms = [s["ms"] for s in u if s["ms"] is not None]
        rt = [s["ratio"] for s in u if s["ratio"] is not None]
        L.append(f"{NAMES[arm]:22}{len(u):>6}{(np.median(ms) if ms else float('nan')):>11.2f}{(np.median(rt) if rt else float('nan')):>14.4f}"
                 f"{c['win']:>5}{c['tie']:>5}{c['loss']:>6}{c['timeout']:>9}{c['error'] + c['baseline_failed']:>7}"
                 f"{sum(1 for s in u if s['fallback']):>10}")
    L.append("  units = queries for arms 1/3/3-measured/3-argmin/3-raw, query x repeat for 2a/2b. 'fallback' = failure scored as the original.")
    L.append("")

    L += per_query_section(data, sc, contested)
    L.append("LLM VARIANCE ACROSS REPEATS (arms 2a, 2b)")
    for arm in LLM_ARMS:
        by_q = defaultdict(list)
        for s in sc:
            if s["arm"] == arm:
                by_q[s["query_id"]].append(s)
        agree, sds = [], []
        for q, u in by_q.items():
            picks = Counter(s["pick"] for s in u)
            agree.append(picks.most_common(1)[0][1] / len(u))
            lr = [math.log(s["ratio"]) for s in u if s["ratio"] is not None]
            if len(lr) > 1:
                sds.append(float(np.std(lr, ddof=1)))
        if agree:
            L.append(f"  {NAMES[arm]:22} queries={len(by_q)}  mean pick agreement={np.mean(agree):.2f} (1.00 = same pick every repeat)  "
                     f"mean within-query SD of ln(ratio)={np.mean(sds) if sds else 0.0:.3f}  "
                     f"(variance {np.mean(sds) ** 2 if sds else 0.0:.4f})")
    L.append("")

    L.append("PAIRED COMPARISONS: per-query mean ln(ratio to baseline); negative difference = first arm is faster")
    L.append(f"{'first vs second':18}{'n':>4}{'identical':>10}{'median diff':>13}{'= speed':>10}{'Wilcoxon p':>12}{'effect r':>10}   95% CI of mean diff (bootstrap)")
    for a, b in PAIRS:
        la, lb = per_query_log_ratio(sc, a), per_query_log_ratio(sc, b)
        common = sorted(set(la) & set(lb))
        if not common:
            continue
        r = wilcoxon_paired([la[q] for q in common], [lb[q] for q in common])
        md = r["median_diff"]
        ci = bootstrap_ci([la[q] - lb[q] for q in common])
        L.append(f"{a + ' vs ' + b:18}{r['n']:>4}{r['n_zero']:>10}{md:>13.4f}{(math.exp(md) - 1) * 100:>+9.1f}%{fmt_p(r['p']):>12}"
                 f"{'n/a' if r['r'] is None else format(r['r'], '+.2f'):>10}   "
                 f"{'n/a' if ci is None else f'[{ci[0]:+.3f}, {ci[1]:+.3f}]'}")
    L.append("  effect r = matched-pairs rank-biserial (-1..+1). 'identical' pairs are dropped from the test. With few")
    L.append("  queries the p-values have almost no power: read the effect size and the counts, not the stars.")
    L.append("  CI: 10,000 resamples of queries, fixed seed; a CI that spans 0 means no detectable difference.")
    L.append("")
    L += per_category_section(data, sc, queries)

    L.append("FAILURES (nothing is dropped; each is also scored as the original where an arm was affected)")
    c = Counter((f["stage"], f["kind"]) for f in data["failures"])
    if not c:
        L.append("  none")
    else:
        L.append(f"  {'stage':26}{'kind':28}{'count':>6}  example")
        for (stage, kind), n in sorted(c.items()):
            ex = next(f for f in data["failures"] if (f["stage"], f["kind"]) == (stage, kind))
            L.append(f"  {stage:26}{kind:28}{n:>6}  {ex['query_id']}: {ex['detail'][:70]}")
    L.append("")
    L.append("Threats to validity: docs/threats_to_validity.md")
    return "\n".join(L)



if __name__ == "__main__":
    print(report(Path(sys.argv[1])))
