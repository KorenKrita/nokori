# Candidate TTL uses calendar days, not active days

Candidates expire 20 calendar days after `created_at` (40 for anti_pattern). Maintenance scan runs at most every 30 days.

Considered: counting only days with active Claude sessions. Rejected because it requires additional session-day tracking and delays cleanup for infrequent users. Calendar days are simpler and predictable. May revisit in v0.2.
