"""Tests for the nokori add CLI command."""
from __future__ import annotations

import argparse
import json

import pytest

from nokori.commands.add import run, _manual_trigger_structure
from nokori.config import Config
from nokori.db import open_db
from nokori.errors import NokoriError


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    return Config.from_env()


class TestInputValidation:
    def test_trigger_too_short(self, cfg):
        args = argparse.Namespace(
            trigger="ab", action="do something", severity="reminder",
            variants=None, terms_en=None, terms_zh=None, project_id=None,
        )
        with pytest.raises(NokoriError, match="at least 3"):
            run(args, cfg)

    def test_trigger_too_long(self, cfg):
        args = argparse.Namespace(
            trigger="x" * 20000, action="do something", severity="reminder",
            variants=None, terms_en=None, terms_zh=None, project_id=None,
        )
        with pytest.raises(NokoriError, match="exceeds"):
            run(args, cfg)

    def test_action_empty(self, cfg):
        args = argparse.Namespace(
            trigger="valid trigger", action="", severity="reminder",
            variants=None, terms_en=None, terms_zh=None, project_id=None,
        )
        with pytest.raises(NokoriError, match="empty"):
            run(args, cfg)

    def test_action_whitespace_only(self, cfg):
        args = argparse.Namespace(
            trigger="valid trigger", action="   ", severity="reminder",
            variants=None, terms_en=None, terms_zh=None, project_id=None,
        )
        with pytest.raises(NokoriError, match="empty"):
            run(args, cfg)


class TestBasicAdd:
    def test_adds_candidate_rule(self, cfg):
        args = argparse.Namespace(
            trigger="force push to shared branch",
            action="use --force-with-lease",
            severity="reminder",
            variants="git push --force,git push -f",
            terms_en="force push,overwrite",
            terms_zh=None,
            project_id=None,
        )
        rc = run(args, cfg)
        assert rc == 0

        db = open_db(cfg.db_path)
        try:
            rows = db.fetchall("SELECT * FROM rules")
            assert len(rows) == 1
            row = rows[0]
            assert row["status"] == "candidate"
            assert row["severity"] == "reminder"
            assert row["trigger_canonical"] == "force push to shared branch"
            assert row["action_instruction"] == "use --force-with-lease"
            assert row["project_scope"] == "global"
        finally:
            db.close()

    def test_project_scope(self, cfg):
        args = argparse.Namespace(
            trigger="test rule", action="test action", severity="reminder",
            variants=None, terms_en=None, terms_zh=None,
            project_id="my-project-123",
        )
        rc = run(args, cfg)
        assert rc == 0

        db = open_db(cfg.db_path)
        try:
            row = db.fetchone("SELECT project_scope, project_id FROM rules")
            assert row["project_scope"] == "project"
            assert row["project_id"] == "my-project-123"
        finally:
            db.close()

    def test_severity_high_risk(self, cfg):
        args = argparse.Namespace(
            trigger="dangerous operation", action="double check first",
            severity="high_risk",
            variants=None, terms_en=None, terms_zh=None, project_id=None,
        )
        rc = run(args, cfg)
        assert rc == 0

        db = open_db(cfg.db_path)
        try:
            row = db.fetchone("SELECT severity FROM rules")
            assert row["severity"] == "high_risk"
        finally:
            db.close()

    def test_short_id_generated(self, cfg):
        args = argparse.Namespace(
            trigger="rule one", action="action one", severity="reminder",
            variants=None, terms_en=None, terms_zh=None, project_id=None,
        )
        run(args, cfg)

        db = open_db(cfg.db_path)
        try:
            row = db.fetchone("SELECT short_id FROM rules")
            assert row["short_id"] is not None
            assert len(row["short_id"]) >= 6
        finally:
            db.close()

    def test_search_terms_stored(self, cfg):
        args = argparse.Namespace(
            trigger="test trigger", action="test action", severity="reminder",
            variants=None, terms_en="search,terms", terms_zh="搜索,条件",
            project_id=None,
        )
        run(args, cfg)

        db = open_db(cfg.db_path)
        try:
            row = db.fetchone("SELECT search_terms FROM rules")
            terms = json.loads(row["search_terms"])
            assert "en" in terms
            assert "search" in terms["en"]
            assert "zh" in terms
            assert "搜索" in terms["zh"]
        finally:
            db.close()


class TestManualTriggerStructure:
    def test_concepts_and_groups(self):
        concepts, groups, variants = _manual_trigger_structure(
            "force push", ["git push --force"]
        )
        assert len(concepts) == 1
        assert concepts[0]["id"] == "manual_trigger"
        assert concepts[0]["match_mode"] == "phrase"
        assert len(groups) == 1
        assert groups[0]["all_of"] == ["manual_trigger"]

    def test_aliases_deduplication(self):
        concepts, _, _ = _manual_trigger_structure(
            "force push", ["force push", "git push --force"]
        )
        aliases = concepts[0]["aliases"]
        texts = [a["text"] for a in aliases]
        assert texts.count("force push") == 1

    def test_variant_entries_deduplication(self):
        _, _, variants = _manual_trigger_structure(
            "force push", ["force push", "force push", "other"]
        )
        texts = [v["text"] for v in variants]
        assert texts.count("force push") == 1
        assert "other" in texts

    def test_empty_variants(self):
        concepts, groups, variants = _manual_trigger_structure("trigger", [])
        assert len(concepts[0]["aliases"]) == 1
        assert len(variants) == 1


class TestConcurrencySafety:
    def test_unique_short_ids_sequential_adds(self, cfg):
        for i in range(5):
            args = argparse.Namespace(
                trigger=f"rule number {i} trigger text",
                action=f"action {i}",
                severity="reminder",
                variants=None, terms_en=None, terms_zh=None, project_id=None,
            )
            rc = run(args, cfg)
            assert rc == 0

        db = open_db(cfg.db_path)
        try:
            rows = db.fetchall("SELECT short_id FROM rules")
            short_ids = [r["short_id"] for r in rows]
            assert len(short_ids) == 5
            assert len(set(short_ids)) == 5
        finally:
            db.close()
