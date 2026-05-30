"""Tests for project_id resolution cache."""
from nokori.utils import project as project_util


def test_project_id_cached(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[int] = []

    def fake_run(*args, **kwargs):
        calls.append(1)
        class R:
            returncode = 0
            stdout = str(repo)
        return R()

    monkeypatch.setattr(project_util.subprocess, "run", fake_run)
    project_util._project_id_for_cwd.cache_clear()
    cwd = str(repo / "sub")
    a = project_util.resolve_project_id(cwd)
    b = project_util.resolve_project_id(cwd)
    assert a == b
    assert len(calls) == 1
