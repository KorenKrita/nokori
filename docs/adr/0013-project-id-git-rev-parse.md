# Project ID derived from git root path hash

`resolve_project_id(cwd)` runs `git rev-parse --show-toplevel`, then computes `sha256(resolved_root)[:8]` formatted as `{dirname}-{hash}`. Result is LRU-cached (64 entries).

Threat model: running git in a directory with a malicious `.git/config` is the same class of risk as running git there at all. No additional sandbox. Non-git directories use cwd path hash as fallback.
