from __future__ import annotations

import argparse
from dataclasses import replace

from ..config import Config
from ..db import dumps_json, fetch_rule_by_short_id, open_db
from ..errors import NokoriError
from ..matcher.compiler import validate_rule_compilation
from ..search.embedding import index_rule_if_enabled
from ..search.evidence import trigger_data_for_rule
from ..utils.text import split_csv
from ..utils.time import now_iso

_EDITABLE_COLUMNS = frozenset(
    {
        "trigger_canonical",
        "action_instruction",
        "status",
        "severity",
        "archived_reason",
        "replacement_id",
        "trigger_variants",
        "search_terms",
    }
)

_MATCHER_REVALIDATE_COLUMNS = frozenset(
    {"trigger_canonical", "trigger_variants", "search_terms"}
)


def run(args: argparse.Namespace, cfg: Config) -> int:
    db = open_db(cfg.db_path)
    try:
        rule = fetch_rule_by_short_id(db, args.short_id)
        if rule is None:
            raise NokoriError(f"no rule with short_id {args.short_id!r}")

        updates: list[tuple[str, str | int | None]] = []
        if args.trigger is not None:
            updates.append(("trigger_canonical", args.trigger))
        if args.action is not None:
            updates.append(("action_instruction", args.action))
        if getattr(args, "severity", None) is not None:
            _VALID_SEVERITIES = frozenset({"reminder", "high_risk", "gate_eligible"})
            if args.severity not in _VALID_SEVERITIES:
                raise NokoriError(
                    f"invalid severity {args.severity!r}; must be one of {sorted(_VALID_SEVERITIES)}"
                )
            if args.severity == "gate_eligible":
                raise NokoriError(
                    "manual gate_eligible severity changes are disabled; "
                    "Gate eligibility is assigned autonomously"
                )
            updates.append(("severity", args.severity))
        if args.status is not None:
            new_status = args.status
            allowed = {
                "candidate": {"archived"},
                "active": {"archived"},
                "trusted": {"archived"},
                "suppressed": {"archived"},
                "archived": set(),
            }
            if new_status not in allowed.get(rule.status, set()):
                raise NokoriError(f"invalid status transition {rule.status!r} -> {new_status!r}")
            updates.append(("status", new_status))
            if new_status == "archived":
                updates.append(("archived_reason", "manual_edit"))
        if args.variants is not None:
            updates.append(("trigger_variants", dumps_json(split_csv(args.variants))))
        if args.terms_en is not None or args.terms_zh is not None:
            terms = dict(rule.search_terms)
            if args.terms_en is not None:
                terms["en"] = split_csv(args.terms_en)
            if args.terms_zh is not None:
                terms["zh"] = split_csv(args.terms_zh)
            updates.append(("search_terms", dumps_json(terms)))

        if not updates:
            print("nothing to update")
            return 0

        now = now_iso()
        for col, _ in updates:
            if col not in _EDITABLE_COLUMNS:
                raise NokoriError(f"internal error: disallowed column {col!r}")
        updated_cols = {col for col, _ in updates}
        if updated_cols & _MATCHER_REVALIDATE_COLUMNS:
            proposed_values = dict(updates)
            proposed_rule = replace(
                rule,
                trigger_canonical=proposed_values.get(
                    "trigger_canonical", rule.trigger_canonical
                ),
                trigger_variants=split_csv(args.variants)
                if args.variants is not None
                else rule.trigger_variants,
                search_terms=terms
                if args.terms_en is not None or args.terms_zh is not None
                else rule.search_terms,
            )
            trigger_data = trigger_data_for_rule(proposed_rule)
            if trigger_data is None:
                raise NokoriError(
                    "trigger structure invalid: matcher compilation failed: "
                    "missing trigger structure"
                )
            compile_err = validate_rule_compilation(
                concepts=trigger_data.get("concepts", []),
                required_concept_groups=trigger_data.get("required_concept_groups", []),
                excluded_contexts=trigger_data.get("excluded_contexts", []),
                variants=trigger_data.get("variants", []),
                trigger_canonical=proposed_rule.trigger_canonical,
                search_terms=proposed_rule.search_terms,
            )
            if compile_err:
                raise NokoriError(f"trigger structure invalid: {compile_err}")
        sets = ", ".join(f"{col} = ?" for col, _ in updates)
        params: list = [val for _, val in updates]
        params.extend([now, rule.id])
        with db.transaction() as tx:
            tx.execute(
                f"UPDATE rules SET {sets}, updated_at = ? WHERE id = ?",
                tuple(params),
            )
        print(f"updated {rule.short_id}: {', '.join(c for c, _ in updates)}")
        reindex_cols = {
            "trigger_canonical",
            "trigger_variants",
            "search_terms",
            "action_instruction",
        }
        if reindex_cols & updated_cols:
            updated_rule = fetch_rule_by_short_id(db, args.short_id)
            if updated_rule and updated_rule.status not in ("archived", "suppressed"):
                index_rule_if_enabled(db, updated_rule, cfg)
    finally:
        db.close()

    return 0
