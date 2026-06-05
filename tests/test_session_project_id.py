"""Session-scoped project_id cache (SessionStart + UserPromptSubmit / SessionEnd)."""
from nokori.config import Config
from nokori.utils import sessions


def test_git_fallback_does_not_overwrite_session_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sid = "sess-git-fallback"
    git_id = "repo-aaaaaaaa"
    cwd_hash_id = "repo-bbbbbbbb"

    sessions.register(cfg, sid, project_id=git_id, project_id_from_git=True)

    def fake_detailed(cwd):
        return cwd_hash_id, False

    monkeypatch.setattr(
        "nokori.utils.project.resolve_project_id_detailed",
        fake_detailed,
    )

    assert (
        sessions.resolve_project_id_for_session(cfg, sid, "/any/cwd")
        == git_id
    )
    assert sessions.resolve_project_id_for_session(cfg, sid, None) == git_id


def test_git_resolved_id_refreshes_when_repo_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sid = "sess-repo-switch"
    old_id = "old-repo-11111111"
    new_id = "new-repo-22222222"

    sessions.register(cfg, sid, project_id=old_id)

    def fake_detailed(cwd):
        return new_id, True

    monkeypatch.setattr(
        "nokori.utils.project.resolve_project_id_detailed",
        fake_detailed,
    )

    assert (
        sessions.resolve_project_id_for_session(cfg, sid, "/other/repo")
        == new_id
    )
    assert sessions.resolve_project_id_for_session(cfg, sid, None) == new_id


def test_non_git_cwd_change_refreshes_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sid = "sess-cwd-only"
    old_id = "dir-a-11111111"
    new_id = "dir-b-22222222"

    sessions.register(cfg, sid, project_id=old_id, project_id_from_git=False)

    def fake_detailed(cwd):
        return new_id, False

    monkeypatch.setattr(
        "nokori.utils.project.resolve_project_id_detailed",
        fake_detailed,
    )

    assert (
        sessions.resolve_project_id_for_session(cfg, sid, "/other/dir")
        == new_id
    )
    assert sessions.resolve_project_id_for_session(cfg, sid, None) == new_id


def test_no_cwd_returns_cached_project_id(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    sid = "sess-no-cwd"
    cached = "cached-proj-33333333"
    sessions.register(cfg, sid, project_id=cached)

    assert sessions.resolve_project_id_for_session(cfg, sid, None) == cached
