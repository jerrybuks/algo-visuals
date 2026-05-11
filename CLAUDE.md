# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Activate virtualenv (required before anything else)
source venv/bin/activate

# Start dev server (auto-reloads on file save)
fastapi dev main.py

# Kill server
kill $(lsof -t -i:8000)

# Install dependencies
pip install -r requirements.txt

# Run tests
pytest
pytest tests/test_sandbox.py          # single file
pytest tests/test_sandbox.py::test_fn # single test
```

## Environment

Copy `.env.example` to `.env` and fill in:
- `OPENROUTER_API_KEY` — from openrouter.ai (format: `sk-or-v1-...`)
- `AI_MODEL` — OpenRouter model ID for narration/Manim generation (default: `anthropic/claude-sonnet-4.6` — use **dot** not dash)
- `VERIFIER_MODEL` — model for narration accuracy checker (default: `openai/gpt-4o`)

All other env vars have sensible defaults. TTS defaults to `edge` (free). Supabase and Cloudinary are optional — pipeline degrades gracefully without them.

## Architecture

FastAPI backend. Each `POST /api/v1/generate` runs a full video generation pipeline and streams status via SSE (`GET /api/v1/status/{job_id}`).

### Request lifecycle (`app/services/video_pipeline.py`)

```
POST /api/v1/generate
  → background task: video_pipeline.run(job_id, prompt, jobs)
      1. _generate_narration()              → sentences + algorithm metadata (Claude)
      2. _verify_with_generator_feedback()  → up to 4 verify attempts; corrections fed back to generator
      3. generate_audio_files()             → TTS per sentence (edge-tts / MiniMax / OpenAI / ElevenLabs)
      4. get_audio_duration()               → per-sentence durations for Manim timing
      5. _generate_manim_code()             → full Manim scene (Claude, 1 retry on render failure)
      6. render_manim()                     → subprocess, produces raw video
      7. mix_audio_video()                  → ffmpeg mux
      8. upload_video()                     → Cloudinary CDN (falls back to local /videos/)
      9. db.save_generation()              → Supabase upsert (best-effort)
```

### Key design decisions

**LLM layer** (`app/services/ai_generator.py`): Uses `openai` SDK pointed at OpenRouter. `_chat(system, user, max_tokens, model=None)` is the shared helper — pass `model=` to override per-call. `_parse_json()` strips fences, `//` comments, and trailing commas before `json.loads` to handle Claude's imperfect JSON output.

**Narration verifier** (`app/services/video_pipeline.py`): `_run_verifier()` calls `VERIFIER_MODEL` (gpt-4o) and returns `(confidence, corrections)`. It **evaluates only** — corrections go back to the generator (`_generate_narration(feedback=...)`) to regenerate. Up to 4 attempts. If confidence never reaches 95%, the job is marked `flagged=True` in the DB and the pipeline continues.

**Manim boundaries**: All generated scenes must keep content within x ∈ [-6.2, 6.2], y ∈ [-1.8, 2.6]. Enforced via prompt rules in `app/prompts/manim_scene.py` and validated in `app/services/manim_renderer.py::_validate_manim_code()`.

**Feedback loops**: Narration generator has 1 feedback pass (2 total attempts). Manim code generator has 1 retry on render crash. Both limits exist to control token spend.

**TTS**: `app/services/manim_renderer.py::generate_audio_files()` dispatches to the provider set in `TTS_PROVIDER`. Failures are silent — pipeline continues with 4s-per-sentence fallback durations.

**Persistence**: Supabase (`app/services/db.py`) stores completed jobs. The `generations` table schema is in the file header. Run this migration if the table already exists:
```sql
ALTER TABLE generations ADD COLUMN IF NOT EXISTS flagged BOOLEAN DEFAULT FALSE;
```

### Deployment

- **Backend**: Railway (Docker via `railway.json`) or Render (`render.yaml`, `env: docker`)
- **Frontend**: Vercel (`algo-visuals-ui/`). Set `VITE_API_URL` env var in Vercel dashboard to the Railway URL.
- Docker is required — Manim needs Cairo, Pango, and FFmpeg system packages.
