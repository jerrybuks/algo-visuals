# Architecture — Algo LightsOn Backend

> Single source of truth for how the system works. Read this before touching the code.

---

## What This System Does

A user submits a natural language prompt like _"MPI Reduce where 6 nodes each hold different arrays and the final sum lands on node 0"_ and the backend:

1. Generates narration sentences + algorithm metadata using an LLM (with scratchpad chain-of-thought for correctness)
2. Synthesises each sentence into an MP3 voiceover via TTS
3. Generates a Manim animation script (Python) timed to the audio durations
4. Renders the Manim script into an MP4 video
5. Mixes the audio and video with FFmpeg
6. Uploads the final MP4 to Cloudinary CDN and deletes the local file
7. Persists the job to Supabase
8. Returns a shareable video URL

A **separate frontend** (`algo-visuals-ui`) polls job status and displays results.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI (Python 3.11) |
| ASGI server | Uvicorn |
| LLM | OpenRouter API (OpenAI-compatible) — default `openai/gpt-4.5` |
| Animation | Manim Community Edition |
| TTS | Edge TTS (free, default) / MiniMax / OpenAI / ElevenLabs |
| Video mixing | FFmpeg |
| CDN | Cloudinary |
| Persistence | Supabase (primary) + SQLite (local fallback) |
| Deployment | Render (Python environment + `build.sh`) |

---

## Project Structure

```
algo-visuals/
├── main.py                   FastAPI app, CORS, lifespan, router mounting
├── build.sh                  Render build script — installs system deps + pip
├── render.yaml               Render deployment config
├── .env.example              Template for secrets
├── requirements.txt
│
└── app/
    ├── config.py             Typed settings from .env (pydantic-settings)
    ├── database.py           SQLAlchemy async engine + init_db()
    │
    ├── api/v1/
    │   ├── generate.py       POST /api/v1/generate  — starts job, returns job_id
    │   ├── generate.py       GET  /api/v1/status/:id — polls job progress
    │   ├── history.py        GET  /api/v1/history    — list past generations
    │   ├── history.py        GET  /api/v1/history/:id — single past generation
    │   └── health.py         GET  /api/v1/health
    │
    ├── prompts/
    │   ├── narration_direct.py   Narration + algorithm metadata prompt (with scratchpad)
    │   └── manim_scene.py        Manim code generation prompt + user prompt builder
    │
    └── services/
        ├── video_pipeline.py     Main orchestrator — runs all stages in order
        ├── ai_generator.py       LLM call helper (_chat)
        ├── manim_renderer.py     TTS, Manim rendering, FFmpeg mixing, code patching/validation
        ├── cloud_storage.py      Cloudinary upload
        └── db.py                 Supabase persistence
```

---

## API Endpoints

### `POST /api/v1/generate`
Starts a generation job. Returns immediately with a `job_id`. The pipeline runs as a background task.

**Request:** `{ "prompt": "..." }`
**Response:** `{ "job_id": "uuid", "status": "pending", ... }`

### `GET /api/v1/status/{job_id}`
Poll this every 3 seconds to get pipeline progress.

**Response fields:**
- `status` — `pending | narrating | synthesizing | generating | rendering | mixing | done | failed`
- `message` — human-readable current step description
- `algorithm` — `{ name, execution_model, visual_type, complexity }` (populated after narration)
- `steps` — high-level algorithm phase labels
- `narration` — narration sentences
- `video_url` — Cloudinary URL (only present when `status: "done"`)

### `GET /api/v1/history`
Returns past public generations (`is_public: true`). Query params: `limit`, `offset`.

### `GET /api/v1/history/{job_id}`
Returns full detail for a single past generation including narration sentences.

### `GET /api/v1/health`
Returns `{ status, version, ai_available, model }`.

---

## The Pipeline — Step by Step

`app/services/video_pipeline.py` orchestrates everything. One `POST /generate` runs:

```
User prompt
    │
    ▼
[1] Generate narration + algorithm metadata          (LLM call)
    - Scratchpad: model works through the algorithm step-by-step before writing
    - Returns: sentences[], algorithm{ name, execution_model, visual_type, steps, complexity }
    - visual_type picks the Manim style guide (one of 8 types — see below)
    │
    ▼
[2] Synthesise TTS audio                             (Edge TTS / MiniMax / OpenAI / ElevenLabs)
    - One MP3 per narration sentence, generated in parallel
    - Best-effort: if TTS fails, pipeline continues without audio (silent video)
    │
    ▼
[3] Measure audio durations                          (mutagen)
    - Each sentence gets an exact duration in seconds
    - Used to time the Manim animation sections
    │
    ▼
[4] Generate Manim scene code                        (LLM call, up to 3 attempts)
    - System prompt: screen layout, z-order rules, helper methods, banned patterns,
      8 visual type style guides, opening frame rule, highlighting rule
    - User prompt: description + narration with durations + visual_type hint
    - After generation: _patch_manim_code() applies deterministic fixes
      (Rank→Node in strings, remove buff= from constructors, clamp run_time/wait)
    - _validate_manim_code() checks syntax, z-order, off-screen content, banned patterns
    - On failure: error fed back to LLM for retry (up to 3 attempts total)
    - Generated code saved to debug_scenes/<job_id>.py for debugging
    │
    ▼
[5] Render Manim → MP4                               (subprocess: manim render)
    - 720p quality (-qm flag)
    - 180s timeout
    - On crash: error fed back to LLM, regenerates up to 3 times total (shared with step 4)
    │
    ▼
[6] Mix audio + video                                (FFmpeg)
    - Concatenates all MP3s into one narration track
    - tpad filter freezes last frame if video ends before audio
    - Final length = total audio duration
    │
    ▼
[7] Upload to Cloudinary, delete local file
    │
    ▼
[8] Persist to Supabase
```

---

## Visual Types

The narration LLM picks one `visual_type` per algorithm. The Manim code generation prompt contains a style guide for each type:

| visual_type | Used for | Key behaviour |
|---|---|---|
| `array_sequential` | Sorting, searching | Single horizontal array, highlight active cells with GOLD |
| `array_parallel_rounds` | Parallel prefix scan, parallel reduction | All ops in a round fire in ONE `self.play()` |
| `node_communication` | MPI Reduce, broadcast, scatter | Node boxes grow upward in a tree; each round spawns a new row above |
| `recursive_split_merge` | Merge sort, quicksort | Max 2 rows on screen; FadeOut parent before showing children |
| `tree_traversal` | BST, DFS, BFS | Circles for nodes, Lines for edges; GOLD = active, GREEN_D = visited |
| `graph_traversal` | Dijkstra, BFS on general graphs | 5–6 nodes, Lines for edges, active edge GOLD stroke_width=4 |
| `matrix_fill` | DP, Floyd-Warshall | 2D grid of Squares, fill cell by cell or row by row |
| `sorter_network` | Bitonic sort, odd-even sort | Vertical elements column on left, horizontal wires, sub-stages left→right |

---

## Manim Code Quality

Three layers prevent bad Manim code from reaching the renderer:

**1. Prompt rules** (in `manim_scene.py` SYSTEM_PROMPT)
- Z-order: `VGroup(shape, text)` — shape always first
- Opening frame: `self.add(title, data)` before any narration section
- Highlighting: change `shape` fill only; always set `label.set_color(WHITE)` after
- Banned: `Tex`, `MathTex`, `numpy`, `buff=` in constructors, content below y=-1.8

**2. Deterministic patching** (`_patch_manim_code`)
- Clamp `run_time` to ≥ 0.1 and `self.wait()` to ≥ 0.01
- Replace `"Rank N"` → `"Node N"` inside all string literals
- Strip `buff=` from Mobject constructors (Square, Circle, VGroup, etc.)

**3. Static validation** (`_validate_manim_code`)
- Python syntax check
- Forbidden pattern check (numpy, Tex, MathTex)
- Z-order bug detection (`VGroup(Text(...)` pattern)
- Off-screen content detection (y < -1.8 in move_to calls)
- `buff=` in constructor detection
- On failure → error message fed back to LLM for retry

---

## TTS Providers

Configured via `TTS_PROVIDER` in `.env`:

| Provider | Key required | Quality | Notes |
|---|---|---|---|
| `edge` (default) | None | Good | Free, Microsoft Edge TTS |
| `minimax` | `MINIMAX_API_KEY` | High | Best prosody |
| `openai` | `OPENAI_API_KEY` | High | tts-1-hd model |
| `elevenlabs` | `ELEVENLABS_API_KEY` | Highest | Most expensive |

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | required | OpenRouter key (`sk-or-v1-...`) |
| `AI_MODEL` | `openai/gpt-4.5` | Any OpenRouter model ID |
| `TTS_PROVIDER` | `edge` | `edge` / `minimax` / `openai` / `elevenlabs` |
| `SUPABASE_URL` | — | Supabase project URL |
| `SUPABASE_SECRET_KEY` | — | Supabase service role key |
| `CLOUDINARY_CLOUD_NAME` | — | Cloudinary cloud name |
| `CLOUDINARY_API_KEY` | — | Cloudinary API key |
| `CLOUDINARY_API_SECRET` | — | Cloudinary API secret |
| `VIDEOS_DIR` | `videos` | Local dir for temp video files |
| `DATABASE_URL` | `sqlite+aiosqlite:///./algo_visuals.db` | SQLAlchemy DB URL |
| `ENV` | `development` | `development` / `production` |
