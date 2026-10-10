"""Default Litopenaeus vannamei limits and deep-merge overrides.

The nursery band is the one used at NorCal Premier Prawn unless a tank
overrides it. Comparisons are strict: a value sitting on the limit is inside
the band ("under 5", "over 0.5", "over 2 F").
"""

from __future__ import annotations

import copy
from typing import Any

# Low water is critical because a nursery can uncover the biofilter intake.
# High water is a warning. Both are configurable.
DEFAULT_THRESHOLDS: dict[str, Any] = {
    "do_mg_l": {"warn_below": 5.0, "critical_below": 4.0},
    "temp_f": {
        "low": 82.0,
        "high": 86.0,
        "swing_warn_above": 2.0,
        "swing_hours": 24.0,
    },
    "ph": {"low": 7.5, "high": 8.3},
    "tan_mg_l": {"warn_above": 0.5, "critical_above": 1.0},
    "nitrite_mg_l": {"warn_above": 0.5},
    "nitrate_mg_l": {"warn_above": 100.0},
    "alkalinity_mg_l": {"warn_below": 120.0},
    "water_level": {
        "low_severity": "critical",
        "high_severity": "warning",
    },
    "offline": {
        "node_s": 120.0,
        "node_severity": "critical",
        "sensor_severity": "warning",
        "critical_sensors": ["dissolved_oxygen"],
    },
    "cooldown_s": {"warning": 3600.0, "critical": 900.0},
}


def deep_merge(base: dict[str, Any], override: dict[str, Any] | None) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    if not override:
        return merged
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def effective_thresholds(*layers: dict[str, Any] | None) -> dict[str, Any]:
    merged: dict[str, Any] = copy.deepcopy(DEFAULT_THRESHOLDS)
    for layer in layers:
        merged = deep_merge(merged, layer)
    return merged
