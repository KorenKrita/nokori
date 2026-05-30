"""Tests for shadow pool, async extract, and local embedding features."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from nokori.config import Config
from nokori.db import open_db, fetch_shadow_rules


def _utcnow_iso(delta_days: int = 0) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _make_rule(db, *, id_, status="active", source_type="correction",
               confidence="high", project_id=None, project_scope="project"):
    short = id_[:6]
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, trigger_text, action, source_type, "
            "confidence, status, project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (id_, short, f"trigger {id_}", f"action {id_}",
             source_type, confidence, status, project_scope, project_id, now, now),
        )


# --- Shadow Pool Tests ---

class TestFetchShadowRules:
    def test_returns_other_project_high_confidence_active(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-mine", project_id="my-proj")
            _make_rule(db, id_="rule-other", project_id="other-proj")
            results = fetch_shadow_rules(db, project_id="my-proj")
            ids = [r.id for r in results]
            assert "rule-other" in ids
            assert "rule-mine" not in ids
        finally:
            db.close()

    def test_excludes_preference(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="pref-other", project_id="other-proj",
                       source_type="preference")
            results = fetch_shadow_rules(db, project_id="my-proj")
            assert len(results) == 0
        finally:
            db.close()

    def test_excludes_medium_confidence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="med-other", project_id="other-proj",
                       confidence="medium")
            results = fetch_shadow_rules(db, project_id="my-proj")
            assert len(results) == 0
        finally:
            db.close()

    def test_excludes_global_scope(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="glob-other", project_id="other-proj",
                       project_scope="global")
            results = fetch_shadow_rules(db, project_id="my-proj")
            assert len(results) == 0
        finally:
            db.close()

    def test_returns_empty_when_no_project_id(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-x", project_id="proj-x")
            results = fetch_shadow_rules(db, project_id=None)
            assert results == []
        finally:
            db.close()


class TestShadowPoolHotTier:
    def test_shadow_pool_skips_non_hot_matches(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_PROMOTION_ENABLED", "1")
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            now = _utcnow_iso()
            for rid, trig in (
                ("rule-a", "deploy prisma schema migration"),
                ("rule-b", "deploy prisma schema version"),
            ):
                with db.transaction() as tx:
                    tx.execute(
                        "INSERT INTO rules (id, short_id, trigger_text, action, "
                        "source_type, confidence, status, project_scope, project_id, "
                        "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            rid, rid[:6], trig, f"action {rid}",
                            "correction", "high", "active", "project", "other-proj",
                            now, now,
                        ),
                    )
            from nokori.hooks.user_prompt_submit import _run_shadow_pool
            _run_shadow_pool(db, "deploy prisma schema", "my-proj", cfg, pool_size=0)
            for rid in ("rule-a", "rule-b"):
                row = db.fetchone(
                    "SELECT cross_project_hits FROM rules WHERE id = ?", (rid,)
                )
                assert row["cross_project_hits"] == 0
        finally:
            db.close()

    def test_shadow_pool_records_dominant_hot(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_PROMOTION_ENABLED", "1")
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            now = _utcnow_iso()
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO rules (id, short_id, trigger_text, action, "
                    "source_type, confidence, status, project_scope, project_id, "
                    "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "rule-strong", "rst001",
                        "never git force push remote",
                        "use regular push",
                        "correction", "high", "active", "project", "other-proj",
                        now, now,
                    ),
                )
            from nokori.hooks.user_prompt_submit import _run_shadow_pool
            _run_shadow_pool(db, "git push force remote branch", "my-proj", cfg, pool_size=0)
            row = db.fetchone(
                "SELECT cross_project_hits, evidence_score FROM rules WHERE id = ?",
                ("rule-strong",),
            )
            assert row["cross_project_hits"] == 1
            assert row["evidence_score"] == 1
        finally:
            db.close()


class TestShadowHitEvidence:
    def test_shadow_hit_adds_evidence(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-ev", project_id="proj-A")
            from nokori.lifecycle.promotion import record_shadow_hit
            record_shadow_hit(db, "rule-ev", "proj-B")
            row = db.fetchone(
                "SELECT evidence_score, evidence_log FROM rules WHERE id = ?",
                ("rule-ev",),
            )
            assert row["evidence_score"] == 1
            log_entries = json.loads(row["evidence_log"])
            assert len(log_entries) == 1
            assert log_entries[0]["kind"] == "shadow_hot"
        finally:
            db.close()


# --- Async Extract Tests ---

class TestAsyncExtract:
    def test_async_mode_spawns_subprocess(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
        cfg = Config.from_env()

        transcript = tmp_path / "session.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": "hi"}) + "\n")

        payload = {
            "session_id": "s-async-1",
            "cwd": str(tmp_path),
            "transcript_path": str(transcript),
        }

        with patch("nokori.hooks.session_end.subprocess.Popen") as mock_popen, \
             patch("nokori.utils.project.subprocess.run", return_value=type("R", (), {"returncode": 1, "stdout": ""})()) as _:
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg)

        assert result == {"continue": True}
        mock_popen.assert_called_once()
        call_args = mock_popen.call_args
        assert call_args[0][0] == [sys.executable, "-m", "nokori", "extract"]
        env = call_args[1]["env"]
        assert env.get("NOKORI_EXTRACTING") != "1"
        assert env["NOKORI_DATA_DIR"] == str(tmp_path)

    def test_manual_mode_does_not_spawn(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "manual")
        cfg = Config.from_env()

        transcript = tmp_path / "session.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": "hi"}) + "\n")

        payload = {
            "session_id": "s-manual-1",
            "cwd": str(tmp_path),
            "transcript_path": str(transcript),
        }

        with patch("nokori.hooks.session_end.subprocess.Popen") as mock_popen, \
             patch("nokori.utils.project.subprocess.run", return_value=type("R", (), {"returncode": 1, "stdout": ""})()) as _:
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg)

        assert result == {"continue": True}
        mock_popen.assert_not_called()

    def test_async_spawn_failure_does_not_crash(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EXTRACT_MODE", "async")
        cfg = Config.from_env()

        transcript = tmp_path / "session.jsonl"
        transcript.write_text(json.dumps({"type": "user", "message": "hi"}) + "\n")

        payload = {
            "session_id": "s-async-fail",
            "cwd": str(tmp_path),
            "transcript_path": str(transcript),
        }

        with patch("nokori.hooks.session_end.subprocess.Popen",
                   side_effect=OSError("no such file")), \
             patch("nokori.utils.project.subprocess.run", return_value=type("R", (), {"returncode": 1, "stdout": ""})()) as _:
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg)

        assert result == {"continue": True}


# --- Local Embedding Tests ---

class TestLocalEmbedding:
    def test_auto_enabled_with_local_available(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=True):
            from nokori.search.embedding import auto_enabled
            assert auto_enabled(cfg, 20) is True
            assert auto_enabled(cfg, 19) is False

    def test_auto_enabled_without_local(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=False):
            from nokori.search.embedding import auto_enabled
            assert auto_enabled(cfg, 20) is False

    def test_auto_enabled_prefers_remote(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_EMBED_BASE_URL", "http://localhost:11434/v1")
        monkeypatch.setenv("NOKORI_EMBED_MODEL", "nomic-embed-text")
        cfg = Config.from_env()

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=True):
            from nokori.search.embedding import auto_enabled, use_local
            assert auto_enabled(cfg, 20) is True
            assert use_local(cfg) is False

    def test_use_local_true_when_no_remote(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=True):
            from nokori.search.embedding import use_local
            assert use_local(cfg) is True

    def test_local_client_available_check(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=False):
            from nokori.search.embedding import LocalEmbeddingClient
            client = LocalEmbeddingClient(cfg)
            assert client.available() is False

    def test_local_client_embed_calls_model(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        mock_model = MagicMock()
        # sentence-transformers returns numpy arrays, but tolist() is called
        mock_arr = MagicMock()
        mock_arr.tolist.return_value = [0.1] * 384
        mock_model.encode.return_value = [mock_arr]

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=True):
            from nokori.search.embedding import LocalEmbeddingClient
            client = LocalEmbeddingClient(cfg)
            client._model = mock_model
            vectors = client.embed("test text")
            assert len(vectors) == 1
            assert len(vectors[0]) == 384
            mock_model.encode.assert_called_once()