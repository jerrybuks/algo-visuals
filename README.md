# Algo LightsOn 💡 — Backend

> Describe an algorithm in plain English. Get an animated explainer video.

The backend receives a natural language prompt, uses an LLM to write narration and generate a timed Manim animation, renders it to video, adds a voiceover, and returns a Cloudinary URL.

---

## How It Works

```
Prompt → Narration + Metadata → TTS Audio → Manim Animation Code → Render → Mix → Upload
```

1. **Narration** — LLM writes 12–18 sentences explaining the algorithm with a scratchpad to verify correctness. Returns algorithm metadata including which of 8 visual types to use.
2. **TTS** — Each sentence is synthesised to an MP3. Duration is measured and used to time the animation.
3. **Manim code** — LLM generates a Python Manim script synced to the audio durations. A deterministic patcher fixes common mistakes before validation.
4. **Render** — Manim renders the script to a 720p MP4. On crash, the error is fed back to the LLM for retry (up to 3 attempts).
5. **Mix** — FFmpeg concatenates the audio and overlays it on the video.
6. **Upload** — Video is uploaded to Cloudinary. Local file is deleted.

---

## Quick Start

```bash
# 1. Clone and create virtualenv
git clone https://github.com/jerrybuks/algo-visuals
cd algo-visuals
python -m venv venv && source venv/bin/activate

# 2. Install Python deps
pip install -r requirements.txt

# 3. Install system deps (macOS)
brew install cairo pango pkg-config ffmpeg

# 4. Configure
cp .env.example .env
# Fill in OPENROUTER_API_KEY (required)
# Fill in SUPABASE_* and CLOUDINARY_* for persistence and CDN

# 5. Start dev server
fastapi dev main.py
```

API docs at `http://localhost:8000/docs`

---

## API

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/v1/generate` | Start a generation job |
| `GET` | `/api/v1/status/:id` | Poll job progress |
| `GET` | `/api/v1/history` | List past public generations |
| `GET` | `/api/v1/history/:id` | Single past generation detail |
| `GET` | `/api/v1/health` | Server health check |

**Generate a video:**
```bash
curl -X POST http://localhost:8000/api/v1/generate \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Merge sort on an array of 8 elements"}'
```

Poll `/api/v1/status/:job_id` every 3 seconds until `status: "done"`, then use `video_url`.

---

## Visual Types

The LLM picks the right visualization style automatically:

| Type | Algorithms |
|---|---|
| `array_sequential` | Sorting, searching, single-pass algorithms |
| `array_parallel_rounds` | Parallel prefix scan, parallel reduction |
| `node_communication` | MPI Reduce, broadcast, scatter/gather |
| `recursive_split_merge` | Merge sort, quicksort |
| `tree_traversal` | BST, DFS, BFS |
| `graph_traversal` | Dijkstra, general graph algorithms |
| `matrix_fill` | Dynamic programming, Floyd-Warshall |
| `sorter_network` | Bitonic sort, odd-even sort |

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | Yes | OpenRouter API key |
| `AI_MODEL` | No | Model ID (default: `openai/gpt-4.5`) |
| `TTS_PROVIDER` | No | `edge` (default, free) / `minimax` / `openai` / `elevenlabs` |
| `SUPABASE_URL` | No | Supabase project URL |
| `SUPABASE_SECRET_KEY` | No | Supabase service role key |
| `CLOUDINARY_CLOUD_NAME` | No | Cloudinary cloud name |
| `CLOUDINARY_API_KEY` | No | Cloudinary API key |
| `CLOUDINARY_API_SECRET` | No | Cloudinary API secret |

Without Supabase/Cloudinary the app still works — videos are served locally and history is stored in SQLite.

---

## Deployment (Render)

The `render.yaml` and `build.sh` are included. On Render:

1. Connect the GitHub repo
2. Render detects `render.yaml` automatically
3. Add environment variables in the Render dashboard
4. Deploy

> Use at least the **Standard** plan (1GB RAM) — Manim rendering is memory-intensive.

---

## Frontend

The frontend lives in a separate repo: [`algo-visuals-ui`](https://github.com/jerrybuks/algo-visuals-ui)
