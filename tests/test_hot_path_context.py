"""Unit tests for nokori.hooks.context.HotPathContext."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from nokori.config import Config
from nokori.errors import DbError
from nokori.hooks.context import ErrorCategory, HotPathContext
from nokori.utils.host import Host


@pytest.fixture
def ctx_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    cfg.ensure_dirs()
    payload = {"session_id": "test-sess-1", "cwd": str(tmp_path)}
    return payload, cfg


class TestLifecycle:
    def test_enter_returns_self(self, ctx_env):
        payload, cfg = ctx_env
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        with ctx as c:
            assert c is ctx

    def test_exit_closes_db(self, ctx_env):
        payload, cfg = ctx_env
        mock_db = MagicMock()
        with (
            patch("nokori.hooks.context.open_db", return_value=mock_db),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            _ = ctx.db  # trigger lazy open
        mock_db.close.assert_called_once()

    def test_exit_without_db_access_no_close(self, ctx_env):
        """If db is never accessed, no close is attempted."""
        payload, cfg = ctx_env
        with (
            patch("nokori.hooks.context.open_db") as mock_open,
            HotPathContext(payload, cfg, host=Host.CLAUDE),
        ):
            pass
        mock_open.assert_not_called()

    def test_db_set_to_none_after_exit(self, ctx_env):
        payload, cfg = ctx_env
        mock_db = MagicMock()
        with patch("nokori.hooks.context.open_db", return_value=mock_db):
            ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
            with ctx:
                _ = ctx.db
            # After exit, db property should return None
            assert ctx.db is None


class TestLazyDb:
    def test_db_not_opened_until_first_access(self, ctx_env):
        payload, cfg = ctx_env
        with patch("nokori.hooks.context.open_db") as mock_open:
            mock_open.return_value = MagicMock()
            with HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx:
                mock_open.assert_not_called()
                _ = ctx.db
                mock_open.assert_called_once_with(cfg.db_path)

    def test_db_opened_only_once_on_multiple_accesses(self, ctx_env):
        payload, cfg = ctx_env
        mock_db = MagicMock()
        with patch("nokori.hooks.context.open_db", return_value=mock_db) as mock_open:
            with HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx:
                _ = ctx.db
                _ = ctx.db
                _ = ctx.db
            mock_open.assert_called_once()


class TestDbOpenFailure:
    def test_db_returns_none_on_failure(self, ctx_env):
        payload, cfg = ctx_env
        with (
            patch("nokori.hooks.context.open_db", side_effect=DbError("locked")),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            assert ctx.db is None

    def test_db_unavailable_flag_set_on_failure(self, ctx_env):
        payload, cfg = ctx_env
        with (
            patch("nokori.hooks.context.open_db", side_effect=DbError("locked")),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            _ = ctx.db
            assert ctx.db_unavailable is True

    def test_error_added_on_db_failure(self, ctx_env):
        payload, cfg = ctx_env
        with (
            patch("nokori.hooks.context.open_db", side_effect=DbError("timeout")),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            _ = ctx.db
            assert len(ctx.errors) == 1
            err = ctx.errors[0]
            assert err.subsystem == "db"
            assert err.category == ErrorCategory.DEGRADED
            assert "timeout" in err.message
            assert isinstance(err.exception, DbError)

    def test_db_unavailable_false_initially(self, ctx_env):
        payload, cfg = ctx_env
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        assert ctx.db_unavailable is False

    def test_db_returns_none_on_non_db_error(self, ctx_env):
        payload, cfg = ctx_env
        with (
            patch("nokori.hooks.context.open_db", side_effect=OSError("permission denied")),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            assert ctx.db is None
            assert ctx.db_unavailable is True
            assert ctx.errors[0].subsystem == "db"


class TestRecordEvent:
    def test_writes_event_when_db_available(self, ctx_env):
        payload, cfg = ctx_env
        mock_db = MagicMock()
        with (
            patch("nokori.hooks.context.open_db", return_value=mock_db),
            patch("nokori.hooks.context.write_event", return_value="evt-123") as mock_write,
            HotPathContext(payload, cfg, host=Host.CLAUDE, session_id="s1") as ctx,
        ):
            result = ctx.record_event("test_hook", "ok", details={"k": "v"})
            assert result == "evt-123"
            mock_write.assert_called_once_with(
                mock_db,
                source="test_hook",
                outcome="ok",
                session_id="s1",
                prompt_snippet=None,
                details={"k": "v"},
            )

    def test_returns_none_when_db_unavailable(self, ctx_env):
        payload, cfg = ctx_env
        with (
            patch("nokori.hooks.context.open_db", side_effect=DbError("nope")),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            result = ctx.record_event("test_hook", "ok")
            assert result is None

    def test_returns_none_when_write_event_raises(self, ctx_env):
        payload, cfg = ctx_env
        mock_db = MagicMock()
        with (
            patch("nokori.hooks.context.open_db", return_value=mock_db),
            patch("nokori.hooks.context.write_event", side_effect=Exception("write fail")),
            HotPathContext(payload, cfg, host=Host.CLAUDE) as ctx,
        ):
            result = ctx.record_event("hook", "ok")
            assert result is None
            assert len(ctx.errors) == 1
            assert ctx.errors[0].subsystem == "observability"
            assert ctx.errors[0].category == ErrorCategory.DEGRADED
            assert ctx.db_unavailable is False

    def test_prompt_snippet_forwarded(self, ctx_env):
        payload, cfg = ctx_env
        mock_db = MagicMock()
        with (
            patch("nokori.hooks.context.open_db", return_value=mock_db),
            patch("nokori.hooks.context.write_event") as mock_write,
            HotPathContext(payload, cfg, host=Host.CLAUDE, session_id="s2") as ctx,
        ):
            ctx.record_event("hook", "ok", prompt_snippet="hello")
            _, kwargs = mock_write.call_args
            assert kwargs["prompt_snippet"] == "hello"


class TestAddError:
    def test_appends_to_errors_list(self, ctx_env):
        payload, cfg = ctx_env
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        ctx.add_error("retrieval", ErrorCategory.DEGRADED, "search failed")
        ctx.add_error("gate", ErrorCategory.TERMINAL, "schema mismatch")
        assert len(ctx.errors) == 2

    def test_error_fields_correct(self, ctx_env):
        payload, cfg = ctx_env
        exc = ValueError("bad")
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        ctx.add_error("posthoc", ErrorCategory.RETRIABLE, "locked", exc)
        err = ctx.errors[0]
        assert err.subsystem == "posthoc"
        assert err.category == ErrorCategory.RETRIABLE
        assert err.message == "locked"
        assert err.exception is exc

    def test_error_without_exception(self, ctx_env):
        payload, cfg = ctx_env
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        ctx.add_error("embed", ErrorCategory.DEGRADED, "timeout")
        assert ctx.errors[0].exception is None


class TestSessionId:
    def test_explicit_session_id_used(self, ctx_env):
        payload, cfg = ctx_env
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE, session_id="explicit-id")
        assert ctx.session_id == "explicit-id"

    def test_session_id_from_payload(self, ctx_env):
        payload, cfg = ctx_env
        payload["session_id"] = "from-payload"
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        assert ctx.session_id == "from-payload"

    def test_session_id_defaults_when_missing(self, ctx_env):
        payload, cfg = ctx_env
        del payload["session_id"]
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE)
        assert ctx.session_id == "-"

    def test_empty_string_session_id_falls_through_to_payload(self, ctx_env):
        payload, cfg = ctx_env
        payload["session_id"] = "from-payload"
        ctx = HotPathContext(payload, cfg, host=Host.CLAUDE, session_id="")
        assert ctx.session_id == "from-payload"
