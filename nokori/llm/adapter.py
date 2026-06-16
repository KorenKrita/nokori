from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from collections.abc import Callable

from ..config import Config
from ..constants import MAX_CLAUDE_CLI_INPUT_CHARS
from ..errors import LlmError, LlmRateLimitError, LlmTimeoutError
from ..utils.logging import get_logger
from ..utils.url_safe import safe_log_url

log = get_logger("nokori.llm.adapter")


class LLMAdapter:
    def __init__(
        self,
        cfg: Config,
        *,
        http_open: Callable | None = None,
        subprocess_run: Callable | None = None,
    ):
        self.cfg = cfg
        self._open = http_open or urllib.request.urlopen
        self._run = subprocess_run or subprocess.run

    def configured(self) -> bool:
        return bool(self.cfg.llm_base_url and self.cfg.llm_model)

    def complete(self, prompt: str, *, max_tokens: int = 2000, timeout: int = 30) -> str | None:
        """Single user message (legacy). Prefer complete_messages for extract/merge."""
        return self.complete_messages(None, prompt, max_tokens=max_tokens, timeout=timeout)

    def call_raw(
        self,
        model: str,
        system: str,
        user: str,
        max_tokens: int = 2000,
        timeout: int = 30,
    ) -> str:
        """Direct LLM call with explicit model. Raises on failure.

        Note: when llm_base_url is not configured, falls back to claude CLI
        which uses a hardcoded model; the `model` parameter is ignored in that path.
        """
        if self.cfg.llm_base_url:
            result = self._call_openai_compatible(
                system, user, max_tokens, timeout, model_id=model
            )
        else:
            result = self._fallback_claude_cli(system, user, timeout)
        if result is None:
            raise LlmError("LLM call returned None")
        return result

    def complete_role(
        self,
        role: str,
        system: str,
        user: str,
        *,
        max_tokens: int | None = None,
        timeout: int | None = None,
    ) -> str | None:
        if os.environ.get("NOKORI_EXTRACTING") == "1":
            log.warning("recursion guard tripped; skipping LLM call for role=%s", role)
            return None
        model_id = self.cfg.role_models.get(role) or self.cfg.llm_model
        effective_max = self.cfg.role_max_tokens.get(role) or max_tokens or 2000
        effective_timeout = self.cfg.role_timeouts.get(role) or timeout or 30
        log.info("LLM role call: role=%s model=%s", role, model_id or "claude-cli")
        if model_id and self.cfg.llm_base_url:
            return self._call_openai_compatible(
                system, user, effective_max, effective_timeout, model_id=model_id
            )
        return self._fallback_claude_cli(system, user, effective_timeout)

    def complete_messages(
        self,
        system: str | None,
        user: str,
        *,
        max_tokens: int = 2000,
        timeout: int = 30,
    ) -> str | None:
        if os.environ.get("NOKORI_EXTRACTING") == "1":
            log.warning("recursion guard tripped; skipping LLM call")
            return None

        if self.configured():
            return self._call_openai_compatible(
                system,
                user,
                max_tokens,
                timeout,
            )
        return self._fallback_claude_cli(system, user, timeout)

    def _call_openai_compatible(
        self,
        system: str | None,
        user: str,
        max_tokens: int,
        timeout: int,
        *,
        model_id: str | None = None,
        response_format: dict | None = None,
    ) -> str | None:
        if response_format is None:
            response_format = {"type": "json_object"}
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload: dict[str, object] = {
            "model": model_id or self.cfg.llm_model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        headers = {"Content-Type": "application/json"}
        if self.cfg.llm_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.llm_api_key}"
        base_url = self.cfg.llm_base_url
        if base_url is None:
            raise LlmError("llm_base_url is not configured")
        url = f"{base_url.rstrip('/')}/chat/completions"
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with self._open(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            log.warning("LLM HTTP error %s on %s", e.code, safe_log_url(url))
            if e.code == 429:
                raise LlmRateLimitError(f"HTTP {e.code}") from e
            raise LlmError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("LLM connection failed: %s", type(e).__name__)
            raise LlmTimeoutError(str(e)) from e
        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
            if content is None:
                raise LlmError("LLM returned null content")
            if not isinstance(content, str):
                raise LlmError(f"LLM returned non-string content: {type(content).__name__}")
            return content.strip()
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as e:
            log.warning("LLM response unparseable: %s", type(e).__name__)
            raise LlmError(f"bad response shape: {e}") from e

    def _fallback_claude_cli(
        self,
        system: str | None,
        user: str,
        timeout: int,
    ) -> str | None:
        env = os.environ.copy()
        env["NOKORI_EXTRACTING"] = "1"
        env["CLAUDECODE"] = ""
        system_prompt = system or (
            "You are a JSON extraction engine. Output only valid JSON. No explanations."
        )
        if len(user) > MAX_CLAUDE_CLI_INPUT_CHARS:
            log.warning(
                "claude -p input truncated %d -> %d chars", len(user), MAX_CLAUDE_CLI_INPUT_CHARS
            )
            user = user[:MAX_CLAUDE_CLI_INPUT_CHARS]
        try:
            result = self._run(
                [
                    "claude",
                    "-p",
                    "--model",
                    "haiku",
                    "--system-prompt",
                    system_prompt,
                    "--strict-mcp-config",
                    "--no-session-persistence",
                    "--no-chrome",
                ],
                input=user,
                capture_output=True,
                text=True,
                timeout=min(timeout, 30),
                env=env,
                check=False,
            )
            if result.returncode != 0:
                log.warning("claude -p exited %s", result.returncode)
                return None
            return result.stdout.strip()
        except FileNotFoundError:
            log.warning("`claude` CLI not found; LLM unavailable")
            return None
        except subprocess.TimeoutExpired:
            log.warning("claude -p timed out")
            return None
