from fastapi import APIRouter

from app.config import settings

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "version": "1.0.0",
        "ai_available": bool(settings.OPENROUTER_API_KEY),
        "model": settings.AI_MODEL,
        "openrouter_base_url": settings.OPENROUTER_BASE_URL,
        "env": settings.ENV,
    }
