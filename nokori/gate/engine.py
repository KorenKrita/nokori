"""GateEngine — deep module owning the gate blocking decision.

Interface: should_block(tool_name, prompt_hash, session_id, payload) → GateDecision
Implementation: marker I/O, eligibility revalidation, tool matching, expiry,
single-round enforcement — all internal.
"""

from __future__ import annotations

import functools
import json
import re
import time
from dataclasses import dataclass

from ..config import Config
from ..db import Db, loads_json
from ..policy import RUNTIME_POLICY_VERSION
from ..utils.logging import get_logger
from ..utils.time import local_now, parse_iso
from . import marker as marker_io
from .marker import MarkerRule
from .state import MarkerState

log = get_logger("nokori.gate.engine")

DEFERRAL_THRESHOLD_S = 2.0


@dataclass(frozen=True)
class GateDecision:
    blocked: bool
    state: MarkerState | None = None
    rules: tuple[MarkerRule, ...] = ()
    reason: str = ""
    rules_checked: int = 0
    rules_blocked: int = 0
    elapsed_ms: float = 0.0
    deferred: bool = False


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
        *,
        gate_matcher: str | None = None,
    ) -> GateDecision:
        if not self._cfg.gate_enabled:
            return GateDecision(blocked=False, reason="gate_disabled")

        matcher = gate_matcher if gate_matcher is not None else self._cfg.gate_matcher
        if not tool_matches_gate(tool_name, matcher):
            return GateDecision(blocked=False, reason="tool_not_matched")

        if not prompt_hash:
            return GateDecision(blocked=False, reason="no_prompt_hash")

        t0 = time.monotonic()

        marker = marker_io.read(self._cfg, session_id, prompt_hash_value=prompt_hash)
        if marker is None:
            return GateDecision(blocked=False, reason="no_marker")

        if marker_io.is_expired(marker, self._cfg.gate_ttl_seconds):
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            elapsed = (time.monotonic() - t0) * 1000
            return GateDecision(
                blocked=False,
                state=MarkerState.expired,
                reason="marker_expired",
                elapsed_ms=elapsed,
            )

        if not marker.rules:
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            elapsed = (time.monotonic() - t0) * 1000
            return GateDecision(
                blocked=False,
                state=MarkerState.empty,
                reason="empty_marker",
                elapsed_ms=elapsed,
            )

        if not marker_io.prompt_hash_matches(marker, prompt_hash, session_id=session_id):
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            elapsed = (time.monotonic() - t0) * 1000
            return GateDecision(
                blocked=False,
                state=MarkerState.hash_mismatch,
                reason="hash_mismatch",
                elapsed_ms=elapsed,
            )

        try:
            eligibility = _batch_check_eligibility(list(marker.rules), self._db)
            gate_rules = []
            for rule, eligible, excluded_contexts in eligibility:
                if not eligible:
                    continue
                if not has_tool_evidence(rule, payload):
                    continue
                if tool_input_exclusion_fires(rule, payload, excluded_contexts):
                    continue
                gate_rules.append(rule)
        except Exception:
            log.exception("gate rule processing failed; consuming marker")
            elapsed = (time.monotonic() - t0) * 1000
            marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)
            return GateDecision(
                blocked=False,
                state=MarkerState.error,
                reason="processing_error",
                rules_checked=len(marker.rules),
                elapsed_ms=elapsed,
            )

        elapsed = (time.monotonic() - t0) * 1000
        marker_io.delete(self._cfg, session_id, prompt_hash_value=prompt_hash)

        if not gate_rules:
            return GateDecision(
                blocked=False,
                state=MarkerState.ineligible,
                reason="no_eligible_rules",
                rules_checked=len(marker.rules),
                rules_blocked=0,
                elapsed_ms=elapsed,
            )

        deferred = False
        try:
            created = parse_iso(marker.created_at)
            if created is not None:
                age = (local_now() - created).total_seconds()
                deferred = age > DEFERRAL_THRESHOLD_S
        except Exception:
            pass

        return GateDecision(
            blocked=True,
            state=MarkerState.consumed,
            rules=tuple(gate_rules),
            reason="blocked",
            rules_checked=len(marker.rules),
            rules_blocked=len(gate_rules),
            elapsed_ms=elapsed,
            deferred=deferred,
        )


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


def _batch_check_eligibility(
    rules: list[MarkerRule], db: Db
) -> list[tuple[MarkerRule, bool, list | None]]:
    """Single DB query for all rules, returns (rule, eligible, excluded_contexts) triples."""
    if not rules:
        return []

    ids = [r.rule_id for r in rules if r.rule_id]
    short_ids = [r.short_id for r in rules if r.short_id]

    lookup_by_id: dict[str, dict] = {}
    lookup_by_short: dict[str, dict] = {}
    if ids or short_ids:
        placeholders_parts = []
        params: list[str] = []
        if ids:
            placeholders_parts.append(f"id IN ({','.join('?' * len(ids))})")
            params.extend(ids)
        if short_ids:
            placeholders_parts.append(f"short_id IN ({','.join('?' * len(short_ids))})")
            params.extend(short_ids)
        where = " OR ".join(placeholders_parts)
        rows = db.fetchall(
            "SELECT id, short_id, status, severity, rule_version, "
            "runtime_policy_version, excluded_contexts FROM rules WHERE " + where,
            tuple(params),
        )
        for row in rows:
            lookup_by_id[row["id"]] = row
            lookup_by_short[row["short_id"]] = row

    results: list[tuple[MarkerRule, bool, list | None]] = []
    for rule in rules:
        row = None
        if rule.rule_id and rule.rule_id in lookup_by_id:
            row = lookup_by_id[rule.rule_id]
        if row is None and rule.short_id and rule.short_id in lookup_by_short:
            row = lookup_by_short[rule.short_id]

        if row is None:
            results.append((rule, False, None))
            continue

        try:
            if row["status"] != "trusted" or row["severity"] != "gate_eligible":
                results.append((rule, False, None))
                continue
            marker_version = getattr(rule, "rule_version", None)
            db_version = row["rule_version"]
            if marker_version is not None:
                if db_version is None or int(db_version) != marker_version:
                    results.append((rule, False, None))
                    continue
            marker_policy = getattr(rule, "runtime_policy_version", None)
            if marker_policy and marker_policy != row["runtime_policy_version"]:
                results.append((rule, False, None))
                continue
            excluded_contexts = (
                loads_json(row["excluded_contexts"], []) if row["excluded_contexts"] else []
            )
            eligible = row["runtime_policy_version"] == RUNTIME_POLICY_VERSION
            results.append((rule, eligible, excluded_contexts if eligible else None))
        except (ValueError, TypeError):
            results.append((rule, False, None))

    return results


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
        excluded_contexts = (
            loads_json(row["excluded_contexts"], []) if row["excluded_contexts"] else []
        )
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
        if t
        not in {
            "the",
            "and",
            "for",
            "with",
            "before",
            "after",
            "rule",
            "when",
            "that",
            "this",
            "from",
            "into",
            "also",
            "have",
            "been",
            "will",
            "should",
            "must",
            "always",
            "never",
        }
    }
    if not tokens:
        return True
    haystack = haystack[:8000]
    hits = {t for t in tokens if t in haystack}
    return len(hits) >= max(1, len(tokens) // 2)


def tool_input_exclusion_fires(
    rule: MarkerRule, payload: dict, excluded_contexts: list | None
) -> bool:
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
