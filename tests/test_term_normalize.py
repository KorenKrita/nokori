import json

from nokori.extract.extractor import extract
from nokori.extract.term_normalize import normalize_search_terms, normalize_trigger_variants


class _FakeLLM:
    def __init__(self, response: str):
        self.response = response

    def complete_messages(self, system, user, *, max_tokens=2000, timeout=30):
        return self.response

    def complete_role(self, role, system, user, *, max_tokens=2000, timeout=30):
        return self.response


def test_normalize_splits_mixed_zh_term():
    out = normalize_search_terms({"zh": ["为什么不用skill"]})
    assert out["en"] == ["skill"]
    assert out["zh"] == ["为什么不用"]


def test_normalize_moves_latin_from_zh():
    out = normalize_search_terms({"zh": ["rust-analyzer-lsp", "没看到caveman的hook"]})
    assert "rust-analyzer-lsp" in out["en"]
    assert "caveman" in out["en"]
    assert "hook" in out["en"]
    assert any("没看到" in t for t in out.get("zh", []))


def test_normalize_splits_cjk_in_en():
    out = normalize_search_terms({"en": ["为什么不用skill"]})
    assert out["en"] == ["skill"]
    assert out["zh"] == ["为什么不用"]


def test_normalize_drops_negation_only_zh():
    out = normalize_search_terms({"zh": ["不对", "代码审查"]})
    assert "zh" not in out or "不对" not in out["zh"]
    assert out["zh"] == ["代码审查"]


def test_normalize_trigger_variants_drop_actor_phrasing():
    out = normalize_trigger_variants([
        "SSH to a remote server",
        "User asks to access a server with stored credentials",
        "When the user requests deployment",
    ])
    assert out == ["SSH to a remote server"]


def test_extractor_applies_search_terms_normalization():
    response = json.dumps({"candidates": [{
        "trigger": "Connecting to a remote server via SSH",
        "trigger_zh": "通过SSH连接远程服务器",
        "trigger_variants": [
            "SSH to a remote server",
            "User asks to access a server",
        ],
        "trigger_variants_zh": ["SSH到远程服务器"],
        "search_terms": {"en": [], "zh": ["为什么不用skill", "不对"]},
        "required_concepts": ["SSH", "remote server"],
        "excluded_contexts": [],
        "non_generalization_boundaries": [],
        "near_miss_examples": [],
        "severity": "reminder",
        "domain_tags": [],
        "tool_tags": [],
        "file_or_path_patterns": [],
        "behavior": "did not check credentials",
        "action": "Check memory for credentials before SSH",
        "action_zh": "SSH前先检查凭证",
        "rationale": "user corrected",
        "evidence_quotes": ["why no skill"],
    }]})
    cands, ok = extract("[User] why no skill\n", _FakeLLM(response))
    assert ok and len(cands) == 1
    c = cands[0]
    assert c.trigger_variants == ["SSH to a remote server"]
    assert c.search_terms["en"] == ["skill"]
    assert c.search_terms["zh"] == ["为什么不用"]
