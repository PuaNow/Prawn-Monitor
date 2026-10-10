"""Pure alert evaluation and cooldown planning.

``evaluate_conditions`` looks at one moment. ``plan_alert_actions`` decides
whether that moment should open, escalate, or resolve a stored alert. Delivery
and acknowledgement live outside this module so the rules can be tested
without a database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Mapping

from prawn_monitor.timeutil import as_utc

SEVERITY_RANK = {"warning": 1, "critical": 2}
EXPECTED_SENSORS = ("dissolved_oxygen", "temperature", "ph", "water_level")


@dataclass(frozen=True)
class Condition:
    kind: str
    severity: str
    message: str
    value: float | None = None
    unit: str = ""


@dataclass(frozen=True)
class PriorAlert:
    """Latest stored alert for one kind."""

    kind: str
    severity: str
    opened_at: datetime
    acked_at: datetime | None = None
    resolved_at: datetime | None = None
    notified_at: datetime | None = None


@dataclass(frozen=True)
class AlertAction:
    op: str  # open, escalate, resolve
    kind: str
    severity: str
    message: str
    value: float | None = None
    unit: str = ""


def _fmt(value: float) -> str:
    return f"{value:.2f}"


def _limit(value: float) -> str:
    return f"{value:g}"


def _over(
    *,
    tank: str,
    kind: str,
    label: str,
    value: float,
    warn: float | None,
    critical: float | None,
    unit: str,
    when: str = "",
) -> Condition | None:
    severity = None
    limit = None
    if critical is not None and value > critical:
        severity = "critical"
        limit = critical
    elif warn is not None and value > warn:
        severity = "warning"
        limit = warn
    if severity is None or limit is None:
        return None
    extra = f" {when}" if when else ""
    return Condition(
        kind=kind,
        severity=severity,
        message=(
            f"{tank} {label} {_fmt(value)} {unit}{extra} is over the "
            f"{severity} limit of {_limit(limit)} {unit}."
        ),
        value=value,
        unit=unit,
    )


def _under(tank: str, kind: str, label: str, value: float, limit: float, unit: str, when: str = "") -> Condition:
    extra = f" {when}" if when else ""
    return Condition(
        kind=kind,
        severity="warning",
        message=(
            f"{tank} {label} {_fmt(value)} {unit}{extra} is under the "
            f"warning limit of {_limit(limit)} {unit}."
        ),
        value=value,
        unit=unit,
    )


def _sensor_map(sensors: Mapping[str, Any] | None) -> dict[str, Any]:
    if not sensors:
        return {}
    return dict(sensors)


def _ok_value(sensors: Mapping[str, Any], kind: str) -> tuple[bool, Any]:
    row = sensors.get(kind)
    if not isinstance(row, dict):
        return False, None
    if not row.get("ok", False):
        return False, row.get("value")
    return True, row.get("value")


def evaluate_conditions(
    *,
    tank_name: str,
    now: datetime,
    node_online: bool,
    sensors: Mapping[str, Any] | None,
    temp_history: list[tuple[datetime | str, float]] | None,
    manual: Mapping[str, tuple[float, datetime | str]] | None,
    thresholds: Mapping[str, Any],
    expected_sensors: tuple[str, ...] = EXPECTED_SENSORS,
) -> list[Condition]:
    """Return active conditions. The caller supplies the latest manual value per field."""
    now = as_utc(now)
    found: list[Condition] = []
    if not node_online:
        offline = thresholds["offline"]
        found.append(
            Condition(
                kind="node_offline",
                severity=str(offline.get("node_severity", "critical")),
                message=f"{tank_name} node is offline.",
            )
        )
        return found

    rows = _sensor_map(sensors)
    offline_cfg = thresholds["offline"]
    critical_sensors = set(offline_cfg.get("critical_sensors") or [])
    default_sensor_severity = str(offline_cfg.get("sensor_severity", "warning"))
    for kind in expected_sensors:
        ok, _value = _ok_value(rows, kind)
        if ok:
            continue
        severity = "critical" if kind in critical_sensors else default_sensor_severity
        label = kind.replace("_", " ")
        found.append(
            Condition(
                kind=f"sensor_offline:{kind}",
                severity=severity,
                message=f"{tank_name} {label} sensor is offline.",
            )
        )

    do_ok, do_value = _ok_value(rows, "dissolved_oxygen")
    if do_ok and do_value is not None:
        do_cfg = thresholds["do_mg_l"]
        value = float(do_value)
        if value < float(do_cfg["critical_below"]):
            found.append(
                Condition(
                    kind="dissolved_oxygen",
                    severity="critical",
                    message=(
                        f"{tank_name} dissolved oxygen {_fmt(value)} mg/L is under the "
                        f"critical limit of {_limit(float(do_cfg['critical_below']))} mg/L."
                    ),
                    value=value,
                    unit="mg/L",
                )
            )
        elif value < float(do_cfg["warn_below"]):
            found.append(
                Condition(
                    kind="dissolved_oxygen",
                    severity="warning",
                    message=(
                        f"{tank_name} dissolved oxygen {_fmt(value)} mg/L is under the "
                        f"warning limit of {_limit(float(do_cfg['warn_below']))} mg/L."
                    ),
                    value=value,
                    unit="mg/L",
                )
            )

    temp_ok, temp_value = _ok_value(rows, "temperature")
    temp_cfg = thresholds["temp_f"]
    if temp_ok and temp_value is not None:
        temp_f = float(temp_value)
        low = float(temp_cfg["low"])
        high = float(temp_cfg["high"])
        if temp_f < low or temp_f > high:
            found.append(
                Condition(
                    kind="temperature",
                    severity="warning",
                    message=(
                        f"{tank_name} temperature {_fmt(temp_f)} F is outside "
                        f"{_limit(low)}-{_limit(high)} F."
                    ),
                    value=temp_f,
                    unit="F",
                )
            )

    history = []
    for ts, temp_f in temp_history or []:
        history.append((as_utc(ts), float(temp_f)))
    if history:
        window = timedelta(hours=float(temp_cfg["swing_hours"]))
        recent = [temp for ts, temp in history if now - window <= ts <= now]
        if len(recent) >= 2:
            swing = max(recent) - min(recent)
            limit = float(temp_cfg["swing_warn_above"])
            if swing > limit:
                found.append(
                    Condition(
                        kind="temperature_swing",
                        severity="warning",
                        message=(
                            f"{tank_name} temperature moved {_fmt(swing)} F over the last "
                            f"{_limit(float(temp_cfg['swing_hours']))} h "
                            f"(from {_fmt(min(recent))} F to {_fmt(max(recent))} F, "
                            f"limit {_limit(limit)} F)."
                        ),
                        value=swing,
                        unit="F",
                    )
                )

    ph_ok, ph_value = _ok_value(rows, "ph")
    if ph_ok and ph_value is not None:
        ph = float(ph_value)
        low = float(thresholds["ph"]["low"])
        high = float(thresholds["ph"]["high"])
        if ph < low or ph > high:
            found.append(
                Condition(
                    kind="ph",
                    severity="warning",
                    message=(
                        f"{tank_name} pH {_fmt(ph)} is outside {_limit(low)}-{_limit(high)}."
                    ),
                    value=ph,
                    unit="pH",
                )
            )

    level_ok, level_value = _ok_value(rows, "water_level")
    if level_ok and level_value in ("low", "high"):
        cfg = thresholds["water_level"]
        severity = str(cfg["low_severity"] if level_value == "low" else cfg["high_severity"])
        found.append(
            Condition(
                kind="water_level",
                severity=severity,
                message=f"{tank_name} water level is {level_value}.",
                unit="state",
            )
        )

    for field, kind, label, unit, spec in (
        ("tan_mg_l", "tan", "TAN", "mg/L", thresholds["tan_mg_l"]),
        ("nitrite_mg_l", "nitrite", "nitrite", "mg/L", thresholds["nitrite_mg_l"]),
        ("nitrate_mg_l", "nitrate", "nitrate", "mg/L", thresholds["nitrate_mg_l"]),
    ):
        sample = (manual or {}).get(field)
        if not sample:
            continue
        value, tested_at = sample
        when = f"(tested {as_utc(tested_at).strftime('%Y-%m-%d %H:%M UTC')})"
        hit = _over(
            tank=tank_name,
            kind=kind,
            label=label,
            value=float(value),
            warn=spec.get("warn_above"),
            critical=spec.get("critical_above"),
            unit=unit,
            when=when,
        )
        if hit:
            found.append(hit)

    alk = (manual or {}).get("alkalinity_mg_l")
    if alk:
        value, tested_at = alk
        limit = float(thresholds["alkalinity_mg_l"]["warn_below"])
        if float(value) < limit:
            when = as_utc(tested_at).strftime("%Y-%m-%d %H:%M UTC")
            found.append(
                Condition(
                    kind="alkalinity",
                    severity="warning",
                    message=(
                        f"{tank_name} alkalinity {_fmt(float(value))} mg/L as CaCO3 "
                        f"(tested {when}) is under the warning limit of {_limit(limit)} mg/L."
                    ),
                    value=float(value),
                    unit="mg/L",
                )
            )
    return found


def plan_alert_actions(
    conditions: list[Condition],
    priors: list[PriorAlert],
    now: datetime,
    cooldown_s: Mapping[str, float],
) -> list[AlertAction]:
    """Turn active conditions plus the latest alert per kind into stored actions.

    An unacknowledged open alert is not repeated. Acknowledgement starts the
    cooldown; when the condition is still true after the cooldown, a new alert
    is opened. A jump from warning to critical notifies immediately. A cleared
    condition resolves the open alert.
    """
    now = as_utc(now)
    by_kind: dict[str, PriorAlert] = {}
    for prior in priors:
        by_kind[prior.kind] = prior
    active: dict[str, Condition] = {}
    for condition in conditions:
        current = active.get(condition.kind)
        if current is None or SEVERITY_RANK[condition.severity] > SEVERITY_RANK[current.severity]:
            active[condition.kind] = condition

    actions: list[AlertAction] = []
    for kind, prior in by_kind.items():
        if kind not in active and prior.resolved_at is None:
            actions.append(
                AlertAction(
                    op="resolve",
                    kind=kind,
                    severity=prior.severity,
                    message="",
                )
            )

    for kind, condition in active.items():
        prior = by_kind.get(kind)
        if prior is not None and prior.resolved_at is None and prior.acked_at is None:
            if SEVERITY_RANK[condition.severity] > SEVERITY_RANK[prior.severity]:
                actions.append(
                    AlertAction(
                        op="escalate",
                        kind=kind,
                        severity=condition.severity,
                        message=condition.message,
                        value=condition.value,
                        unit=condition.unit,
                    )
                )
            continue
        worsened = (
            prior is not None
            and prior.resolved_at is None
            and SEVERITY_RANK[condition.severity] > SEVERITY_RANK[prior.severity]
        )
        if prior is not None and not worsened:
            anchor = prior.acked_at or prior.resolved_at or prior.notified_at or prior.opened_at
            wait = float(cooldown_s.get(condition.severity, 0))
            if (now - as_utc(anchor)).total_seconds() < wait:
                continue
        actions.append(
            AlertAction(
                op="open",
                kind=kind,
                severity=condition.severity,
                message=condition.message,
                value=condition.value,
                unit=condition.unit,
            )
        )
    return actions
