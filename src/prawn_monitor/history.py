"""Downsample reading rows so a phone can chart a week of polls."""

from __future__ import annotations

from typing import Any


_NUMERIC = ("do_mg_l", "temp_f", "ph", "level_cm")


def bucket_readings(rows: list[dict[str, Any]], max_points: int = 400) -> list[dict[str, Any]]:
    if max_points < 1 or len(rows) <= max_points:
        return rows
    size = len(rows) / max_points
    out: list[dict[str, Any]] = []
    index = 0
    while index < len(rows):
        end = min(len(rows), int(index + size))
        if end <= index:
            end = index + 1
        chunk = rows[index:end]
        point: dict[str, Any] = {"ts": chunk[-1]["ts"]}
        for key in _NUMERIC:
            values = [row[key] for row in chunk if row.get(key) is not None]
            point[key] = (sum(values) / len(values)) if values else None
        states = [row.get("level_state") for row in chunk if row.get("level_state")]
        point["level_state"] = states[-1] if states else None
        out.append(point)
        index = end
    return out
