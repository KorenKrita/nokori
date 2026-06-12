"""GateEngine — deep module owning the gate blocking decision.

Interface: should_block(tool_name, prompt_hash, session_id, payload) → GateDecision
Implementation: marker I/O, eligibility revalidation, tool matching, expiry,
single-round enforcement — all internal.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass

from ..config import Config
from ..db import Db, loads_json
from ..policy import RUNTIME_POLICY_VERSION
from ..utils.logging import get_logger
from . import marker as marker_io
from .marker import MarkerRule

log = get_logger("nokori.gate.engine")


@dataclass(frozen=True)
class GateDecision:
    blocked: bool
    rules: tuple[MarkerRule, ...] = ()
    reason: str = ""


class GateEngine:
    """Owns the full gate decision: should a tool call be blocked?"""

    def __init__(self, cfg: Config, db: Db) -> None:
        self._cfg = cfg
        self._db = db

    @property
    def cfg(self) -> Config:
        return self._cfg

    @property
    def db(self) -> Db:
        return self._db

    def should_block(
        self,
        tool_name: str | None,
        prompt_hash: str | None,
        session_id: str,
        payload: dict,
    ) -> GateDecision:
        if not self._cfg.gate_enabled:
            return GateDecision(blocked=False, reason="gate_disabled")

        if not tool_matches_gate(tool_name, self._cfg.gate_matcher):
            return GateDecision(blocked=False, reason="tool_not_matched")

        if not prompt_hash:
            return GateDecision(blocked=False, reason="no_prompt_hash")

        marker = marker_io.read(self._cfg, session_id, prompt_hash_value=prompt_hash)
        if marker is None:
            return GateDecision(blocked=False, reason="no_marker")

        if marker_io.is_expired(marker, self._cfg.gate_ttl_seconds):
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            return GateDecision(blocked=False, reason="marker_expired")

        if not marker.rules:
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            return GateDecision(blocked=False, reason="empty_marker")

        if not marker_io.prompt_hash_matches(marker, prompt_hash, session_id=session_id):
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            return GateDecision(blocked=False, reason="hash_mismatch")

        try:
            gate_rules = []
            for r in marker.rules:
                eligible, excluded_contexts = is_gate_eligible_rule(r, self._db)
                if not eligible:
                    continue
                if not has_tool_evidence(r, payload):
                    continue
                if tool_input_exclusion_fires(r, payload, excluded_contexts):
                    continue
                gate_rules.append(r)
        except Exception:
            log.exception("gate rule processing failed; consuming marker")
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            return GateDecision(blocked=False, reason="processing_error")

        if not gate_rules:
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            return GateDecision(blocked=False, reason="no_eligible_rules")

        marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
        return GateDecision(blocked=True, rules=tuple(gate_rules), reason="blocked")


@functools.lru_cache(maxsize=8)
def compiled_gate_matcher(matcher: str) -> re.Pattern[str] | None:
    try:
        return re.compile(matcher)
    except re.error:
        return None


def tool_matches_gate(tool_name: str | None, matcher: str) -> bool:
    if not tool_name or not matcher:
        return False
    pattern = compiled_gate_matcher(matcher)
    if pattern is None:
        log.warning("invalid gate matcher %r; skipping gate for this tool", matcher)
        return False
    return bool(pattern.fullmatch(tool_name))


def is_gate_eligible_rule(rule: MarkerRule, db: Db) -> tuple[bool, list | None]:
    row = None
    if getattr(rule, "rule_id", None):
        row = db.fetchone(
            "SELECT id, short_id, status, severity, rule_version, "
            "runtime_policy_version, excluded_contexts FROM rules WHERE id = ?",
            (rule.rule_id,),
        )
    if row is None and getattr(rule, "short_id", None):
        row = db.fetchone(
            "SELECT id, short_id, status, severity, rule_version, "
            "runtime_policy_version, excluded_contexts FROM rules WHERE short_id = ?",
            (rule.short_id,),
        )
    if row is not None:
        if row["status"] != "trusted" or row["severity"] != "gate_eligible":
            return False, None
        marker_version = getattr(rule, "rule_version", None)
        if marker_version is not None and int(row["rule_version"]) != marker_version:
            return False, None
        marker_policy = getattr(rule, "runtime_policy_version", None)
        if marker_policy and marker_policy != row["runtime_policy_version"]:
            return False, None
        excluded_contexts = loads_json(row["excluded_contexts"], []) if row["excluded_contexts"] else []
        eligible = row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
        return eligible, excluded_contexts if eligible else None
    return False, None


def has_tool_evidence(rule: MarkerRule, payload: dict) -> bool:
    tool_input = payload.get("tool_input") or payload.get("input")
    if not tool_input:
        return True
    if isinstance(tool_input, str):
        haystack = tool_input.lower()
    else:
        haystack = json.dumps(tool_input, ensure_ascii=False, sort_keys=True).lower()

    trigger = getattr(rule, "trigger", "") or ""
    action = getattr(rule, "action", "") or ""
    for phrase in (trigger, action):
        phrase = phrase.strip().lower()
        if phrase and phrase in haystack:
            return True

    tokens = {
        t
        for t in re.findall(r"[a-z0-9_+-]{4,}", f"{trigger} {action}".lower())
        if t not in {"the", "and", "for", "with", "before", "after", "rule",
                     "when", "that", "this", "from", "into", "also", "have",
                     "been", "will", "should", "must", "always", "never"}
    }
    if not tokens:
        return True
    haystack = haystack[:8000]
    hits = {t for t in tokens if t in haystack}
    return len(hits) >= max(1, len(tokens) // 2)


def tool_input_exclusion_fires(rule: MarkerRule, payload: dict, excluded_contexts: list | None) -> bool:
    tool_input = payload.get("tool_input") or payload.get("input")
    if not tool_input:
        return False
    if not excluded_contexts:
        return False

    if isinstance(tool_input, str):
        haystack = tool_input.lower()
    else:
        haystack = json.dumps(tool_input, ensure_ascii=False).lower()

    from ..matcher.compiler import CompilationError, _compile_excluded_context
    from ..matcher.runtime import _excluded_context_matches

    rule_id = getattr(rule, "rule_id", None) or getattr(rule, "short_id", None)
    for ctx in excluded_contexts:
        if ctx.get("scope") != "tool_input_only":
            continue
        try:
            compiled = _compile_excluded_context(ctx)
        except (CompilationError, TypeError, AttributeError) as exc:
            log.warning("invalid tool_input_only exclusion for gate rule %s: %s", rule_id, exc)
            continue
        if _excluded_context_matches(compiled, haystack):
            return True
    return False
