from datetime import datetime, timedelta, timezone

from prawn_monitor.alerts import (
    Condition,
    PriorAlert,
    evaluate_conditions,
    plan_alert_actions,
)
from prawn_monitor.thresholds import effective_thresholds

NOW = datetime(2026, 10, 10, 18, 0, tzinfo=timezone.utc)
LIMITS = effective_thresholds()


def sensors(**overrides):
    base = {
        "dissolved_oxygen": {"ok": True, "value": 6.5, "unit": "mg/L"},
        "temperature": {"ok": True, "value": 84.0, "unit": "F"},
        "ph": {"ok": True, "value": 7.9, "unit": "pH"},
        "water_level": {"ok": True, "value": "ok", "unit": "state"},
    }
    for key, value in overrides.items():
        base[key] = value
    return base


def kinds(conditions):
    return {(item.kind, item.severity) for item in conditions}


def evaluate(**kwargs):
    payload = {
        "tank_name": "Nursery",
        "now": NOW,
        "node_online": True,
        "sensors": sensors(),
        "temp_history": [(NOW, 84.0)],
        "manual": {},
        "thresholds": LIMITS,
    }
    payload.update(kwargs)
    return evaluate_conditions(**payload)


def test_defaults_match_vannamei_band():
    assert LIMITS["do_mg_l"] == {"warn_below": 5.0, "critical_below": 4.0}
    assert LIMITS["temp_f"]["low"] == 82.0
    assert LIMITS["temp_f"]["high"] == 86.0
    assert LIMITS["temp_f"]["swing_warn_above"] == 2.0
    assert LIMITS["temp_f"]["swing_hours"] == 24.0
    assert LIMITS["ph"] == {"low": 7.5, "high": 8.3}
    assert LIMITS["tan_mg_l"] == {"warn_above": 0.5, "critical_above": 1.0}
    assert LIMITS["nitrite_mg_l"]["warn_above"] == 0.5
    assert LIMITS["nitrate_mg_l"]["warn_above"] == 100.0
    assert LIMITS["alkalinity_mg_l"]["warn_below"] == 120.0


def test_healthy_water_is_quiet():
    assert evaluate() == []


def test_dissolved_oxygen_boundaries():
    assert kinds(evaluate(sensors=sensors(dissolved_oxygen={"ok": True, "value": 5.0}))) == set()
    assert kinds(evaluate(sensors=sensors(dissolved_oxygen={"ok": True, "value": 4.999}))) == {
        ("dissolved_oxygen", "warning")
    }
    assert kinds(evaluate(sensors=sensors(dissolved_oxygen={"ok": True, "value": 4.0}))) == {
        ("dissolved_oxygen", "warning")
    }
    hit = evaluate(sensors=sensors(dissolved_oxygen={"ok": True, "value": 3.999}))
    assert kinds(hit) == {("dissolved_oxygen", "critical")}
    assert "critical limit" in hit[0].message
    assert hit[0].value == 3.999


def test_temperature_band_and_swing():
    assert evaluate(sensors=sensors(temperature={"ok": True, "value": 82.0})) == []
    assert evaluate(sensors=sensors(temperature={"ok": True, "value": 86.0})) == []
    assert kinds(evaluate(sensors=sensors(temperature={"ok": True, "value": 81.99}))) == {
        ("temperature", "warning")
    }
    assert kinds(evaluate(sensors=sensors(temperature={"ok": True, "value": 86.01}))) == {
        ("temperature", "warning")
    }
    flat = [(NOW - timedelta(hours=3), 83.0), (NOW, 85.0)]
    assert evaluate(temp_history=flat) == []
    swing = [(NOW - timedelta(hours=3), 83.0), (NOW, 85.01)]
    hit = evaluate(temp_history=swing)
    assert kinds(hit) == {("temperature_swing", "warning")}
    old = [(NOW - timedelta(hours=25), 70.0), (NOW, 84.0)]
    assert evaluate(temp_history=old) == []
    assert evaluate(temp_history=[(NOW, 84.0)]) == []


def test_ph_and_level():
    assert evaluate(sensors=sensors(ph={"ok": True, "value": 7.5})) == []
    assert evaluate(sensors=sensors(ph={"ok": True, "value": 8.3})) == []
    assert kinds(evaluate(sensors=sensors(ph={"ok": True, "value": 7.49}))) == {("ph", "warning")}
    assert kinds(evaluate(sensors=sensors(ph={"ok": True, "value": 8.31}))) == {("ph", "warning")}
    low = evaluate(sensors=sensors(water_level={"ok": True, "value": "low"}))
    high = evaluate(sensors=sensors(water_level={"ok": True, "value": "high"}))
    assert kinds(low) == {("water_level", "critical")}
    assert kinds(high) == {("water_level", "warning")}


def test_manual_kit_uses_latest_value_per_field():
    tested = NOW - timedelta(hours=2)
    older = NOW - timedelta(days=1)
    manual = {
        "tan_mg_l": (0.5, tested),
        "nitrite_mg_l": (0.5, tested),
        "nitrate_mg_l": (100.0, older),
        "alkalinity_mg_l": (120.0, tested),
        "salinity_ppt": (40.0, tested),
    }
    assert evaluate(manual=manual) == []
    manual["nitrite_mg_l"] = (0.51, tested)
    assert kinds(evaluate(manual=manual)) == {("nitrite", "warning")}
    manual["tan_mg_l"] = (1.0, tested)
    assert kinds(evaluate(manual=manual)) == {("nitrite", "warning"), ("tan", "warning")}
    manual["tan_mg_l"] = (1.01, tested)
    manual["nitrate_mg_l"] = (100.01, tested)
    manual["alkalinity_mg_l"] = (119.9, tested)
    found = kinds(evaluate(manual=manual))
    assert ("tan", "critical") in found
    assert ("nitrite", "warning") in found
    assert ("nitrate", "warning") in found
    assert ("alkalinity", "warning") in found
    assert all(kind != "salinity" for kind, _severity in found)


def test_offline_node_and_sensors():
    offline = evaluate(node_online=False, sensors=sensors(dissolved_oxygen={"ok": False, "value": None}))
    assert kinds(offline) == {("node_offline", "critical")}
    failed = sensors(dissolved_oxygen={"ok": False, "value": None}, temperature={"ok": False, "value": None})
    found = kinds(evaluate(sensors=failed))
    assert ("sensor_offline:dissolved_oxygen", "critical") in found
    assert ("sensor_offline:temperature", "warning") in found
    assert all(kind != "dissolved_oxygen" for kind, _severity in found)


def test_tank_override_changes_only_the_named_limit():
    custom = effective_thresholds({"do_mg_l": {"warn_below": 6.0}})
    assert custom["do_mg_l"]["critical_below"] == 4.0
    assert kinds(evaluate(sensors=sensors(dissolved_oxygen={"ok": True, "value": 5.5}), thresholds=custom)) == {
        ("dissolved_oxygen", "warning")
    }


def _prior(kind, severity, *, acked=None, resolved=None, opened=None):
    return PriorAlert(
        kind=kind,
        severity=severity,
        opened_at=opened or NOW - timedelta(minutes=5),
        notified_at=opened or NOW - timedelta(minutes=5),
        acked_at=acked,
        resolved_at=resolved,
    )


def _condition(kind="dissolved_oxygen", severity="warning"):
    return Condition(kind=kind, severity=severity, message=f"{severity} {kind}", value=4.5, unit="mg/L")


def test_cooldown_ack_escalation_and_resolve():
    cooldown = {"warning": 3600, "critical": 900}
    warning = _condition()
    opened = plan_alert_actions([warning], [], NOW, cooldown)
    assert [item.op for item in opened] == ["open"]
    held = plan_alert_actions([warning], [_prior("dissolved_oxygen", "warning")], NOW, cooldown)
    assert held == []
    acked = _prior("dissolved_oxygen", "warning", acked=NOW - timedelta(minutes=10))
    cooled = plan_alert_actions([warning], [acked], NOW, cooldown)
    assert cooled == []
    later = NOW + timedelta(hours=2)
    again = plan_alert_actions([warning], [acked], later, cooldown)
    assert [item.op for item in again] == ["open"]
    critical = _condition(severity="critical")
    escalated = plan_alert_actions([critical], [_prior("dissolved_oxygen", "warning")], NOW, cooldown)
    assert [item.op for item in escalated] == ["escalate"]
    jumped = plan_alert_actions([critical], [acked], NOW, cooldown)
    assert [item.op for item in jumped] == ["open"]
    cleared = plan_alert_actions([], [_prior("dissolved_oxygen", "warning")], NOW, cooldown)
    assert [item.op for item in cleared] == ["resolve"]
    acked_clear = plan_alert_actions([], [acked], NOW, cooldown)
    assert [item.op for item in acked_clear] == ["resolve"]
