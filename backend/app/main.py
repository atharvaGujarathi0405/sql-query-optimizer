from fastapi import FastAPI, HTTPException
from mysql.connector import Error

from app.api.advisor import router as advisor_router
from app.api.overview import router as overview_router
from app.api.results import router as results_router
from app.api.studio import router as studio_router
from app.db import get_readonly_conn

app = FastAPI(title="sql-query-optimizer")
app.include_router(overview_router)
app.include_router(advisor_router)
app.include_router(studio_router)
app.include_router(results_router)


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
