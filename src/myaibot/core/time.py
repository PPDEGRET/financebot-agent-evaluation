"""Time helpers for timestamp-safe trading research.

The project treats `available_at` as the only clock that matters for model input.
Source timestamps such as `published_at` are evidence, but a replay can only see an
item once `available_at <= decision_time`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Iterable


def ensure_utc(value: datetime | date | str) -> datetime:
    """Return a timezone-aware UTC datetime."""
    if isinstance(value, str):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min)
    else:  # pragma: no cover - defensive branch
        raise TypeError(f"Unsupported time value: {type(value)!r}")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def is_available(available_at: datetime | date | str, decision_time: datetime | date | str) -> bool:
    """True when a data item is legal to use at `decision_time`."""
    return ensure_utc(available_at) <= ensure_utc(decision_time)


def require_all_available(
    available_times: Iterable[datetime | date | str], decision_time: datetime | date | str
) -> None:
    """Raise when any timestamp is unavailable for the requested decision time."""
    cutoff = ensure_utc(decision_time)
    illegal = [ensure_utc(t) for t in available_times if ensure_utc(t) > cutoff]
    if illegal:
        first = min(illegal)
        raise ValueError(f"Data leakage: item available at {first.isoformat()} after {cutoff.isoformat()}")
