import asyncio
import json
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

from app.config import settings
from app.prompts import narration_direct, manim_scene as manim_prompt
from app.services.ai_generator import _chat, _strip_fences, _parse_json
from app.services.manim_renderer import (
    generate_audio_files,
    get_audio_duration,
    render_manim,
    mix_audio_video,
    _patch_manim_code,
    _validate_manim_code,
)
from app.services.cloud_storage import upload_video
from app.services import db
from app.utils.exceptions import AIGenerationError


async def _generate_narration(description: str) -> tuple[list[str], dict]:
    """Returns (sentences, algorithm_info)."""
    raw = await _chat(
        system=narration_direct.SYSTEM_PROMPT,
        user=narration_direct.build_user_prompt(description),
        max_tokens=4096,
    )
    data = _parse_json(raw)

    if isinstance(data, list):
        # Legacy plain list — no metadata
        return [str(s) for s in data], {}

    sentences = [str(s) for s in data.get("sentences", [])]
    algorithm = data.get("algorithm", {})
    # scratchpad is consumed for correctness reasoning — strip it, don't pass downstream
    return sentences, algorithm


_VERIFIER_SYSTEM = """\
You are a technical accuracy checker for algorithm narrations.
Given an algorithm name and its narration sentences, verify that the narration
correctly and accurately explains that specific algorithm.

Return ONLY valid JSON in this exact shape:
{
  "confidence": <integer 0-100>,
  "corrections": ["<specific correction>", ...]
}

- confidence 100 = narration is fully accurate, no changes needed
- corrections = list of specific fixes (empty list if confidence is 100)
  Each correction must state the sentence number and exactly what to change.
  Only include corrections for factual/technical errors, not style preferences.
"""


async def _verify_and_correct_narration(sentences: list[str], algorithm_name: str) -> list[str]:
    """Verify narration against algorithm name. Return corrected sentences if needed."""
    numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))
    user_prompt = f"Algorithm: {algorithm_name}\n\nNarration:\n{numbered}"

    raw = await _chat(
        system=_VERIFIER_SYSTEM,
        user=user_prompt,
        max_tokens=1024,
        model=settings.VERIFIER_MODEL,
    )

    try:
        result = _parse_json(raw)
    except json.JSONDecodeError:
        logger.warning("Narration verifier returned invalid JSON — skipping correction")
        return sentences

    confidence = int(result.get("confidence", 100))
    corrections = result.get("corrections", [])

    if confidence == 100 or not corrections:
        logger.info("Narration verified at %d%% confidence — no corrections needed", confidence)
        return sentences

    logger.info("Narration verified at %d%% confidence — applying %d correction(s)", confidence, len(corrections))

    corrections_text = "\n".join(f"- {c}" for c in corrections)
    fix_prompt = (
        f"Here are the original narration sentences for the algorithm '{algorithm_name}':\n\n"
        f"{numbered}\n\n"
        f"Apply these corrections:\n{corrections_text}\n\n"
        "Return ONLY a JSON array of the corrected sentences in the same order, e.g.:\n"
        '["sentence 1", "sentence 2", ...]'
    )

    fixed_raw = await _chat(
        system="You are a precise editor. Apply the requested corrections to narration sentences and return a JSON array.",
        user=fix_prompt,
        max_tokens=2048,
        model=settings.VERIFIER_MODEL,
    )

    try:
        fixed = json.loads(_strip_fences(fixed_raw))
        if isinstance(fixed, list) and len(fixed) == len(sentences):
            return [str(s) for s in fixed]
        logger.warning("Corrected narration had wrong length — using original")
        return sentences
    except json.JSONDecodeError:
        logger.warning("Correction response was not valid JSON — using original")
        return sentences


async def _generate_manim_code(description: str, narration_with_durations: list[dict], algorithm_info: dict | None = None, extra_hint: str = "") -> str:
    user_prompt = manim_prompt.build_user_prompt(
        description=description,
        narration_with_durations=narration_with_durations,
        algorithm_info=algorithm_info or {},
    )
    if extra_hint:
        user_prompt += f"\n\n{extra_hint}"
    error = None
    for attempt in range(3):
        raw = await _chat(
            system=manim_prompt.SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=8000,
        )
        code = _patch_manim_code(_strip_fences(raw))
        error = _validate_manim_code(code)
        if error is None:
            return code
        user_prompt += f"\n\nPREVIOUS ATTEMPT FAILED: {error}\nFix it and regenerate the complete script."

    raise AIGenerationError(f"Manim code generation failed after 3 attempts: {error}")


async def run(job_id: str, prompt: str, jobs: dict) -> None:
    """Full pipeline: narration → TTS → Manim code → render → mix audio."""
    job = jobs[job_id]
    job["prompt"] = prompt

    try:
        # 1. Generate narration + algorithm metadata
        job["status"] = "narrating"
        job["message"] = "Writing narration..."
        narration, algorithm_info = await _generate_narration(prompt)
        job["algorithm"] = {k: v for k, v in algorithm_info.items() if k != "steps"}
        job["steps"] = algorithm_info.get("steps", [])

        # 1b. Verify narration against algorithm name — correct if needed
        algorithm_name = algorithm_info.get("name", prompt)
        job["message"] = "Verifying narration accuracy..."
        narration = await _verify_and_correct_narration(narration, algorithm_name)
        job["narration"] = narration

        with tempfile.TemporaryDirectory(prefix="algovis_") as tmpdir:
            tmp = Path(tmpdir)

            # 2. Synthesise audio (best-effort — proceed silently if TTS fails)
            job["status"] = "synthesizing"
            job["message"] = "Generating voiceover..."
            audio_paths = []
            try:
                audio_paths = await generate_audio_files(narration, "", tmp)
                job["has_audio"] = True
            except Exception as tts_err:
                job["has_audio"] = False
                job["message"] = f"Voiceover unavailable ({tts_err.__class__.__name__}) — continuing without audio"

            # 3. Measure durations (fallback: 4s per sentence if no audio)
            if audio_paths:
                narration_with_durations = [
                    {"text": text, "duration": get_audio_duration(path)}
                    for text, path in zip(narration, audio_paths)
                ]
            else:
                narration_with_durations = [
                    {"text": text, "duration": 4.0}
                    for text in narration
                ]

            # 4. Generate Manim scene code + render (retry loop)
            job["status"] = "generating"
            job["message"] = "Generating animation scene..."
            loop = asyncio.get_event_loop()
            render_hint = ""
            raw_video = None
            for render_attempt in range(3):
                scene_code = await _generate_manim_code(prompt, narration_with_durations, algorithm_info=algorithm_info, extra_hint=render_hint)
                # Save generated code for debugging (overwritten each attempt)
                debug_dir = Path("debug_scenes")
                debug_dir.mkdir(exist_ok=True)
                (debug_dir / f"{job_id}.py").write_text(scene_code)
                job["status"] = "rendering"
                job["message"] = f"Rendering video {'(retry) ' if render_attempt else ''}(~30–60s)..."
                try:
                    raw_video = await loop.run_in_executor(None, render_manim, scene_code, tmp)
                    break  # success
                except RuntimeError as e:
                    if render_attempt == 2:
                        raise
                    # Feed the crash back to Claude and regenerate
                    crash = str(e)[-1500:]
                    render_hint = (
                        f"PREVIOUS MANIM RENDER CRASHED with this error:\n{crash}\n"
                        "Fix the root cause (e.g. IndexError means you accessed a list out of bounds — "
                        "check all list accesses with len() guards). Regenerate the complete script."
                    )
                    job["status"] = "generating"
                    job["message"] = f"Render failed, fixing scene code (attempt {render_attempt + 2}/3)..."

            # 6. Mix audio + video
            job["status"] = "mixing"
            job["message"] = "Mixing audio and video..."
            videos_dir = Path(settings.VIDEOS_DIR)
            videos_dir.mkdir(parents=True, exist_ok=True)
            output_path = videos_dir / f"{job_id}.mp4"
            await loop.run_in_executor(None, mix_audio_video, raw_video, audio_paths, output_path)

        job["video_url"] = f"/videos/{job_id}.mp4"

        # Upload to Cloudinary CDN then delete local file
        try:
            cdn_url = upload_video(output_path)
            job["video_url"] = cdn_url
            job["has_cloudinary"] = True
            output_path.unlink(missing_ok=True)
        except Exception:
            job["has_cloudinary"] = False

        job["status"] = "done"
        job["message"] = "Done"

        # Persist to Supabase (best-effort — DB failure must not break the pipeline)
        try:
            asyncio.create_task(db.save_generation(job_id, job))
        except Exception:
            logger.warning("Failed to persist generation %s to Supabase", job_id, exc_info=True)

    except Exception as e:  # noqa: BLE001
        job["status"] = "failed"
        job["error"] = str(e)
        job["message"] = f"Failed: {e}"
