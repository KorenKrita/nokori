# All hooks fail-open by default

Any hook exception is caught and returns `{"continue": true}` with `log.exception`. This prevents Nokori from blocking Claude Code sessions when something goes wrong internally.

`NOKORI_STRICT=1` makes hooks re-raise exceptions for debugging purposes only.
