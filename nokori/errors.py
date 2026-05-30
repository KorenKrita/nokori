class NokoriError(Exception):
    """Base error. Hooks degrade silently; CLI surfaces with exit code 1."""


class ConfigError(NokoriError):
    pass


class DbError(NokoriError):
    pass


class LlmError(NokoriError):
    pass


class LlmTimeoutError(LlmError):
    pass


class LlmRateLimitError(LlmError):
    pass


class EmbeddingError(LlmError):
    pass
