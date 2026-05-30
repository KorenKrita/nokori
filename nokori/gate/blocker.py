from __future__ import annotations

from collections.abc import Iterable

from .marker import MarkerRule


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
) -> str:
    """Build the additionalContext text for UserPromptSubmit.

    HOT: trigger + action + rationale (full).
    WARM: trigger + action one-liner.
    Caps total at max_chars; spillover from HOT becomes WARM.
    """
    if not hot and not warm:
        return ""

    parts: list[str] = []
    parts.append("[Nokori] past lessons relevant to this prompt:")
    used = len(parts[0]) + 1

    sorted_hot = sorted(
        hot,
        key=lambda r: (
            0 if r.rule.source_type == "correction" else 1,
            -r.rrf_score,
        ),
    )

    spillover = []
    for r in sorted_hot:
        block = (
            f"\n[HOT {r.rule.short_id}] {r.rule.trigger_text}\n"
            f"  do: {r.rule.action}"
        )
        if r.rule.rationale:
            block += f"\n  why: {r.rule.rationale}"
        if used + len(block) > max_chars:
            spillover.append(r)
            continue
        parts.append(block)
        used += len(block)

    for r in list(warm) + spillover:
        line = f"\n[warm {r.rule.short_id}] {r.rule.trigger_text} — {r.rule.action}"
        if used + len(line) > max_chars:
            break
        parts.append(line)
        used += len(line)

    parts.append(
        f"\n(Say `{dismiss_phrase} <short_id>` to retire an outdated rule.)"
    )
    return "".join(parts)


def select_gate_rules(hot):
    """Subset of HOT that meets gate criteria: confidence=high + status=active."""
    return [
        r
        for r in hot
        if r.rule.confidence == "high" and r.rule.status == "active"
    ]
