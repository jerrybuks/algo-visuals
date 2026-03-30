import logging
from contextlib import asynccontextmanager
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.database import init_db
from app.api.v1 import generate, health, history


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    Path(settings.VIDEOS_DIR).mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="AlgoVisuals",
    description="Describe an algorithm — get a video explaining it.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api/v1", tags=["health"])
app.include_router(generate.router, prefix="/api/v1", tags=["generate"])
app.include_router(history.router, prefix="/api/v1", tags=["history"])

# Serve rendered videos as static files
Path(settings.VIDEOS_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/videos", StaticFiles(directory=settings.VIDEOS_DIR), name="videos")
