# Integration test: needs the MySQL container. Proves the read-only boundary
# from CLAUDE.md: candidates run as `reader`, which must not be able to write.
import pytest
from mysql.connector import Error

from app.config import ADMIN_DB
from app.db import get_admin_conn, get_readonly_conn

T = "_priv_smoke"


@pytest.fixture
def table():
    with get_admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"CREATE TABLE {T} (id INT)")
        conn.commit()
    yield
    with get_admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {T}")


def test_reader_cannot_write_admin_can(table):
    with get_readonly_conn() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {T}")  # SELECT is allowed
        assert cur.fetchone() == (0,)
        with pytest.raises(Error) as exc:
            cur.execute(f"INSERT INTO {T} VALUES (1)")
        assert exc.value.errno == 1142  # ER_TABLEACCESS_DENIED_ERROR
    with get_admin_conn() as conn, conn.cursor() as cur:
        cur.execute(f"INSERT INTO {T} VALUES (1)")
        conn.commit()
        cur.execute(f"SELECT COUNT(*) FROM {T}")
        assert cur.fetchone() == (1,)
