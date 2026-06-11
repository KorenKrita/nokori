# Formal and shadow pools share one retrieval pass

`retrieve_formal_and_shadow` combines `formal ∪ shadow_only` into one pool, runs BM25 (+ optional embedding RRF) once, then splits results by id set. Shadow HOT triggers `record_shadow_hit`; formal HOT/WARM triggers injection.

Considered: separate retrieval passes or SessionEnd async shadow scoring. Rejected because a single pass is simpler and the pool size increase is marginal (shadow rules are few early on). If hook latency becomes a problem, shadow can be moved to a background pass in v0.2.
