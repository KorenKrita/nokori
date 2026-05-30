from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def safe_log_url(url: str) -> str:
    """Redact credentials and query strings for log lines."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path or "", "", "", ""))
