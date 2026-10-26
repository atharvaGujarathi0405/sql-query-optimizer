# Raw mysql-connector connections, not SQLAlchemy engines: the optimizer runs
# hand-written SQL, EXPLAIN / EXPLAIN ANALYZE and index DDL, and needs exact
# control over what hits the server. A pool or ORM adds hidden state (reused
# sessions, warm caches) that could skew timing between arms, and SQLAlchemy
# isn't in the fixed stack. Callers own the connection:
#     with get_readonly_conn() as conn: ...
import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from app.config import ADMIN_DB, READONLY_DB, DBConfig


def _connect(cfg: DBConfig) -> MySQLConnectionAbstract:
    return mysql.connector.connect(
        host=cfg.host,
        port=cfg.port,
        database=cfg.database,
        user=cfg.user,
        password=cfg.password,
    )


def get_admin_conn() -> MySQLConnectionAbstract:
    """Seeding data and index experiments only."""
    return _connect(ADMIN_DB)


def get_readonly_conn() -> MySQLConnectionAbstract:
    """Candidate query execution only."""
    return _connect(READONLY_DB)
