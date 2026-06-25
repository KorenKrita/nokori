class NokoriError(Exception):
    """Base error. Hooks degrade silently; CLI surfaces with exit code 1."""

    def __init__(self, *args: object, remediation: str | None = None) -> None:
        super().__init__(*args)
        self.remediation = remediation


class ConfigError(NokoriError):
    pass


class DbError(NokoriError):
    pass


class LlmError(NokoriError):
    def __init__(
        self, *args: object, remediation: str | None = None, status_code: int | None = None
    ) -> None:
        super().__init__(*args, remediation=remediation)
        self.status_code = status_code


class LlmTimeoutError(LlmError):
    pass


class LlmRateLimitError(LlmError):
    pass


class EmbeddingError(LlmError):
    pass
