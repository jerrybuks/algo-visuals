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
- `AI_MODEL` — any OpenRouter model ID (e.g. `anthropic/claude-sonnet-4-5`, `openai/gpt-4o`)

All other env vars have sensible defaults.

## Architecture

This is a **stateless request/response** FastAPI backend. Each `POST /api/v1/generate` runs a full AI pipeline and stores the result in SQLite by UUID for later retrieval.

### Request lifecycle

```
POST /api/v1/generate
  → app/api/v1/generate.py        (route handler, DB save)
  → app/services/pipeline.py      (orchestrator — all stages run here)
      1. ai_generator.generate_algorithm_code()      → raw Python code string
      2. sandbox.pre_scan()                          → AST safety check (blocks imports etc.)
      3. ai_generator.generate_algorithm_properties() → name, complexity, sample_input, expected_output
      4. sandbox.execute()                           → runs code in isolated process, collects steps
      5. tracer.build_trace()                        → normalises raw steps → StepTrace
      6. validator.run_all_checks()                  → 5 independent checks
      7. confidence.compute()                        → 0.0–1.0 score
      8. scene_builder.build()                       → StepTrace → SceneTimeline (frontend-ready)
      9. ai_generator.generate_narration()           → list[str] narration sentences
     10. assemble + return GenerateResponse
```

### Key design decisions

**LLM layer** (`app/services/ai_generator.py`): Uses the `openai` SDK pointed at OpenRouter (`OPENROUTER_BASE_URL`). Switching models is a one-line `.env` change. All three Claude calls (`generate_algorithm_code`, `generate_algorithm_properties`, `generate_narration`) go through the shared `_chat()` helper.

**Sandbox** (`app/services/sandbox.py`): 4-layer protection — prompt instructs no imports → AST pre-scan rejects forbidden nodes → restricted `__builtins__` dict → `multiprocessing.Process` with hard timeout + kill. Generated code must define `def run(arr: list, steps: list) -> any` and call `record_step()` to emit trace events.

**Trace → Timeline**: `tracer.py` normalises the raw step dicts the sandbox collects into a typed `StepTrace`. `scene_builder.py` then converts that into a `SceneTimeline` with per-frame `array_state`, `highlight_indices`, and `active_connections` — ready for the frontend animation renderer to consume directly.

**Partial failure**: The pipeline never raises to the caller. Each stage failure sets `status: "partial"` or `status: "failed"` and appends to `errors[]`, so the frontend always gets a structured response.

**Prompt templates** (`app/prompts/`): Treat these like code. The code generation prompt is the highest-leverage file — it controls sandbox safety (instructs the model to call `record_step()` and never import) and trace quality.

### Response shape

`GenerateResponse` (defined in `app/schemas/response.py`) is the single top-level type returned by both endpoints. It contains `algorithm`, `code`, `narration`, `trace` (StepTrace), `scene_timeline` (SceneTimeline), `validation`, `confidence_score`, `status`, and `errors`.
