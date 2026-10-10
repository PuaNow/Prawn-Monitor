"""Import AMADEUS tracking CSV files and derive activity metrics.

AMADEUS (AGPL-3.0-only) is not bundled. The final CSV in its ``results/``
folder has one row per frame:

    frame,cx0,cy0,w0,h0,heading0,cx1,cy1,w1,h1,heading1,...

Coordinates are pixels, origin at the top left. A run without direction
estimation omits ``heading``. Empty fields are missing detections.

Speed and distance come from center displacement. Approximate body length is
the long side of the oriented box times ``mm_per_pixel``. Crowding is the
mean nearest-neighbor distance, plus the fraction of animals closer than a
threshold. Heading is accepted and kept out of these calculations.
"""

from __future__ import annotations

import csv
import io
import math
import re
import statistics
from typing import Any

_COL = re.compile(r"^(cx|cy|w|h|heading)(\d+)$", re.IGNORECASE)


def parse_tracking_csv(text: str) -> list[dict[str, Any]]:
    sample = text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(sample))
    if not reader.fieldnames or "frame" not in [name.strip().lower() for name in reader.fieldnames]:
        raise ValueError("CSV needs a frame column and cx/cy/w/h columns per animal id")
    field_map = {name.strip().lower(): name for name in reader.fieldnames}
    groups: dict[int, dict[str, str]] = {}
    for lowered, original in field_map.items():
        match = _COL.match(lowered)
        if not match:
            continue
        animal_id = int(match.group(2))
        groups.setdefault(animal_id, {})[match.group(1).lower()] = original
    if not groups:
        raise ValueError("CSV has no cx/cy/w/h columns")
    for animal_id, cols in groups.items():
        missing = {"cx", "cy", "w", "h"} - set(cols)
        if missing:
            raise ValueError(f"animal {animal_id} is missing {', '.join(sorted(missing))}")

    frames: list[dict[str, Any]] = []
    for line_no, row in enumerate(reader, start=2):
        raw_frame = (row.get(field_map["frame"]) or "").strip()
        if raw_frame == "":
            continue
        try:
            frame_index = int(float(raw_frame))
        except ValueError as exc:
            raise ValueError(f"line {line_no}: frame is not an integer") from exc
        animals: dict[int, dict[str, float | None]] = {}
        for animal_id, cols in groups.items():
            parsed: dict[str, float | None] = {}
            present = False
            for key in ("cx", "cy", "w", "h"):
                cell = (row.get(cols[key]) or "").strip()
                if cell == "" or cell.lower() == "nan":
                    parsed[key] = None
                else:
                    present = True
                    try:
                        parsed[key] = float(cell)
                    except ValueError as exc:
                        raise ValueError(f"line {line_no}: {key}{animal_id} is not a number") from exc
            if not present:
                continue
            if any(parsed[key] is None for key in ("cx", "cy", "w", "h")):
                continue
            animals[animal_id] = parsed
        frames.append({"frame": frame_index, "animals": animals})
    frames.sort(key=lambda item: item["frame"])
    if not frames:
        raise ValueError("CSV has no frame rows")
    return frames


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.fmean(values))


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def analyze_tracks(
    frames: list[dict[str, Any]],
    *,
    fps: float,
    mm_per_pixel: float,
    crowding_mm: float = 40.0,
    max_gap_frames: int = 3,
) -> dict[str, Any]:
    if fps <= 0:
        raise ValueError("fps must be positive")
    if mm_per_pixel <= 0:
        raise ValueError("mm_per_pixel must be positive")
    ids = sorted({animal_id for frame in frames for animal_id in frame["animals"]})
    previous: dict[int, tuple[int, float, float]] = {}
    cumulative = {animal_id: 0.0 for animal_id in ids}
    samples: list[dict[str, Any]] = []
    total_distance = 0.0
    all_lengths: list[float] = []
    speed_samples: list[float] = []

    for frame in frames:
        speeds: list[float] = []
        lengths: list[float] = []
        points: list[tuple[float, float]] = []
        for animal_id, animal in frame["animals"].items():
            cx = float(animal["cx"])
            cy = float(animal["cy"])
            points.append((cx, cy))
            length = max(float(animal["w"]), float(animal["h"])) * mm_per_pixel
            lengths.append(length)
            all_lengths.append(length)
            if animal_id in previous:
                prev_frame, prev_x, prev_y = previous[animal_id]
                gap = int(frame["frame"]) - prev_frame
                if 0 < gap <= max_gap_frames:
                    dist_mm = math.hypot(cx - prev_x, cy - prev_y) * mm_per_pixel
                    dt = gap / fps
                    speeds.append(dist_mm / dt)
                    cumulative[animal_id] += dist_mm
                    total_distance += dist_mm
            previous[animal_id] = (int(frame["frame"]), cx, cy)
        nearest: list[float] = []
        crowded = 0
        count = len(points)
        if count >= 2:
            for index, (x, y) in enumerate(points):
                best = None
                for other, (x2, y2) in enumerate(points):
                    if other == index:
                        continue
                    distance = math.hypot(x - x2, y - y2) * mm_per_pixel
                    if best is None or distance < best:
                        best = distance
                if best is None:
                    continue
                nearest.append(best)
                if best <= crowding_mm:
                    crowded += 1
        mean_speed = _mean(speeds)
        if mean_speed is not None:
            speed_samples.append(mean_speed)
        samples.append(
            {
                "frame": int(frame["frame"]),
                "t_s": int(frame["frame"]) / fps,
                "mean_speed_mm_s": mean_speed,
                "cum_distance_mm": float(sum(cumulative.values())),
                "mean_nn_mm": _mean(nearest),
                "crowded_fraction": (crowded / count) if count >= 2 else None,
                "mean_length_mm": _mean(lengths),
            }
        )

    per_animal = [
        {"id": animal_id, "distance_mm": cumulative[animal_id]} for animal_id in ids
    ]
    return {
        "summary": {
            "frames": len(frames),
            "animals": len(ids),
            "fps": fps,
            "mm_per_pixel": mm_per_pixel,
            "crowding_mm": crowding_mm,
            "total_distance_mm": total_distance,
            "mean_distance_mm": (total_distance / len(ids)) if ids else 0.0,
            "mean_speed_mm_s": _mean(speed_samples),
            "median_length_mm": _median(all_lengths),
            "per_animal": per_animal,
            "length_note": (
                "Approximate body length is the long side of the AMADEUS oriented "
                "box times mm per pixel. It is not a caliper measurement."
            ),
        },
        "samples": samples,
    }
