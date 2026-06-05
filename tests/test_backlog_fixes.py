"""Tests for #57 #59 #54 backlog fixes."""
import json
import subprocess
import sys
from types import SimpleNamespace

from nokori.config import Config
from nokori.db import open_db
from nokori.gate import marker as marker_io
from nokori.gate.marker import MarkerRule, prompt_hash
from nokori.lifecycle.evidence import MAX_EVIDENCE_LOG_ENTRIES, compute_evidence_append


def test_evidence_log_capped():
    score = 0
    log_json = "[]"
    for _ in range(MAX_EVIDENCE_LOG_ENTRIES + 10):
        score, log_json = compute_evidence_append(score, log_json, "shadow_hot", 1)
    entries = json.loads(log_json)
    assert len(entries) == MAX_EVIDENCE_LOG_ENTRIES
    assert entries[0]["kind"] == "shadow_hot"


def test_import_rolls_back_on_failure(tmp_path):
    data = tmp_path / "data"
    out = tmp_path / "bad_batch.json"
    payload = {
        "format": "nokori-export",
        "version": 2,
        "rules": [
            {
                "id": "00000000-0000-4000-8000-000000000001",
                "short_id": "good001",
                "trigger_text": "valid trigger one",
                "action": "ok",
            },
            {
                "id": "00000000-0000-4000-8000-000000000002",
                "short_id": "bad002",
                "trigger_text": "also valid trigger",
                "action": "ok",
                "source_type": "not_a_real_type",
            },
        ],
    }
    out.write_text(json.dumps(payload), encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin", "NOKORI_DATA_DIR": str(data),
           "NOKORI_EMBED_ENABLED": "0", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    r = subprocess.run(
        [sys.executable, "-m", "nokori", "import", str(out)],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode != 0
    db = open_db(data / "rules.db")
    try:
        assert db.fetchone("SELECT id FROM rules WHERE short_id='good001'") is None
    finally:
        db.close()


def test_dismiss_strips_gate_marker(tmp_path, monkeypatch):
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    cfg = Config.from_env()
    env = {"PATH": "/usr/bin:/bin", "NOKORI_DATA_DIR": str(tmp_path),
           "NOKORI_EMBED_ENABLED": "0", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
    subprocess.run(
        [sys.executable, "-m", "nokori", "add",
         "--trigger", "deploy prisma", "--action", "use lease",
         "--source-type", "correction", "--confidence", "high"],
        check=True,
        env=env,
        capture_output=True,
    )
    from datetime import datetime, timezone

    db = open_db(cfg.db_path)
    try:
        rule = db.fetchone("SELECT id, short_id FROM rules LIMIT 1")
        ph = prompt_hash("deploy now")
        marker_io.write(
            cfg,
            "sess-x",
            "deploy now",
            [MarkerRule(rule["short_id"], "use lease", "correction")],
            ph=ph,
        )
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db.transaction() as tx:
            tx.execute(
                "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, level, created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("fe-backlog", rule["id"], "sess-x", ph, "hot", now),
            )
    finally:
        db.close()
    r = subprocess.run(
        [sys.executable, "-m", "nokori", "dismiss", rule["short_id"]],
        capture_output=True,
        text=True,
        env=env,
    )
    assert r.returncode == 0, r.stderr
    assert not cfg.marker_path("sess-x", ph).exists()


def test_no_gate_marker_when_injection_empty(monkeypatch, tmp_path):
    """#71: budget overflow → empty injection must not leave a gate marker."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.hooks import prompt_inject as pinject
    from nokori.hooks.user_prompt_submit import handle
    from nokori.models import Rule, ScoredResult
    from nokori.search.retrieve import RetrievalResult

    cfg = Config.from_env()
    now = "2026-01-01T00:00:00Z"
    rule = Rule(
        id="r1",
        short_id="r1abcd",
        schema_version=1,
        rule_version=1,
        created_by_pipeline_version="1.0.0",
        runtime_policy_version="1.0.0",
        last_rewritten_by_role=None,
        status="active",
        severity="reminder",
        trigger_canonical="trigger",
        action_instruction="do something important",
        project_scope="global",
        created_at=now,
        updated_at=now,
    )
    hot = ScoredResult(rule=rule, strong_variant_phrase_hit=True, required_concepts_match=True)

    def fake_retrieve(*_a, **_k):
        return RetrievalResult([hot], [], 1, "off"), [], []

    def fake_fetch(_db, _cfg, _project_id):
        return ([rule], [])

    monkeypatch.setattr(pinject, "_fetch_formal_and_shadow", fake_fetch)
    monkeypatch.setattr(pinject, "retrieve_formal_and_shadow", fake_retrieve)
    monkeypatch.setattr(pinject, "format_injection", lambda *_a, **_k: ("", []))

    from nokori.utils.host import Host

    out = handle({"session_id": "sess-empty", "prompt": "hello"}, cfg, host=Host.CLAUDE)
    assert out == {"continue": True}
    assert marker_io.read_latest_marker(cfg, "sess-empty") is None


def test_decision_features_include_decision_reason():
    from nokori.hooks.prompt_inject import _build_decision_features
    from nokori.web.models import DecisionFeaturesOut

    features = _build_decision_features(
        SimpleNamespace(
            trigger_idf_sum=1.2,
            trigger_coverage=0.5,
            distinct_trigger_terms=2,
            strong_variant_phrase_hit=False,
            required_concepts_match=True,
            excluded_context_hit=False,
            bm25_score=3.0,
            cosine=None,
            rrf_score=0.1,
            decision_reason="active with observed useful + strong trigger evidence",
        )
    )

    assert features["decision_reason"] == (
        "active with observed useful + strong trigger evidence"
    )
    assert features["weak_variant_recall_hit"] is False
    assert features["excluded_context_override_passed"] is False
    assert features["action_only_match"] is False
    assert DecisionFeaturesOut(**features).decision_reason == (
        "active with observed useful + strong trigger evidence"
    )


def test_record_fire_events_passes_turn_index(monkeypatch):
    from nokori.hooks import prompt_inject as pinject

    captured = {}

    def fake_create_fire_event(*_args, **kwargs):
        captured["turn_index"] = kwargs.get("turn_index")
        return "event-1"

    monkeypatch.setattr(pinject, "create_fire_event", fake_create_fire_event)
    result = SimpleNamespace(
        rule=SimpleNamespace(id="rule-1"),
        trigger_idf_sum=0.0,
        trigger_coverage=0.0,
        distinct_trigger_terms=0,
        strong_variant_phrase_hit=False,
        required_concepts_match=True,
        excluded_context_hit=False,
        bm25_score=0.0,
        cosine=None,
        rrf_score=0.0,
        decision_reason="test",
        trigger_idf_pool_version="idf",
        embedding_profile_version=None,
    )

    pinject._record_fire_events(
        SimpleNamespace(), "session-1", "prompt-hash", [result], "warm", turn_index=7
    )

    assert captured["turn_index"] == 7


def test_record_shadow_events_passes_runtime_policy_version(monkeypatch):
    from nokori.hooks import prompt_inject as pinject

    captured = {}

    def fake_create_shadow_event(*_args, **kwargs):
        captured["runtime_policy_version"] = kwargs.get("runtime_policy_version")
        return "shadow-1"

    monkeypatch.setattr(pinject, "create_shadow_event", fake_create_shadow_event)
    monkeypatch.setattr(pinject, "is_duplicate_shadow_context", lambda *_args: False)
    result = SimpleNamespace(
        rule=SimpleNamespace(id="rule-1", status="candidate"),
        trigger_idf_sum=0.0,
        trigger_coverage=0.0,
        distinct_trigger_terms=0,
        strong_variant_phrase_hit=False,
        weak_variant_recall_hit=True,
        required_concepts_match=True,
        excluded_context_hit=False,
        excluded_context_override_passed=False,
        action_only_match=False,
        search_only_match=False,
        embedding_only_match=False,
        matched_trigger_tokens=frozenset(),
        matched_variant_tokens=frozenset(),
        bm25_score=0.0,
        cosine=None,
        rrf_score=0.0,
        decision_reason="test",
        trigger_idf_pool_version="idf",
        runtime_policy_version="policy-v1",
        embedding_profile_version="embed",
    )

    pinject._record_shadow_events(
        SimpleNamespace(), "session-1", "prompt-hash", [result], turn_index=7
    )

    assert captured["runtime_policy_version"] == "policy-v1"


def test_inject_for_prompt_records_only_rendered_entries(monkeypatch):
    from nokori.config import Config
    from nokori.hooks import prompt_inject as pinject

    cfg = Config.from_env()

    def make_result(rule_id: str, short_id: str):
        return SimpleNamespace(
            rule=SimpleNamespace(
                id=rule_id,
                short_id=short_id,
                trigger_canonical=f"trigger {short_id}",
                action_instruction=f"action {short_id}",
                severity="reminder",
            ),
            trigger_idf_sum=1.0,
            trigger_coverage=1.0,
            distinct_trigger_terms=2,
            strong_variant_phrase_hit=True,
            required_concepts_match=True,
            excluded_context_hit=False,
            bm25_score=1.0,
            cosine=None,
            rrf_score=1.0,
            decision_reason="test",
            trigger_idf_pool_version="idf",
            embedding_profile_version="embed",
        )

    hot_unrendered = make_result("rule-hot", "hot123")
    warm_rendered = make_result("rule-warm", "warm123")

    monkeypatch.setattr(
        pinject,
        "_fetch_formal_and_shadow",
        lambda *_args, **_kwargs: ([hot_unrendered.rule, warm_rendered.rule], []),
    )
    monkeypatch.setattr(
        pinject,
        "retrieve_formal_and_shadow",
        lambda *_args, **_kwargs: (
            SimpleNamespace(hot=[hot_unrendered], warm=[warm_rendered]),
            [],
            [],
        ),
    )
    monkeypatch.setattr(
        pinject,
        "format_injection",
        lambda *_args, **_kwargs: ("rendered", [("rule-warm", "warm")]),
    )

    recorded: list[tuple[str, str]] = []

    def fake_create_fire_event(_db, rule, *_args, **kwargs):
        recorded.append((rule.id, kwargs["level"]))
        return f"event-{rule.id}"

    monkeypatch.setattr(pinject, "create_fire_event", fake_create_fire_event)

    outcome = pinject.inject_for_prompt(
        SimpleNamespace(),
        cfg,
        session_id="session-1",
        prompt="trigger text",
        project_id="proj",
    )

    assert outcome is not None
    assert outcome.rendered_entries == [("rule-warm", "warm")]
    assert recorded == [("rule-warm", "warm")]


def test_select_gate_rules_requires_runtime_gate_level():
    from nokori.gate.blocker import select_gate_rules

    trusted_gate_hot = SimpleNamespace(
        rule=SimpleNamespace(status="trusted", severity="gate_eligible"),
        level="hot",
    )
    trusted_gate = SimpleNamespace(
        rule=SimpleNamespace(status="trusted", severity="gate_eligible"),
        level="gate",
    )

    assert select_gate_rules([trusted_gate_hot, trusted_gate]) == [trusted_gate]
