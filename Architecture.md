# Architecture — AI Algorithm Animation Engine

> This document is the single source of truth for how the system works. It is updated as new features are built. Anyone new to the project should read this top to bottom before touching the code.

---

## What This System Does

This is the **backend** of an AI Algorithm Animation Engine. A user sends a natural language prompt like _"Parallel reduction with 8 elements"_ and the backend:

1. Uses AI to generate a working Python implementation of the algorithm
2. Executes that code safely in a sandboxed environment
3. Captures a detailed step-by-step trace of what the algorithm did
4. Validates the result using multiple checks
5. Converts the trace into an animation-ready scene timeline
6. Generates narration text to accompany the animation
7. Returns everything in one structured JSON response
8. Persists the result in a database so it can be retrieved later by ID

A **separate frontend** (not in this codebase) consumes the API and handles all rendering and animation.

---

## Technology Stack

| Layer | Technology |
|---|---|
| Web framework | FastAPI (Python 3.11) |
| ASGI server | Uvicorn |
| AI / LLM | OpenRouter API (OpenAI-compatible), defaults to `anthropic/claude-sonnet-4-5` |
| Data validation | Pydantic v2 |
| Database | SQLite via SQLAlchemy async + aiosqlite |
| Safe code execution | Python `multiprocessing` + restricted `exec` |

---

## Project Structure

```
algo-visuals/
├── main.py                  Entry point — FastAPI app, CORS, DB lifespan, router mounting
├── .env                     Secrets and configuration (never committed)
├── .env.example             Template for .env
├── requirements.txt
│
└── app/
    ├── config.py            Reads .env into a typed Settings object (pydantic-settings)
    ├── database.py          SQLAlchemy async engine, session factory, init_db()
    │
    ├── api/v1/
    │   ├── generate.py      POST /api/v1/generate
    │   ├── results.py       GET  /api/v1/results/{request_id}
    │   └── health.py        GET  /api/v1/health
    │
    ├── schemas/
    │   ├── request.py       GenerateRequest (what the client sends)
    │   └── response.py      GenerateResponse and all nested types (single source of truth for API contract)
    │
    ├── models/
    │   └── generation.py    SQLAlchemy ORM model — one row per generation stored in SQLite
    │
    ├── services/
    │   ├── pipeline.py      The orchestrator — calls every other service in the right order
    │   ├── ai_generator.py  All LLM calls (3 per request)
    │   ├── sandbox.py       Safe code execution
    │   ├── tracer.py        Converts raw sandbox steps → typed StepTrace
    │   ├── scene_builder.py Converts StepTrace → SceneTimeline (frontend animation format)
    │   ├── validator.py     Runs 5 independent validation checks
    │   └── confidence.py    Computes a 0.0–1.0 confidence score
    │
    ├── prompts/
    │   ├── code_generation.py   System + user prompt for algorithm code generation
    │   ├── properties.py        Prompt for extracting algorithm metadata (name, complexity, etc.)
    │   └── narration.py         Prompt for generating narration sentences
    │
    └── utils/
        └── exceptions.py    Custom exception types used across services
```

---

## API Endpoints

### `POST /api/v1/generate`

The main endpoint. Accepts a natural language prompt, runs the full pipeline, saves the result, and returns it.

**Request:**
```json
{ "prompt": "Parallel reduction with 8 elements" }
```

**Response** — `GenerateResponse` (see Data Contracts section below)

---

### `GET /api/v1/results/{request_id}`

Fetches a previously generated result by its UUID. The `request_id` comes from a prior `POST /api/v1/generate` response.

---

### `GET /api/v1/health`

Returns server status, configured model, and whether the API key is present.

---

## The Pipeline — Step by Step

`app/services/pipeline.py` is the orchestrator. Every `POST /api/v1/generate` call runs through these stages in order:

```
User prompt
    │
    ▼
[Stage 1] ai_generator.generate_algorithm_code(prompt)
          → Sends prompt to LLM with a strict system prompt
          → Returns Python code string
          → The code defines def run(arr, steps) and calls record_step() at each operation
          → HARD FAILURE if this fails (nothing to work with)
    │
    ▼
[Stage 2] sandbox.pre_scan(code)
          → Walks the Python AST
          → Rejects: import statements, open/eval/exec calls, os/sys/subprocess access
          → HARD FAILURE if blocked (code is unsafe)
    │
    ▼
[Stage 3] ai_generator.generate_algorithm_properties(prompt, code)
          → Second LLM call
          → Returns: name, type, time/space complexity, input_size, sample_input, expected_output
          → SOFT FAILURE — uses safe defaults if this fails, continues as "partial"
    │
    ▼
[Stage 4] sandbox.execute(code, sample_input, timeout=10s)
          → Spawns an isolated subprocess
          → Runs exec(code) with restricted __builtins__
          → Collects the steps list that record_step() built up
          → Returns SandboxResult (output + steps + execution_time_ms)
          → SOFT FAILURE on timeout or crash — continues as "partial"
    │
    ▼
[Stage 5] tracer.build_trace(algorithm_name, input_data, final_output, raw_steps)
          → Normalises raw step dicts into typed TraceStep objects
          → Assigns step_id, parallel_group
          → Returns StepTrace
    │
    ▼
[Stage 6] validator.run_all_checks(code, sandbox_result, props)
          → Runs 5 independent checks (all run even if some fail):
             1. syntax_check      — ast.parse() succeeds
             2. execution_check   — sandbox ran without error/timeout
             3. output_check      — actual output matches expected_output
             4. complexity_check  — step count is consistent with claimed time complexity
             5. step_count_check  — steps list is non-empty and has required fields
          → Returns ValidationResult
    │
    ▼
[Stage 7] scene_builder.build(trace, algorithm_name)
          → Converts StepTrace → SceneTimeline
          → Creates Scene 0 (init frame), one Scene per stage, final result Scene
          → Each SceneFrame has: array_state, highlight_indices, active_connections, value_labels, narration_index
          → SOFT FAILURE — continues as "partial" if this fails
    │
    ▼
[Stage 8] ai_generator.generate_narration(algorithm_name, description, stages, final_output, input_data)
          → Third LLM call
          → Returns list[str] — one narration sentence per logical stage
          → SOFT FAILURE — returns empty list if fails
    │
    ▼
[Stage 9] confidence.compute(validation, props, sandbox_result, narration)
          → Pure function, no I/O
          → Scores 0.0–1.0 based on: checks passed (50%), output correct (20%),
            complexity consistent (10%), multi-stage trace (10%), narration present (10%)
    │
    ▼
Assemble GenerateResponse → save to SQLite → return to client
```

### Pipeline failure modes

The pipeline **never throws an unhandled exception**. Every stage has a defined failure mode:

| Stage | Failure type | Effect on response |
|---|---|---|
| Code generation | Hard | `status: "failed"`, request ends immediately |
| AST security scan | Hard | `status: "failed"`, request ends immediately |
| Properties generation | Soft | Uses defaults, `status: "partial"` |
| Sandbox execution | Soft | No trace/timeline, `status: "partial"` |
| Trace build | Soft | No timeline, `status: "partial"` |
| Validation | Soft | Empty validation result |
| Scene build | Soft | No timeline, `status: "partial"` |
| Narration | Soft | Empty narration list, `status: "partial"` |

---

## The Sandbox — Safe Code Execution

This is the most security-critical part of the system. AI-generated code is untrusted and must not be able to do anything except pure computation.

**4 layers of protection (in order):**

**Layer 1 — Prompt engineering**
The code generation system prompt instructs the LLM to never use import statements, only call `record_step()`, and only use arithmetic and list operations. This is the first line of defence but cannot be relied upon alone.

**Layer 2 — AST pre-scan** (`sandbox.pre_scan`)
Before any execution, the code is parsed as an AST and walked. Any of the following immediately rejects the code with `SandboxSecurityError`:
- `import` or `from X import` statements
- Calls to: `open`, `eval`, `exec`, `compile`, `__import__`, `getattr`, `setattr`, `delattr`, `vars`, `dir`, `globals`, `locals`, `breakpoint`
- Attribute access on: `os`, `sys`, `subprocess`, `socket`, `pathlib`, `shutil`, `importlib`

**Layer 3 — Restricted exec environment**
The code runs inside `exec()` with a custom `__builtins__` dict containing only safe primitives:
`len, range, enumerate, zip, min, max, sum, abs, round, int, float, str, list, dict, tuple, set, bool, isinstance, type, print, sorted, reversed, map, filter, any, all`

The `record_step` function is also injected into this environment as the only non-builtin the code can call.

**Layer 4 — Process isolation with timeout**
The `exec()` call runs inside a `multiprocessing.Process`. The parent process calls `process.join(timeout=N)`. If the process is still alive after N seconds it is terminated, then killed. Results are passed back via `multiprocessing.Queue` as plain dicts (no arbitrary objects can escape).

**The `record_step` contract**
Generated code is expected to call `record_step(steps, stage, operation, input_indices, output_index, input_values, output_value, array_snapshot, description)` after each meaningful operation. This is how the trace is built — the LLM self-instruments its own code. The `steps` list is passed into `run(arr, steps)` and `record_step` appends to it directly.

---

## The LLM Layer

`app/services/ai_generator.py` handles all three LLM calls. It uses the `openai` Python SDK pointed at OpenRouter's base URL, which means any model on OpenRouter can be used — not just Claude.

**Configuration** (in `.env`):
```
OPENROUTER_API_KEY=sk-or-v1-...
AI_MODEL=anthropic/claude-sonnet-4-5   # change this to swap models
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
```

To switch to GPT-4o: set `AI_MODEL=openai/gpt-4o`. No code changes needed.

All three calls go through the shared `_chat(system, user, max_tokens)` helper which handles the OpenRouter call and raises `AIGenerationError` on any failure.

The three calls are:
1. **Code generation** — returns raw Python code (markdown fences stripped)
2. **Properties** — returns a JSON object with name, type, complexity, sample_input, expected_output
3. **Narration** — returns a JSON array of strings

---

## Data Contracts

All API shapes are defined in `app/schemas/response.py`. This is the contract between the backend and frontend.

### `GenerateResponse` (top-level)

```
request_id        string (UUID)
status            "success" | "partial" | "failed"
confidence_score  float 0.0–1.0
algorithm         AlgorithmInfo | null
code              string | null      — the generated Python code
narration         string[]           — one sentence per stage
trace             StepTrace | null
scene_timeline    SceneTimeline | null
validation        ValidationResult | null
errors            string[]           — list of human-readable error messages
meta              ResponseMeta       — model, timing
```

### `StepTrace`

Represents what the algorithm actually did, step by step.

```
algorithm_name    string
total_steps       int
stages            int           — number of logical rounds/iterations
input             any[]         — the original input array
final_output      any           — the return value of run()
steps             TraceStep[]
```

Each `TraceStep`:
```
step_id           int           — 0-indexed position in the full steps list
stage             int           — which logical round this step belongs to (1-indexed)
operation         string        — "add", "compare", "swap", "read", "write", etc.
input_indices     int[]         — indices being read from the array
output_index      int           — index being written to
input_values      any[]         — values at input_indices
output_value      any           — value written to output_index
array_snapshot    any[]         — full array state AFTER this operation
description       string        — human-readable step description
parallel_group    int           — steps with the same stage can be animated in parallel
```

### `SceneTimeline`

The animation-ready format. The frontend should iterate `scenes` in order and play each `frame` in sequence.

```
algorithm_name    string
total_frames      int
duration_hint_ms  int           — suggested total animation duration
input_array       any[]
scenes            Scene[]
```

Each `Scene` has a `label` and a list of `SceneFrame`s. Each `SceneFrame`:
```
frame_id          int
type              "init" | "operation" | "result" | "summary"
step_ref          int | null    — links back to the TraceStep that caused this frame
array_state       any[]         — full array snapshot (frontend can scrub without recomputing)
highlight_indices int[]         — which array positions to visually highlight
active_connections Connection[] — edges to draw: { from, to, label }
value_labels      dict          — index → display string for value annotations
narration_index   int           — which narration sentence to show for this frame
description       string
```

---

## Database

SQLite is used for persistence. The schema has a single table:

```sql
CREATE TABLE generations (
    id              TEXT PRIMARY KEY,   -- UUID, same as request_id in the response
    prompt          TEXT NOT NULL,
    status          TEXT NOT NULL,      -- "success" | "partial" | "failed"
    confidence_score REAL,
    result_json     TEXT NOT NULL,      -- full GenerateResponse serialised as JSON
    created_at      DATETIME
);
```

The full `GenerateResponse` is stored as a JSON string so no schema migration is needed when the response shape changes during development. Retrieval just deserialises with `GenerateResponse.model_validate_json(record.result_json)`.

The database file (`algo_visuals.db`) is created automatically on first server start and is gitignored.

---

## Configuration Reference

All config lives in `.env` and is loaded into `app/config.py` via pydantic-settings.

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | required | Your OpenRouter key (`sk-or-v1-...`) |
| `AI_MODEL` | `anthropic/claude-sonnet-4-5` | Any model ID on OpenRouter |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter endpoint |
| `SANDBOX_TIMEOUT` | `10` | Max seconds for code execution |
| `MAX_ARRAY_SIZE` | `64` | Max elements in sample_input |
| `DATABASE_URL` | `sqlite+aiosqlite:///./algo_visuals.db` | SQLAlchemy async DB URL |
| `ENV` | `development` | Environment label |
| `LOG_LEVEL` | `INFO` | Log verbosity |

---

## What's Not Built Yet

The following are planned for future development and this document will be updated as they are added:

- Authentication / API key protection on the endpoints
- Queue-based async processing for long-running generations
- Support for non-array algorithms (graphs, trees, etc.)
- Streaming progress events (SSE) so the frontend can show live pipeline progress
- Frontend renderer / animation layer (separate codebase)
