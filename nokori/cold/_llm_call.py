"""Shared LLM call infrastructure for cold-path pipeline stages.

Provides the durable idempotency/circuit-breaker layer used by all
cold-path roles (admission_judge, rule_rewriter, final_judge, merge_planner,
synthetic_eval_generator).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from ..db import Db, dumps_json
from ..utils.logging import get_logger
from .jobs import (
    enqueue_job,
    get_cached_output,
    is_circuit_breaker_open,
    mark_job_complete,
    mark_job_failed,
)
from .roles import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_TIMEOUTS,
    PROMPT_VERSIONS,
)

log = get_logger("nokori.cold.pipeline")


class CircuitBreakerOpenError(RuntimeError):
    """Raised when a circuit breaker is open — job should remain pending, not rejected."""

    pass


def prompt_text(value: str) -> str:
    """Fence untrusted content with unique boundary markers (spec section 5)."""
    boundary = "---UNTRUSTED-CONTENT-BOUNDARY---"
    return f"{boundary}\n{value}\n{boundary}"


def llm_input_hash(role: str, system: str, user: str, model_id: str = "") -> str:
    payload = dumps_json(
        {
            "role": role,
            "prompt_version": PROMPT_VERSIONS.get(role),
            "model_id": model_id,
            "system": system,
            "user": user,
        }
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def call_llm_role(
    db: Db,
    llm: Any,
    *,
    role: str,
    model_id: str,
    system: str,
    user: str,
    max_tokens: int,
    timeout: int,
    validate_response: Callable[[str], Any] | None = None,
) -> str:
    """Call an LLM role through the durable idempotency/circuit-breaker layer."""
    from .roles import compute_prompt_version

    prompt_version = compute_prompt_version(role, system)
    input_hash = llm_input_hash(role, system, user, model_id)

    if is_circuit_breaker_open(db, role, model_id=model_id):
        raise CircuitBreakerOpenError(f"circuit breaker open for role {role}")

    cached = get_cached_output(db, role, model_id, prompt_version, input_hash)
    if cached is not None:
        log.info("role=%s model=%s cache_hit=true", role, model_id)
        return cached

    log.info(
        "role=%s model=%s calling LLM (max_tokens=%d timeout=%ds)",
        role,
        model_id,
        max_tokens,
        timeout,
    )
    job_id = enqueue_job(db, role, model_id, prompt_version, input_hash)

    _MAX_IMMEDIATE_RETRIES = 2
    last_error: Exception | None = None
    for attempt in range(_MAX_IMMEDIATE_RETRIES):
        try:
            response = llm.call_raw(
                model=model_id,
                system=system,
                user=user,
                max_tokens=max_tokens,
                timeout=timeout,
            )
        except Exception as exc:
            last_error = exc
            if attempt < _MAX_IMMEDIATE_RETRIES - 1:
                log.warning(
                    "role=%s model=%s attempt %d/%d LLM call failed: %s, retrying",
                    role,
                    model_id,
                    attempt + 1,
                    _MAX_IMMEDIATE_RETRIES,
                    exc,
                )
                continue
            error_info = f"{type(exc).__name__}: {exc}"
            mark_job_failed(db, job_id, error_info=error_info)
            raise

        try:
            if validate_response is not None:
                validate_response(response)
            else:
                json.loads(response)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            last_error = exc
            if attempt < _MAX_IMMEDIATE_RETRIES - 1:
                log.warning(
                    "role=%s model=%s attempt %d/%d validation failed: %s, retrying",
                    role,
                    model_id,
                    attempt + 1,
                    _MAX_IMMEDIATE_RETRIES,
                    exc,
                )
                continue
            error_info = f"schema validation failed: {exc}"
            mark_job_failed(db, job_id, error_info=error_info)
            raise ValueError(f"LLM role {role} returned invalid output: {exc}") from exc

        response_str: str = response
        log.info("role=%s model=%s call OK (response_len=%d)", role, model_id, len(response_str))
        mark_job_complete(db, job_id, response_str)
        return response_str

    raise last_error  # type: ignore[misc]


def role_max_tokens(role: str, role_max_tokens: dict[str, int] | None) -> int:
    if role_max_tokens and role_max_tokens.get(role):
        return role_max_tokens[role]
    return DEFAULT_MAX_TOKENS[role]


def role_timeout(role: str, role_timeouts: dict[str, int] | None) -> int:
    if role_timeouts and role_timeouts.get(role):
        return role_timeouts[role]
    return DEFAULT_TIMEOUTS[role]
