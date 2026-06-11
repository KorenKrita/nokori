# Gate markers are per-prompt-hash files

Path: `{data_dir}/gate_markers/{session}/{prompt_hash}.json`. Each user message gets its own marker file.

Rationale: rapid consecutive user messages could overwrite a single-file marker, causing PreToolUse to block for the wrong prompt. Per-hash files isolate each prompt's gate state.

prompt_hash is SHA256 of the user prompt, first 16 hex characters.
