# Local embedding uses one chunk per rule; remote uses conservative splitting

Local (Granite R2): one vector per rule, up to 24576 characters of `_rule_text`. Single `encode_document` call.

Remote API: defaults to 4000 chars x 2 chunks (~8K per rule), fitting `text-embedding-3-small` token limits.

Query retrieval uses only the first query vector. Chunk configuration (`chunk_size`, `chunk_count`) falls back to local defaults when unset; empty env vars are treated as unset.
