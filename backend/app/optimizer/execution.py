"""Run a query as the read-only user with a per-query timeout.

Every arm and every candidate goes through here, so execution is identical.
The timeout is MySQL's own max_execution_time (SELECT only), set per session.
"""
import re

from mysql.connector import Error

ER_QUERY_TIMEOUT = 3024


class QueryTimeout(Exception):
    pass


class QueryFailed(Exception):
    pass


def open_session(conn, dataset: str, timeout_s: int):
    """Cursor on `dataset` with the timeout applied. `conn` must be the reader connection."""
    if not re.fullmatch(r"\w+", dataset):
        raise ValueError(f"bad dataset name {dataset!r}")
    cur = conn.cursor()
    cur.execute(f"SET SESSION max_execution_time = {int(timeout_s) * 1000}")
    cur.execute(f"USE `{dataset}`")
    return cur


def run(cur, sql: str) -> tuple[list[tuple], int]:
    """-> (rows, column count). Raises QueryTimeout or QueryFailed."""
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        return rows, len(cur.description)
    except Error as e:
        if e.errno == ER_QUERY_TIMEOUT:
            raise QueryTimeout(str(e)) from e
        raise QueryFailed(str(e)) from e
