"""Print live row counts per table (via the read-only user) vs known sizes.
Exit code 1 on any mismatch."""
import sys
from pathlib import Path

import yaml

from app.db import get_readonly_conn

expected = yaml.safe_load((Path(__file__).resolve().parents[1] / "data" / "expected_counts.yaml").read_text())
bad = 0
print(f"{'schema.table':32}{'actual':>12}{'expected':>12}  status")
with get_readonly_conn() as conn, conn.cursor() as cur:
    for schema, tables in expected.items():
        for table, want in tables.items():
            try:
                cur.execute(f"SELECT COUNT(*) FROM `{schema}`.`{table}`")
                got = cur.fetchone()[0]
            except Exception as e:  # missing table etc.
                got = f"ERR {e.__class__.__name__}"
            ok = got == want
            bad += not ok
            print(f"{schema + '.' + table:32}{got:>12}{want:>12}  {'OK' if ok else 'MISMATCH'}")
print("ALL COUNTS MATCH" if not bad else f"{bad} MISMATCH(ES)")
sys.exit(bool(bad))
