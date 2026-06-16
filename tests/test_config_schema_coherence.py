"""Verify Config dataclass fields stay in sync with config_schema FIELDS.

Catches the bug class where a developer adds a Config field but forgets
to add the corresponding schema entry (or vice versa).
"""
import dataclasses

from nokori.config import Config
from nokori.config_schema import FIELDS

# Config fields that are computed/internal and intentionally have no
# direct schema entry. Each must have a comment justifying exclusion.
_EXCLUDED_CONFIG_FIELDS = frozenset({
    "embed_chunk_size_configured",   # computed: whether user explicitly set chunk_size
    "embed_chunk_count_configured",  # computed: whether user explicitly set chunk_count
    "role_models",                   # dict populated from [models] section; schema has per-role entries
    "role_max_tokens",               # dict populated from [models.limits]; schema has per-role entries
    "role_timeouts",                 # dict populated from [models.timeouts]; schema has per-role entries
})

# Schema entries that map to Config dict fields (role_models, etc.)
# rather than to a single scalar Config attribute.
_SCHEMA_DICT_FIELD_PREFIXES = ("models.",)


def _schema_id_to_config_field(schema_id: str) -> str:
    """Convert schema dotted ID to Config flat field name."""
    return schema_id.replace(".", "_")


def test_every_config_field_has_schema_entry():
    """Non-excluded Config fields must have a matching schema entry."""
    config_fields = {f.name for f in dataclasses.fields(Config)}
    schema_config_names = set()
    for field in FIELDS:
        if any(field.id.startswith(p) for p in _SCHEMA_DICT_FIELD_PREFIXES):
            continue
        schema_config_names.add(_schema_id_to_config_field(field.id))

    testable = config_fields - _EXCLUDED_CONFIG_FIELDS
    missing_in_schema = testable - schema_config_names
    assert not missing_in_schema, (
        f"Config fields without schema entry (add to config_schema.py or _EXCLUDED_CONFIG_FIELDS): "
        f"{sorted(missing_in_schema)}"
    )


def test_every_scalar_schema_entry_has_config_field():
    """Non-dict schema entries must map to a Config dataclass field."""
    config_fields = {f.name for f in dataclasses.fields(Config)}
    for field in FIELDS:
        if any(field.id.startswith(p) for p in _SCHEMA_DICT_FIELD_PREFIXES):
            continue
        expected_config_field = _schema_id_to_config_field(field.id)
        assert expected_config_field in config_fields, (
            f"Schema field {field.id!r} expects Config.{expected_config_field} but it doesn't exist"
        )


def test_excluded_fields_actually_exist():
    """Prevent stale entries in the exclusion set."""
    config_fields = {f.name for f in dataclasses.fields(Config)}
    stale = _EXCLUDED_CONFIG_FIELDS - config_fields
    assert not stale, f"_EXCLUDED_CONFIG_FIELDS contains non-existent fields: {sorted(stale)}"
