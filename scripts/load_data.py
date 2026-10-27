"""Load Employees, Sakila, TPC-H into MySQL as the admin user (seeding only).

Runs the mysql client inside the container; files come from the read-only
/seed mount (= data/raw). Ends with ANALYZE TABLE so optimizer statistics are
fresh and identical for every arm.
Usage: python scripts/load_data.py [employees] [sakila] [tpch]   (default: all)
"""
import os
import subprocess
import sys
from pathlib import Path

from app.config import ADMIN_DB  # importing app.config also loads .env

TPCH_TABLES = ["region", "nation", "part", "supplier", "partsupp", "customer", "orders", "lineitem"]
ROOT = Path(__file__).resolve().parent


def mysql(sql: str = "", cwd: str = "/seed", stdin: str | None = None) -> None:
    # Password goes via the client environment (`-e MYSQL_PWD` with no value), never argv: a failing
    # subprocess prints its argv in the traceback.
    cmd = ["docker", "compose", "exec", "-T", "-w", cwd, "-e", "MYSQL_PWD",
           "mysql", "mysql", f"-u{ADMIN_DB.user}", "--local-infile=1"]
    if sql:
        cmd += ["-e", sql]
    env = {**os.environ, "MYSQL_PWD": ADMIN_DB.password}
    subprocess.run(cmd, input=stdin, text=True, check=True, cwd=ROOT.parent, env=env)


def root_sql(sql: str) -> str:
    """local_infile is GLOBAL-scope and needs privileges admin lacks, so root flips it. Root does nothing else."""
    cmd = ["docker", "compose", "exec", "-T", "-e", "MYSQL_PWD", "mysql", "mysql", "-uroot", "-N", "-e", sql]
    env = {**os.environ, "MYSQL_PWD": os.environ["MYSQL_ROOT_PASSWORD"]}
    return subprocess.run(cmd, text=True, check=True, capture_output=True, cwd=ROOT.parent, env=env).stdout.strip()


def analyze(schema: str, tables: list[str]) -> None:
    mysql(f"USE {schema}; " + " ".join(f"ANALYZE TABLE {t};" for t in tables))


def employees() -> None:
    # The dump has `flush logs;`, which needs the global RELOAD privilege (admin is schema-scoped, and
    # the binlog is off anyway), so that one statement is dropped in transit; the file on disk is untouched.
    # cwd matters: the dump `source`s its .dump files by relative path.
    dump = (Path(ROOT.parent) / "data/raw/test_db/employees.sql").read_text()
    kept = [ln for ln in dump.splitlines() if not ln.strip().lower().startswith("flush ")]
    assert len(kept) == len(dump.splitlines()) - 1, "expected to drop exactly one FLUSH statement"
    mysql(cwd="/seed/test_db", stdin="\n".join(kept) + "\n")
    analyze("employees", ["employees", "departments", "dept_emp", "dept_manager", "titles", "salaries"])


def sakila() -> None:
    # `source` is a client command that takes the rest of the line, so one per line, not `a; source b`.
    mysql(stdin="source sakila-db/sakila-schema.sql\nsource sakila-db/sakila-data.sql\n")
    analyze("sakila", ["actor", "address", "category", "city", "country", "customer", "film", "film_actor",
                       "film_category", "film_text", "inventory", "language", "payment", "rental", "staff", "store"])


def tpch() -> None:
    mysql(stdin=(ROOT / "tpch_schema.sql").read_text())
    loads = "".join(
        f"LOAD DATA LOCAL INFILE '/seed/tpch/{t}.tbl' INTO TABLE {t} "
        f"FIELDS TERMINATED BY '|' LINES TERMINATED BY '|\\n';\n"
        for t in TPCH_TABLES
    )
    # Parent-first order already; FK/unique checks off skips per-row lookups on the 6M-row load.
    # local_infile is on only for this window (server-wide while on; reader has no INSERT/FILE so cannot use it).
    root_sql("SET GLOBAL local_infile=1")
    try:
        mysql(stdin="USE tpch; SET foreign_key_checks=0; SET unique_checks=0;\n" + loads)
    finally:
        root_sql("SET GLOBAL local_infile=0")
        print("live @@GLOBAL.local_infile after load:", root_sql("SELECT @@GLOBAL.local_infile"))
    analyze("tpch", TPCH_TABLES)


if __name__ == "__main__":
    for name in sys.argv[1:] or ["employees", "sakila", "tpch"]:
        print(f"=== loading {name}", flush=True)
        globals()[name]()
