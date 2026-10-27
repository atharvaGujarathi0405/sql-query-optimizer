"""Feasibility check of the registry against the live DB, as the read-only user.

Runs every query once with a per-query timeout and reports ONE outcome each:
  ok | error | empty | timeout
plus an independent flag NONDET when the outermost query has LIMIT but no
ORDER BY (result rows are then arbitrary, so correctness comparison is unsound).

This is NOT a measurement (one run, no warm-up) and NOT a filter: queries are
never dropped because of what this prints (CLAUDE.md rule 9). Error, empty and
NONDET are defects in the query definition and are fixed from the message /
emptiness alone, never from runtime. Timeouts are a separate outcome, not a
runtime.
Usage: python scripts/validate_queries.py [timeout_seconds=config/run.yaml query_timeout_s]
"""
import sys
import time
from collections import Counter
from pathlib import Path

import sqlglot
import yaml

from app.config import QUERY_TIMEOUT_S
from app.db import get_readonly_conn

timeout_s = int(sys.argv[1]) if len(sys.argv) > 1 else QUERY_TIMEOUT_S
queries = yaml.safe_load((Path(__file__).resolve().parents[1] / "data" / "queries.yaml").read_text())


def limit_without_order(sql: str) -> bool:
    tree = sqlglot.parse_one(sql, read="mysql")
    return bool(tree.args.get("limit")) and not tree.args.get("order")


print(f"{'id':38}{'dataset':11}{'rows':>9}{'secs':>9}  outcome")
outcomes = Counter()
nondet = []
with get_readonly_conn() as conn, conn.cursor() as cur:
    cur.execute(f"SET SESSION max_execution_time = {timeout_s * 1000}")
    for q in queries:
        cur.execute(f"USE `{q['dataset']}`")
        t = time.perf_counter()
        rows = -1
        try:
            cur.execute(q["sql"])
            rows = len(cur.fetchall())
            outcome = "ok" if rows else "empty"
        except Exception as e:
            # errno 3024 = ER_QUERY_TIMEOUT
            outcome = "timeout" if getattr(e, "errno", None) == 3024 else f"error: {str(e)[:80]}"
        flag = ""
        if limit_without_order(q["sql"]):
            flag = "  NONDET (LIMIT without ORDER BY)"
            nondet.append(q["id"])
        outcomes[outcome.split(":")[0]] += 1
        print(f"{q['id']:38}{q['dataset']:11}{rows:>9}{time.perf_counter() - t:>9.2f}  {outcome}{flag}", flush=True)
print(f"\n{len(queries)} queries (timeout {timeout_s}s): " + ", ".join(f"{k}={v}" for k, v in sorted(outcomes.items()))
      + f", nondeterministic={len(nondet)} {nondet}")
sys.exit(bool(outcomes["error"] or outcomes["empty"] or nondet))
