# Extract uses fcntl exclusive lock for single-instance

`{data_dir}/extract.lock` with `fcntl.flock` (Unix) or `msvcrt.locking` (Windows) ensures only one extract process runs at a time. A second invocation exits immediately (exit code 2).

This prevents parallel merge operations from producing duplicate rules or conflicting LLM calls. The `--session` flag shares the same lock.
