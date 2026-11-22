"""Workload Advisor API. An advisor run takes minutes (real CREATE INDEX + timed runs), so it is a background
job the UI polls. One at a time: concurrent index experiments would corrupt each other's timings (409)."""
import json
import threading
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.advisor.advise import ADV_DIR, CFG, LOCK, advise, dev_workload
from app.api.studio import datasets

router = APIRouter(prefix="/advisor", tags=["advisor"])
JOBS: dict[str, dict] = {}


class AdvisorRequest(BaseModel):
    dataset: str
    query_ids: list[str] | None = None      # default: every dev query of the dataset
    budget_mb: float | None = Field(None, gt=0, le=4096)
    max_indexes: int | None = Field(None, ge=1, le=10)


def _work(job: dict, req: AdvisorRequest) -> None:
    try:
        job["result"] = advise(req.dataset, req.query_ids, req.budget_mb, req.max_indexes, log=job["log"].append)
        job["status"] = "done"
    except Exception as e:  # the advisor has already dropped its indexes; surface the reason
        job["status"], job["error"] = "error", f"{type(e).__name__}: {e}"


@router.post("/run", status_code=202)
def start(req: AdvisorRequest) -> dict:
    if req.dataset not in datasets():
        raise HTTPException(422, f"unknown dataset {req.dataset!r}; one of {sorted(datasets())}")
    try:
        dev_workload(req.dataset, req.query_ids)  # rejects test-split or wrong-dataset queries before any DDL
    except ValueError as e:
        raise HTTPException(422, str(e))
    if LOCK.locked():
        raise HTTPException(409, "an advisor run is already in progress")
    job = {"id": uuid.uuid4().hex[:8], "status": "running", "dataset": req.dataset, "log": [], "result": None, "error": None}
    JOBS[job["id"]] = job
    threading.Thread(target=_work, args=(job, req), daemon=True).start()
    return {"job_id": job["id"]}


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict:
    if job_id not in JOBS:
        raise HTTPException(404, f"unknown job {job_id!r}")
    return JOBS[job_id]


@router.get("/settings")
def settings() -> dict:
    return CFG


@router.get("/runs")
def runs() -> list[dict]:
    """Saved advisor results, newest first."""
    files = sorted(ADV_DIR.glob("adv-*.json"), reverse=True) if ADV_DIR.exists() else []
    return [json.loads(p.read_text(encoding="utf-8")) for p in files]
