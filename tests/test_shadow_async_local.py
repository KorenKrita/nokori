"""Tests for shadow pool, async extract, and local embedding features."""
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch


from nokori.config import Config
from nokori.db import open_db, fetch_shadow_rules
from nokori.utils.host import Host


def _utcnow_iso(delta_days: int = 0) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc) + timedelta(days=delta_days)
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _make_rule(db, *, id_, status="active", source_origin="transcript_extraction",
               project_id=None, project_scope="project"):
    short = id_[:6]
    now = _utcnow_iso()
    with db.transaction() as tx:
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "source_origin, status, severity, "
            "project_scope, project_id, created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
            (id_, short, f"trigger {id_}", f"action {id_}",
             source_origin, status, "reminder",
             project_scope, project_id, now, now),
        )


# --- Shadow Pool Tests ---

class TestFetchShadowRules:
    def test_returns_candidate_rules_for_project(self, monkeypatch, tmp_path):
        """Shadow pool contains candidate rules visible to the project."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-cand", status="candidate", project_id="my-proj")
            _make_rule(db, id_="rule-active", status="active", project_id="my-proj")
            results = fetch_shadow_rules(db, project_id="my-proj")
            ids = [r.id for r in results]
            assert "rule-cand" in ids
            assert "rule-active" not in ids
        finally:
            db.close()

    def test_returns_suppressed_rules(self, monkeypatch, tmp_path):
        """Shadow pool includes suppressed rules (suppression_recovery)."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-supp", status="suppressed", project_id="my-proj")
            results = fetch_shadow_rules(db, project_id="my-proj")
            ids = [r.id for r in results]
            assert "rule-supp" in ids
        finally:
            db.close()

    def test_excludes_active_rules(self, monkeypatch, tmp_path):
        """Active rules are not in shadow pool (they are in formal pool)."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-active", status="active", project_id="my-proj")
            results = fetch_shadow_rules(db, project_id="my-proj")
            assert len(results) == 0
        finally:
            db.close()

    def test_excludes_archived_rules(self, monkeypatch, tmp_path):
        """Archived rules are not in shadow pool."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-arch", status="archived", project_id="my-proj")
            results = fetch_shadow_rules(db, project_id="my-proj")
            assert len(results) == 0
        finally:
            db.close()

    def test_returns_empty_when_no_project_id(self, monkeypatch, tmp_path):
        """Without project_id, returns all candidate/suppressed rules."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-x", status="candidate", project_id="proj-x")
            results = fetch_shadow_rules(db, project_id=None)
            # project_id=None returns all candidate/suppressed regardless of project
            ids = [r.id for r in results]
            assert "rule-x" in ids
        finally:
            db.close()


class TestShadowPoolHotTier:
    def test_shadow_pool_records_warm_matches(self, monkeypatch, tmp_path):
        """Shadow pool candidate rules get matched via BM25 and recorded as shadow events."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
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
                        "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                        "created_by_pipeline_version, runtime_policy_version, "
                        "trigger_canonical, action_instruction, "
                        "source_origin, status, severity, "
                        "project_scope, project_id, created_at, updated_at) "
                        "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
                        (
                            rid, rid[:6], trig, f"action {rid}",
                            "transcript_extraction", "candidate", "reminder",
                            "project", "my-proj",
                            now, now,
                        ),
                    )
            from nokori.hooks.user_prompt_submit import handle

            proj = tmp_path / "my-proj"
            proj.mkdir()
            with patch("nokori.utils.sessions.resolve_project_id_for_session",
                       return_value="my-proj"):
                handle({
                    "session_id": "s-shadow-warm",
                    "prompt": "deploy prisma schema",
                    "cwd": str(proj),
                }, cfg, host=Host.CLAUDE)
            # Shadow events should be recorded in rule_shadow_events
            events = db.fetchall(
                "SELECT rule_id FROM rule_shadow_events WHERE rule_id IN ('rule-a','rule-b')"
            )
            assert len(events) >= 1
        finally:
            db.close()

    def test_shadow_pool_records_dominant_hot(self, monkeypatch, tmp_path):
        """A dominant shadow match creates a shadow event."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            now = _utcnow_iso()
            with db.transaction() as tx:
                tx.execute(
                    "INSERT INTO rules (id, short_id, schema_version, rule_version, "
                    "created_by_pipeline_version, runtime_policy_version, "
                    "trigger_canonical, action_instruction, "
                    "source_origin, status, severity, "
                    "project_scope, project_id, created_at, updated_at) "
                    "VALUES (?,?,1,1,'v1','v1',?,?,?,?,?,?,?,?,?)",
                    (
                        "rule-strong", "rst001",
                        "never git force push remote",
                        "use regular push",
                        "transcript_extraction", "candidate", "reminder",
                        "project", "my-proj",
                        now, now,
                    ),
                )
            from nokori.hooks.user_prompt_submit import handle

            proj = tmp_path / "my-proj"
            proj.mkdir()
            with patch("nokori.utils.sessions.resolve_project_id_for_session",
                       return_value="my-proj"):
                handle({
                    "session_id": "s-shadow-hot",
                    "prompt": "git push force remote branch",
                    "cwd": str(proj),
                }, cfg, host=Host.CLAUDE)
            events = db.fetchall(
                "SELECT rule_id FROM rule_shadow_events WHERE rule_id = 'rule-strong'"
            )
            assert len(events) >= 1
        finally:
            db.close()


class TestShadowHitEvidence:
    def test_shadow_hit_adds_evidence(self, monkeypatch, tmp_path):
        """promotion.record_shadow_hit is now a no-op (returns False)."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()
        db = open_db(cfg.db_path)
        try:
            _make_rule(db, id_="rule-ev", project_id="proj-A")
            from nokori.lifecycle.promotion import record_shadow_hit
            result = record_shadow_hit(db, "rule-ev", "proj-B")
            assert result is False
        finally:
            db.close()


# --- Session End / Posthoc Enqueue Tests ---

class TestSessionEndPosthoc:
    def test_session_end_enqueues_posthoc(self, monkeypatch, tmp_path):
        """session_end.handle enqueues posthoc jobs and returns continue."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        payload = {
            "session_id": "s-async-1",
            "cwd": str(tmp_path),
        }

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session") as mock_enqueue:
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg, host=Host.CLAUDE)

        assert result == {"continue": True}
        mock_enqueue.assert_called_once()

    def test_session_end_disabled_does_not_enqueue(self, monkeypatch, tmp_path):
        """When disabled, session_end returns early without enqueuing."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        monkeypatch.setenv("NOKORI_DISABLED", "1")
        cfg = Config.from_env()

        payload = {
            "session_id": "s-manual-1",
            "cwd": str(tmp_path),
        }

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session") as mock_enqueue:
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg, host=Host.CLAUDE)

        assert result == {"continue": True}
        mock_enqueue.assert_not_called()

    def test_session_end_enqueue_failure_does_not_crash(self, monkeypatch, tmp_path):
        """If posthoc enqueue raises, session_end still returns continue."""
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        payload = {
            "session_id": "s-async-fail",
            "cwd": str(tmp_path),
        }

        with patch("nokori.hooks.session_end.enqueue_posthoc_for_session",
                   side_effect=RuntimeError("db error")):
            from nokori.hooks.session_end import handle
            result = handle(payload, cfg, host=Host.CLAUDE)

        assert result == {"continue": True}


# --- Local Embedding Tests ---

class TestLocalEmbedding:
    def test_auto_enabled_with_local_available(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        with patch("nokori.search.embedding.local_embed_package_available",
                   return_value=True):
            from nokori.search.embedding import auto_enabled
            assert auto_enabled(cfg, 20) is True
            assert auto_enabled(cfg, 19) is False

    def test_auto_enabled_without_local(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
        cfg = Config.from_env()

        with patch("nokori.search.embedding.local_embed_package_available",
                   return_value=False):
            with patch("nokori.search.embedding.local_model_cached", return_value=False):
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
        mock_model.encode_document.return_value = [mock_arr]

        with patch("nokori.search.embedding._sentence_transformers_available",
                   return_value=True):
            from nokori.search.embedding import LocalEmbeddingClient
            client = LocalEmbeddingClient(cfg)
            client._model = mock_model
            vectors = client.embed("test text", kind="document")
            assert len(vectors) == 1
            assert len(vectors[0]) == 384
            mock_model.encode_document.assert_called_once()
