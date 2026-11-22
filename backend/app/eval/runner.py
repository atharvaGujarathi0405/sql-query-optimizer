"""Three-arm evaluation harness. Writes results/<run_id>.jsonl: every query, every repeat, every failure.

Arms (CLAUDE.md): 1 = MySQL on the original query; 2a = LLM picks from the pool seeing SQL + schema;
2b = same plus the metric table; 3 = OR engine (TOPSIS + noise guard); 3-raw = TOPSIS without the guard
(same data, dev-set ablation only); 3-measured = declared secondary arm, the same engine on the two measured
criteria only (config/ahp.yaml `variants`).

Design (see docs/threats_to_validity.md):
- Arms 2 and 3 choose from the SAME cached pool (original + correctness-verified candidates).
- SELECTION pass: one measurement of every alternative; arms 2b and 3 both read this one table.
- EVALUATION pass: a second, independent measurement of every alternative; ALL arms are scored from it,
  so no arm is scored on the numbers it selected from, and two arms picking the same query get the
  same time. Both passes use the rule-7 protocol and a seeded shuffled measurement order.
- A failed LLM answer, an unusable OR result or a missing pool is logged as a failure and scored as the
  original (the baseline), the same for every arm. Nothing is dropped (rule 9).
- One provider per run: every LLM call is pinned to --provider, and a pool made by another
  provider/model is refused.
"""
import argparse
import hashlib
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from app.config import QUERY_TIMEOUT_S, RUN_CONFIG
from app.db import get_readonly_conn
from app.eval.llm_arm import build_messages, option_order, parse_choice
from app.eval.runinfo import run_metadata
from app.llm.client import LLMClient
from app.optimizer.analysis import schema_text, tables_in
from app.optimizer.candidates import POOL_DIR, load_pool, pool_path, valid_candidates
from app.optimizer.decision import NOISE_K, or_decision, runtime_argmin_decision
from app.optimizer.execution import open_session
from app.optimizer.metrics import collect

ROOT = Path(__file__).resolve().parents[3]
RESULTS_DIR = ROOT / "results"
SEL_SEED, EVAL_SEED = 101, 202  # measurement-order shuffles for the two passes
ARMS_LLM = ("2a", "2b")
TRANSPORT_ERRORS = ("ConnectError", "ConnectTimeout", "ReadTimeout", "WriteError", "RemoteProtocolError", "PoolTimeout")


def sha256_file(p: Path) -> str | None:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None


def select_queries(split: str, limit: int | None = None, ids: list[str] | None = None) -> list[dict]:
    """Registry queries in `split` ('dev'|'test'|'all'). With `limit`, take them round-robin across
    datasets (registry order within each) so a small run still covers every dataset."""
    reg = yaml.safe_load((ROOT / "data" / "queries.yaml").read_text())
    splits = yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())
    keep = set(splits["dev"]) | set(splits["test"]) if split == "all" else set(splits[split])
    qs = [q for q in reg if q["id"] in keep]
    if ids:
        by = {q["id"]: q for q in qs}
        missing = [i for i in ids if i not in by]
        if missing:
            raise SystemExit(f"not in split {split!r}: {missing}")
        return [by[i] for i in ids]
    if limit:
        by_ds: dict[str, list[dict]] = {}
        for q in qs:
            by_ds.setdefault(q["dataset"], []).append(q)
        out, queues = [], [list(v) for _, v in sorted(by_ds.items())]
        while len(out) < limit and any(queues):
            for qq in queues:
                if qq and len(out) < limit:
                    out.append(qq.pop(0))
        return out
    return qs


class Writer:
    def __init__(self, path: Path):
        self.f = path.open("x", encoding="utf-8")  # 'x': never overwrite a previous run's file
        self.n = 0

    def put(self, **rec) -> None:
        self.f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self.f.flush()
        self.n += 1


def run(run_id: str, provider: str, queries: list[dict], *, client: LLMClient, split: str,
        pool_dir: Path = POOL_DIR, out_dir: Path = RESULTS_DIR, repeats: int | None = None) -> Path:
    repeats = repeats or RUN_CONFIG["llm_repeats"]
    model = client.cfg.groq_model if provider == "groq" else client.cfg.ollama_model
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{run_id}.jsonl"
    w = Writer(path)
    t_run = time.perf_counter()
    with get_readonly_conn() as conn:
        w.put(type="run_meta", run_id=run_id, split=split, query_ids=[q["id"] for q in queries], repeats=repeats,
              provider=provider, model=model, temperature=client.cfg.temperature, seed=client.cfg.seed,
              tie_band_pct=RUN_CONFIG["tie_band_pct"], noise_guard_iqr_multiple=NOISE_K,
              query_timeout_s=QUERY_TIMEOUT_S, warmup_runs=RUN_CONFIG["warmup_runs"], timed_runs=RUN_CONFIG["timed_runs"],
              config_sha256={n: sha256_file(ROOT / "config" / n) for n in ("ahp.yaml", "run.yaml")},
              data_sha256={n: sha256_file(ROOT / "data" / n) for n in ("queries.yaml", "splits.yaml")},
              environment=run_metadata(conn), started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        for q in queries:
            _run_query(conn, w, client, provider, model, q, repeats, pool_dir)
    w.put(type="run_end", run_id=run_id, n_records=w.n + 1, elapsed_s=round(time.perf_counter() - t_run, 1))
    w.f.close()
    return path


def _fail(w: Writer, run_id_q: str, stage: str, kind: str, detail: str, **extra) -> None:
    w.put(type="failure", query_id=run_id_q, stage=stage, kind=kind, detail=str(detail)[:500], **extra)


def _measure_pass(cur, w: Writer, qid: str, alts: list[dict], phase: str, seed: int) -> dict:
    order = list(range(len(alts)))
    random.Random(seed).shuffle(order)
    res = {}
    for i in order:
        m = collect(cur, alts[i]["sql"]).to_dict()
        res[alts[i]["id"]] = m
        w.put(type="measurement", phase=phase, query_id=qid, alt_id=alts[i]["id"], metrics=m)
        if m["outcome"] != "ok":
            _fail(w, qid, f"{phase}_measurement", m["outcome"], m["error"], alt_id=alts[i]["id"])
    return res


def _run_query(conn, w: Writer, client, provider: str, model: str, q: dict, repeats: int, pool_dir: Path) -> None:
    qid, dataset, sql = q["id"], q["dataset"], q["sql"].strip()
    t0 = time.perf_counter()
    cur = open_session(conn, dataset, QUERY_TIMEOUT_S)

    pool, valid = load_pool(qid, pool_dir), []
    if pool is None:
        _fail(w, qid, "pool", "pool_missing", f"no cached pool at {pool_path(qid, pool_dir)}")
    elif (pool["generator"]["provider"], pool["generator"]["model"]) != (provider, model):
        _fail(w, qid, "pool", "pool_provider_mismatch",
              f"pool made by {pool['generator']['provider']}/{pool['generator']['model']}, run is {provider}/{model}")
    else:
        valid = valid_candidates(pool)
    alts = [{"id": "original", "sql": sql}] + [{"id": c["id"], "sql": c["sql"]} for c in valid]
    w.put(type="query", query_id=qid, dataset=dataset, tags=q["tags"], original_sql=sql, alts=alts,
          pool_file_sha256=sha256_file(pool_path(qid, pool_dir)),
          n_proposed=(pool or {}).get("n_proposed"), n_valid=len(valid),
          pool_statuses=[c["status"] for c in (pool or {}).get("candidates", [])])

    sel = _measure_pass(cur, w, qid, alts, "selection", SEL_SEED)
    ev = _measure_pass(cur, w, qid, alts, "evaluation", EVAL_SEED)
    sel_alts = [{"id": a["id"], "metrics": sel[a["id"]]} for a in alts]
    options = [{**a, "metrics": sel[a["id"]]} for a in alts]

    def arm(name, pick, repeat=None, by="", failure=None, **extra):
        w.put(type="arm", query_id=qid, arm=name, repeat=repeat, pick=pick, picked_by=by,
              fallback=failure is not None, failure=failure, **extra)

    arm("1", "original", by="baseline")

    d, dm, da = or_decision(sel_alts), or_decision(sel_alts, variant="measured"), runtime_argmin_decision(sel_alts)
    for name, pick, dec in (("3", d["winner"], d), ("3-raw", d["winner_raw"], d), ("3-measured", dm["winner"], dm),
                            ("3-argmin", da["winner"], da)):
        if pick is None:
            _fail(w, qid, "or_engine", "no_rankable_alternative", f"excluded: {dec['excluded']}", arm=name)
        arm(name, pick or "original", by="or_engine", failure=None if pick else "no_rankable_alternative",
            closeness=dec["closeness"], guard=dec["guard"], winner_raw=dec["winner_raw"], criteria=dec["criteria"])

    ddl = schema_text(conn, dataset, tables_in(sql))
    for variant in ARMS_LLM:
        for r in range(repeats):
            if len(alts) == 1:  # nothing to choose between: not a failure, not an LLM call
                arm(variant, "original", r, by="skipped_single_option")
                continue
            order = option_order(qid, r, len(alts))
            msgs, label_map = build_messages(variant, dataset, ddl, options, order)
            resp = client.chat(msgs, provider=provider, json_mode=True)
            info = dict(prompt_sha256=hashlib.sha256(json.dumps(msgs).encode()).hexdigest(), order=order,
                        label_map=label_map, latency_s=resp.latency_s, attempts=[a.__dict__ for a in resp.attempts],
                        answer=(resp.text or "")[:1500], llm_provider=resp.provider, llm_model=resp.model)
            if resp.text is None:
                # an unreachable provider (network/DNS) is an infrastructure fault, not model behaviour: same
                # scoring (the original), but a separate kind so a run hit by an outage is recognisable
                kind = "llm_unreachable" if any(k in resp.error for k in TRANSPORT_ERRORS) else "llm_failure"
                _fail(w, qid, f"arm_{variant}", kind, resp.error, repeat=r)
                arm(variant, "original", r, by="llm", failure=kind, **info)
                continue
            pick, err = parse_choice(resp.text, label_map)
            if pick is None:
                _fail(w, qid, f"arm_{variant}", err.split(":")[0], err, repeat=r)
                arm(variant, "original", r, by="llm", failure=err.split(":")[0], **info)
            else:
                arm(variant, pick, r, by="llm", **info)
    w.put(type="query_end", query_id=qid, elapsed_s=round(time.perf_counter() - t0, 1))


def run_metadata_git_dirty() -> bool:
    import subprocess
    out = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=ROOT).stdout
    return any(line[3:] != "CLAUDE.md" for line in out.splitlines())  # CLAUDE.md is the author's own notes file


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--provider", required=True, choices=["ollama", "groq"], help="the ONE provider for this run")
    ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
    ap.add_argument("--limit", type=int, help="first N queries, round-robin across datasets")
    ap.add_argument("--queries", help="comma-separated query ids")
    ap.add_argument("--allow-test", action="store_true", help="required to touch the test split (report on test, never tune on it)")
    a = ap.parse_args()
    if a.split != "dev" and not a.allow_test:
        raise SystemExit("refusing to run the test split without --allow-test: tune on dev, report on test once")
    if a.split != "dev" and run_metadata_git_dirty():
        raise SystemExit("refusing to run the test split from a dirty git tree: commit first so the run is traceable")
    qs = select_queries(a.split, a.limit, a.queries.split(",") if a.queries else None)
    client = LLMClient(log_path=RESULTS_DIR / "llm_calls.jsonl")
    p = run(a.run_id, a.provider, qs, client=client, split=a.split)
    print(f"wrote {p}")


if __name__ == "__main__":
    main()
