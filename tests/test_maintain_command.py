"""Tests for the maintain command production worker wiring."""

import argparse
from contextlib import contextmanager

from nokori.commands import maintain
from nokori.config import Config


class _FakeDb:
    def close(self):
        pass

    def fetchone(self, sql, params=()):
        return None


def test_maintain_skips_when_another_worker_holds_lock(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()

    @contextmanager
    def _busy_lock(*args, **kwargs):
        yield False

    monkeypatch.setattr(maintain.file_lock, "acquire", _busy_lock)
    monkeypatch.setattr(
        maintain,
        "open_db",
        lambda path: (_ for _ in ()).throw(AssertionError("database should not open")),
    )

    rc = maintain.run(argparse.Namespace(), cfg)

    assert rc == 0
    assert "already running; skipping" in capsys.readouterr().out


def test_maintain_runs_shadow_counterfactual_worker(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = _FakeDb()
    calls: list[str] = []

    monkeypatch.setattr(maintain, "open_db", lambda path: db)
    monkeypatch.setattr(
        maintain.maintenance,
        "run_maintenance",
        lambda db_arg, cfg_arg: {
            "transitions_applied": 0,
            "candidate_cleanup": 0,
            "injection_cleanup": 0,
            "unmerge_check": 0,
            "observability_cleanup": {"hook_events_deleted": 0, "error_events_deleted": 0},
        },
    )
    monkeypatch.setattr(
        maintain,
        "process_pending_posthoc_jobs",
        lambda db_arg, llm, *, limit: {"done": 0, "unclear": 0, "failed": 0},
    )
    monkeypatch.setattr(
        maintain,
        "run_shadow_counterfactual_evaluation",
        lambda db_arg, llm, *, limit: calls.append("shadow")
        or {
            "processed": 2,
            "labeled": 2,
            "failed": 0,
            "transitions_applied": 1,
        },
    )
    monkeypatch.setattr(maintain, "_PosthocLLMAdapter", lambda cfg_arg: object())
    monkeypatch.setattr(maintain, "expire_stale_ingest_jobs", lambda db_arg: 0)
    monkeypatch.setattr(maintain, "fetch_rules", lambda db_arg, statuses: [])
    monkeypatch.setattr(maintain, "compute_eligible_rule_set_hash", lambda rules: "empty")
    monkeypatch.setattr(maintain, "build_idf_stats", lambda rules: {})
    monkeypatch.setattr(maintain, "store_idf_stats", lambda db_arg, stats: None)

    rc = maintain.run(argparse.Namespace(), cfg)

    assert rc == 0
    assert calls == ["shadow"]
    out = capsys.readouterr().out
    assert "shadow.processed    processed=2 labeled=2 failed=0 transitions=1" in out
