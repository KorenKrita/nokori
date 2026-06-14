"""Tests for CLI commands: dismiss, edit, export/import, search.

Covers:
1. dismiss — valid short_id, invalid short_id, already-archived
2. edit — trigger update, non-existent short_id
3. export/import — round-trip integrity
4. search — basic output, with --project flag
"""
from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import replace

import pytest

from nokori.config import Config
from nokori.db import open_db

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NOW = "2026-06-01T00:00:00Z"

_UUID_1 = str(uuid.UUID("00000000-0000-0000-0000-000000000001"))
_UUID_2 = str(uuid.UUID("00000000-0000-0000-0000-000000000002"))

_RULE_INSERT_SQL = (
    "INSERT INTO rules ("
    "id, short_id, schema_version, rule_version, "
    "created_by_pipeline_version, runtime_policy_version, "
    "status, severity, "
    "trigger_canonical, "
    "concepts, required_concept_groups, excluded_contexts, "
    "trigger_variants, search_terms, "
    "action_instruction, "
    "domain_tags, tool_tags, path_patterns, "
    "source_origin, "
    "project_scope, project_id, "
    "created_at, updated_at"
    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def _make_rule_params(
    *,
    rule_id=_UUID_1,
    short_id="abc123",
    status="active",
    severity="reminder",
    trigger="never force push to main",
    action="use --force-with-lease instead",
    project_scope="global",
    project_id=None,
):
    return (
        rule_id, short_id, 10, 1,
        "pipeline-v3", "policy-v2",
        status, severity,
        trigger,
        '[{"id": "git", "label": "git", "match_mode": "any_alias", "aliases": [{"text": "git push", "strength": "strong"}]}]',
        '[{"id": "git-push", "all_of": ["git"]}]',
        '[]',
        '[{"text": "force push", "kind": "weak_recall", "requires_concepts": []}]',
        '{"en": ["force push", "main branch"]}',
        action,
        '["git"]', '["cli"]', '["*.sh"]',
        "transcript_extraction",
        project_scope, project_id,
        NOW, NOW,
    )


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("NOKORI_EMBED_ENABLED", "0")
    base = Config.from_env()
    return replace(base, data_dir=tmp_path)


@pytest.fixture
def db_with_rules(cfg):
    """DB with two rules: one active, one for project-scoped testing."""
    db = open_db(cfg.db_path)
    with db.transaction() as tx:
        tx.execute(_RULE_INSERT_SQL, _make_rule_params())
        tx.execute(
            _RULE_INSERT_SQL,
            _make_rule_params(
                rule_id=_UUID_2,
                short_id="def456",
                trigger="always run tests before deploy",
                action="run pytest first",
                project_scope="project",
                project_id="proj-A",
            ),
        )
    yield db
    db.close()


# ---------------------------------------------------------------------------
# 1. dismiss command
# ---------------------------------------------------------------------------


class TestDismissCommand:
    def test_dismiss_valid_short_id(self, cfg, db_with_rules, capsys):
        from nokori.commands.dismiss import run

        args = argparse.Namespace(short_id="abc123")
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "archived abc123" in out

        # Verify status changed in DB
        row = db_with_rules.fetchone(
            "SELECT status FROM rules WHERE short_id = ?", ("abc123",)
        )
        assert row["status"] == "archived"

    def test_dismiss_invalid_short_id(self, cfg, db_with_rules):
        from nokori.commands.dismiss import run
        from nokori.errors import NokoriError

        args = argparse.Namespace(short_id="zzz999")
        with pytest.raises(NokoriError, match="no rule with short_id"):
            run(args, cfg)

    def test_dismiss_already_archived(self, cfg, db_with_rules, capsys):
        from nokori.commands.dismiss import run

        # First dismiss
        args = argparse.Namespace(short_id="abc123")
        run(args, cfg)

        # Second dismiss — should report already archived
        capsys.readouterr()  # clear buffer
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "already archived" in out


# ---------------------------------------------------------------------------
# 2. edit command
# ---------------------------------------------------------------------------


class TestEditCommand:
    def test_edit_trigger(self, cfg, db_with_rules, capsys):
        from nokori.commands.edit import run

        args = argparse.Namespace(
            short_id="abc123",
            trigger="never rebase shared branches",
            action=None,
            severity=None,
            status=None,
            variants=None,
            terms_en=None,
            terms_zh=None,
        )
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "updated abc123" in out
        assert "trigger_canonical" in out

        # Verify DB updated
        row = db_with_rules.fetchone(
            "SELECT trigger_canonical FROM rules WHERE short_id = ?", ("abc123",)
        )
        assert row["trigger_canonical"] == "never rebase shared branches"

    def test_edit_nonexistent_short_id(self, cfg, db_with_rules):
        from nokori.commands.edit import run
        from nokori.errors import NokoriError

        args = argparse.Namespace(
            short_id="zzz999",
            trigger="something",
            action=None,
            severity=None,
            status=None,
            variants=None,
            terms_en=None,
            terms_zh=None,
        )
        with pytest.raises(NokoriError, match="no rule with short_id"):
            run(args, cfg)

    def test_edit_nothing_to_update(self, cfg, db_with_rules, capsys):
        from nokori.commands.edit import run

        args = argparse.Namespace(
            short_id="abc123",
            trigger=None,
            action=None,
            severity=None,
            status=None,
            variants=None,
            terms_en=None,
            terms_zh=None,
        )
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "nothing to update" in out


# ---------------------------------------------------------------------------
# 3. export/import round-trip
# ---------------------------------------------------------------------------


class TestExportImport:
    def test_export_creates_valid_json(self, cfg, db_with_rules, tmp_path):
        from nokori.commands.export_import import run_export

        export_path = tmp_path / "exported.json"
        args = argparse.Namespace(path=str(export_path))
        rc = run_export(args, cfg)
        assert rc == 0
        assert export_path.exists()

        data = json.loads(export_path.read_text())
        assert data["format"] == "nokori-export"
        assert data["version"] == 10
        assert len(data["rules"]) == 2

    def test_import_round_trip(self, cfg, db_with_rules, tmp_path):
        from nokori.commands.export_import import run_export, run_import

        export_path = tmp_path / "roundtrip.json"

        # Export existing rules
        run_export(argparse.Namespace(path=str(export_path)), cfg)

        # Create a fresh DB (different data_dir)
        fresh_dir = tmp_path / "fresh"
        fresh_dir.mkdir()
        fresh_cfg = replace(cfg, data_dir=fresh_dir)
        fresh_cfg.ensure_dirs()

        # Import into fresh DB
        rc = run_import(argparse.Namespace(path=str(export_path)), fresh_cfg)
        assert rc == 0

        # Verify rules were imported
        fresh_db = open_db(fresh_cfg.db_path)
        try:
            rows = fresh_db.fetchall("SELECT short_id FROM rules ORDER BY short_id")
            imported_ids = {r["short_id"] for r in rows}
            # short_ids are regenerated on import, but count should match
            assert len(imported_ids) == 2
        finally:
            fresh_db.close()

    def test_import_nonexistent_file(self, cfg, db_with_rules, tmp_path):
        from nokori.commands.export_import import run_import
        from nokori.errors import NokoriError

        args = argparse.Namespace(path=str(tmp_path / "does_not_exist.json"))
        with pytest.raises(NokoriError, match="file not found"):
            run_import(args, cfg)


# ---------------------------------------------------------------------------
# 4. search command
# ---------------------------------------------------------------------------


class TestSearchCommand:
    def test_search_returns_results_or_empty(self, cfg, db_with_rules, capsys, monkeypatch):
        """Search command runs successfully and produces table or no-match output."""
        from nokori.commands.search_debug import run

        # Force global scope by monkeypatching resolve_project_id to return None
        monkeypatch.setattr(
            "nokori.commands.search_debug.resolve_project_id", lambda _: None
        )
        args = argparse.Namespace(prompt="force push to main", project=None)
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "abc123" in out or "no matching rules" in out

    def test_search_no_matches(self, cfg, db_with_rules, capsys, monkeypatch):
        from nokori.commands.search_debug import run

        monkeypatch.setattr(
            "nokori.commands.search_debug.resolve_project_id", lambda _: None
        )
        args = argparse.Namespace(
            prompt="xyzzy plugh qwerty asdf jkl zxcvbn", project=None
        )
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        assert "no matching rules" in out

    def test_search_with_project_filter(self, cfg, db_with_rules, capsys):
        from nokori.commands.search_debug import run

        # Search with explicit project filter — should not crash
        args = argparse.Namespace(prompt="run tests before deploy", project="proj-A")
        rc = run(args, cfg)
        assert rc == 0
        out = capsys.readouterr().out
        # Either finds matching rule or reports no matches — both valid
        assert "no matching rules" in out or "def456" in out
