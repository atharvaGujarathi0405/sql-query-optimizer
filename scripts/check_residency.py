"""Is the data really served from the InnoDB buffer pool during timed runs? (threats_to_validity item 6)

For every registry query (original SQL only), as the read-only user: run the rule-7 protocol once so the
pages are warm, then run it again and read the server-wide counter Innodb_buffer_pool_reads (= logical
reads that MISSED the pool and went to disk) before and after. A warm second pass should add ~0.
Prints counts only, never runtimes (no timing of test-split queries is shown).

Run on an otherwise idle server: the counter is global, so other sessions would add to it.
Usage: python scripts/check_residency.py            (from backend/ or with the package installed)
"""
import sys
from pathlib import Path

import yaml

from app.config import QUERY_TIMEOUT_S
from app.db import get_readonly_conn
from app.optimizer.execution import open_session
from app.optimizer.metrics import collect

ROOT = Path(__file__).resolve().parents[1]
TOLERANCE = 100  # physical page reads allowed in a warm pass; a 16 KiB page each, i.e. ~1.6 MiB


def misses(cur) -> int:
    cur.execute("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")
    return int(cur.fetchone()[1])


def main() -> int:
    queries = yaml.safe_load((ROOT / "data" / "queries.yaml").read_text())
    bad = []
    print(f"{'id':38}{'warm-pass pool misses':>22}")
    with get_readonly_conn() as conn:
        cur = open_session(conn, queries[0]["dataset"], QUERY_TIMEOUT_S)
        for q in queries:
            cur.execute(f"USE `{q['dataset']}`")
            collect(cur, q["sql"].strip())  # pass 1: warm
            before = misses(cur)
            m = collect(cur, q["sql"].strip())  # pass 2: the one that must be resident
            d = misses(cur) - before
            flag = "" if m.outcome == "ok" and d <= TOLERANCE else "   <-- NOT RESIDENT" if m.outcome == "ok" else f"   <-- {m.outcome}"
            print(f"{q['id']:38}{d:>22}{flag}")
            if flag:
                bad.append(q["id"])
    print(f"\n{len(queries) - len(bad)} of {len(queries)} queries ran their warm pass with <= {TOLERANCE} pool misses.")
    if bad:
        print("not resident / not ok:", ", ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
