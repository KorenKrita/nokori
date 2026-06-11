# V6 lifecycle: autonomous rule quality flywheel

Schema-breaking redesign replacing the old v0.1 lifecycle (dormant/merged states, manual curation) with an autonomous flywheel: candidate extraction -> quality judgment -> merge planning -> synthetic eval -> injection -> posthoc evaluation -> automatic state transitions.

Key constraints:
- Hot path remains LLM-free; all judgment is cold/posthoc
- State upgrades are slow and evidence-heavy; downgrades are fast
- Precision over recall: fewer good rules > many noisy rules
- No manual promote/trust/suppress controls; user archive is the only manual intervention
- Different LLM roles use configurable model ids with role-specific prompts
- Embedding thresholds come from checked-in benchmark profiles, not user data

States: candidate -> active -> trusted / suppressed -> archived. No dormant, no merged, no quarantined.

The full design specification lives in `docs/autonomous-rule-quality-flywheel-plan.md` (retained as reference for implementation stages not yet complete).
