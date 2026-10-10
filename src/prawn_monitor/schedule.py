"""Local-time slots for fixed overhead recordings."""

from __future__ import annotations

from datetime import datetime


def slot_key(moment: datetime, hhmm: str) -> str:
    return f"{moment.date().isoformat()} {hhmm}"


def slots_due(
    now_local: datetime,
    times: list[str],
    fired: set[str],
    lookback_s: float = 90.0,
) -> list[str]:
    """Return HH:MM slots whose scheduled instant has arrived and not fired.

    ``lookback_s`` covers a poll loop that wakes a bit after the minute.
    """
    due: list[str] = []
    for hhmm in times:
        hour_text, minute_text = hhmm.split(":")
        target = now_local.replace(
            hour=int(hour_text),
            minute=int(minute_text),
            second=0,
            microsecond=0,
        )
        delta = (now_local - target).total_seconds()
        if delta < 0 or delta > lookback_s:
            continue
        key = slot_key(now_local, hhmm)
        if key in fired:
            continue
        due.append(hhmm)
    return due
