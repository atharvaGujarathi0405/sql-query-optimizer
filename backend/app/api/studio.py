"""POST /studio/optimize: candidates, validity flags, metrics, TOPSIS ranking, winner."""
import hashlib
import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException
from mysql.connector import Error
from pydantic import BaseModel

from app.db import get_readonly_conn
from app.llm.client import LLMClient
from app.optimizer.candidates import POOL_DIR
from app.optimizer.checker import read_only_select_problem
from app.optimizer.studio import optimize

router = APIRouter(prefix="/studio", tags=["studio"])
REGISTRY_PATH = Path(__file__).resolve().parents[3] / "data" / "queries.yaml"


class OptimizeRequest(BaseModel):
    query_id: str | None = None   # a registry id (dataset + SQL come from data/queries.yaml) ...
    dataset: str | None = None    # ... or an ad-hoc query: dataset + sql
    sql: str | None = None
    provider: Literal["ollama", "groq"] | None = None  # pin one provider (no fallback)
    force_pool: bool = False      # regenerate the cached pool (development only)


@lru_cache
def registry() -> dict:
    return {q["id"]: q for q in yaml.safe_load(REGISTRY_PATH.read_text())}


@lru_cache
def get_llm_client() -> LLMClient:
    return LLMClient(log_path=POOL_DIR.parent / "results" / "llm_calls.jsonl")


def get_pool_dir() -> Path:
    return POOL_DIR


def datasets() -> set[str]:
    return {d.strip() for d in os.environ.get("DATASET_SCHEMAS", "employees,sakila,tpch").split(",") if d.strip()}


@router.post("/optimize")
def optimize_endpoint(req: OptimizeRequest, client: LLMClient = Depends(get_llm_client),
                      pool_dir: Path = Depends(get_pool_dir)) -> dict:
    if req.query_id and req.query_id in registry():
        q = registry()[req.query_id]
        query_id, dataset, sql = q["id"], q["dataset"], q["sql"]
    elif req.query_id:
        raise HTTPException(404, f"unknown query_id {req.query_id!r}")
    elif req.sql and req.dataset:
        dataset, sql = req.dataset, req.sql.strip().rstrip(";")
        query_id = "adhoc-" + hashlib.sha256(f"{dataset}\n{sql}".encode()).hexdigest()[:12]
        pool_dir = pool_dir / "adhoc"  # ad-hoc pools never mix with the registry's
    else:
        raise HTTPException(422, "give a query_id, or both dataset and sql")
    if dataset not in datasets():
        raise HTTPException(422, f"unknown dataset {dataset!r}; one of {sorted(datasets())}")
    if (problem := read_only_select_problem(sql)):
        raise HTTPException(422, f"not an optimizable query: {problem}")
    try:
        with get_readonly_conn() as conn:
            return optimize(conn, client, dataset, sql, query_id, provider=req.provider,
                            pool_dir=pool_dir, force_pool=req.force_pool)
    except Error as e:
        raise HTTPException(503, f"database error: {e}")
