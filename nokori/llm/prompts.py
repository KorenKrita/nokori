EXTRACT_PROMPT = """You read a Claude Code conversation transcript and extract behavioral rules worth remembering across sessions.

Output a JSON array of rule candidates. Each item:
{
  "trigger": "<English canonical scenario, NOT project/file names>",
  "trigger_variants": ["<2-3 alternative phrasings, English>"],
  "search_terms": {"en": ["<concrete keywords>"], "zh": ["<中文关键词>"]},
  "behavior": "<the wrong/old approach>",
  "action": "<the correct approach>",
  "rationale": "<one sentence evidence from the transcript>",
  "source_type": "correction" | "preference" | "solution" | "anti_pattern",
  "confidence": "high" | "medium"
}

source_type:
- correction: user corrected the assistant ("don't ...", "stop ...", explicit fix)
- preference: user expressed a preference ("we use pnpm", "in this codebase ...")
- solution: a working approach discovered after a clear failure-then-fix loop
- anti_pattern: an approach that failed and should be avoided

confidence:
- high: user explicitly stated the rule
- medium: inferred from a failure → fix loop, or implied preference

DO NOT extract:
- style micro-preferences mentioned only once in passing
- single-file or single-task specifics
- information that can be derived from code or git history
- factual recall, narrative summaries

If nothing useful, output []. No prose. JSON only.

TRANSCRIPT:
{transcript}
"""

MERGE_PROMPT = """Compare a NEW rule candidate against EXISTING rules. Decide each existing rule's relationship to the candidate.

NEW:
- trigger: {trigger}
- action: {action}
- source_type: {source_type}
- confidence: {confidence}

EXISTING:
{existing_formatted}

For each existing rule, choose one:
A) SAME — same lesson, different words → merge
B) BROADER — new is more general → new supersedes existing
C) NARROWER — new is a special case of existing → keep both
D) CONTRADICTS — opposite advice → new supersedes existing
E) UNRELATED — different topics → independent

When uncertain choose E (UNRELATED). Better to keep two than wrongly merge.

Output JSON:
{{"relationships": [{{"existing_id": "...", "judgment": "A|B|C|D|E", "reasoning": "..."}}]}}
"""
