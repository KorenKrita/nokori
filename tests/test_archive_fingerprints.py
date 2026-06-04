"""Tests for nokori.archive.fingerprints -- archive fingerprint blocking logic."""

import pytest

from nokori.archive.fingerprints import (
    check_fingerprint_block,
    compute_signature,
    create_archived_fingerprint,
)
from nokori.db import open_db


@pytest.fixture()
def db(tmp_path):
    d = open_db(tmp_path / "rules.db")
    yield d
    d.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRule:
    """Minimal rule-like object for create_archived_fingerprint."""

    def __init__(
        self,
        *,
        id="rule-1",
        trigger_canonical="when user asks for help",
        action_instruction="provide helpful response",
        domain_tags=None,
        tool_tags=None,
        path_patterns=None,
    ):
        self.id = id
        self.trigger_canonical = trigger_canonical
        self.action_instruction = action_instruction
        self.domain_tags = domain_tags or ["general"]
        self.tool_tags = tool_tags or []
        self.path_patterns = path_patterns or []


# ---------------------------------------------------------------------------
# 1. User archive blocks equivalent/broader future rules
# ---------------------------------------------------------------------------


def test_user_archive_blocks_equivalent(db):
    """User-strength archive blocks exact same trigger+action without scope evidence."""
    rule = _FakeRule()
    create_archived_fingerprint(db, rule, strength="user")

    result = check_fingerprint_block(
        db,
        trigger_canonical="when user asks for help",
        action_instruction="provide helpful response",
        domain_tags=["general"],
    )
    assert result is not None
    assert result["blocked"] is True
    assert result["archive_strength"] == "user"
    assert result["reason"] == "user_archive_blocks_equivalent_or_broader"


# ---------------------------------------------------------------------------
# 2. System archive is weaker (overridable with evidence)
# ---------------------------------------------------------------------------


def test_system_archive_is_overridable(db):
    """System-strength archive reports blocked=True but overridable=True."""
    rule = _FakeRule()
    create_archived_fingerprint(db, rule, strength="system")

    result = check_fingerprint_block(
        db,
        trigger_canonical="when user asks for help",
        action_instruction="provide helpful response",
        domain_tags=["general"],
    )
    assert result is not None
    assert result["blocked"] is True
    assert result["archive_strength"] == "system"
    assert result["overridable"] is True
    assert result["reason"] == "system_archive"


# ---------------------------------------------------------------------------
# 3. Replacement archive blocks exact duplicates only
# ---------------------------------------------------------------------------


def test_replacement_archive_blocks_exact_duplicate(db):
    """Replacement-strength archive blocks exact duplicate signature."""
    rule = _FakeRule()
    create_archived_fingerprint(db, rule, strength="replacement")

    # Exact same inputs -> blocked
    result = check_fingerprint_block(
        db,
        trigger_canonical="when user asks for help",
        action_instruction="provide helpful response",
        domain_tags=["general"],
    )
    assert result is not None
    assert result["blocked"] is True
    assert result["archive_strength"] == "replacement"
    assert result["reason"] == "replacement_blocks_equivalent_or_weaker"

    # Different trigger -> not blocked (different signature)
    result2 = check_fingerprint_block(
        db,
        trigger_canonical="when user requests debugging assistance",
        action_instruction="provide helpful response",
        domain_tags=["general"],
    )
    assert result2 is None


# ---------------------------------------------------------------------------
# 4. Narrower scope rule can pass user archive with scope_change_evidence
# ---------------------------------------------------------------------------


def test_user_archive_passable_with_scope_change_evidence(db):
    """User archive blocks equivalent/broader rules even with scope_change_evidence.

    Only a genuinely NARROWER-scope rule (different token set) can pass.
    Exact same trigger/action is NOT narrower so remains blocked (spec section 3.5).
    """
    rule = _FakeRule()
    create_archived_fingerprint(db, rule, strength="user")

    # Without evidence -> blocked
    result_blocked = check_fingerprint_block(
        db,
        trigger_canonical="when user asks for help",
        action_instruction="provide helpful response",
        domain_tags=["general"],
    )
    assert result_blocked is not None
    assert result_blocked["blocked"] is True

    # With scope_change_evidence but SAME scope (exact signature) -> still blocked
    # because it's not narrower
    result_same = check_fingerprint_block(
        db,
        trigger_canonical="when user asks for help",
        action_instruction="provide helpful response",
        domain_tags=["general"],
        scope_change_evidence="narrower scope: only applies to Python files",
    )
    assert result_same is not None
    assert result_same["blocked"] is True

    # A genuinely narrower/different rule with scope_change_evidence may pass
    # (related but non-exact match triggers the is_narrower_scope logic)
    result_narrower = check_fingerprint_block(
        db,
        trigger_canonical="when user asks for help in Python specifically with pytest",
        action_instruction="provide pytest-specific test guidance",
        domain_tags=["python", "testing"],
        scope_change_evidence="narrower: only pytest context, not general help",
    )
    # Different enough to not match the exact signature, and narrower logic applies
    assert result_narrower is None


# ---------------------------------------------------------------------------
# 5. compute_signature is stable for same input
# ---------------------------------------------------------------------------


def test_compute_signature_stable():
    """Same inputs always produce the same signature."""
    sig1 = compute_signature("trigger text", "action text", ["tag_a", "tag_b"])
    sig2 = compute_signature("trigger text", "action text", ["tag_a", "tag_b"])
    assert sig1 == sig2

    # Order of tags doesn't matter (sorted internally)
    sig3 = compute_signature("trigger text", "action text", ["tag_b", "tag_a"])
    assert sig1 == sig3

    # Different input -> different signature
    sig4 = compute_signature("different trigger", "action text", ["tag_a", "tag_b"])
    assert sig4 != sig1


# ---------------------------------------------------------------------------
# 6. create_archived_fingerprint stores all fields
# ---------------------------------------------------------------------------


def test_create_archived_fingerprint_stores_fields(db):
    """create_archived_fingerprint persists all expected fields."""
    rule = _FakeRule(
        id="rule-abc",
        trigger_canonical="do X when Y",
        action_instruction="perform Z",
        domain_tags=["python", "testing"],
        tool_tags=["pytest"],
        path_patterns=["tests/**"],
    )
    fp_id = create_archived_fingerprint(db, rule, strength="system")

    row = db.fetchone(
        "SELECT * FROM archived_fingerprints WHERE id = ?", (fp_id,)
    )
    assert row is not None
    assert row["rule_id"] == "rule-abc"
    assert row["archive_strength"] == "system"
    assert row["blocked_trigger_area"] == "do X when Y"
    assert row["blocked_action_area"] == "perform Z"
    assert row["signature"] == compute_signature(
        "do X when Y", "perform Z", ["python", "testing"]
    )
    assert row["created_at"] is not None
    # scope_summary should mention domain tags
    assert "python" in row["scope_summary"]


# ---------------------------------------------------------------------------
# 7. check_fingerprint_block returns None when no conflict
# ---------------------------------------------------------------------------


def test_check_fingerprint_block_no_conflict(db):
    """No archived fingerprint matching the signature -> returns None."""
    result = check_fingerprint_block(
        db,
        trigger_canonical="completely novel trigger",
        action_instruction="novel action",
        domain_tags=["novel"],
    )
    assert result is None
