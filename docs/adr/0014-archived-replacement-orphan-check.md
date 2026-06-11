# Archived rules with dead replacements recover as candidates

Maintenance (every 90 days max) checks archived rules whose `replacement_id` points to a deleted, suppressed, or archived rule. These are restored to candidate status with cleared replacement metadata.

Rationale: replacement archiving is automatic merge behavior, not user veto. If the replacement itself fails, the original rule deserves a chance to re-prove itself through the candidate shadow/posthoc path — not to return directly to active.
