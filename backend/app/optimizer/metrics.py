"""Per-candidate metrics (CLAUDE.md rule 7). One protocol for every query and every arm:
`warmup` runs discarded, then `runs` timed runs; report median and IQR.

Runtime is wall-clock around execute()+fetchall() on the reader connection, so it
includes sending rows to the client, identically for all candidates.
est_cost / est_rows come from EXPLAIN FORMAT=JSON (no execution). rows_examined is
what MySQL actually read, from performance_schema's statement history.
"""
import json
import time
from dataclasses import asdict, dataclass, field

import numpy as np

from app.config import RUN_CONFIG
from app.optimizer.execution import QueryFailed, QueryTimeout, run

WARMUP = RUN_CONFIG.get("warmup_runs", 2)
RUNS = RUN_CONFIG.get("timed_runs", 5)


@dataclass
class Metrics:
    outcome: str = "ok"  # ok | timeout | error. A timeout is an outcome, never a runtime.
    error: str = ""
    runtime_ms_median: float | None = None
    runtime_ms_iqr: float | None = None
    runs_ms: list[float] = field(default_factory=list)
    rows_examined: int | None = None
    est_cost: float | None = None
    est_rows: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _walk(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _walk(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v)


def parse_explain(explain_json: str) -> tuple[float | None, float | None]:
    """-> (est_cost, est_rows) from EXPLAIN FORMAT=JSON.

    est_cost: the top query_block's query_cost; for UNIONs (no top-level cost) the sum over the
    member query_blocks. est_rows: the sum of rows_examined_per_scan over every table access, a
    plan-size proxy (per-scan, so nested-loop multiplication is NOT included). Both are optimizer
    estimates, not measurements.
    """
    plan = json.loads(explain_json)
    top = plan.get("query_block", {})
    cost = (top.get("cost_info") or {}).get("query_cost")
    if cost is None:  # UNION: each member lives under query_specifications[*].query_block
        costs = [float(spec["query_block"]["cost_info"]["query_cost"]) for d in _walk(top)
                 for spec in d.get("query_specifications", []) if "cost_info" in spec.get("query_block", {})]
        cost = sum(costs) if costs else None
    rows = [float(d["rows_examined_per_scan"]) for d in _walk(top) if "rows_examined_per_scan" in d]
    return (float(cost) if cost is not None else None), (sum(rows) if rows else None)


def _thread_id(cur) -> int | None:
    try:
        cur.execute("SELECT THREAD_ID FROM performance_schema.threads WHERE PROCESSLIST_ID = CONNECTION_ID()")
        return cur.fetchone()[0]
    except Exception:
        return None


def _rows_examined(cur, tid: int | None) -> int | None:
    """ROWS_EXAMINED of the last COMPLETED statement on this connection. Call it right after the run
    being measured, with nothing in between (the thread id is therefore looked up beforehand)."""
    if tid is None:
        return None
    try:
        cur.execute("SELECT ROWS_EXAMINED FROM performance_schema.events_statements_history "
                    "WHERE THREAD_ID = %s ORDER BY EVENT_ID DESC LIMIT 1", (tid,))
        row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None


def collect(cur, sql: str, warmup: int = WARMUP, runs: int = RUNS) -> Metrics:
    m = Metrics()
    tid = _thread_id(cur)
    try:
        cur.execute("EXPLAIN FORMAT=JSON " + sql)
        m.est_cost, m.est_rows = parse_explain(cur.fetchone()[0])
        cur.fetchall()
    except Exception as e:  # unplannable query: record it, keep going to the run, which will fail too
        m.error = f"explain: {str(e)[:160]}"
    try:
        for _ in range(warmup):
            run(cur, sql)
        times = []
        for _ in range(runs):
            t = time.perf_counter()
            run(cur, sql)
            times.append((time.perf_counter() - t) * 1000)
        m.rows_examined = _rows_examined(cur, tid)
    except QueryTimeout as e:
        m.outcome, m.error = "timeout", str(e)
        return m  # remaining repeats skipped; the timeout is recorded once
    except QueryFailed as e:
        m.outcome, m.error = "error", str(e)[:300]
        return m
    q1, med, q3 = np.percentile(times, [25, 50, 75])
    m.runs_ms = [round(t, 3) for t in times]
    m.runtime_ms_median, m.runtime_ms_iqr = round(float(med), 3), round(float(q3 - q1), 3)
    return m
