import json

from nokori.config import Config
from nokori.db import SCHEMA_VERSION, loads_json, open_db, fetch_rules
from nokori.policy import RUNTIME_POLICY_VERSION
from nokori.extract.extractor import Candidate
from nokori.extract.merger import _normalize_merge_verdict, merge_candidate


class FakeMergeLLM:
    def __init__(self, response):
        self.response = response

    def complete(self, prompt, *, max_tokens=2000, timeout=30):
        return self.response

    def complete_messages(self, system, user, *, max_tokens=2000, timeout=30):
        return self.complete(user, max_tokens=max_tokens, timeout=timeout)


def _cand(
    trigger="rule x",
    action="do y",
    *,
    variants=(),
    source_type="correction",
    source="correction",
    confidence="high",
    conf="high",
):
    return Candidate(
        trigger=trigger,
        trigger_variants=list(variants),
        search_terms={},
        behavior=None,
        action=action,
        rationale=None,
        source_type=source if source != "correction" else source_type,
        confidence=conf if conf != "high" else confidence,
    )


def test_normalize_merge_verdict_strips_wrappers():
    assert _normalize_merge_verdict("(A)") == "A"
    assert _normalize_merge_verdict("[B]") == "B"
    assert _normalize_merge_verdict("{C}") == "C"
    assert _normalize_merge_verdict("CONTRADICTS") == "D"


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


def test_merge_persists_current_v6_schema(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(
            _cand(
                "never force push shared branches",
                "use force-with-lease",
                variants=["git push --force"],
            ),
            db,
            FakeMergeLLM("[]"),
        )

        rule = fetch_rules(db, statuses=("active", "candidate"))[0]
        assert rule.schema_version == SCHEMA_VERSION
        assert rule.runtime_policy_version == RUNTIME_POLICY_VERSION
        assert loads_json(rule.concepts, [])
        assert loads_json(rule.required_concept_groups, [])
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


def test_merge_same_adds_evidence_to_active(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("shared trigger", "do X"), db, FakeMergeLLM("[]"))
        existing = fetch_rules(db, statuses=("active",))[0]
        response = json.dumps({
            "relationships": [
                {"existing_id": existing.id, "judgment": "A", "reasoning": "same"}
            ]
        })
        merge_candidate(_cand("shared trigger", "do X"), db, FakeMergeLLM(response))
        row = db.fetchone(
            "SELECT evidence_support_score FROM rules WHERE id = ?",
            (existing.id,),
        )
        assert row["evidence_support_score"] >= 1.0
    finally:
        db.close()


def test_merge_same_activates_candidate(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        # First insert as a medium candidate
        merge_candidate(
            _cand(conf="medium", source="solution"), db, FakeMergeLLM("[]")
        )
        rules = fetch_rules(db, statuses=("candidate",))
        assert len(rules) == 1
        existing_id = rules[0].id

        # Now a high-confidence correction that's SAME - should activate
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


def test_merge_narrower_with_same_inserts_new(monkeypatch, tmp_path):
    """NARROWER (C) + SAME (A): merge evidence on existing and still insert candidate."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("shared trigger", "do X"), db, FakeMergeLLM("[]"))
        existing = fetch_rules(db, statuses=("active",))[0]
        response = json.dumps({
            "relationships": [
                {"existing_id": existing.id, "judgment": "A", "reasoning": "same"},
                {"existing_id": existing.id, "judgment": "C", "reasoning": "narrower scope"},
            ]
        })
        before = len(fetch_rules(db, statuses=("active", "candidate")))
        outcome = merge_candidate(
            _cand("shared trigger", "narrower action"), db, FakeMergeLLM(response),
        )
        after = fetch_rules(db, statuses=("active", "candidate"))
        assert outcome.inserted == 1
        assert len(after) == before + 1
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
        assert statuses[existing.id] == "archived"
        # And there's a new active rule
        actives = [r for r in all_rules if r.status == "active"]
        assert any(r.action_instruction == "do B" for r in actives)
    finally:
        db.close()


def test_merge_bm25_prefers_lexical_match_over_recency(monkeypatch, tmp_path):
    """Older semantically similar rule must reach the LLM even when many newer rules exist."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(
            _cand(
                "Never force push to a shared branch",
                "use --force-with-lease",
                variants=["git push --force"],
            ),
            db,
            FakeMergeLLM("[]"),
            project_id="proj-a",
        )
        target = fetch_rules(db, statuses=("active",), project_id="proj-a")[0]

        unrelated_llm = FakeMergeLLM('{"relationships": []}')
        for i in range(12):
            merge_candidate(
                _cand(f"unrelated topic number {i}", f"action {i}"),
                db,
                unrelated_llm,
                project_id="proj-a",
            )

        response = json.dumps({
            "relationships": [
                {"existing_id": target.id, "judgment": "A", "reasoning": "same"},
            ]
        })
        outcome = merge_candidate(
            _cand(
                "git push --force to remote",
                "use --force-with-lease instead",
                variants=["git push -f"],
            ),
            db,
            FakeMergeLLM(response),
            project_id="proj-a",
        )
        assert outcome.inserted == 0
        assert outcome.activated == 0
        rules = fetch_rules(db, statuses=("active",), project_id="proj-a")
        assert sum(1 for r in rules if "force" in r.trigger_canonical.lower()) == 1
    finally:
        db.close()


def test_merge_multiple_bd_inserts_once(monkeypatch, tmp_path):
    """B and D against two neighbors should create one new rule, two supersedes."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("rule one", "act one"), db, FakeMergeLLM("[]"))
        merge_candidate(
            _cand("rule two", "act two"),
            db,
            FakeMergeLLM('{"relationships": []}'),
        )
        existing = fetch_rules(db, statuses=("active",))
        assert len(existing) == 2
        id_a, id_b = existing[0].id, existing[1].id
        response = json.dumps({
            "relationships": [
                {"existing_id": id_a, "judgment": "B", "reasoning": "broader"},
                {"existing_id": id_b, "judgment": "D", "reasoning": "contradicts"},
            ]
        })
        outcome = merge_candidate(
            _cand("unified rule", "unified act"), db, FakeMergeLLM(response)
        )
        assert outcome.inserted == 1
        assert outcome.superseded == 2
        active = fetch_rules(db, statuses=("active",))
        assert len(active) == 1
        assert active[0].action_instruction == "unified act"
    finally:
        db.close()


def test_merge_a_then_bd_reuses_anchor_without_second_insert(monkeypatch, tmp_path):
    """SAME on X then BROADER on Y should supersede Y onto X, not insert a second active rule."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("old narrow", "do A"), db, FakeMergeLLM("[]"))
        merge_candidate(
            _cand("other rule", "do B"),
            db,
            FakeMergeLLM('{"relationships": []}'),
        )
        rules = fetch_rules(db, statuses=("active",))
        assert len(rules) == 2
        by_action = {r.action_instruction: r for r in rules}
        id_narrow = by_action["do A"].id
        id_other = by_action["do B"].id
        response = json.dumps({
            "relationships": [
                {"existing_id": id_narrow, "judgment": "A", "reasoning": "same"},
                {"existing_id": id_other, "judgment": "B", "reasoning": "broader"},
            ]
        })
        outcome = merge_candidate(
            _cand("unified advice", "do unified"),
            db,
            FakeMergeLLM(response),
        )
        assert outcome.inserted == 0
        assert outcome.superseded == 1
        active = fetch_rules(db, statuses=("active",))
        assert len(active) == 1
        assert active[0].id == id_narrow
        archived = db.fetchone("SELECT status, replacement_id FROM rules WHERE id = ?", (id_other,))
        assert archived["status"] == "archived"
        assert archived["replacement_id"] == id_narrow
    finally:
        db.close()


def test_merge_bd_before_a_reuses_anchor_without_orphan_insert(monkeypatch, tmp_path):
    """LLM order B/D before A must still supersede onto the SAME anchor, not a new rule."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("old narrow", "do A"), db, FakeMergeLLM("[]"))
        merge_candidate(
            _cand("other rule", "do B"),
            db,
            FakeMergeLLM('{"relationships": []}'),
        )
        rules = fetch_rules(db, statuses=("active",))
        assert len(rules) == 2
        by_action = {r.action_instruction: r for r in rules}
        id_narrow = by_action["do A"].id
        id_other = by_action["do B"].id
        response = json.dumps({
            "relationships": [
                {"existing_id": id_other, "judgment": "B", "reasoning": "broader first"},
                {"existing_id": id_narrow, "judgment": "A", "reasoning": "same second"},
            ]
        })
        outcome = merge_candidate(
            _cand("unified advice", "do unified"),
            db,
            FakeMergeLLM(response),
        )
        assert outcome.inserted == 0
        assert outcome.superseded == 1
        active = fetch_rules(db, statuses=("active",))
        assert len(active) == 1
        assert active[0].id == id_narrow
        archived = db.fetchone(
            "SELECT status, replacement_id FROM rules WHERE id = ?", (id_other,)
        )
        assert archived["status"] == "archived"
        assert archived["replacement_id"] == id_narrow
    finally:
        db.close()


def test_merge_multiple_a_uses_first_anchor_for_bd(monkeypatch, tmp_path):
    """When LLM marks two neighbors as A, B/D anchor is the first A, not the last."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("first rule", "action first"), db, FakeMergeLLM("[]"))
        id_first = fetch_rules(db, statuses=("active",))[0].id
        merge_candidate(_cand("second rule", "action second"), db, FakeMergeLLM("[]"))
        merge_candidate(_cand("third rule", "action third"), db, FakeMergeLLM("[]"))
        id_second = next(
            r.id for r in fetch_rules(db, statuses=("active",))
            if r.action_instruction == "action second"
        )
        id_third = next(
            r.id for r in fetch_rules(db, statuses=("active",))
            if r.action_instruction == "action third"
        )
        response = json.dumps({
            "relationships": [
                {"existing_id": id_first, "judgment": "A", "reasoning": "same"},
                {"existing_id": id_second, "judgment": "A", "reasoning": "same"},
                {"existing_id": id_third, "judgment": "B", "reasoning": "broader"},
            ]
        })
        merge_candidate(
            _cand("unified", "unified action"),
            db,
            FakeMergeLLM(response),
        )
        row = db.fetchone(
            "SELECT status, replacement_id FROM rules WHERE id = ?", (id_third,)
        )
        assert row["status"] == "archived"
        assert row["replacement_id"] == id_first
    finally:
        db.close()


def test_merge_verdict_full_word_contradicts(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    db = open_db(Config.from_env().db_path)
    try:
        merge_candidate(_cand("git push force", "use lease"), db, FakeMergeLLM("[]"))
        id_old = fetch_rules(db, statuses=("active",))[0].id
        response = json.dumps({
            "relationships": [
                {"existing_id": id_old, "judgment": "CONTRADICTS", "reasoning": "conflict"},
            ]
        })
        merge_candidate(
            _cand("never force push", "always use lease"),
            db,
            FakeMergeLLM(response),
        )
        row = db.fetchone(
            "SELECT status, replacement_id FROM rules WHERE id = ?", (id_old,)
        )
        assert row["status"] == "archived"
    finally:
        db.close()


def test_merge_llm_failure_keeps_pending_when_neighbors(monkeypatch, tmp_path):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    db = open_db(cfg.db_path)
    try:
        merge_candidate(_cand("rule a", "act a"), db, FakeMergeLLM("[]"))

        class BadLLM:
            def complete(self, *a, **k):
                raise RuntimeError("boom")

            def complete_messages(self, *a, **k):
                raise RuntimeError("boom")

        outcome = merge_candidate(_cand("rule b", "act b"), db, BadLLM())
        assert outcome.inserted == 0
        assert outcome.merge_ok is False
    finally:
        db.close()
