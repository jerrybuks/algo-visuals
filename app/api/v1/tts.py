import asyncio
import base64
import tempfile
from pathlib import Path

import edge_tts
import httpx
from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from app.config import settings

router = APIRouter()

# ---------------------------------------------------------------------------
# Edge TTS (free, no API key — Microsoft neural voices)
# ---------------------------------------------------------------------------
# Popular voices: en-US-AriaNeural, en-US-JennyNeural, en-US-GuyNeural,
#   en-US-DavisNeural, en-GB-SoniaNeural, en-AU-NatashaNeural
EDGE_VOICES = {
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
    "en-US-DavisNeural", "en-US-AmberNeural", "en-US-AnaNeural",
    "en-GB-SoniaNeural", "en-GB-RyanNeural", "en-AU-NatashaNeural",
}

_EDGE_SEMAPHORE = asyncio.Semaphore(6)


async def _synthesise_edge(index: int, text: str, voice: str) -> "AudioClip":
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name

    async with _EDGE_SEMAPHORE:
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp_path)

    audio_bytes = Path(tmp_path).read_bytes()
    Path(tmp_path).unlink(missing_ok=True)
    return AudioClip(
        narration_index=index,
        audio_b64=base64.b64encode(audio_bytes).decode("utf-8"),
    )


# ---------------------------------------------------------------------------
# MiniMax TTS
# ---------------------------------------------------------------------------
MINIMAX_VOICES = {
    "Calm_Woman", "Wise_Woman", "Friendly_Person", "Inspirational_girl",
    "Deep_Voice_Man", "Casual_Guy", "Lively_Girl", "Patient_Man",
    "Sweet_Girl_v2", "Steadfast_Man", "Elegant_Man", "Abbigail",
}

_MINIMAX_SEMAPHORE = asyncio.Semaphore(5)


async def _synthesise_minimax(index: int, text: str, voice: str) -> "AudioClip":
    if not settings.MINIMAX_API_KEY:
        raise HTTPException(status_code=503, detail="MINIMAX_API_KEY is not configured")

    payload = {
        "model": settings.MINIMAX_TTS_MODEL,
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": voice, "speed": 1.0, "vol": 1.0, "pitch": 0},
        "audio_setting": {"format": "mp3", "sample_rate": 32000, "bitrate": 128000, "channel": 1},
    }
    headers = {
        "Authorization": f"Bearer {settings.MINIMAX_API_KEY}",
        "Content-Type": "application/json",
    }

    async with _MINIMAX_SEMAPHORE:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.MINIMAX_API_BASE}/t2a_v2",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

    status = data.get("base_resp", {}).get("status_code", -1)
    if status != 0:
        msg = data.get("base_resp", {}).get("status_msg", "unknown error")
        raise HTTPException(status_code=502, detail=f"MiniMax TTS error: {msg}")

    audio_hex: str = data["data"]["audio"]
    audio_bytes = bytes.fromhex(audio_hex)
    return AudioClip(
        narration_index=index,
        audio_b64=base64.b64encode(audio_bytes).decode("utf-8"),
    )


# ---------------------------------------------------------------------------
# OpenAI TTS
# ---------------------------------------------------------------------------
OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}

_openai_client: AsyncOpenAI | None = None
_OPENAI_SEMAPHORE = asyncio.Semaphore(5)


def _get_openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        if not settings.OPENAI_API_KEY:
            raise HTTPException(status_code=503, detail="OPENAI_API_KEY is not configured")
        _openai_client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


async def _synthesise_openai(index: int, text: str, voice: str) -> "AudioClip":
    client = _get_openai_client()
    async with _OPENAI_SEMAPHORE:
        response = await client.audio.speech.create(
            model=settings.OPENAI_TTS_MODEL,
            voice=voice,  # type: ignore[arg-type]
            input=text,
            response_format="mp3",
        )
        audio_bytes = response.content
    return AudioClip(
        narration_index=index,
        audio_b64=base64.b64encode(audio_bytes).decode("utf-8"),
    )


# ---------------------------------------------------------------------------
# ElevenLabs TTS (legacy / optional)
# ---------------------------------------------------------------------------
ELEVENLABS_VOICES: dict[str, str] = {
    "Aria":    "9BWtsMINqrJLrRacOk9x",
    "Matilda": "XrExE9yKIg1WjnnlVkGX",
    "Rachel":  "21m00Tcm4TlvDq8ikWAM",
    "Bella":   "EXAVITQu4vr4xnSDxMaL",
    "Antoni":  "ErXwobaYiN019PkySvjV",
    "Josh":    "TxGEqnHWrfWFTfGW9XjX",
    "Adam":    "pNInz6obpgDQGcFmaJgB",
    "Sam":     "yoZ06aMxZJJ28mfd3POQ",
}

_elevenlabs_client = None
_EL_SEMAPHORE = asyncio.Semaphore(2)


def _get_elevenlabs_client():
    global _elevenlabs_client
    if _elevenlabs_client is None:
        if not settings.ELEVENLABS_API_KEY:
            raise HTTPException(status_code=503, detail="ELEVENLABS_API_KEY is not configured")
        from elevenlabs.client import AsyncElevenLabs
        _elevenlabs_client = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
    return _elevenlabs_client


async def _synthesise_elevenlabs(index: int, text: str, voice_name: str) -> "AudioClip":
    from elevenlabs import VoiceSettings
    client = _get_elevenlabs_client()
    voice_id = ELEVENLABS_VOICES.get(voice_name, ELEVENLABS_VOICES["Rachel"])
    calm_settings = VoiceSettings(
        stability=0.75, similarity_boost=0.6, style=0.0, use_speaker_boost=False,
    )
    async with _EL_SEMAPHORE:
        audio_stream = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=settings.ELEVENLABS_MODEL,
            output_format="mp3_44100_128",
            voice_settings=calm_settings,
        )
        audio_bytes = b"".join([chunk async for chunk in audio_stream])
    return AudioClip(
        narration_index=index,
        audio_b64=base64.b64encode(audio_bytes).decode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Shared schemas + route
# ---------------------------------------------------------------------------

class TTSRequest(BaseModel):
    sentences: list[str] = Field(..., min_length=1, max_length=20)
    voice: str = ""  # provider-specific voice name; empty = use server default


class AudioClip(BaseModel):
    narration_index: int
    audio_b64: str
    format: str = "mp3"


class TTSResponse(BaseModel):
    clips: list[AudioClip]
    voice: str
    model: str


@router.post("/tts", response_model=TTSResponse)
async def generate_tts(request: TTSRequest) -> TTSResponse:
    provider = settings.TTS_PROVIDER.lower()

    if provider == "elevenlabs":
        voice = request.voice if request.voice in ELEVENLABS_VOICES else settings.ELEVENLABS_DEFAULT_VOICE
        clips = await asyncio.gather(*[
            _synthesise_elevenlabs(i, text, voice)
            for i, text in enumerate(request.sentences)
        ])
        return TTSResponse(clips=list(clips), voice=voice, model=settings.ELEVENLABS_MODEL)

    if provider == "openai":
        voice = request.voice if request.voice in OPENAI_VOICES else settings.OPENAI_TTS_VOICE
        clips = await asyncio.gather(*[
            _synthesise_openai(i, text, voice)
            for i, text in enumerate(request.sentences)
        ])
        return TTSResponse(clips=list(clips), voice=voice, model=settings.OPENAI_TTS_MODEL)

    if provider == "minimax":
        voice = request.voice if request.voice in MINIMAX_VOICES else settings.MINIMAX_TTS_VOICE
        clips = await asyncio.gather(*[
            _synthesise_minimax(i, text, voice)
            for i, text in enumerate(request.sentences)
        ])
        return TTSResponse(clips=list(clips), voice=voice, model=settings.MINIMAX_TTS_MODEL)

    # Default: Edge TTS (free)
    voice = request.voice if request.voice in EDGE_VOICES else settings.EDGE_TTS_VOICE
    clips = await asyncio.gather(*[
        _synthesise_edge(i, text, voice)
        for i, text in enumerate(request.sentences)
    ])
    return TTSResponse(clips=list(clips), voice=voice, model="edge-tts")
