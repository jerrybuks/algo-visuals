import ast
import math
from dataclasses import dataclass

from app.schemas.response import ValidationCheck, ValidationResult
from app.services.ai_generator import AlgorithmProperties
from app.services.sandbox import SandboxResult


@dataclass
class _CheckResult:
    name: str
    passed: bool
    message: str | None = None


def run_all_checks(
    code: str,
    sandbox_result: SandboxResult,
    props: AlgorithmProperties,
) -> ValidationResult:
    checks = [
        _syntax_check(code),
        _execution_check(sandbox_result),
        _output_check(sandbox_result, props),
        _complexity_check(sandbox_result, props),
        _step_count_check(sandbox_result),
    ]

    passed = all(c.passed for c in checks)
    return ValidationResult(
        passed=passed,
        checks=[ValidationCheck(name=c.name, passed=c.passed, message=c.message) for c in checks],
    )


def _syntax_check(code: str) -> _CheckResult:
    try:
        ast.parse(code)
        return _CheckResult("syntax_check", True)
    except SyntaxError as e:
        return _CheckResult("syntax_check", False, str(e))


def _execution_check(result: SandboxResult) -> _CheckResult:
    if result.success:
        return _CheckResult("execution_check", True)
    return _CheckResult("execution_check", False, result.error or "Execution failed")


def _normalize_for_compare(value: object) -> object:
    """Normalize a value for semantic comparison across categories."""
    if isinstance(value, dict):
        return {str(k): _normalize_for_compare(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return sorted(_normalize_for_compare(x) for x in value)
    return value


def _output_check(result: SandboxResult, props: AlgorithmProperties) -> _CheckResult:
    if not result.success:
        return _CheckResult("output_check", False, "Skipped — execution failed")
    if props.expected_output is None:
        return _CheckResult("output_check", True, "No expected output to compare")
    try:
        actual = result.output
        expected = props.expected_output
        # Numeric comparison with tolerance
        if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
            if math.isclose(float(actual), float(expected), rel_tol=1e-6):
                return _CheckResult("output_check", True, f"Output {actual} matches expected {expected}")
            return _CheckResult("output_check", False, f"Output {actual} != expected {expected}")
        # Exact match first
        if actual == expected:
            return _CheckResult("output_check", True, f"Output matches expected {expected}")
        # Semantic match: normalize dicts (stringify keys) and lists (sort elements)
        if isinstance(actual, (list, dict)) or isinstance(expected, (list, dict)):
            if _normalize_for_compare(actual) == _normalize_for_compare(expected):
                return _CheckResult("output_check", True, f"Output matches expected (order-insensitive)")
        return _CheckResult("output_check", False, f"Output {actual!r} != expected {expected!r}")
    except Exception as e:  # noqa: BLE001
        return _CheckResult("output_check", False, str(e))


def _complexity_check(result: SandboxResult, props: AlgorithmProperties) -> _CheckResult:
    if not result.success or not result.steps:
        return _CheckResult("complexity_check", False, "Skipped — no steps recorded")

    n = props.input_size
    step_count = len(result.steps)

    # Use work complexity for parallel, time complexity for serial
    complexity_label = props.work_complexity or props.time_complexity or ""
    if not complexity_label:
        return _CheckResult("complexity_check", True, "No complexity info to validate against")

    complexity_lower = complexity_label.lower()

    # Rough sanity bounds — steps include comparisons, swaps, copies etc.
    # so real step counts are several multiples of the theoretical complexity.
    if "log n" in complexity_lower or "log(n)" in complexity_lower:
        expected_max = n * int(math.log2(n) + 1) * 8
    elif "n^2" in complexity_lower or "n²" in complexity_lower:
        expected_max = n * n * 4
    else:
        expected_max = n * 20  # O(n) with generous margin

    if step_count <= expected_max:
        return _CheckResult("complexity_check", True, f"{step_count} steps for n={n} is consistent with {complexity_label}")
    return _CheckResult("complexity_check", False, f"{step_count} steps seems high for {complexity_label} with n={n}")


def _step_count_check(result: SandboxResult) -> _CheckResult:
    if not result.success:
        return _CheckResult("step_count_check", False, "Skipped — execution failed")
    steps = result.steps
    if not steps:
        return _CheckResult("step_count_check", False, "No steps were recorded — check record_step() calls")
    # Only universal fields required — category-specific fields are optional
    required_fields = {"stage", "operation", "description"}
    for i, step in enumerate(steps):
        missing = required_fields - set(step.keys())
        if missing:
            return _CheckResult("step_count_check", False, f"Step {i} missing fields: {missing}")
    return _CheckResult("step_count_check", True, f"{len(steps)} steps recorded with all required fields")
