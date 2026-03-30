import asyncio
import base64
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import edge_tts
import httpx
from mutagen.mp3 import MP3
from openai import AsyncOpenAI

from app.config import settings
from app.prompts import manim_scene as manim_prompt
from app.services.ai_generator import _chat, _strip_fences
from app.utils.exceptions import AIGenerationError

# ElevenLabs voice name → voice ID
_EL_VOICES: dict[str, str] = {
    "Aria":    "9BWtsMINqrJLrRacOk9x",
    "Matilda": "XrExE9yKIg1WjnnlVkGX",
    "Rachel":  "21m00Tcm4TlvDq8ikWAM",
    "Bella":   "EXAVITQu4vr4xnSDxMaL",
    "Antoni":  "ErXwobaYiN019PkySvjV",
    "Josh":    "TxGEqnHWrfWFTfGW9XjX",
    "Adam":    "pNInz6obpgDQGcFmaJgB",
    "Sam":     "yoZ06aMxZJJ28mfd3POQ",
}

_OPENAI_VOICES = {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
_MINIMAX_VOICES = {
    "Calm_Woman", "Wise_Woman", "Friendly_Person", "Inspirational_girl",
    "Deep_Voice_Man", "Casual_Guy", "Lively_Girl", "Patient_Man",
    "Sweet_Girl_v2", "Steadfast_Man", "Elegant_Man", "Abbigail",
}
_EDGE_VOICES = {
    "en-US-AriaNeural", "en-US-JennyNeural", "en-US-GuyNeural",
    "en-US-DavisNeural", "en-US-AmberNeural", "en-GB-SoniaNeural",
    "en-GB-RyanNeural", "en-AU-NatashaNeural",
}

_OPENAI_SEMAPHORE = asyncio.Semaphore(5)
_EL_SEMAPHORE = asyncio.Semaphore(2)
_MINIMAX_SEMAPHORE = asyncio.Semaphore(5)
_EDGE_SEMAPHORE = asyncio.Semaphore(6)


# ---------------------------------------------------------------------------
# Step 1 — Generate TTS audio files and measure durations
# ---------------------------------------------------------------------------

async def _synthesise_edge(text: str, voice: str, out_path: Path) -> None:
    async with _EDGE_SEMAPHORE:
        for attempt in range(4):
            try:
                communicate = edge_tts.Communicate(text, voice)
                await communicate.save(str(out_path))
                return
            except Exception:
                if attempt == 3:
                    raise
                await asyncio.sleep(2 ** attempt)  # 1s, 2s, 4s backoff


async def _synthesise_minimax(text: str, voice: str, out_path: Path) -> None:
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
        raise RuntimeError(f"MiniMax TTS error: {msg}")

    audio_bytes = bytes.fromhex(data["data"]["audio"])
    out_path.write_bytes(audio_bytes)


async def _synthesise_openai(client: AsyncOpenAI, text: str, voice: str, out_path: Path) -> None:
    async with _OPENAI_SEMAPHORE:
        response = await client.audio.speech.create(
            model=settings.OPENAI_TTS_MODEL,
            voice=voice,  # type: ignore[arg-type]
            input=text,
            response_format="mp3",
        )
        out_path.write_bytes(response.content)


async def _synthesise_elevenlabs(text: str, voice_id: str, out_path: Path) -> None:
    from elevenlabs import VoiceSettings
    from elevenlabs.client import AsyncElevenLabs
    calm = VoiceSettings(stability=0.75, similarity_boost=0.6, style=0.0, use_speaker_boost=False)
    client = AsyncElevenLabs(api_key=settings.ELEVENLABS_API_KEY)
    async with _EL_SEMAPHORE:
        stream = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=settings.ELEVENLABS_MODEL,
            output_format="mp3_44100_128",
            voice_settings=calm,
        )
        audio_bytes = b"".join([chunk async for chunk in stream])
    out_path.write_bytes(audio_bytes)


async def generate_audio_files(
    narration: list[str],
    voice_name: str,
    output_dir: Path,
) -> list[Path]:
    """Synthesise each narration sentence to an MP3 file. Returns ordered list of paths."""
    paths: list[Path] = [output_dir / f"narr_{i:02d}.mp3" for i in range(len(narration))]
    provider = settings.TTS_PROVIDER.lower()

    if provider == "elevenlabs":
        if not settings.ELEVENLABS_API_KEY:
            raise RuntimeError("ELEVENLABS_API_KEY not configured")
        voice_id = _EL_VOICES.get(voice_name, _EL_VOICES["Rachel"])
        await asyncio.gather(*[
            _synthesise_elevenlabs(text, voice_id, path)
            for text, path in zip(narration, paths)
        ])
    elif provider == "openai":
        if not settings.OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not configured")
        voice = voice_name if voice_name in _OPENAI_VOICES else settings.OPENAI_TTS_VOICE
        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        await asyncio.gather(*[
            _synthesise_openai(client, text, voice, path)
            for text, path in zip(narration, paths)
        ])
    elif provider == "minimax":
        if not settings.MINIMAX_API_KEY:
            raise RuntimeError("MINIMAX_API_KEY not configured")
        voice = voice_name if voice_name in _MINIMAX_VOICES else settings.MINIMAX_TTS_VOICE
        await asyncio.gather(*[
            _synthesise_minimax(text, voice, path)
            for text, path in zip(narration, paths)
        ])
    else:
        # Default: Edge TTS (free, no API key)
        voice = voice_name if voice_name in _EDGE_VOICES else settings.EDGE_TTS_VOICE
        await asyncio.gather(*[
            _synthesise_edge(text, voice, path)
            for text, path in zip(narration, paths)
        ])

    return paths


def get_audio_duration(path: Path) -> float:
    """Return duration of an MP3 file in seconds."""
    return MP3(str(path)).info.length


# ---------------------------------------------------------------------------
# Step 2 — Build trace summary for the prompt
# ---------------------------------------------------------------------------

def _build_trace_summary(trace: dict, category: str) -> dict:
    steps = trace.get("steps", [])
    stages: dict[int, list] = {}
    for s in steps:
        stages.setdefault(s.get("stage", 1), []).append(s)

    lines: list[str] = []
    for stage_num in sorted(stages.keys())[:5]:  # cap at 5 stages — enough context, fewer input tokens
        stage_steps = stages[stage_num]
        sample = stage_steps[0]
        desc = sample.get("description", "")
        op = sample.get("operation", "")
        count = len(stage_steps)
        lines.append(f"  Stage {stage_num} ({count} ops): {op} — e.g. \"{desc}\"")

        # One key field per category only
        if category == "array" and stage_steps:
            snap = stage_steps[-1].get("array_snapshot")
            if snap:
                lines.append(f"    array after: {snap}")

    return {
        "total_steps": trace.get("total_steps"),
        "stages": trace.get("stages"),
        "final_output": trace.get("final_output"),
        "steps_summary": "\n".join(lines) if lines else "  (no steps)",
    }


# ---------------------------------------------------------------------------
# Step 3 — Generate Manim scene code via LLM
# ---------------------------------------------------------------------------

def _patch_manim_code(code: str) -> str:
    """Deterministically fix known recurring LLM mistakes in generated Manim code."""

    # 1. Clamp run_time= to at least 0.1 — Manim throws ValueError on run_time <= 0
    code = re.sub(
        r'run_time\s*=\s*(-?\d+\.?\d*)',
        lambda m: f'run_time={max(0.1, float(m.group(1))):.2f}',
        code,
    )

    # 2. Clamp self.wait() to at least 0.01 — negative/zero waits also error
    code = re.sub(
        r'self\.wait\s*\(\s*(-?\d+\.?\d*)\s*\)',
        lambda m: f'self.wait({max(0.01, float(m.group(1))):.3f})',
        code,
    )

    # 3. Replace "Rank N" with "Node N" inside string literals.
    #    Handles f-strings too: f"Rank {i}" → f"Node {i}"
    code = re.sub(r'(?<=["\'`])Rank(?=[ \t\n{])', 'Node', code)

    # 4. Remove buff= from Mobject constructors that don't accept it.
    #    Works line-by-line: if a line contains a bad constructor AND buff=, strip the buff= arg.
    #    Safe: skips lines with .arrange( or .next_to( which legitimately use buff=.
    _BAD_CTOR_NAMES = (
        "VGroup(", "Square(", "Circle(", "Rectangle(",
        "CurvedArrow(", "Arrow(", "Line(", "Dot(",
    )
    _SAFE_BUFF_CONTEXTS = (".arrange(", ".next_to(", "def ", "_make_array")
    patched_lines = []
    for line in code.splitlines():
        if "buff=" in line and not any(ctx in line for ctx in _SAFE_BUFF_CONTEXTS):
            if any(ctor in line for ctor in _BAD_CTOR_NAMES):
                # Remove ", buff=<value>" or "buff=<value>," from the line
                line = re.sub(r',\s*buff\s*=\s*[\d.a-zA-Z_]+', '', line)
                line = re.sub(r'buff\s*=\s*[\d.a-zA-Z_]+,?\s*', '', line)
        patched_lines.append(line)
    code = "\n".join(patched_lines)

    return code


def _validate_manim_code(code: str) -> str | None:
    """Return an error description if the code has obvious problems, else None."""
    # Syntax check
    try:
        compile(code, "<manim_scene>", "exec")
    except SyntaxError as e:
        return f"SyntaxError at line {e.lineno}: {e.msg}"

    # Forbidden patterns that always cause import/runtime failures
    forbidden = [
        ("import numpy", "numpy is not available — use math module instead"),
        ("from numpy", "numpy is not available — use math module instead"),
        ("MathTex(", "MathTex is forbidden — use Text() only"),
        ("Tex(", "Tex is forbidden — use Text() only"),
    ]
    for pattern, reason in forbidden:
        if pattern in code:
            return f"Forbidden usage: {reason}"

    # Must contain the required class
    if "class AlgorithmScene" not in code:
        return "Missing required class AlgorithmScene"

    import re as _re

    # Check for hardcoded coordinates that place content outside the safe zone
    # y < -1.8 (narration zone / off bottom)
    bad_y_low = _re.findall(r'move_to\(\s*\[[-\d. ]+,\s*(-[2-9]\d*\.\d+|-[2-9]\d+)', code)
    if bad_y_low:
        return (
            f"Layout violation: content placed at y={bad_y_low[0]} which is below the content zone. "
            f"Keep all visuals within y ∈ [-1.8, 2.6]."
        )
    # y > 2.6 (above content zone)
    bad_y_high = _re.findall(r'move_to\(\s*\[[-\d. ]+,\s*([3-9]\.\d+|[3-9]\d+\.\d+)', code)
    if bad_y_high:
        return (
            f"Layout violation: content placed at y={bad_y_high[0]} which is above the content zone. "
            f"Keep all visuals within y ∈ [-1.8, 2.6]."
        )
    # x < -6.2 or x > 6.2 (off left/right)
    bad_x = _re.findall(r'move_to\(\s*\[(-[7-9]\.\d+|-[7-9]\d*\.|[7-9]\.\d+|[7-9]\d*\.)\d*,', code)
    if bad_x:
        return (
            f"Layout violation: content placed at x={bad_x[0]}... which is outside the screen. "
            f"Keep all visuals within x ∈ [-6.2, 6.2]."
        )

    # Detect z-order bug: Circle/Square with fill_opacity=1 added to VGroup AFTER a Text —
    # the shape will hide the text. Pattern: VGroup(Text(...), Circle/Square/Rectangle(...))
    zorder_bug = _re.search(
        r'VGroup\s*\(\s*Text\s*\(', code
    )
    if zorder_bug:
        return (
            "Z-order bug: Text is the first item in a VGroup followed by a filled shape — "
            "the shape will render on top and hide the text. "
            "Always add the shape FIRST, then the Text: VGroup(Circle(...), Text(...))"
        )

    # Detect buff= passed to Mobject constructors that don't accept it.
    # These constructors never take buff= — spacing is set via .arrange() or .next_to() only.
    # Use a line-by-line scan to handle multiline calls.
    _BAD_BUFF_CONSTRUCTORS = (
        "VGroup(", "Square(", "Circle(", "Rectangle(", "Text(",
        "CurvedArrow(", "Arrow(", "Line(", "Dot(",
    )
    # Flatten code to single lines for simple scanning (join continuation lines)
    _code_lines = code.splitlines()
    for _lineno, _line in enumerate(_code_lines, 1):
        _stripped = _line.strip()
        if "buff=" in _stripped:
            # Skip legitimate uses: .arrange(), .next_to(), _make_array signature, def lines
            if any(ok in _stripped for ok in (".arrange(", ".next_to(", "def ", "buff=0.", "buff=buff")):
                continue
            for _ctor in _BAD_BUFF_CONSTRUCTORS:
                if _ctor in _stripped:
                    return (
                        f"TypeError at line {_lineno}: '{_ctor.rstrip('(')}' does not accept 'buff=' "
                        f"in its constructor. Use .arrange(RIGHT, buff=X) or .next_to(..., buff=X) "
                        f"for spacing. Remove buff= from the constructor call."
                    )

    return None


async def generate_manim_code(
    response: dict,
    audio_paths: list[Path],
) -> str:
    alg = response.get("algorithm") or {}
    trace = response.get("trace") or {}
    narration: list[str] = response.get("narration", [])
    category = alg.get("category", "array")

    narration_with_durations = [
        {"text": text, "duration": get_audio_duration(path)}
        for text, path in zip(narration, audio_paths)
    ]

    trace_summary = _build_trace_summary(trace, category)

    user_prompt = manim_prompt.build_user_prompt(
        algorithm_name=alg.get("name", "Algorithm"),
        algorithm_category=category,
        execution_model=alg.get("execution_model", "serial"),
        input_data=trace.get("input"),
        narration_with_durations=narration_with_durations,
        trace_summary=trace_summary,
    )

    for attempt in range(2):
        raw = await _chat(
            system=manim_prompt.SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=7000,
        )
        code = _patch_manim_code(_strip_fences(raw))
        error = _validate_manim_code(code)
        if error is None:
            return code
        # Retry with the validation error appended to the prompt
        user_prompt += f"\n\nPREVIOUS ATTEMPT FAILED: {error}\nFix the issue and regenerate the complete script."

    raise AIGenerationError(f"Manim code generation failed after 2 attempts: {error}")


# ---------------------------------------------------------------------------
# Step 4 — Execute Manim
# ---------------------------------------------------------------------------

def render_manim(scene_code: str, output_dir: Path) -> Path:
    scene_file = output_dir / "scene.py"
    scene_file.write_text(scene_code)

    media_dir = output_dir / "media"
    result = subprocess.run(
        [
            "manim", "render",
            str(scene_file), "AlgorithmScene",
            "-qm",                        # 720p — fast + good quality
            "--media_dir", str(media_dir),
            "--disable_caching",
        ],
        capture_output=True,
        text=True,
        cwd=str(output_dir),
        timeout=180,
    )

    if result.returncode != 0:
        # Combine stderr + stdout; extract the last Python traceback line for a clear message
        detail = (result.stderr or "") + (result.stdout or "")
        # Find the root cause line (last "Error:" line in the traceback)
        lines = detail.splitlines()
        error_lines = [l for l in lines if "Error" in l or "error" in l]
        root_cause = error_lines[-1].strip() if error_lines else "unknown error"
        raise RuntimeError(f"Manim render failed ({root_cause}):\n{detail[-3000:]}")

    # Find the rendered MP4 (Manim nests it under media/videos/...)
    mp4_files = list(media_dir.rglob("*.mp4"))
    if not mp4_files:
        raise RuntimeError("Manim render produced no MP4 file")

    return mp4_files[0]


# ---------------------------------------------------------------------------
# Step 5 — Concatenate audio + mix with video via ffmpeg
# ---------------------------------------------------------------------------

def mix_audio_video(video_path: Path, audio_paths: list[Path], output_path: Path) -> None:
    if not audio_paths:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(video_path), "-c", "copy", str(output_path)],
            check=True, capture_output=True, timeout=60,
        )
        return

    # Total audio duration — used as the authoritative length of the final video
    total_audio_duration = sum(get_audio_duration(p) for p in audio_paths)

    # Concatenate audio files into one
    concat_file = video_path.parent / "concat.txt"
    concat_file.write_text("\n".join(f"file '{p.resolve()}'" for p in audio_paths))

    concat_audio = video_path.parent / "narration.mp3"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-c", "copy",
            str(concat_audio),
        ],
        check=True, capture_output=True, timeout=60,
    )

    # Mix video + audio.
    # -t total_audio_duration: output is exactly as long as the narration audio.
    # tpad filter freezes the last video frame if the video ends before the audio does,
    # so the final narration sentence is never cut off.
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(concat_audio),
            "-filter_complex", f"[0:v]tpad=stop_mode=clone:stop_duration=30[v]",
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-t", str(total_audio_duration),
            str(output_path),
        ],
        check=True, capture_output=True, timeout=120,
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

async def render_video(
    stored_response: dict,
    voice: str = "",
    videos_dir: Path | None = None,
) -> Path:
    """Full pipeline: TTS → Manim code → render → mix audio. Returns final MP4 path."""
    if videos_dir is None:
        videos_dir = Path(settings.VIDEOS_DIR)
    videos_dir.mkdir(parents=True, exist_ok=True)

    narration: list[str] = stored_response.get("narration", [])

    with tempfile.TemporaryDirectory(prefix="algovis_render_") as tmpdir:
        tmp = Path(tmpdir)

        # 1. Generate audio files
        audio_paths: list[Path] = []
        if narration:
            audio_paths = await generate_audio_files(narration, voice, tmp)

        # 2. Generate Manim code
        scene_code = await generate_manim_code(stored_response, audio_paths)

        # 3. Render with Manim
        raw_video = render_manim(scene_code, tmp)

        # 4. Mix audio + video
        job_id = stored_response.get("request_id", f"render_{int(time.time())}")
        output_path = videos_dir / f"{job_id}.mp4"
        mix_audio_video(raw_video, audio_paths, output_path)

    return output_path
