from __future__ import annotations

from datetime import datetime, timezone


def iso_of(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso_of(datetime.now(timezone.utc))


def parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
