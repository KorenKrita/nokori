# Gate blocks only once per user prompt

UserPromptSubmit writes a marker; the first matching PreToolUse consumes and deletes it. All subsequent tool calls in the same user message pass through. The next user message may write a new marker.

This prevents a single rule from repeatedly blocking an entire tool chain. The agent reads the rule on the first block and proceeds informed.
