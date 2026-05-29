import json

import pytest

from nokori.config import Config
from nokori.db import open_db, fetch_rules
from nokori.extract.extractor import Candidate
from nokori.extract.merger import merge_candidate


class FakeMergeLLM:
    def __init__(self, response):
        self.response = response

    def complete(self, prompt, *, max_tokens=2000, timeout=30):
        return self.response


def _cand(trigger="rule x", action="do y", source="correction", conf="high"):
    return Candidate(
        trigger=trigger,
        trigger_variants=[],
        search_terms={},
        behavior=None,
        action=action,
        rationale=None,
        source_type=source,
        confidence=conf,
    )


def test_merge_inserts_when_no_neighbors(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        outcome = merge_candidate(_cand(), db, FakeMergeLLM("[]"))
        assert outcome.inserted == 1
        rules = fetch_rules(db, statuses=("active", "candidate"))
        assert len(rules) == 1
        assert rules[0].status == "active"
    finally:
        db.close()


def test_merge_unrelated_inserts_independent(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("rule a", "act a"), db, FakeMergeLLM("[]"))
        existing_rules = fetch_rules(db, statuses=("active",))
        existing_id = existing_rules[0].id
        response = json.dumps({
            "relationships": [
                {"existing_id": existing_id, "judgment": "E", "reasoning": "diff"}
            ]
        })
        outcome = merge_candidate(_cand("rule b", "act b"), db, FakeMergeLLM(response))
        assert outcome.inserted == 1
        rules = fetch_rules(db, statuses=("active",))
        assert len(rules) == 2
    finally:
        db.close()


def test_merge_same_activates_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        # First insert as a medium candidate
        outcome = merge_candidate(
            _cand(conf="medium", source="solution"), db, FakeMergeLLM("[]")
        )
        rules = fetch_rules(db, statuses=("candidate",))
        assert len(rules) == 1
        existing_id = rules[0].id

        # Now a high-confidence correction that's SAME — should activate
        response = json.dumps({
            "relationships": [
                {"existing_id": existing_id, "judgment": "A", "reasoning": "same"}
            ]
        })
        merge_candidate(_cand(conf="high", source="correction"), db,
                        FakeMergeLLM(response))
        rules_now = fetch_rules(db, statuses=("active",))
        assert any(r.id == existing_id for r in rules_now)
    finally:
        db.close()


def test_merge_contradicts_supersedes(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("old advice", "do A"), db, FakeMergeLLM("[]"))
        existing = fetch_rules(db, statuses=("active",))[0]
        response = json.dumps({
            "relationships": [
                {"existing_id": existing.id, "judgment": "D",
                 "reasoning": "opposite"}
            ]
        })
        merge_candidate(_cand("new advice", "do B"), db, FakeMergeLLM(response))
        all_rules = fetch_rules(db, statuses=None)
        statuses = {r.id: r.status for r in all_rules}
        assert statuses[existing.id] == "merged"
        # And there's a new active rule
        actives = [r for r in all_rules if r.status == "active"]
        assert any(r.action == "do B" for r in actives)
    finally:
        db.close()


def test_merge_llm_failure_falls_back_to_insert(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("rule a", "act a"), db, FakeMergeLLM("[]"))

        class BadLLM:
            def complete(self, *a, **k):
                raise RuntimeError("boom")

        outcome = merge_candidate(_cand("rule b", "act b"), db, BadLLM())
        # When merge LLM fails, default is UNRELATED → still insert
        assert outcome.inserted == 1
        assert len(fetch_rules(db, statuses=("active",))) == 2
    finally:
        db.close()
