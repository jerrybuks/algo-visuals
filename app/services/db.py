# Supabase persistence layer for generation jobs.
#
# The "generations" table must be created manually in the Supabase dashboard
# (SQL Editor → New Query → Run):
#
# CREATE TABLE IF NOT EXISTS generations (
#     id UUID PRIMARY KEY,
#     prompt TEXT NOT NULL,
#     status TEXT NOT NULL,
#     video_url TEXT,
#     narration JSONB DEFAULT '[]',
#     algorithm JSONB,
#     steps JSONB DEFAULT '[]',
#     flagged BOOLEAN DEFAULT FALSE,
#     is_public BOOLEAN DEFAULT TRUE,
#     created_at TIMESTAMPTZ DEFAULT NOW()
# );
#
# If table already exists, add the column:
# ALTER TABLE generations ADD COLUMN IF NOT EXISTS flagged BOOLEAN DEFAULT FALSE;

import json
import logging
from datetime import datetime, timezone

from supabase import create_client, Client

from app.config import settings

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    """Return a configured Supabase client (singleton)."""
    global _client
    if _client is None:
        _client = create_client(settings.SUPABASE_URL, settings.SUPABASE_SECRET_KEY)
    return _client


async def save_generation(job_id: str, job: dict) -> None:
    """Upsert a generation job into the 'generations' table."""
    client = get_client()
    row = {
        "id": job_id,
        "prompt": job.get("prompt", ""),
        "status": job.get("status", "unknown"),
        "video_url": job.get("video_url"),
        "narration": json.dumps(job.get("narration", [])),
        "algorithm": json.dumps(job.get("algorithm", {})),
        "steps": json.dumps(job.get("steps", [])),
        "flagged": bool(job.get("flagged", False)),
        "is_public": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    client.table("generations").upsert(row).execute()


async def get_generations(limit: int = 20, offset: int = 0) -> list[dict]:
    """Fetch recent generations ordered by created_at DESC."""
    client = get_client()
    response = (
        client.table("generations")
        .select("*")
        .order("created_at", desc=True)
        .range(offset, offset + limit - 1)
        .execute()
    )
    return response.data


async def get_generation(job_id: str) -> dict | None:
    """Fetch a single generation by id."""
    client = get_client()
    response = (
        client.table("generations")
        .select("*")
        .eq("id", job_id)
        .maybe_single()
        .execute()
    )
    return response.data
