from __future__ import annotations

from collections.abc import Iterable

from .marker import MarkerRule

# Keep rule text usable when dismiss_phrase is very long (misconfiguration).
_INJECTION_BUDGET_FLOOR = 500


def format_block_reason(rules: Iterable[MarkerRule], dismiss_phrase: str = "dismiss") -> str:
    rules = list(rules)
    if not rules:
        return ""
    lines = [
        "Nokori: past lessons apply to this action — read before proceeding.",
        "",
    ]
    for r in rules:
        lines.append(f"[{r.short_id}] ({r.source_type}) {r.action}")
        if r.rationale:
            lines.append(f"   why: {r.rationale}")
    lines.append("")
    lines.append(
        f"If a rule is outdated, say `{dismiss_phrase} <short_id>` "
        "in your reply or run `nokori dismiss <short_id>`. "
        "Re-issue the tool call to proceed."
    )
    return "\n".join(lines)


def format_injection(
    hot, warm, max_chars: int = 1500, dismiss_phrase: str = "dismiss"
) -> tuple[str, list[tuple[str, str]]]:
    """Build the additionalContext text for UserPromptSubmit.

    HOT: trigger + action + rationale (full).
    WARM: trigger + action one-liner.
    Caps total at max_chars; spillover from HOT becomes WARM.
    """
    if not hot and not warm:
        return "", []

    footer = (
        f"\n(Say `{dismiss_phrase} <short_id>` to retire an outdated rule.)"
    )
    budget = max(_INJECTION_BUDGET_FLOOR, max(0, max_chars - len(footer)))

    parts: list[str] = []
    parts.append("[Nokori] past lessons relevant to this prompt:")
    used = len(parts[0])

    sorted_hot = sorted(
        hot,
        key=lambda r: (
            0 if r.rule.severity == "high_risk" else 1,
            -r.rrf_score,
        ),
    )

    spillover = []
    rendered = False
    logged: list[tuple[str, str]] = []
    for r in sorted_hot:
        block = (
            f"\n[HOT {r.rule.short_id}] {r.rule.trigger_canonical}\n"
            f"  do: {r.rule.action_instruction}"
        )
        if used + len(block) > budget:
            spillover.append(r)
            continue
        parts.append(block)
        used += len(block)
        rendered = True
        logged.append((r.rule.id, "hot"))

    for r in spillover + list(warm):
        line = f"\n[warm {r.rule.short_id}] {r.rule.trigger_canonical} — {r.rule.action_instruction}"
        if used + len(line) > budget:
            break
        parts.append(line)
        used += len(line)
        rendered = True
        logged.append((r.rule.id, "warm"))

    if not rendered:
        return "", []

    parts.append(footer)
    return "".join(parts), logged


def format_cursor_user_notice(
    *,
    tool_name: str,
    rule_short_ids: list[str],
    dismiss_phrase: str = "dismiss",
    deferred: bool = False,
) -> str:
    """Short message for Cursor preToolUse user_message when a tool call is denied."""
    ids = ", ".join(rule_short_ids) if rule_short_ids else "matched rules"
    prefix = (
        "[Nokori] Paused this tool call"
        if deferred
        else "[Nokori] Paused this tool call (safety gate)"
    )
    lines = [
        f"{prefix} ({tool_name or 'tool'}).",
        f"Matched lesson(s): {ids}.",
        "The agent was given the full rule text in this turn's blocked response.",
        f"If a rule is outdated, say `{dismiss_phrase} <short_id>` or run "
        f"`nokori dismiss <short_id>`, then retry the tool call.",
    ]
    return "\n".join(lines)


def format_cursor_agent_delivery(
    injection_text: str,
    gate_rules: Iterable[MarkerRule],
    *,
    dismiss_phrase: str = "dismiss",
) -> str:
    """Full context for Cursor preToolUse agent_message (deny path)."""
    parts: list[str] = []
    if injection_text:
        parts.append(injection_text.strip())
    gate_only = [r for r in gate_rules if r]
    if gate_only:
        block = format_block_reason(gate_only, dismiss_phrase=dismiss_phrase)
        if block:
            if parts:
                parts.append("")
                parts.append("---")
                parts.append("")
            parts.append(block)
    if not parts:
        return ""
    return "\n".join(parts)


def select_gate_rules(hot):
    """HOT rules that trigger PreToolUse block (not the same as injection).

    Injection uses all formal-pool HOT/WARM tiers. Gate is narrower:
    trusted + gate_eligible severity only.
    solution / preference may still appear as HOT/WARM context but never block tools.
    """
    return [
        r
        for r in hot
        if r.rule.status == "trusted"
        and r.rule.severity == "gate_eligible"
    ]
