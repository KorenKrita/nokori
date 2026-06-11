# Cross-project promotion is on by default

When a candidate/suppressed rule is retrieved as shadow HOT in 3+ distinct project_ids, it automatically becomes `project_scope=global`. No CLI confirmation step.

Rationale: target users work across 2-3 related repos and want corrections/anti_patterns to propagate without manual `nokori promote`. The threshold of 3 distinct project_ids (deduplicated by promotion_evidence key, not raw hit count) balances false promotion against cold-start.

`preference` source_type is excluded from shadow hit counting and promotion because preferences are project-specific by nature.

Disable with `NOKORI_PROMOTION_ENABLED=0` (also disables shadow pool loading entirely). This is an explicit product switch, not an implementation gap.
