"""Tests for --global-eligible CLI flag and API endpoint."""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone


def _nokori(tmp_path, *args):
    env = {
        "NOKORI_DATA_DIR": str(tmp_path),
        "PATH": "/usr/bin:/bin",
        "NOKORI_EMBED_ENABLED": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    return subprocess.run(
        [sys.executable, "-m", "nokori", *args],
        capture_output=True,
        text=True,
        env=env,
    )


def _seed_rules_and_events(db, now: str) -> None:
    """Seed test rules and fire events for global-eligible testing."""
    with db.transaction() as tx:
        # Rule 1: trusted, project-scoped, 2 distinct projects (eligible)
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "status, severity, project_scope, project_id, "
            "created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','1.0.0',?,?,?,?,?,?,?,?)",
            (
                "rule-eligible-1",
                "elig01",
                "always use --force-with-lease instead of --force",
                "prevents overwriting others work on shared branches",
                "trusted",
                "reminder",
                "project",
                "proj-A",
                now,
                now,
            ),
        )
        # Fire events from 2 distinct projects
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, "
            "level, posthoc_label, project_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("fe-1", "rule-eligible-1", "s1", "h1", "warm", "observed_useful", "proj-A", now),
        )
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, "
            "level, posthoc_label, project_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("fe-2", "rule-eligible-1", "s2", "h2", "warm", "observed_useful", "proj-B", now),
        )

        # Rule 2: trusted, project-scoped, only 1 project (not eligible)
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "status, severity, project_scope, project_id, "
            "created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','1.0.0',?,?,?,?,?,?,?,?)",
            (
                "rule-not-eligible",
                "nelig1",
                "check error return values",
                "always handle errors explicitly",
                "trusted",
                "reminder",
                "project",
                "proj-A",
                now,
                now,
            ),
        )
        tx.execute(
            "INSERT INTO rule_fire_events (id, rule_id, session_id, prompt_hash, "
            "level, posthoc_label, project_id, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            ("fe-3", "rule-not-eligible", "s3", "h3", "warm", "observed_useful", "proj-A", now),
        )

        # Rule 3: trusted, already global (excluded)
        tx.execute(
            "INSERT INTO rules (id, short_id, schema_version, rule_version, "
            "created_by_pipeline_version, runtime_policy_version, "
            "trigger_canonical, action_instruction, "
            "status, severity, project_scope, project_id, "
            "created_at, updated_at) "
            "VALUES (?,?,1,1,'v1','1.0.0',?,?,?,?,?,?,?,?)",
            (
                "rule-already-global",
                "glob01",
                "never commit secrets",
                "use env vars or secret managers",
                "trusted",
                "reminder",
                "global",
                None,
                now,
                now,
            ),
        )


def test_global_eligible_shows_eligible_rules(tmp_path, monkeypatch):
    """--global-eligible shows only trusted project-scoped rules with 2+ distinct projects."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.config import Config
    from nokori.db import open_db

    cfg = Config.from_env()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    db = open_db(cfg.db_path)
    try:
        _seed_rules_and_events(db, now)
    finally:
        db.close()

    from nokori.policy import CROSS_PROJECT_PROMOTION_THRESHOLD

    r = _nokori(tmp_path, "list", "--global-eligible")
    assert r.returncode == 0, r.stderr
    # Eligible rule should appear
    assert "elig01" in r.stdout
    assert f"2/{CROSS_PROJECT_PROMOTION_THRESHOLD}" in r.stdout
    # Not-eligible rule (only 1 project) should NOT appear
    assert "nelig1" not in r.stdout
    # Already-global rule should NOT appear
    assert "glob01" not in r.stdout


def test_global_eligible_empty_state(tmp_path, monkeypatch):
    """--global-eligible prints informative message when no rules qualify."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))

    r = _nokori(tmp_path, "list", "--global-eligible")
    assert r.returncode == 0, r.stderr
    assert "no rules approaching cross-project promotion" in r.stdout


def test_global_eligible_api_endpoint(tmp_path, monkeypatch):
    """API endpoint returns global-eligible rules."""
    monkeypatch.setenv("NOKORI_DATA_DIR", str(tmp_path))
    from nokori.config import Config
    from nokori.db import open_db

    cfg = Config.from_env()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    db = open_db(cfg.db_path)
    try:
        _seed_rules_and_events(db, now)
    finally:
        db.close()

    from fastapi.testclient import TestClient

    from nokori.policy import CROSS_PROJECT_PROMOTION_THRESHOLD
    from nokori.web.app import create_app

    app = create_app(cfg)
    client = TestClient(app)
    resp = client.get("/api/lifecycle/global-eligible")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    assert data[0]["short_id"] == "elig01"
    assert data[0]["distinct_projects"] == 2
    assert data[0]["target"] == CROSS_PROJECT_PROMOTION_THRESHOLD
