"""Generate (or reuse) candidate pools for registry queries with ONE pinned provider.

Pools are written to candidates/<query_id>.json after every candidate has passed the correctness check.
An existing pool from the same provider is reused, never regenerated (fairness rule 2); one from a
different provider is refused. The evaluation runner only reads these files.

Usage: python scripts/generate_pools.py --provider groq --split dev --limit 5
"""
import argparse

from app.db import get_readonly_conn
from app.eval.runner import RESULTS_DIR, select_queries
from app.llm.client import LLMClient
from app.optimizer.candidates import generate_pool

ap = argparse.ArgumentParser()
ap.add_argument("--provider", required=True, choices=["ollama", "groq"])
ap.add_argument("--split", default="dev", choices=["dev", "test", "all"])
ap.add_argument("--limit", type=int)
ap.add_argument("--queries", help="comma-separated ids")
ap.add_argument("--allow-test", action="store_true")
a = ap.parse_args()
if a.split != "dev" and not a.allow_test:
    raise SystemExit("pass --allow-test to generate pools for the test split")

client = LLMClient(log_path=RESULTS_DIR / "llm_calls.jsonl")
print(f"{'query':40}{'source':>9}{'proposed':>9}{'valid':>6}  statuses / error")
with get_readonly_conn() as conn:
    for q in select_queries(a.split, a.limit, a.queries.split(",") if a.queries else None):
        pool = generate_pool(conn, client, q["id"], q["dataset"], q["sql"].strip(), provider=a.provider)
        if pool.get("error"):
            print(f"{q['id']:40}{'FAILED':>9}{'':>9}{'':>6}  {pool['error'][:90]}")
            continue
        st = [c["status"] if c["status"] != "invalid" else f"invalid:{c['check']['reason']}" for c in pool["candidates"]]
        print(f"{q['id']:40}{'cached' if pool['cached'] else 'new':>9}{pool['n_proposed']:>9}"
              f"{sum(s == 'valid' for s in st):>6}  {', '.join(st) or '(none proposed)'}")
