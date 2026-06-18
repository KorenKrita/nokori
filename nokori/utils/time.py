from __future__ import annotations

from datetime import datetime, timedelta


def local_now() -> datetime:
    """Return current time as an aware datetime in the local timezone."""
    return datetime.now().astimezone()


def iso_of(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def now_iso() -> str:
    return iso_of(local_now())


def local_days_ago(days: int) -> str:
    """Return ISO timestamp for N days ago in local time."""
    return iso_of(local_now() - timedelta(days=days))


def local_hours_ago(hours: int) -> str:
    """Return ISO timestamp for N hours ago in local time."""
    return iso_of(local_now() - timedelta(hours=hours))


def parse_iso(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.astimezone()
        return dt
    except ValueError:
        pass
    try:
        return datetime.strptime(iso, "%Y-%m-%d %H:%M:%S").astimezone()
    except ValueError:
        return None


def normalize_db_timestamp(value: str | None) -> str | None:
    """Normalize API timestamp input to nokori's local DB timestamp string."""
    dt = parse_iso(value)
    if dt is None:
        return value
    # Normalize to local tz: parse_iso may return a non-local aware datetime
    # (e.g. a UTC timestamp parsed from an ISO Z suffix).
    return iso_of(dt.astimezone())
