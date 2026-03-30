import time
import uuid
from typing import Any, Literal

from app.config import settings
from app.schemas.response import AlgorithmInfo, Complexity, GenerateResponse, ResponseMeta, ValidationResult, ValidationCheck
from app.services import ai_generator, sandbox, tracer, scene_builder, validator, confidence
from app.utils.exceptions import AIGenerationError, SandboxSecurityError, SandboxTimeoutError

_MAX_ATTEMPTS = 2


async def run(prompt: str, user_input: Any = None) -> GenerateResponse:
    request_id = str(uuid.uuid4())
    errors: list[str] = []
    status: Literal["success", "partial", "failed"] = "success"
    pipeline_start = time.monotonic()

    code_str: str | None = None
    props: ai_generator.AlgorithmProperties | None = None
    type_info: ai_generator.AlgorithmTypeInfo | None = None
    sandbox_result = None
    trace = None
    timeline = None
    validation: ValidationResult | None = None
    narration_sentences: list[str] = []
    step_indices: list[int] = []
    confidence_score = 0.0
    exec_time_ms = 0

    # Stage 1 — Detect algorithm type + build sample input
    try:
        type_info = await ai_generator.detect_algorithm_type(prompt, user_input)
    except AIGenerationError as e:
        errors.append(str(e))
        return _failed_response(request_id, errors, pipeline_start, settings.AI_MODEL)

    category = type_info.category
    subtype = type_info.subtype
    sample_input = type_info.sample_input

    # Stages 2–7 — Generate code + execute + validate, with one retry if output_check fails
    retry_hint: str | None = None

    for attempt in range(_MAX_ATTEMPTS):
        attempt_errors: list[str] = []
        attempt_sandbox_result = None
        attempt_trace = None
        attempt_validation: ValidationResult | None = None
        attempt_props: ai_generator.AlgorithmProperties | None = None
        attempt_code: str | None = None

        # Stage 2 — Generate code
        try:
            attempt_code = await ai_generator.generate_algorithm_code(
                prompt, category, sample_input, subtype, retry_hint=retry_hint
            )
        except AIGenerationError as e:
            errors.append(str(e))
            return _failed_response(request_id, errors, pipeline_start, settings.AI_MODEL)

        # Stage 3 — AST pre-scan
        try:
            sandbox.pre_scan(attempt_code)
        except SandboxSecurityError as e:
            errors.append(str(e))
            return _failed_response(request_id, errors, pipeline_start, settings.AI_MODEL, code=attempt_code)

        # Stage 4 — Generate algorithm properties
        try:
            attempt_props = await ai_generator.generate_algorithm_properties(
                prompt, attempt_code, category, subtype, sample_input
            )
        except AIGenerationError as e:
            attempt_errors.append(f"Properties generation failed (using defaults): {e}")
            attempt_props = _default_properties(prompt, category, subtype, sample_input)
            status = "partial"

        # Stage 5 — Execute in sandbox
        try:
            exec_start = time.monotonic()
            attempt_sandbox_result = sandbox.execute(
                attempt_code,
                sample_input,
                category=category,
                timeout=settings.SANDBOX_TIMEOUT,
            )
            exec_time_ms = int((time.monotonic() - exec_start) * 1000)
        except SandboxTimeoutError as e:
            attempt_errors.append(str(e))
            status = "partial"
        except Exception as e:  # noqa: BLE001
            attempt_errors.append(f"Sandbox error: {e}")
            status = "partial"

        # Stage 6 — Build trace
        if attempt_sandbox_result and attempt_sandbox_result.success and attempt_sandbox_result.steps:
            try:
                attempt_trace = tracer.build_trace(
                    algorithm_name=attempt_props.name,
                    algorithm_category=category,
                    algorithm_subtype=subtype,
                    input_data=sample_input,
                    final_output=attempt_sandbox_result.output,
                    raw_steps=attempt_sandbox_result.steps,
                )
            except Exception as e:  # noqa: BLE001
                attempt_errors.append(f"Trace build failed: {e}")
                status = "partial"
        elif attempt_sandbox_result and not attempt_sandbox_result.success:
            attempt_errors.append(f"Execution error: {attempt_sandbox_result.error}")
            status = "partial"

        # Stage 7 — Validation
        if attempt_sandbox_result is not None:
            try:
                attempt_validation = validator.run_all_checks(attempt_code, attempt_sandbox_result, attempt_props)
            except Exception as e:  # noqa: BLE001
                attempt_errors.append(f"Validation error: {e}")
                attempt_validation = _empty_validation()
        else:
            attempt_validation = _timeout_validation()

        # Check output_check result
        output_check = _find_check(attempt_validation, "output_check")

        if output_check is not None and not output_check.passed:
            if attempt < _MAX_ATTEMPTS - 1:
                # Retry with a hint about the failure
                retry_hint = output_check.message or "Output did not match expected value"
                errors.append(f"Attempt {attempt + 1} output check failed ({retry_hint}), retrying…")
                continue
            else:
                # Both attempts failed output_check — hard failure
                fail_msg = output_check.message or "Output did not match expected value after 2 attempts"
                errors.append(f"Algorithm generation failed: {fail_msg}")
                return _failed_response(
                    request_id, errors, pipeline_start, settings.AI_MODEL, code=attempt_code
                )

        # Output check passed (or no expected output to compare) — accept this attempt
        code_str = attempt_code
        props = attempt_props
        sandbox_result = attempt_sandbox_result
        trace = attempt_trace
        validation = attempt_validation
        errors.extend(attempt_errors)
        break

    # Stage 8 — Scene timeline
    if trace:
        try:
            timeline = scene_builder.build(trace)
        except Exception as e:  # noqa: BLE001
            errors.append(f"Scene build failed: {e}")
            status = "partial"

    # Stage 9 — Narration
    try:
        narration_sentences, step_indices = await ai_generator.generate_narration(
            algorithm_name=props.name,
            category=category,
            input_data=sample_input,
            final_output=sandbox_result.output if sandbox_result else None,
            trace_steps=sandbox_result.steps if sandbox_result else [],
            algorithm_steps=props.algorithm_steps or [],
        )
    except Exception as e:  # noqa: BLE001
        errors.append(f"Narration failed: {e}")
        status = "partial"

    # Stage 10 — Confidence score
    if validation and sandbox_result:
        confidence_score = confidence.compute(validation, props, sandbox_result, narration_sentences)
    elif validation:
        confidence_score = 0.1

    if not errors and trace and timeline:
        status = "success"
    elif not trace or not timeline:
        status = "partial" if code_str else "failed"

    gen_time_ms = int((time.monotonic() - pipeline_start) * 1000)

    return GenerateResponse(
        request_id=request_id,
        status=status,
        confidence_score=confidence_score,
        algorithm=AlgorithmInfo(
            name=props.name,
            type=props.type,
            execution_model=props.execution_model,
            category=category,
            subtype=subtype,
            complexity=Complexity(
                time=props.time_complexity,
                space=props.space_complexity,
                work=props.work_complexity,
                span=props.span_complexity,
            ),
            input_size=props.input_size,
            description=props.description,
            steps=props.algorithm_steps or [],
        ) if props else None,
        code=code_str,
        narration=narration_sentences,
        step_indices=step_indices,
        trace=trace,
        scene_timeline=timeline,
        validation=validation,
        errors=errors,
        meta=ResponseMeta(
            ai_model=settings.AI_MODEL,
            generation_time_ms=gen_time_ms,
            execution_time_ms=exec_time_ms,
        ),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find_check(validation: ValidationResult | None, name: str) -> ValidationCheck | None:
    if validation is None:
        return None
    return next((c for c in validation.checks if c.name == name), None)


def _failed_response(request_id, errors, start, model, code=None) -> GenerateResponse:
    return GenerateResponse(
        request_id=request_id, status="failed", confidence_score=0.0,
        code=code, errors=errors,
        meta=ResponseMeta(
            ai_model=model,
            generation_time_ms=int((time.monotonic() - start) * 1000),
            execution_time_ms=0,
        ),
    )


def _default_properties(prompt: str, category: str, subtype: str, sample_input: Any) -> ai_generator.AlgorithmProperties:
    return ai_generator.AlgorithmProperties(
        name="Algorithm", type="other", execution_model="serial",
        algorithm_category=category, algorithm_subtype=subtype,
        time_complexity="O(n)", space_complexity="O(n)",
        work_complexity=None, span_complexity=None,
        input_size=8, description=prompt,
        expected_output=None, sample_input=sample_input,
        algorithm_steps=[],
    )


def _empty_validation() -> ValidationResult:
    return ValidationResult(passed=False, checks=[])


def _timeout_validation() -> ValidationResult:
    return ValidationResult(
        passed=False,
        checks=[ValidationCheck(name="execution_check", passed=False, message="Sandbox timed out or failed to start")],
    )
