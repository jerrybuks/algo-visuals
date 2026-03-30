from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.services import db

router = APIRouter()
logger = logging.getLogger(__name__)


class GenerationSummary(BaseModel):
    job_id: str
    prompt: str
    status: str
    video_url: str | None = None
    algorithm: dict[str, Any] | None = None
    steps: list[str] = []
    narration: list[str] = []
    is_public: bool = True
    created_at: datetime


def _parse_json_field(value: Any, fallback: Any = None) -> Any:
    """Parse a value that may be a JSON string or already decoded."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return fallback
    return value if value is not None else fallback


def _row_to_summary(row: dict) -> GenerationSummary:
    return GenerationSummary(
        job_id=row["id"],
        prompt=row.get("prompt", ""),
        status=row.get("status", "unknown"),
        video_url=row.get("video_url"),
        algorithm=_parse_json_field(row.get("algorithm"), {}),
        steps=_parse_json_field(row.get("steps"), []),
        narration=_parse_json_field(row.get("narration"), []),
        is_public=row.get("is_public", True),
        created_at=row["created_at"],
    )


@router.get("/history", response_model=list[GenerationSummary])
async def get_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[GenerationSummary]:
    """Return past generations, newest first."""
    try:
        rows = await db.get_generations(limit, offset)
    except Exception:
        logger.exception("Failed to fetch generation history")
        return []
    return [_row_to_summary(r) for r in rows]


@router.get("/history/{job_id}", response_model=GenerationSummary)
async def get_history_item(job_id: str) -> GenerationSummary:
    """Return a single generation by job_id."""
    from fastapi import HTTPException
    try:
        row = await db.get_generation(job_id)
    except Exception:
        logger.exception("Failed to fetch generation %s", job_id)
        raise HTTPException(status_code=500, detail="Failed to fetch generation")
    if row is None:
        raise HTTPException(status_code=404, detail="Generation not found")
    return _row_to_summary(row)
