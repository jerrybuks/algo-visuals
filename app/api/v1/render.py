import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models.generation import Generation
from app.services.manim_renderer import render_video

router = APIRouter()


# ---------------------------------------------------------------------------
# In-memory job store (good enough for single-server dev/demo use)
# ---------------------------------------------------------------------------

@dataclass
class RenderJob:
    job_id: str
    request_id: str
    status: Literal["pending", "running", "done", "failed"] = "pending"
    video_url: str | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)


_jobs: dict[str, RenderJob] = {}


def _prune_old_jobs() -> None:
    """Remove jobs older than 2 hours to prevent unbounded memory growth."""
    cutoff = time.time() - 7200
    stale = [jid for jid, job in _jobs.items() if job.created_at < cutoff]
    for jid in stale:
        del _jobs[jid]


# ---------------------------------------------------------------------------
# Background task
# ---------------------------------------------------------------------------

async def _run_render(job: RenderJob, voice: str) -> None:
    job.status = "running"
    try:
        # Load stored response from DB
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Generation).where(Generation.id == job.request_id)
            )
            row = result.scalar_one_or_none()

        if row is None:
            raise ValueError(f"No stored result found for request_id={job.request_id}")

        stored_response = json.loads(row.result_json)

        # Full render pipeline
        video_path = await render_video(
            stored_response=stored_response,
            voice=voice,
            videos_dir=Path(settings.VIDEOS_DIR),
        )

        job.video_url = f"/videos/{video_path.name}"
        job.status = "done"

    except Exception as e:  # noqa: BLE001
        job.status = "failed"
        job.error = str(e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class RenderRequest(BaseModel):
    request_id: str
    voice: str = ""


class RenderJobResponse(BaseModel):
    job_id: str
    status: Literal["pending", "running", "done", "failed"]
    video_url: str | None = None
    error: str | None = None


@router.post("/render", response_model=RenderJobResponse)
async def start_render(request: RenderRequest, background_tasks: BackgroundTasks) -> RenderJobResponse:
    _prune_old_jobs()

    job = RenderJob(job_id=str(uuid.uuid4()), request_id=request.request_id)
    _jobs[job.job_id] = job

    background_tasks.add_task(_run_render, job, request.voice)

    return RenderJobResponse(job_id=job.job_id, status=job.status)


@router.get("/render/{job_id}", response_model=RenderJobResponse)
async def get_render_status(job_id: str) -> RenderJobResponse:
    job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Render job not found")
    return RenderJobResponse(
        job_id=job.job_id,
        status=job.status,
        video_url=job.video_url,
        error=job.error,
    )
