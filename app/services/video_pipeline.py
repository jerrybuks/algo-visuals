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

- confidence 95–100 = acceptable accuracy, no changes needed
- corrections = list of specific fixes (empty if confidence >= 95)
  Each correction must state the sentence number and exactly what to change.
  Only include corrections for factual/technical errors, not style preferences.
"""

_CONFIDENCE_THRESHOLD = 95
_MAX_VERIFY_ATTEMPTS = 4


async def _verify_and_correct_narration(sentences: list[str], algorithm_name: str) -> tuple[list[str], bool]:
    """
    Iteratively verify and correct narration up to _MAX_VERIFY_ATTEMPTS times.
    Returns (sentences, flagged) where flagged=True means confidence never reached threshold.
    """
    current = sentences

    for attempt in range(_MAX_VERIFY_ATTEMPTS):
        numbered = "\n".join(f"{i+1}. {s}" for i, s in enumerate(current))

        try:
            raw = await _chat(
                system=_VERIFIER_SYSTEM,
                user=f"Algorithm: {algorithm_name}\n\nNarration:\n{numbered}",
                max_tokens=1024,
                model=settings.VERIFIER_MODEL,
            )
            result = _parse_json(raw)
        except Exception as e:
            logger.warning("Verifier attempt %d failed (%s) — skipping", attempt + 1, e)
            return current, False

        confidence = int(result.get("confidence", 100))
        corrections = result.get("corrections", [])

        logger.info("Verifier attempt %d/%d — confidence=%d%%", attempt + 1, _MAX_VERIFY_ATTEMPTS, confidence)

        if confidence >= _CONFIDENCE_THRESHOLD or not corrections:
            logger.info("Narration accepted at %d%% confidence after %d attempt(s)", confidence, attempt + 1)
            return current, False

        # Not yet acceptable — apply corrections if more attempts remain
        if attempt < _MAX_VERIFY_ATTEMPTS - 1:
            corrections_text = "\n".join(f"- {c}" for c in corrections)
            fix_prompt = (
                f"Narration for algorithm '{algorithm_name}':\n\n{numbered}\n\n"
                f"Apply these corrections:\n{corrections_text}\n\n"
                "Return ONLY a JSON array of the corrected sentences in the same order."
            )
            try:
                fixed_raw = await _chat(
                    system="You are a precise editor. Apply the requested corrections and return a JSON array of sentences.",
                    user=fix_prompt,
                    max_tokens=2048,
                    model=settings.VERIFIER_MODEL,
                )
                fixed = _parse_json(fixed_raw)
                if isinstance(fixed, list) and len(fixed) == len(current):
                    current = [str(s) for s in fixed]
                    logger.info("Corrections applied — re-verifying")
                else:
                    logger.warning("Corrected list length mismatch — keeping current")
            except Exception as e:
                logger.warning("Correction step failed (%s) — keeping current", e)

    # Exhausted all attempts without reaching threshold
    logger.warning("Narration flagged — confidence below %d%% after %d attempts", _CONFIDENCE_THRESHOLD, _MAX_VERIFY_ATTEMPTS)
    return current, True


async def _generate_manim_code(description: str, narration_with_durations: list[dict], algorithm_info: dict | None = None, extra_hint: str = "") -> str:
    user_prompt = manim_prompt.build_user_prompt(
        description=description,
        narration_with_durations=narration_with_durations,
        algorithm_info=algorithm_info or {},
    )
    if extra_hint:
        user_prompt += f"\n\n{extra_hint}"
    error = None
    for attempt in range(2):
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

    raise AIGenerationError(f"Manim code generation failed after 2 attempts: {error}")


async def run(job_id: str, prompt: str, jobs: dict) -> None:
    """Full pipeline: narration → TTS → Manim code → render → mix audio."""
    job = jobs[job_id]
    job["prompt"] = prompt

    try:
        # 1. Generate narration + algorithm metadata
        job["status"] = "narrating"
        job["message"] = "Writing narration..."
        logger.info("[%s] Starting narration generation", job_id)
        narration, algorithm_info = await _generate_narration(prompt)
        logger.info("[%s] Narration done — %d sentences, algo=%s", job_id, len(narration), algorithm_info.get("name"))
        job["algorithm"] = {k: v for k, v in algorithm_info.items() if k != "steps"}
        job["steps"] = algorithm_info.get("steps", [])

        # 1b. Verify narration against algorithm name — correct if needed
        algorithm_name = algorithm_info.get("name", prompt)
        job["message"] = "Verifying narration accuracy..."
        logger.info("[%s] Starting narration verification", job_id)
        try:
            narration, flagged = await _verify_and_correct_narration(narration, algorithm_name)
        except Exception as verify_err:
            logger.warning("[%s] Verifier failed (%s) — using original narration", job_id, verify_err)
            flagged = False
        logger.info("[%s] Verification done — flagged=%s", job_id, flagged)
        job["narration"] = narration
        job["flagged"] = flagged

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
            for render_attempt in range(2):
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
                    job["message"] = f"Render failed, fixing scene code (retry)..."

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
