import time
import uuid
from typing import Any, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.services import video_pipeline

router = APIRouter()

_jobs: dict[str, dict] = {}


def _prune_old_jobs() -> None:
    cutoff = time.time() - 7200
    stale = [jid for jid, j in _jobs.items() if j["created_at"] < cutoff]
    for jid in stale:
        del _jobs[jid]


class GenerateRequest(BaseModel):
    prompt: str


class JobStatus(BaseModel):
    job_id: str
    status: Literal["pending", "narrating", "synthesizing", "generating", "rendering", "mixing", "done", "failed"]
    message: str = ""
    video_url: str | None = None
    error: str | None = None
    algorithm: dict[str, Any] | None = None
    steps: list[str] = []
    narration: list[str] = []
    has_audio: bool = True


def _job_to_status(job: dict) -> JobStatus:
    return JobStatus(
        job_id=job["job_id"],
        status=job["status"],
        message=job.get("message", ""),
        video_url=job.get("video_url"),
        error=job.get("error"),
        algorithm=job.get("algorithm"),
        steps=job.get("steps", []),
        narration=job.get("narration", []),
        has_audio=job.get("has_audio", True),
    )


@router.post("/generate", response_model=JobStatus, status_code=202)
async def start_generate(request: GenerateRequest, background_tasks: BackgroundTasks) -> JobStatus:
    _prune_old_jobs()

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "message": "Queued...",
        "video_url": None,
        "error": None,
        "algorithm": None,
        "steps": [],
        "narration": [],
        "created_at": time.time(),
    }

    background_tasks.add_task(video_pipeline.run, job_id, request.prompt, _jobs)
    return _job_to_status(_jobs[job_id])


@router.get("/status/{job_id}", response_model=JobStatus)
async def get_status(job_id: str) -> JobStatus:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_status(job)
