from fastapi import FastAPI, HTTPException
from mysql.connector import Error

from app.db import get_readonly_conn

app = FastAPI(title="sql-query-optimizer")


@app.get("/health")
def health() -> dict[str, str]:
    # Goes through the reader user: proves the same path candidates will use.
    try:
        with get_readonly_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
    except Error as e:
        raise HTTPException(status_code=503, detail=f"db unreachable: {e}")
    return {"status": "ok", "db": "connected"}
