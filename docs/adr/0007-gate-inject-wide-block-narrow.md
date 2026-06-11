# Injection is wide, Gate blocking is narrow

Injection (additionalContext): any formal pool HOT+WARM rule of any source_type that passes retrieval thresholds.

Gate blocking (PreToolUse deny): only the subset that is trusted + severity=gate_eligible + runtime applicability + tool evidence.

This means ordinary active rules, reminders, and high_risk rules can remind the agent but never block tool calls. Only proven, gate-eligible trusted rules get blocking authority.
