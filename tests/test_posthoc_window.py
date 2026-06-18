"""Tests for posthoc transcript window resolution (task 06-18-fix-posthoc-window-active-fire-loop).

Covers the fix for Bug2: posthoc evaluation must use the real transcript
window, not the raw user_prompt_submit prompt text (which frequently contains
skill system prompts / task-notifications rather than the real conversation).

Scenarios:
- transcript exists + prompt_hash matches -> real window content returned.
- transcript missing -> None (posthoc skipped, not mislabeled irrelevant).
- prompt_hash has no matching user turn -> None.
- turn_index=None -> located via prompt_hash (not skipped).
- legacy bounded_window_ref (raw prompt text) -> not used as window; None.
- bounded_window_ref="session:..." ref -> None (no transcript path).
- bounded_window_ref outside allowed roots -> None (path traversal guard).
- injection_context is the trigger snapshot, never the raw prompt text.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest

from nokori.db import open_db
from nokori.events.fire import create_fire_event
from nokori.gate.marker import prompt_hash
from nokori.models import Rule
from nokori.posthoc.jobs import (
    _compute_window_from_transcript,
    _load_transcript_window,
    _parse_transcript_ref,
    build_evaluator_input,
)
from nokori.utils.prompt_text import normalize_prompt_for_hash
from nokori.utils.time import now_iso


def _make_db(tmp_path):
    return open_db(tmp_path / "rules.db")


def _insert_rule(db, *, rule_id=None, status="active") -> Rule:
    rule_id = rule_id or str(uuid.uuid4())
    short_id = rule_id[:6]
    now = now_iso()
    concepts = json.dumps(["concept_a"])
    required_concept_groups = json.dumps(["group_1"])
    excluded_contexts = json.dumps(["excluded_ctx"])

    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules "
            "(id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "status, severity, "
            "trigger_canonical, concepts, required_concept_groups, excluded_contexts, "
            "trigger_variants, "
            "action_instruction, "
            "domain_tags, tool_tags, path_patterns, "
            "source_origin, project_scope, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                rule_id,
                short_id,
                1,
                1,
                "pipeline_v1",
                "policy_v1",
                status,
                "reminder",
                "trigger text here",
                concepts,
                required_concept_groups,
                excluded_contexts,
                json.dumps(["variant_a"]),
                "do the action",
                json.dumps(["domain_web"]),
                json.dumps(["tool_git"]),
                json.dumps(["src/**"]),
                "transcript_extraction",
                "project",
                now,
                now,
            ),
        )

    return Rule(
        id=rule_id,
        short_id=short_id,
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="pipeline_v1",
        runtime_policy_version="policy_v1",
        last_rewritten_by_role=None,
        status=status,
        severity="reminder",
        trigger_canonical="trigger text here",
        concepts=concepts,
        required_concept_groups=required_concept_groups,
        excluded_contexts=excluded_contexts,
        near_miss_examples=[],
        trigger_variants=["variant_a"],
        action_instruction="do the action",
        domain_tags=["domain_web"],
        tool_tags=["tool_git"],
        path_patterns=["src/**"],
        quality_score=0.0,
        evidence_support_score=0.0,
        specificity_score=0.0,
        retrieval_readiness_score=0.0,
        observed_usefulness_score=0.0,
        plausible_usefulness_score=0.0,
        false_positive_score=0.0,
        harmful_score=0.0,
        source_origin="transcript_extraction",
        activation_origin=None,
        first_observed_useful_at=None,
        trusted_at=None,
        suppressed_at=None,
        project_scope="project",
        project_id=None,
        archived_reason=None,
        replacement_id=None,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture(autouse=True)
def _allow_tmp_transcript_roots(tmp_path, monkeypatch):
    """Permit transcript paths under tmp_path (is_path_allowed checks _allowed_roots)."""
    monkeypatch.setattr(
        "nokori.utils.transcript._allowed_roots",
        lambda: [tmp_path],
    )


def _write_transcript(
    tmp_path: Path, session_id: str, user_prompt: str, assistant_reply: str | None = None
) -> Path:
    """Write a real JSONL transcript with a user turn + optional assistant turn."""
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = transcript_dir / f"{session_id}.jsonl"
    lines = [json.dumps({"role": "user", "content": user_prompt})]
    if assistant_reply is not None:
        lines.append(json.dumps({"role": "assistant", "content": assistant_reply}))
    transcript_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return transcript_path


# ---------------------------------------------------------------------------
# _parse_transcript_ref
# ---------------------------------------------------------------------------


class TestParseTranscriptRef:
    def test_extracts_path_from_transcript_prefix(self, tmp_path):
        path = tmp_path / "t.jsonl"
        path.write_text("{}", encoding="utf-8")  # must exist; _parse_transcript_ref checks is_file
        ref = f"transcript:{path}"
        assert _parse_transcript_ref(ref) == path

    def test_returns_none_for_legacy_prompt_text(self):
        # Old-format bounded_window_ref stored raw prompt text (>64 chars).
        legacy = "x" * 200
        assert _parse_transcript_ref(legacy) is None

    def test_returns_none_for_session_ref(self):
        assert _parse_transcript_ref("session:sid:prompt:abc") is None

    def test_returns_none_for_hex_hash(self):
        assert _parse_transcript_ref("0123456789abcdef") is None

    def test_returns_none_for_none(self):
        assert _parse_transcript_ref(None) is None

    def test_returns_none_for_empty(self):
        assert _parse_transcript_ref("") is None

    def test_returns_none_for_transcript_colon_only(self):
        assert _parse_transcript_ref("transcript:") is None

    def test_rejects_path_outside_allowed_roots(self, tmp_path, monkeypatch):
        # tmp_path is allowed by the autouse fixture; point roots elsewhere.
        monkeypatch.setattr(
            "nokori.utils.transcript._allowed_roots",
            lambda: [tmp_path / "elsewhere"],
        )
        path = tmp_path / "t.jsonl"
        path.write_text("{}\n", encoding="utf-8")  # exists so only allowed-root rejects
        assert _parse_transcript_ref(f"transcript:{path}") is None


# ---------------------------------------------------------------------------
# _compute_window_from_transcript
# ---------------------------------------------------------------------------


class TestComputeWindowFromTranscript:
    def test_turn_index_none_with_duplicate_prompt_hash_anchors_first_match(self, tmp_path):
        # Symmetric to the session_end-side test: when turn_index is None and
        # the same prompt_hash appears twice, the prompt_hash scan anchors on
        # the first match. Guards against a regression that switches to the
        # last match.
        prompt = "repeated prompt about migrations"
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcript_dir / "s2-duplicate-none.jsonl"
        transcript.write_text(
            json.dumps({"role": "user", "content": prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok first duplicate"})
            + "\n"
            + json.dumps({"role": "user", "content": prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok second duplicate"})
            + "\n",
            encoding="utf-8",
        )
        ph = prompt_hash(normalize_prompt_for_hash(prompt))

        content = _compute_window_from_transcript(
            transcript,
            prompt_hash_value=ph,
            turn_index=None,
            injected_structured_snapshot=None,
        )
        assert content is not None
        # First-match anchor: the first duplicate's response must be present.
        # (Whether the second duplicate falls inside the bounded window depends
        # on topic-shift detection; the anchor choice itself is what's locked.)
        assert "ok first duplicate" in content

    def test_locates_injection_via_prompt_hash(self, tmp_path):
        user_prompt = "please review the database migration handler"
        transcript = _write_transcript(
            tmp_path, "s1", user_prompt, "I'll review the migration now."
        )
        ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

        content = _compute_window_from_transcript(
            transcript, ph, turn_index=None, injected_structured_snapshot=None
        )
        assert content is not None
        assert "database migration" in content
        assert "review the migration" in content  # assistant turn included

    def test_locates_injection_via_turn_index_when_present(self, tmp_path):
        # Two identical user turns: prompt_hash alone would find the first
        # occurrence, so turn_index (validated against the matching hash) must
        # win and anchor the second occurrence.
        prompt = "repeated prompt about migrations"
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcript_dir / "s2.jsonl"
        transcript.write_text(
            json.dumps({"role": "user", "content": prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok first duplicate"})
            + "\n"
            + json.dumps({"role": "user", "content": prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok second duplicate"})
            + "\n",
            encoding="utf-8",
        )
        ph = prompt_hash(normalize_prompt_for_hash(prompt))

        content = _compute_window_from_transcript(
            transcript,
            prompt_hash_value=ph,
            turn_index=2,
            injected_structured_snapshot=None,
        )
        assert content is not None
        assert "ok second duplicate" in content
        assert "ok first duplicate" not in content

    def test_accepts_string_turn_index_when_hash_matches(self, tmp_path):
        # turn_index may arrive as a string from hook payloads / SQLite rows;
        # it must be coerced to int and, when prompt_hash is available,
        # validated against the candidate turn's hash.
        user_prompt = "second prompt about migrations"
        transcript = _write_transcript(
            tmp_path, "s2-string", user_prompt, "ok second"
        )
        ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

        content = _compute_window_from_transcript(
            transcript,
            ph,
            turn_index="0",
            injected_structured_snapshot=None,
        )
        assert content is not None
        assert "second prompt about migrations" in content
        assert "ok second" in content

    def test_turn_index_hash_mismatch_falls_back_to_prompt_hash(self, tmp_path):
        # turn_index points at a turn whose prompt_hash does NOT match the
        # fire event's hash — implementation must reject it and fall back to
        # the prompt_hash scan rather than anchoring the window on the wrong turn.
        wrong_prompt = "first unrelated prompt about recipes"
        actual_prompt = "second prompt about migrations"
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcript_dir / "s2-mismatch.jsonl"
        transcript.write_text(
            json.dumps({"role": "user", "content": wrong_prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok first"})
            + "\n"
            + json.dumps({"role": "user", "content": actual_prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok second"})
            + "\n",
            encoding="utf-8",
        )
        ph = prompt_hash(normalize_prompt_for_hash(actual_prompt))

        content = _compute_window_from_transcript(
            transcript,
            ph,
            turn_index=0,  # points at wrong_prompt, hash won't match
            injected_structured_snapshot=None,
        )
        assert content is not None
        assert actual_prompt in content
        assert "ok second" in content
        assert wrong_prompt not in content

    def test_turn_index_non_user_falls_back_to_prompt_hash(self, tmp_path):
        # turn_index points at an assistant turn; the triggering prompt is a
        # later user turn — implementation must reject the non-user index and
        # fall back to the prompt_hash scan.
        actual_prompt = "second prompt about migrations"
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcript_dir / "s2-non-user.jsonl"
        transcript.write_text(
            json.dumps({"role": "user", "content": "first prompt"})
            + "\n"
            + json.dumps({"role": "assistant", "content": "assistant turn"})
            + "\n"
            + json.dumps({"role": "user", "content": actual_prompt})
            + "\n"
            + json.dumps({"role": "assistant", "content": "ok second"})
            + "\n",
            encoding="utf-8",
        )
        ph = prompt_hash(normalize_prompt_for_hash(actual_prompt))

        content = _compute_window_from_transcript(
            transcript,
            ph,
            turn_index=1,  # points at assistant — must be rejected
            injected_structured_snapshot=None,
        )
        assert content is not None
        assert actual_prompt in content
        assert "ok second" in content
        assert "assistant turn" not in content

    def test_transcript_missing_returns_none(self, tmp_path):
        missing = tmp_path / "nonexistent.jsonl"
        assert (
            _compute_window_from_transcript(
                missing, "anyprompthash", None, None
            )
            is None
        )

    def test_no_matching_prompt_hash_returns_none(self, tmp_path):
        transcript = _write_transcript(
            tmp_path, "s3", "user asked about recipes", "here is a recipe"
        )
        # prompt_hash that won't match any user turn
        assert (
            _compute_window_from_transcript(
                transcript,
                "ffffffffffffffff",
                turn_index=None,
                injected_structured_snapshot=None,
            )
            is None
        )

    def test_empty_transcript_returns_none(self, tmp_path):
        transcript_dir = tmp_path / "transcripts"
        transcript_dir.mkdir(parents=True, exist_ok=True)
        transcript = transcript_dir / "empty.jsonl"
        transcript.write_text("", encoding="utf-8")
        assert (
            _compute_window_from_transcript(
                transcript, "anyprompthash", None, None
            )
            is None
        )

    def test_uses_tool_tags_from_structured_snapshot(self, tmp_path, monkeypatch):
        user_prompt = "run the git push command"
        transcript = _write_transcript(
            tmp_path,
            "s4",
            user_prompt,
            "running git push now",
        )
        ph = prompt_hash(normalize_prompt_for_hash(user_prompt))
        structured = json.dumps({"tool_tags": ["tool_git"]})

        # Capture the tool_tags passed to compute_event_window so the assertion
        # actually verifies the structured-snapshot extraction (without this, the
        # assertion would pass even if tool_tags were ignored).
        captured: dict = {}

        def fake_compute_event_window(session_turns, injection_turn_index, rule_tool_tags, embedding_fn=None):
            captured["tool_tags"] = rule_tool_tags
            return session_turns[injection_turn_index:]

        monkeypatch.setattr(
            "nokori.posthoc.jobs.compute_event_window",
            fake_compute_event_window,
        )

        content = _compute_window_from_transcript(
            transcript, ph, None, structured
        )
        assert content is not None
        assert captured["tool_tags"] == ["tool_git"]
        assert "git push" in content

    def test_malformed_structured_snapshot_does_not_crash(self, tmp_path):
        user_prompt = "do something"
        transcript = _write_transcript(tmp_path, "s5", user_prompt, "done")
        ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

        content = _compute_window_from_transcript(
            transcript, ph, None, "not valid json {{{"
        )
        assert content is not None


# ---------------------------------------------------------------------------
# _load_transcript_window (integration with db + posthoc_jobs)
# ---------------------------------------------------------------------------


class TestLoadTranscriptWindow:
    def test_uses_redacted_window_json_when_present(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            user_prompt = "explain the error handling strategy"
            transcript = _write_transcript(
                tmp_path, "s6", user_prompt, "here is the strategy"
            )
            ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

            eid = create_fire_event(
                db,
                rule,
                "s6",
                ph,
                "hot",
                {"score": 0.9},
                turn_index=0,
                bounded_window_ref=f"transcript:{transcript}",
            )
            # Simulate session_end having precomputed the window.
            # Length intentionally exceeds _MIN_REDACTED_WINDOW_LEN so the
            # redacted_window_json guard accepts it as real content.
            precomputed = (
                "[Turn 0] user: precomputed window content that is long enough "
                "to pass the redacted_window_json content length guard. "
                "Padding to comfortably exceed the minimum length threshold "
                "regardless of small changes to that constant."
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at, redacted_window_json) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    ("job-1", eid, "hash1", "pending", 0, now_iso(), now_iso(), precomputed),
                )

            content = _load_transcript_window(
                db, eid, "s6", ph, 0, f"transcript:{transcript}", None, None
            )
            assert content == precomputed
        finally:
            db.close()

    def test_falls_back_to_transcript_when_redacted_window_null(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            user_prompt = "describe the caching layer"
            transcript = _write_transcript(
                tmp_path, "s7", user_prompt, "the cache uses redis"
            )
            ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

            eid = create_fire_event(
                db,
                rule,
                "s7",
                ph,
                "hot",
                {"score": 0.9},
                turn_index=None,  # UserPromptSubmit has no turn_index
                bounded_window_ref=f"transcript:{transcript}",
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("job-2", eid, "hash2", "pending", 0, now_iso(), now_iso()),
                )

            content = _load_transcript_window(
                db, eid, "s7", ph, None, f"transcript:{transcript}", None, None
            )
            assert content is not None
            assert "caching layer" in content
            assert "redis" in content
        finally:
            db.close()

    def test_legacy_prompt_text_ref_returns_none(self, tmp_path):
        """Old-format bounded_window_ref (raw prompt text) must NOT be used as window."""
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            # Simulate a polluted prompt: skill system prompt text.
            polluted_prompt = (
                "<system>Skill system prompt with task-notification content "
                "that is not the real conversation.</system>"
            )
            eid = create_fire_event(
                db,
                rule,
                "s8",
                "somehash",
                "hot",
                {"score": 0.9},
                bounded_window_ref=polluted_prompt,
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("job-3", eid, "hash3", "pending", 0, now_iso(), now_iso()),
                )

            content = _load_transcript_window(
                db, eid, "s8", "somehash", None, polluted_prompt, None, None
            )
            # Must NOT return the polluted prompt text.
            assert content is None
        finally:
            db.close()

    def test_session_ref_returns_none(self, tmp_path):
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            eid = create_fire_event(
                db,
                rule,
                "s9",
                "hashabc",
                "hot",
                {"score": 0.9},
                bounded_window_ref="session:s9:prompt:hashabc",
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("job-4", eid, "hash4", "pending", 0, now_iso(), now_iso()),
                )

            assert (
                _load_transcript_window(
                    db, eid, "s9", "hashabc", None,
                    "session:s9:prompt:hashabc", None, None,
                )
                is None
            )
        finally:
            db.close()


# ---------------------------------------------------------------------------
# build_evaluator_input: injection_context never uses raw prompt text (AC1, AC3)
# ---------------------------------------------------------------------------


class TestBuildEvaluatorInputCleanContext:
    def test_injection_context_is_trigger_snapshot_not_prompt_text(self, tmp_path):
        """AC1/AC3: injection_context must be the clean trigger snapshot, never
        the raw user_prompt_submit prompt text (which may contain skill system
        prompts)."""
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            user_prompt = "how do I handle the database migration"
            transcript = _write_transcript(
                tmp_path, "s10", user_prompt, "here is how to handle migrations"
            )
            ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

            eid = create_fire_event(
                db,
                rule,
                "s10",
                ph,
                "hot",
                {"score": 0.9},
                turn_index=0,
                bounded_window_ref=f"transcript:{transcript}",
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("job-5", eid, "hash5", "pending", 0, now_iso(), now_iso()),
                )

            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (eid,)
            )
            result = build_evaluator_input(db, dict(row))
            assert result is not None

            # injection_context equals trigger_snapshot (clean rule data),
            # NOT the user_prompt (which is in the transcript only).
            assert result["injection_context"] == rule.trigger_canonical
            assert result["injection_context"] != user_prompt

            # transcript_window is the real conversation, not the prompt text.
            assert "database migration" in result["transcript_window"]
            assert "handle migrations" in result["transcript_window"]
        finally:
            db.close()

    def test_returns_none_when_transcript_missing(self, tmp_path):
        """When transcript file is gone, posthoc must skip (None), not fall back
        to the raw prompt text and mislabel the rule irrelevant."""
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            # Reference a transcript path that does not exist on disk.
            missing_transcript = tmp_path / "transcripts" / "missing.jsonl"
            eid = create_fire_event(
                db,
                rule,
                "s11",
                "someprompthash",
                "hot",
                {"score": 0.9},
                bounded_window_ref=f"transcript:{missing_transcript}",
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("job-6", eid, "hash6", "pending", 0, now_iso(), now_iso()),
                )

            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (eid,)
            )
            assert build_evaluator_input(db, dict(row)) is None
        finally:
            db.close()

    def test_turn_index_none_locates_via_prompt_hash(self, tmp_path):
        """AC2: fire event turn_index=None must still resolve a real window
        via prompt_hash, not be skipped."""
        db = _make_db(tmp_path)
        try:
            rule = _insert_rule(db)
            user_prompt = "explain the authentication flow"
            transcript = _write_transcript(
                tmp_path, "s12", user_prompt, "the auth flow uses jwt"
            )
            ph = prompt_hash(normalize_prompt_for_hash(user_prompt))

            eid = create_fire_event(
                db,
                rule,
                "s12",
                ph,
                "hot",
                {"score": 0.9},
                turn_index=None,  # UserPromptSubmit has no turn_index
                bounded_window_ref=f"transcript:{transcript}",
            )
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO posthoc_jobs "
                    "(id, fire_event_id, window_payload_hash, status, retries, "
                    "created_at, updated_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    ("job-7", eid, "hash7", "pending", 0, now_iso(), now_iso()),
                )

            row = db.fetchone(
                "SELECT * FROM rule_fire_events WHERE id = ?", (eid,)
            )
            result = build_evaluator_input(db, dict(row))
            assert result is not None
            assert "authentication flow" in result["transcript_window"]
            assert "jwt" in result["transcript_window"]
        finally:
            db.close()
