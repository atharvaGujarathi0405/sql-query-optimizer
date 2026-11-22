"""Workload Advisor: real index experiments -> ILP (and greedy) selection -> validation on the real server.

Protocol (docs/threats_to_validity.md, Phase 4 items):
  1. Workload = registry queries of ONE dataset, DEV split only (the test split is never touched: index
     experiments would reveal its timings, rule 5).
  2. Baseline: every query measured on the existing schema, reader user, the rule-7 protocol (metrics.collect).
  3. Candidates from the queries (candidates.py). Each is created ONCE as an INVISIBLE index (admin) and the table
     ANALYZEd. The queries that touch that table are then measured (reader) with the index hidden and shown in
     alternating A B | B A rounds (paired()), so both conditions see the same data, statistics and machine drift.
     The index is dropped afterwards, also on failure. benefit[q, i] = ms saved; a slowdown is recorded as
     regression_ms and charged to the index as a penalty. Either counts only if EVERY round shows it, in the same
     direction, by more than min_gain_pct of that round's baseline (effect_ms; config/advisor.yaml).
  4. ILP and greedy choose from the SAME candidates, benefits, penalties, sizes, budget and cap.
  5. Each chosen set is created TOGETHER (invisible) and the whole workload measured the same alternating way:
     actual gain vs predicted (the per-index benefits assume no interaction). Then everything is dropped and the
     baseline re-measured as a drift check.
  History: a first design compared against one baseline taken at the start (a no-op index showed a 153 ms "gain");
  a second against a baseline taken just before each index, but at TPC-H scale one unchanged query still read 2425,
  1747 and 1341 ms in three passes, and the chosen index "regressed" four queries that had looked faster minutes
  earlier. Alternation within one session removes that confound.
Experimental indexes are named adv_<hash>; any left over (a killed process) is swept at the start and end.
"""
import hashlib
import json
import re
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import yaml

from app.advisor.candidates import ADV_PREFIX, candidates
from app.advisor.ilp import solve_greedy, solve_ilp
from app.config import RUN_CONFIG
from app.db import get_admin_conn, get_readonly_conn
from app.optimizer.execution import open_session
from app.optimizer.metrics import collect

ROOT = Path(__file__).resolve().parents[3]
ADV_DIR = ROOT / "results" / "advisor"
CFG = yaml.safe_load((ROOT / "config" / "advisor.yaml").read_text())
IDENT = re.compile(r"^[\w$]+$")
LOCK = threading.Lock()  # concurrent index experiments would corrupt each other's timings


class AdvisorBusy(Exception):
    pass


class CleanupError(Exception):
    pass


def _q(name: str) -> str:
    if not IDENT.match(name):
        raise ValueError(f"unsafe identifier {name!r}")
    return f"`{name}`"


def index_name(table: str, cols: list[str]) -> str:
    return ADV_PREFIX + hashlib.sha1(f"{table}:{','.join(cols)}".encode()).hexdigest()[:10]


def _analyze(cur, dataset: str, tables) -> None:
    for t in sorted(set(tables)):
        cur.execute(f"ANALYZE TABLE {_q(dataset)}.{_q(t)}")
        cur.fetchall()


def sweep_stale(admin, dataset: str) -> list[str]:
    """Drop every adv_ index in the dataset. Returns what it removed (normally nothing)."""
    cur = admin.cursor()
    cur.execute("SELECT DISTINCT table_name, index_name FROM information_schema.statistics "
                "WHERE table_schema=%s AND index_name LIKE 'adv\\_%%'", (dataset,))
    found = cur.fetchall()
    for t, i in found:
        cur.execute(f"DROP INDEX {_q(i)} ON {_q(dataset)}.{_q(t)}")
    if found:
        _analyze(cur, dataset, [t for t, _ in found])
    return [f"{t}.{i}" for t, i in found]


def _fingerprint(cur, dataset: str, tables) -> dict:
    """{(table, index): (columns, non_unique)} of every NON-experimental index on those tables."""
    out: dict = {}
    for t in sorted(set(tables)):
        cur.execute("SELECT index_name, seq_in_index, column_name, non_unique FROM information_schema.statistics "
                    "WHERE table_schema=%s AND table_name=%s AND index_name NOT LIKE 'adv\\_%%' ORDER BY index_name, seq_in_index", (dataset, t))
        for i, _, c, nu in cur.fetchall():
            cols, _ = out.get((t, i), ((), nu))
            out[(t, i)] = (cols + (c,), nu)
    return out


def _restore(cur, dataset: str, before: dict, tables) -> list[str]:
    """Recreate any pre-existing plain index that is gone (PRIMARY/UNIQUE cannot be guessed and are only reported)."""
    now = _fingerprint(cur, dataset, tables)
    restored = []
    for (t, i), (cols, nu) in before.items():
        if (t, i) not in now and nu and i != "PRIMARY":
            cur.execute(f"CREATE INDEX {_q(i)} ON {_q(dataset)}.{_q(t)} ({', '.join(_q(c) for c in cols)})")
            restored.append(f"{t}.{i}")
    return restored


@contextmanager
def indexes_created(admin, dataset: str, cands: list[dict], invisible: bool = False):
    """Create the indexes (optionally INVISIBLE, so the optimizer ignores them until toggled), refresh statistics,
    yield; ALWAYS drop them and refresh again (also on failure).

    Also proves the schema is as it was. MySQL can silently DROP a pre-existing index as a side effect of CREATE
    INDEX (an implicit foreign-key index that the new index makes redundant: employees.dept_emp, threats 48); the FK
    then depends on the new index and it cannot be dropped. So right after creating we check, and if something is
    gone we restore it first (which frees the new index), clean up, and raise CleanupError: that candidate's
    measurements would have been taken on a schema missing an index, so it must fail rather than report."""
    cur = admin.cursor()
    cur.execute("SET SESSION information_schema_stats_expiry = 0")
    tables = [c["table"] for c in cands]
    before = _fingerprint(cur, dataset, tables)
    completed = False
    try:
        for c in cands:
            cols = ", ".join(_q(x) for x in c["cols"])
            cur.execute(f"CREATE INDEX {_q(c['name'])} ON {_q(dataset)}.{_q(c['table'])} ({cols})" + (" INVISIBLE" if invisible else ""))
        _analyze(cur, dataset, tables)
        gone = sorted(set(before) - set(_fingerprint(cur, dataset, tables)))
        if gone:
            raise CleanupError(f"creating the index dropped existing index(es) {gone}; candidate not measured")
        yield
        completed = True
    finally:
        restored = _restore(cur, dataset, before, tables)  # first: this frees an index the FK had latched onto
        for c in cands:
            try:
                cur.execute(f"DROP INDEX {_q(c['name'])} ON {_q(dataset)}.{_q(c['table'])}")
            except Exception:  # not created (the failure we are unwinding from), or really stuck: sweep decides
                pass
        left = sweep_stale(admin, dataset)  # catches anything the loop could not drop
        _analyze(cur, dataset, tables)
        if left:
            raise CleanupError(f"had to sweep leftover indexes: {left}")
        after = _fingerprint(cur, dataset, tables)
        if after != before:
            raise CleanupError(f"index set differs after cleanup; restored {restored or 'nothing'}; "
                               f"before={sorted(before)} after={sorted(after)}")
        if restored and completed:  # (when an error is already in flight it is the one to report)
            raise CleanupError(f"existing index(es) were lost during the experiment and have been restored: {restored}")


def index_mb(admin, dataset: str, table: str) -> float:
    cur = admin.cursor()
    cur.execute("SET SESSION information_schema_stats_expiry = 0")
    cur.execute("SELECT index_length FROM information_schema.tables WHERE table_schema=%s AND table_name=%s", (dataset, table))
    return cur.fetchone()[0] / 1048576


def new_index_mb(admin, dataset: str, table: str, before: float) -> float:
    """Size of the index just created. A real index is never <= 0 MB, so a non-positive reading is stale statistics
    (seen once on a 24-row table and once on salaries): refresh and re-read, and fail the candidate rather than
    budget it at zero."""
    cur = admin.cursor()
    for _ in range(4):
        mb = index_mb(admin, dataset, table) - before
        if mb > 0:
            return mb
        _analyze(cur, dataset, [table])
        time.sleep(1)
    raise RuntimeError("the server did not report a size for the new index")


def _set_visible(cur, dataset: str, cands: list[dict], visible: bool) -> None:
    for c in cands:
        cur.execute(f"ALTER TABLE {_q(dataset)}.{_q(c['table'])} ALTER INDEX {_q(c['name'])} {'VISIBLE' if visible else 'INVISIBLE'}")


def effect_ms(base_r: list[float], with_r: list[float], pct: float) -> float:
    """Signed ms saved (+) or lost (-), or 0. Each round measured 'without' and 'with' back to back, so the
    within-round difference is nearly free of drift. The effect counts only if EVERY round agrees, in the same
    direction, by more than pct% of that round's baseline; the reported size is the mean difference."""
    d = [b - w for b, w in zip(base_r, with_r)]
    if d and all(x > pct / 100 * b for x, b in zip(d, base_r)):
        return sum(d) / len(d)
    if d and all(-x > pct / 100 * b for x, b in zip(d, base_r)):
        return sum(d) / len(d)
    return 0.0


def paired(admin, reader, dataset: str, sqls: dict[str, str], qids, cands: list[dict], rounds: int) -> dict:
    """Measure `qids` with the (already created, invisible) candidates hidden and shown, alternating A B | B A | ...
    -> {query: {"outcome", "base_rounds", "with_rounds", "base_ms", "with_ms"}} (ms = median over rounds).
    Leaves the indexes invisible."""
    cur = admin.cursor()
    got = {q: {"outcome": "ok", "base_rounds": [], "with_rounds": []} for q in qids}
    for r in range(rounds):
        for vis in ((False, True) if r % 2 == 0 else (True, False)):
            _set_visible(cur, dataset, cands, vis)
            for q, m in measure(reader, dataset, sqls, qids).items():
                if m["outcome"] != "ok":
                    got[q]["outcome"] = m["outcome"]
                else:
                    got[q]["with_rounds" if vis else "base_rounds"].append(m["runtime_ms_median"])
    _set_visible(cur, dataset, cands, False)
    for g in got.values():
        ok = g["outcome"] == "ok" and g["base_rounds"] and g["with_rounds"]
        g["base_ms"] = round(float(np.median(g["base_rounds"])), 3) if ok else None
        g["with_ms"] = round(float(np.median(g["with_rounds"])), 3) if ok else None
    return got


def measure(reader, dataset: str, sqls: dict[str, str], qids) -> dict:
    cur = open_session(reader, dataset, RUN_CONFIG["query_timeout_s"])
    try:
        return {q: collect(cur, sqls[q]).to_dict() for q in qids}
    finally:
        cur.close()


def dev_workload(dataset: str, qids: list[str] | None) -> dict[str, str]:
    reg = {q["id"]: q for q in yaml.safe_load((ROOT / "data" / "queries.yaml").read_text())}
    dev = set(yaml.safe_load((ROOT / "data" / "splits.yaml").read_text())["dev"])
    want = qids or sorted(i for i in dev if reg[i]["dataset"] == dataset)
    bad = [i for i in want if i not in dev or i not in reg or reg[i]["dataset"] != dataset]
    if bad or not want:
        raise ValueError(f"workload must be dev-split queries of {dataset!r}; rejected: {bad or 'empty workload'}")
    return {i: reg[i]["sql"].strip().rstrip(";") for i in want}


def advise(dataset: str, qids: list[str] | None = None, budget_mb: float | None = None, max_indexes: int | None = None,
           log=print, admin_factory=get_admin_conn, reader_factory=get_readonly_conn, save: bool = True) -> dict:
    budget_mb = CFG["default_budget_mb"] if budget_mb is None else budget_mb
    max_indexes = CFG["default_max_indexes"] if max_indexes is None else max_indexes
    sqls = dev_workload(dataset, qids)
    if not LOCK.acquire(blocking=False):
        raise AdvisorBusy("another advisor run is in progress")
    started = datetime.now(timezone.utc)
    band = max(RUN_CONFIG["tie_band_pct"], CFG["min_gain_pct"])  # see config/advisor.yaml: between-pass noise exceeds the 5% tie band
    failures: list[str] = []
    admin = reader = None
    try:
        admin, reader = admin_factory(), reader_factory()
        # Autocommit: an implicit transaction left open by a SELECT would hold a metadata lock and make DDL wait forever.
        admin.autocommit = reader.autocommit = True
        stale = sweep_stale(admin, dataset)
        if stale:
            log(f"swept leftover experimental indexes from an earlier run: {stale}")
        cur = admin.cursor()
        cur.execute("SELECT VERSION()")
        version = cur.fetchone()[0]

        log(f"baseline: measuring {len(sqls)} queries on the existing schema")
        base = measure(reader, dataset, sqls, list(sqls))

        cands, touched = candidates(admin, dataset, sqls, CFG["max_candidates"], CFG["max_index_columns"])
        for c in cands:
            c["name"] = index_name(c["table"], c["cols"])
        log(f"{len(cands)} candidate indexes from the workload")

        benefit: dict = {}
        size: dict = {}
        penalty: dict = {}
        for n, c in enumerate(cands, 1):
            label = f"{c['table']}({', '.join(c['cols'])})"
            qs = [q for q in sqls if c["table"] in touched.get(q, [])]
            log(f"experiment {n}/{len(cands)}: CREATE INDEX on {label}; {len(qs)} queries, {CFG['rounds']} alternating rounds")
            try:
                before = index_mb(admin, dataset, c["table"])
                with indexes_created(admin, dataset, [c], invisible=True):
                    c["size_mb"] = round(new_index_mb(admin, dataset, c["table"], before), 3)
                    res = paired(admin, reader, dataset, sqls, qs, [c], CFG["rounds"])
            except Exception as e:  # logged, never silently dropped (rule 9); the index is already dropped
                failures.append(f"{label}: {type(e).__name__}: {str(e)[:200]}")
                c["error"] = failures[-1]
                log(f"  failed: {failures[-1]}")
                continue
            eff = {q: effect_ms(res[q]["base_rounds"], res[q]["with_rounds"], band) if res[q]["outcome"] == "ok" else 0.0 for q in qs}
            c["benefit_ms"] = {q: round(max(e, 0.0), 2) for q, e in eff.items()}
            c["regression_ms"] = {q: round(max(-e, 0.0), 2) + 0.0 for q, e in eff.items()}  # + 0.0: no "-0.0" in the JSON
            c["paired_base_ms"] = {q: res[q]["base_ms"] for q in qs}
            c["with_index"] = {q: res[q]["with_ms"] for q in qs}
            c["rounds_ms"] = {q: {"without": res[q]["base_rounds"], "with": res[q]["with_rounds"]} for q in qs}
            size[c["name"]] = max(c["size_mb"], 0.01)
            benefit.update({(q, c["name"]): g for q, g in c["benefit_ms"].items() if g > 0})
            penalty[c["name"]] = round(sum(c["regression_ms"].values()), 2)

        arms = {"ilp": solve_ilp(benefit, size, budget_mb, max_indexes, penalty),
                "greedy": solve_greedy(benefit, size, budget_mb, max_indexes, penalty)}
        by_name = {c["name"]: c for c in cands}
        validated: dict = {}
        for arm, sel in arms.items():
            key = tuple(sel["chosen"])
            sel["size_mb"] = round(sum(size[i] for i in key), 2)
            if not key:
                sel["actual"] = None
                continue
            if key not in validated:
                log(f"validating {arm}: {len(key)} indexes created together, whole workload, {CFG['rounds']} alternating rounds")
                chosen = [by_name[i] for i in key]
                with indexes_created(admin, dataset, chosen, invisible=True):
                    now = paired(admin, reader, dataset, sqls, list(sqls), chosen, CFG["rounds"])
                validated[key] = {q: {"base_ms": now[q]["base_ms"], "with_ms": now[q]["with_ms"], "outcome": now[q]["outcome"],
                                      "rounds": {"without": now[q]["base_rounds"], "with": now[q]["with_rounds"]}} for q in sqls}
            per_q = validated[key]
            sel["actual"] = {"per_query": per_q, "gain_ms": round(sum(
                v["base_ms"] - v["with_ms"] for v in per_q.values() if v["outcome"] == "ok" and v["base_ms"] is not None), 2),
                "regressions": [q for q, v in per_q.items() if v["outcome"] == "ok" and v["base_ms"] is not None
                                and v["with_ms"] > v["base_ms"] * (1 + band / 100)]}

        log("drift check: re-measuring the baseline after every index is dropped")
        after = measure(reader, dataset, sqls, list(sqls))
        drift = {q: round(after[q]["runtime_ms_median"] / base[q]["runtime_ms_median"], 3)
                 for q in sqls if after[q]["outcome"] == base[q]["outcome"] == "ok"}
        result = {
            "id": f"adv-{dataset}-{started:%Y%m%dT%H%M%S}", "dataset": dataset, "started": started.isoformat(),
            "finished": datetime.now(timezone.utc).isoformat(), "mysql": version, "budget_mb": budget_mb, "max_indexes": max_indexes,
            "protocol": {**{k: RUN_CONFIG[k] for k in ("warmup_runs", "timed_runs", "query_timeout_s")}, "min_gain_pct": band, "rounds": CFG["rounds"]},
            "baseline": {q: {"ms": m["runtime_ms_median"], "iqr": m["runtime_ms_iqr"], "outcome": m["outcome"]} for q, m in base.items()},
            "candidates": [{k: v for k, v in c.items() if k != "name"} | {"id": c["name"]} for c in cands],
            "selections": arms, "baseline_drift_ratio": drift, "failures": failures,
        }
        if save:
            ADV_DIR.mkdir(parents=True, exist_ok=True)
            (ADV_DIR / f"{result['id']}.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        log("done")
        return result
    finally:
        try:
            if admin is not None:
                sweep_stale(admin, dataset)
        finally:
            for c in (admin, reader):
                if c is not None:
                    c.close()
            LOCK.release()
