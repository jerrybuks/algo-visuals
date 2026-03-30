class AIGenerationError(Exception):
    """Raised when a Claude API call fails."""


class SandboxTimeoutError(Exception):
    """Raised when sandboxed code execution exceeds the time limit."""


class SandboxSecurityError(Exception):
    """Raised when generated code fails the AST security scan."""


class PipelineError(Exception):
    """Catch-all for pipeline stage failures."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"[{stage}] {message}")
