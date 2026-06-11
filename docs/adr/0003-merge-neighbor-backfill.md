# Merge backfills recent rules when BM25 neighbors are sparse

When BM25 returns fewer than 5 neighbors for a candidate during merge, the system backfills with the most recently updated rules (up to the 20-rule limit). This prevents zero-token-overlap scenarios from completely missing SAME/BROADER/CONTRADICTS relationships.

Cost: may send irrelevant rules to the LLM (producing E/UNRELATED judgments and wasting tokens). Accepted because the cold path is latency-tolerant and missing a merge is worse than extra LLM calls.

Constants: `MERGE_NEIGHBOR_LIMIT=20`, `MERGE_RECENT_FALLBACK=5`. No env switch to disable. If pool is empty, LLM is skipped entirely and the candidate is inserted directly.
