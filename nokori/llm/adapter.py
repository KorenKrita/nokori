from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Callable

from ..config import Config
from ..errors import LlmError, LlmRateLimitError, LlmTimeoutError
from ..utils.logging import get_logger

log = get_logger("nokori.llm.adapter")


class LLMAdapter:
    def __init__(self, cfg: Config, *, http_open: Callable | None = None,
                 subprocess_run: Callable | None = None):
        self.cfg = cfg
        self._open = http_open or urllib.request.urlopen
        self._run = subprocess_run or subprocess.run

    def configured(self) -> bool:
        return bool(self.cfg.llm_base_url and self.cfg.llm_model)

    def complete(self, prompt: str, *, max_tokens: int = 2000,
                 timeout: int = 30) -> str | None:
        """Single user message (legacy). Prefer complete_messages for extract/merge."""
        return self.complete_messages(None, prompt, max_tokens=max_tokens, timeout=timeout)

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
                system, user, max_tokens, timeout,
            )
        return self._fallback_claude_cli(system, user, timeout)

    def _call_openai_compatible(
        self,
        system: str | None,
        user: str,
        max_tokens: int,
        timeout: int,
    ) -> str | None:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user})
        payload = {
            "model": self.cfg.llm_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.1,
        }
        headers = {"Content-Type": "application/json"}
        if self.cfg.llm_api_key:
            headers["Authorization"] = f"Bearer {self.cfg.llm_api_key}"
        url = f"{self.cfg.llm_base_url.rstrip('/')}/chat/completions"
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
            log.warning("LLM HTTP error %s on %s", e.code, url)
            if e.code == 429:
                raise LlmRateLimitError(f"HTTP {e.code}") from e
            raise LlmError(f"HTTP {e.code}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            log.warning("LLM connection failed: %s", type(e).__name__)
            raise LlmTimeoutError(str(e)) from e
        try:
            data = json.loads(body)
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
            log.warning("LLM response unparseable: %s", type(e).__name__)
            raise LlmError(f"bad response shape: {e}") from e

    def _fallback_claude_cli(
        self, system: str | None, user: str, timeout: int,
    ) -> str | None:
        env = os.environ.copy()
        env["NOKORI_EXTRACTING"] = "1"
        env["CLAUDECODE"] = ""
        system_prompt = system or (
            "You are a JSON extraction engine. Output only valid JSON. "
            "No explanations."
        )
        try:
            result = self._run(
                [
                    "claude", "-p",
                    "--model", "haiku",
                    "--system-prompt", system_prompt,
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
