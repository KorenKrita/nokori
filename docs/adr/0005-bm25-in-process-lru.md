# BM25 uses in-process LRU, no persistent index

IDF is computed on-the-fly over the current rule list. A process-internal LRU cache (capacity 64) keyed by `(rule.id, updated_at)` reuses previously built indexes. No SQLite inverted index.

Rationale: ~500 rules complete in <100ms. Persistent indexing adds complexity without measurable benefit at current scale. Revisit if rule count reaches thousands.
