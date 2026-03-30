from app.schemas.response import ValidationResult
from app.services.ai_generator import AlgorithmProperties
from app.services.sandbox import SandboxResult


def compute(
    validation: ValidationResult,
    props: AlgorithmProperties,
    sandbox_result: SandboxResult,
    narration: list[str],
) -> float:
    score = 0.0

    checks_by_name = {c.name: c for c in validation.checks}

    # All 5 checks passed → 0.50
    passed_count = sum(1 for c in validation.checks if c.passed)
    score += (passed_count / max(len(validation.checks), 1)) * 0.50

    # Output correctness → 0.20
    if checks_by_name.get("output_check", None) and checks_by_name["output_check"].passed:
        score += 0.20

    # Complexity check → 0.10
    if checks_by_name.get("complexity_check", None) and checks_by_name["complexity_check"].passed:
        score += 0.10

    # Code structure heuristic → 0.10
    if sandbox_result.success and sandbox_result.steps:
        steps = sandbox_result.steps
        has_stages = len({s.get("stage") for s in steps}) > 1
        score += 0.10 if has_stages else 0.05

    # Narration coherence → 0.10
    if narration and len(narration) >= 2:
        score += 0.10

    return round(min(score, 1.0), 4)
