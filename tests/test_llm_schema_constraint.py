"""Tests for LLM schema-level generation-time constraint (response_format: json_schema).

Covers PRD requirements R1-R7:
- R1: call_llm_role sends json_schema response_format for each cold-path role.
- R2: loose mode (no strict: true).
- R3: HTTP 400/422 triggers one-shot downgrade to json_object.
- R4: complete_role / complete_messages keep json_object default.
- R7: downgrade is logged.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nokori.cold._llm_call import call_llm_role
from nokori.cold.roles import (
    ROLE_IDS,
    ROLE_SCHEMAS,
    _build_role_response_format,
)
from nokori.db import Db, open_db
from nokori.errors import LlmError, LlmRateLimitError, LlmTimeoutError

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db(tmp_path: Path) -> Db:
    return open_db(tmp_path / "test_schema_constraint.db")


def _admission_json() -> str:
    return json.dumps({
        "scores": {
            "overall_quality": 0.92,
            "evidence_support": 0.93,
            "trigger_specificity": 0.90,
            "action_clarity": 0.85,
            "scope_control": 0.88,
            "generalization_safety": 0.87,
            "retrieval_readiness": 0.86,
        },
        "decision": "accept",
        "reasoning": "Test reasoning.",
    })


# ---------------------------------------------------------------------------
# Step 4 case 1: _build_role_response_format
# ---------------------------------------------------------------------------


class TestBuildRoleResponseFormat:
    def test_returns_json_schema_wrapper_for_each_role(self):
        for role in ROLE_IDS:
            rf = _build_role_response_format(role)
            assert rf is not None
            assert rf["type"] == "json_schema"
            assert "json_schema" in rf
            inner = rf["json_schema"]
            assert inner["name"] == role
            assert inner["schema"] is ROLE_SCHEMAS[role]

    def test_does_not_set_strict(self):
        # R2: loose mode — no `strict: true` anywhere in the wrapper.
        for role in ROLE_IDS:
            rf = _build_role_response_format(role)
            assert rf is not None
            assert "strict" not in rf["json_schema"]
            assert rf["json_schema"].get("strict") is not True

    def test_unknown_role_returns_none(self):
        assert _build_role_response_format("nonexistent_role") is None

    def test_returns_new_dict_each_call(self):
        # IMMUTABILITY: helper must not return a cached shared dict.
        a = _build_role_response_format("admission_judge")
        b = _build_role_response_format("admission_judge")
        assert a == b
        assert a is not b
        assert a["json_schema"]["schema"] is b["json_schema"]["schema"]  # schema dict itself is shared read-only


# ---------------------------------------------------------------------------
# Step 4 case 2: call_llm_role passes json_schema on first attempt
# ---------------------------------------------------------------------------


class TestCallLlmRolePassesJsonSchema:
    def test_first_call_uses_json_schema_response_format(self, db: Db):
        captured: list[dict] = []

        def _call_raw(**kwargs):
            captured.append(kwargs)
            return _admission_json()

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        response = call_llm_role(
            db, llm,
            role="admission_judge", model_id="m1",
            system="admission judge", user="u",
            max_tokens=1000, timeout=30,
            validate_response=lambda raw: json.loads(raw),
        )

        assert response == _admission_json()
        assert len(captured) == 1
        rf = captured[0]["response_format"]
        assert rf is not None
        assert rf["type"] == "json_schema"
        assert rf["json_schema"]["name"] == "admission_judge"
        assert "strict" not in rf["json_schema"]


# ---------------------------------------------------------------------------
# Step 4 case 3: HTTP 400/422 triggers downgrade to json_object
# ---------------------------------------------------------------------------


class TestSchemaDowngrade:
    def test_http_400_triggers_json_object_downgrade(self, db: Db, caplog):
        captured: list[dict] = []

        def _call_raw(**kwargs):
            captured.append(kwargs)
            if len(captured) == 1:
                # First attempt: backend rejects json_schema with HTTP 400.
                raise LlmError("HTTP 400", status_code=400)
            # Second attempt (downgraded): succeeds.
            return _admission_json()

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        with caplog.at_level(logging.WARNING, logger="nokori.cold.pipeline"):
            response = call_llm_role(
                db, llm,
                role="admission_judge", model_id="m1",
                system="admission judge", user="u",
                max_tokens=1000, timeout=30,
                validate_response=lambda raw: json.loads(raw),
            )

        assert response == _admission_json()
        assert len(captured) == 2
        # First attempt sent json_schema.
        assert captured[0]["response_format"]["type"] == "json_schema"
        # Second attempt downgraded to json_object.
        assert captured[1]["response_format"] == {"type": "json_object"}
        # R7: downgrade was logged.
        downgrade_logs = [r for r in caplog.records if "downgrad" in r.getMessage().lower()]
        assert downgrade_logs, "expected a downgrade warning log"

    def test_http_422_triggers_json_object_downgrade(self, db: Db):
        captured: list[dict] = []

        def _call_raw(**kwargs):
            captured.append(kwargs)
            if len(captured) == 1:
                raise LlmError("HTTP 422", status_code=422)
            return _admission_json()

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        response = call_llm_role(
            db, llm,
            role="final_judge", model_id="m1",
            system="final judge", user="u",
            max_tokens=1000, timeout=30,
            validate_response=lambda raw: json.loads(raw),
        )

        assert response == _admission_json()
        assert len(captured) == 2
        assert captured[0]["response_format"]["type"] == "json_schema"
        assert captured[1]["response_format"] == {"type": "json_object"}

    def test_downgrade_only_happens_once(self, db: Db):
        # If json_object also fails with HTTP 400, must not loop — let the
        # error propagate through the normal retry path.
        call_count = {"n": 0}

        def _call_raw(**kwargs):
            call_count["n"] += 1
            # Always raise 400; downgrade happens once, then the downgraded
            # attempt also fails and should propagate.
            raise LlmError("HTTP 400", status_code=400)

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        with pytest.raises(LlmError) as excinfo:
            call_llm_role(
                db, llm,
                role="admission_judge", model_id="m1",
                system="admission judge", user="u",
                max_tokens=1000, timeout=30,
                validate_response=lambda raw: json.loads(raw),
            )

        # First attempt (json_schema) + 2 immediate retries on downgraded
        # json_object attempt = 3 total calls. Downgrade triggers exactly once
        # after the first failure.
        assert excinfo.value.status_code == 400
        assert call_count["n"] == 3


# ---------------------------------------------------------------------------
# Step 4 case 4: non-downgradable errors flow through existing retry path
# ---------------------------------------------------------------------------


class TestNonDowngradableErrors:
    def test_http_429_does_not_trigger_downgrade(self, db: Db):
        captured: list[dict] = []

        def _call_raw(**kwargs):
            captured.append(kwargs)
            raise LlmRateLimitError("HTTP 429", status_code=429)

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        with pytest.raises(LlmRateLimitError):
            call_llm_role(
                db, llm,
                role="admission_judge", model_id="m1",
                system="admission judge", user="u",
                max_tokens=1000, timeout=30,
                validate_response=lambda raw: json.loads(raw),
            )

        # No downgrade: every attempt used json_schema.
        assert len(captured) == 2  # _MAX_IMMEDIATE_RETRIES = 2
        for call_kwargs in captured:
            assert call_kwargs["response_format"]["type"] == "json_schema"

    def test_timeout_does_not_trigger_downgrade(self, db: Db):
        captured: list[dict] = []

        def _call_raw(**kwargs):
            captured.append(kwargs)
            raise LlmTimeoutError("timed out")

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        with pytest.raises(LlmTimeoutError):
            call_llm_role(
                db, llm,
                role="admission_judge", model_id="m1",
                system="admission judge", user="u",
                max_tokens=1000, timeout=30,
                validate_response=lambda raw: json.loads(raw),
            )

        # LlmTimeoutError has status_code=None — not 400/422, so no downgrade.
        assert len(captured) == 2
        for call_kwargs in captured:
            assert call_kwargs["response_format"]["type"] == "json_schema"

    def test_http_500_does_not_trigger_downgrade(self, db: Db):
        captured: list[dict] = []

        def _call_raw(**kwargs):
            captured.append(kwargs)
            raise LlmError("HTTP 500", status_code=500)

        llm = MagicMock()
        llm.call_raw = MagicMock(side_effect=_call_raw)

        with pytest.raises(LlmError):
            call_llm_role(
                db, llm,
                role="admission_judge", model_id="m1",
                system="admission judge", user="u",
                max_tokens=1000, timeout=30,
                validate_response=lambda raw: json.loads(raw),
            )

        assert len(captured) == 2
        for call_kwargs in captured:
            assert call_kwargs["response_format"]["type"] == "json_schema"


# ---------------------------------------------------------------------------
# Step 4 case 5: complete_role / complete_messages default to json_object
# ---------------------------------------------------------------------------


def _make_cfg():
    from pathlib import Path

    from nokori.config import Config

    return Config(
        data_dir=Path("/tmp/nokori-test"),
        max_injection_chars=1500,
        gate_enabled=False,
        gate_ttl_seconds=600,
        gate_matcher="",
        extract_mode="manual",
        extract_defer_when_active=False,
        extract_fork_cache=False,
        llm_base_url="http://llm.test/v1",
        llm_model="default-model",
        llm_api_key="sk-test",
        embed_enabled=False,
        embed_base_url=None,
        embed_model=None,
        embed_api_key=None,
        embed_dimensions=0,
        embed_chunk_size=4000,
        embed_chunk_count=2,
        embed_chunk_size_configured=False,
        embed_chunk_count_configured=False,
        embed_hook_timeout_seconds=2,
        embed_server_idle_seconds=3600,
        embed_server_auto_start=True,
        hot_cache_enabled=False,
        session_idle_seconds=1800,
        promotion_enabled=False,
        strict=False,
        disabled=False,
        dismiss_phrase="dismiss",
        role_models={},
        role_max_tokens={},
        role_timeouts={},
        log_level="warn",
    )


class TestCompleteRoleDefaultsToJsonObject:
    def test_complete_role_uses_json_object(self, monkeypatch):
        monkeypatch.delenv("NOKORI_EXTRACTING", raising=False)
        captured: dict = {}

        def fake_open(req, timeout=30):
            captured["body"] = json.loads(req.data.decode("utf-8"))

            class Resp:
                def read(self):
                    return json.dumps(
                        {"choices": [{"message": {"content": "{}"}}]}
                    ).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return Resp()

        from nokori.llm.adapter import LLMAdapter

        adapter = LLMAdapter(_make_cfg(), http_open=fake_open)
        adapter.complete_role("admission_judge", "sys", "user")

        # R4: complete_role path keeps json_object default.
        assert captured["body"]["response_format"] == {"type": "json_object"}

    def test_complete_messages_uses_json_object(self, monkeypatch):
        monkeypatch.delenv("NOKORI_EXTRACTING", raising=False)
        captured: dict = {}

        def fake_open(req, timeout=30):
            captured["body"] = json.loads(req.data.decode("utf-8"))

            class Resp:
                def read(self):
                    return json.dumps(
                        {"choices": [{"message": {"content": "{}"}}]}
                    ).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return Resp()

        from nokori.llm.adapter import LLMAdapter

        adapter = LLMAdapter(_make_cfg(), http_open=fake_open)
        adapter.complete_messages("sys", "user")

        assert captured["body"]["response_format"] == {"type": "json_object"}

    def test_call_raw_without_response_format_uses_json_object(self, monkeypatch):
        monkeypatch.delenv("NOKORI_EXTRACTING", raising=False)
        captured: dict = {}

        def fake_open(req, timeout=30):
            captured["body"] = json.loads(req.data.decode("utf-8"))

            class Resp:
                def read(self):
                    return json.dumps(
                        {"choices": [{"message": {"content": "{}"}}]}
                    ).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return Resp()

        from nokori.llm.adapter import LLMAdapter

        adapter = LLMAdapter(_make_cfg(), http_open=fake_open)
        adapter.call_raw(model="m", system="sys", user="u", max_tokens=100, timeout=10)

        # call_raw with response_format=None falls back to json_object default
        # inside _call_openai_compatible.
        assert captured["body"]["response_format"] == {"type": "json_object"}

    def test_call_raw_forwards_explicit_response_format(self, monkeypatch):
        monkeypatch.delenv("NOKORI_EXTRACTING", raising=False)
        captured: dict = {}

        def fake_open(req, timeout=30):
            captured["body"] = json.loads(req.data.decode("utf-8"))

            class Resp:
                def read(self):
                    return json.dumps(
                        {"choices": [{"message": {"content": "{}"}}]}
                    ).encode("utf-8")

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    pass

            return Resp()

        from nokori.llm.adapter import LLMAdapter

        adapter = LLMAdapter(_make_cfg(), http_open=fake_open)
        custom_rf = {"type": "json_schema", "json_schema": {"name": "x", "schema": {}}}
        adapter.call_raw(
            model="m", system="sys", user="u",
            max_tokens=100, timeout=10, response_format=custom_rf,
        )

        assert captured["body"]["response_format"] == custom_rf


# ---------------------------------------------------------------------------
# Bonus: LlmError status_code attribute behavior
# ---------------------------------------------------------------------------


class TestLlmErrorStatusCode:
    def test_default_status_code_is_none(self):
        e = LlmError("boom")
        assert e.status_code is None

    def test_status_code_preserved_through_subclass(self):
        e = LlmRateLimitError("HTTP 429", status_code=429)
        assert e.status_code == 429
        assert isinstance(e, LlmError)

    def test_status_code_none_on_timeout(self):
        e = LlmTimeoutError("timed out")
        assert e.status_code is None
