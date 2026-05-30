"""Tests for project_id resolution."""
from nokori.utils import project as project_util


def test_project_id_stable_for_same_cwd(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    cwd = str(repo / "sub")
    a = project_util.resolve_project_id(cwd)
    b = project_util.resolve_project_id(cwd)
    assert a == b
    assert a is not None
