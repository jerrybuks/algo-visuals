import json
import re
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI

from app.config import settings
from app.prompts import code_generation, properties, narration, type_detection
from app.utils.exceptions import AIGenerationError

_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.OPENROUTER_API_KEY,
            base_url=settings.OPENROUTER_BASE_URL,
            timeout=90.0,
        )
    return _client


def _strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        end = -1 if lines[-1].strip() == "```" else len(lines)
        text = "\n".join(lines[1:end])
    return text.strip()


def _parse_json(text: str) -> Any:
    """Parse JSON tolerantly — strips fences, removes trailing commas and // comments."""
    text = _strip_fences(text)
    # Remove single-line comments
    text = re.sub(r'//[^\n]*', '', text)
    # Remove trailing commas before ] or }
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return json.loads(text)


async def _chat(system: str, user: str, max_tokens: int = 2048, model: str | None = None) -> str:
    try:
        response = await get_client().chat.completions.create(
            model=model or settings.AI_MODEL,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""
    except Exception as e:
        raise AIGenerationError(str(e)) from e


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AlgorithmTypeInfo:
    category: str   # "array" | "tree" | "graph" | "matrix"
    subtype: str    # "default" | "linked_list" | "stack" | "queue" | "none"
    sample_input: Any


@dataclass
class AlgorithmProperties:
    name: str
    type: str
    execution_model: str   # "serial" | "parallel"
    algorithm_category: str
    algorithm_subtype: str
    # Serial complexity
    time_complexity: str | None
    space_complexity: str | None
    # Parallel complexity
    work_complexity: str | None
    span_complexity: str | None
    input_size: int
    description: str
    expected_output: Any
    sample_input: Any
    algorithm_steps: list[str] = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Stage 1 — Detect algorithm type + build sample input
# ---------------------------------------------------------------------------

async def detect_algorithm_type(prompt: str, user_input: Any = None) -> AlgorithmTypeInfo:
    """Detect category/subtype and produce sample input. If user_input is provided, use it directly."""
    raw = await _chat(
        system=type_detection.SYSTEM_PROMPT,
        user=type_detection.build_user_prompt(prompt),
        max_tokens=1024,
    )
    try:
        data = _parse_json(raw)
        category = data.get("category", "array")
        subtype = data.get("subtype") or "default"
        sample_input = user_input if user_input is not None else data.get("sample_input", [1, 2, 3, 4, 5, 6, 7, 8])
        return AlgorithmTypeInfo(category=category, subtype=subtype, sample_input=sample_input)
    except (json.JSONDecodeError, KeyError) as e:
        raise AIGenerationError(f"Type detection failed: {e}\nRaw: {raw}") from e


# ---------------------------------------------------------------------------
# Stage 2 — Generate algorithm code (category-aware)
# ---------------------------------------------------------------------------

async def generate_algorithm_code(
    prompt: str, category: str, sample_input: Any, subtype: str = "default", retry_hint: str | None = None
) -> str:
    raw = await _chat(
        system=code_generation.get_system_prompt(category),
        user=code_generation.build_user_prompt(prompt, category, sample_input, subtype, retry_hint=retry_hint),
        max_tokens=2048,
    )
    return _strip_fences(raw)


# ---------------------------------------------------------------------------
# Stage 3 — Generate algorithm properties
# ---------------------------------------------------------------------------

async def generate_algorithm_properties(
    prompt: str,
    code: str,
    category: str,
    subtype: str = "default",
    sample_input: Any = None,
) -> AlgorithmProperties:
    raw = await _chat(
        system=properties.SYSTEM_PROMPT,
        user=properties.build_user_prompt(prompt, code, category, sample_input),
        max_tokens=1024,
    )
    try:
        data = _parse_json(raw)
    except json.JSONDecodeError as e:
        raise AIGenerationError(f"Properties JSON parse failed: {e}\nRaw: {raw}") from e

    complexity = data.get("complexity", {})
    execution_model = data.get("execution_model", "serial")
    return AlgorithmProperties(
        name=data.get("name", "Algorithm"),
        type=data.get("type", "other"),
        execution_model=execution_model,
        algorithm_category=category,
        algorithm_subtype=subtype,
        time_complexity=complexity.get("time"),
        space_complexity=complexity.get("space"),
        work_complexity=complexity.get("work"),
        span_complexity=complexity.get("span"),
        input_size=int(data.get("input_size", 8)),
        description=data.get("description", ""),
        expected_output=data.get("expected_output"),
        sample_input=data.get("sample_input"),
        algorithm_steps=data.get("algorithm_steps") or [],
    )


# ---------------------------------------------------------------------------
# Stage 4 — Generate narration
# ---------------------------------------------------------------------------

_MAX_NARRATION_STAGES = 12  # must match scene_builder._MAX_NARRATION_STAGES


async def generate_narration(
    algorithm_name: str,
    category: str,
    input_data: Any,
    final_output: Any,
    trace_steps: list[dict],
    algorithm_steps: list[str] | None = None,
) -> tuple[list[str], list[int]]:
    # Group steps by stage
    stages_map: dict[int, list[dict]] = {}
    for step in trace_steps:
        stages_map.setdefault(step.get("stage", 1), []).append(step)

    all_stages = [
        {"stage": s, "ops": ops}
        for s, ops in sorted(stages_map.items())
    ]

    # Sample _MAX_NARRATION_STAGES stages evenly across all stages.
    # scene_builder distributes narration indices evenly too, so sentence i
    # covers the i-th chunk of stages — not just the first N stages.
    n = _MAX_NARRATION_STAGES
    total = len(all_stages)
    if total <= n:
        stages_with_steps = all_stages
    else:
        indices = [i * total // n for i in range(n)]
        stages_with_steps = [all_stages[i] for i in indices]

    raw = await _chat(
        system=narration.SYSTEM_PROMPT,
        user=narration.build_user_prompt(
            algorithm_name=algorithm_name,
            category=category,
            input_data=input_data,
            final_output=final_output,
            stages_with_steps=stages_with_steps,
            algorithm_steps=algorithm_steps or [],
        ),
        max_tokens=2048,
    )
    try:
        result = _parse_json(raw)
        # New structured format: {"sentences": [...], "stage_step_indices": [...]}
        if isinstance(result, dict):
            sentences = [str(s) for s in result.get("sentences", [])]
            step_indices = []
            for idx in result.get("stage_step_indices", []):
                try:
                    step_indices.append(int(idx))
                except (ValueError, TypeError):
                    step_indices.append(0)
            return sentences, step_indices
        # Legacy fallback: plain list
        if isinstance(result, list):
            return [str(s) for s in result], []
        return [], []
    except json.JSONDecodeError as e:
        raise AIGenerationError(f"Narration JSON parse failed: {e}\nRaw: {raw}") from e
