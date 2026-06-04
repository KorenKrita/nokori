"""Test session_end posthoc job enqueue behavior (replaces old async extract defer)."""
from unittest.mock import patch

from nokori.config import Config
from nokori.hooks import session_end
from nokori.utils import sessions
from nokori.utils.host import Host


def test_session_end_enqueues_posthoc_for_session(monkeypatch, tmp_path):
    """session_end always enqueues posthoc jobs for the ending session."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()

    # Register and then end a session to verify posthoc enqueue is called
    sessions.register(cfg, "ending-session", "proj")

    payload = {
        "session_id": "ending-session",
        "cwd": str(tmp_path),
    }
    with patch("nokori.hooks.session_end.enqueue_posthoc_for_session") as mock_enqueue:
        session_end.handle(payload, cfg, host=Host.CLAUDE)

    mock_enqueue.assert_called_once()
    # The session_id is passed to the enqueue function
    args = mock_enqueue.call_args
    assert args[0][1] == "ending-session"
