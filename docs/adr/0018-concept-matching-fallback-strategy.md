# Concept matching uses relaxed fallback when extractor omits concepts

## Status

Accepted

## Context

The cold pipeline's `_draft_concepts` function generates concept structures from extractor output. When the extractor produces no `required_concepts` (either because the LLM omits the field or the extraction yields no distinctive concepts), the pipeline needs a fallback strategy.

Previously, the fallback used the trigger's full text as a single alias with `match_mode: any_alias`, requiring an exact substring match. This made candidate rules unmatchable in practice — shadow events never accumulated, blocking promotion entirely.

## Decision

1. **Extractor prompt now explicitly requests `required_concepts`** — the field is documented in the prompt with examples, reducing fallback frequency.

2. **Fallback uses `all_terms` mode with `required: False`**:
   - Alias text = trigger text (capped at 120 chars)
   - `match_mode: all_terms` — all tokens in the alias must appear in the prompt
   - `required: False` — aliases don't contribute to trigger_coverage anchors
   - The concept group still gates `required_concepts_match`, but the rule can fire via other evidence paths (trigger_coverage, strong variants)

3. **This is intentionally conservative**: draft-stage rules with no explicit concepts should not easily match. False negatives (rule doesn't match when it should) are acceptable at the candidate stage — the rewriter will produce proper concepts if the rule proves viable through other evidence.

## Consequences

- Candidate rules with no explicit concepts will rarely satisfy `required_concepts_match` via the fallback concept alone
- Rules can still fire via trigger_coverage and variant matching (other evidence paths)
- Once promoted and rewritten, proper concepts replace the fallback
- The schema now requires `search_terms` to always have both `en` and `zh` keys, enforced at validation
