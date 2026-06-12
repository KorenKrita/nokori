"""Tests for RoleSpec unified registry."""

from __future__ import annotations


class TestRoleSpec:
    def test_all_roles_have_spec(self):
        from nokori.cold.roles import ROLE_IDS, ROLE_SPECS

        for role_id in ROLE_IDS:
            assert role_id in ROLE_SPECS, f"Missing RoleSpec for {role_id}"

    def test_spec_fields_match_legacy_dicts(self):
        from nokori.cold.roles import (
            DEFAULT_MAX_TOKENS,
            DEFAULT_TIMEOUTS,
            PROMPT_VERSIONS,
            ROLE_SCHEMAS,
            ROLE_SPECS,
        )

        for role_id, spec in ROLE_SPECS.items():
            assert spec.prompt_version == PROMPT_VERSIONS[role_id]
            assert spec.max_tokens == DEFAULT_MAX_TOKENS[role_id]
            assert spec.timeout == DEFAULT_TIMEOUTS[role_id]
            assert spec.schema is ROLE_SCHEMAS[role_id]

    def test_spec_is_frozen(self):
        import pytest
        from nokori.cold.roles import ROLE_SPECS

        spec = ROLE_SPECS["admission_judge"]
        with pytest.raises(AttributeError):
            spec.max_tokens = 9999  # type: ignore[misc]

    def test_resolve_model_id(self):
        from nokori.cold.roles import PROVIDER_DEFAULT_MODEL, resolve_model_id

        model = resolve_model_id("admission_judge", {"admission_judge": "custom-model"}, None)
        assert model == "custom-model"

        model_default = resolve_model_id("admission_judge", None, None)
        assert model_default == PROVIDER_DEFAULT_MODEL
