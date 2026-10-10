"""Dissolved-oxygen saturation for analog probe calibration.

Garcia and Gordon (1992) combined-fit solubility, converted from µmol/kg to
mg/L with a Millero-style density. Freshwater checks at 1 atm:

- 0 °C ≈ 14.62 mg/L
- 20 °C ≈ 9.09 mg/L
- 25 °C ≈ 8.26 mg/L

A linear pressure correction scales saturation by P / 101.325 kPa. It ignores
water-vapor pressure, which is a small error next to probe drift. Atlas EZO
boards do their own compensation; use this for the DFRobot analog path.
"""

from __future__ import annotations

import math

_ATM_KPA = 101.325


def seawater_density_kg_l(temp_c: float, salinity_ppt: float) -> float:
    """Approximate density in kg/L (Millero & Poisson style)."""
    t = temp_c
    fresh_kg_m3 = (
        999.842594
        + 6.793952e-2 * t
        - 9.095290e-3 * t**2
        + 1.001685e-4 * t**3
        - 1.120083e-6 * t**4
        + 6.536332e-9 * t**5
    )
    a = 8.24493e-1 - 4.0899e-3 * t + 7.6438e-5 * t**2 - 8.2467e-7 * t**3 + 5.3875e-9 * t**4
    b = -5.72466e-3 + 1.0227e-4 * t - 1.6546e-6 * t**2
    c = 4.8314e-4
    s = salinity_ppt
    rho = fresh_kg_m3 + a * s + b * s**1.5 + c * s**2
    return rho / 1000.0


def oxygen_umol_per_kg(temp_c: float, salinity_ppt: float) -> float:
    """Garcia & Gordon 1992 combined fit, µmol/kg, at 1 atm total pressure."""
    if temp_c <= -2 or temp_c >= 40:
        raise ValueError(f"temperature {temp_c} C is outside the fit")
    ts = math.log((298.15 - temp_c) / (273.15 + temp_c))
    a0, a1, a2, a3, a4, a5 = 5.80818, 3.20684, 4.11890, 4.93845, 1.01567, 1.41575
    b0, b1, b2, b3 = -7.01211e-3, -7.25958e-3, -7.93334e-3, -5.54491e-3
    c0 = -1.32412e-7
    ln_c = a0 + ts * (a1 + ts * (a2 + ts * (a3 + ts * (a4 + ts * a5))))
    ln_c += salinity_ppt * (b0 + ts * (b1 + ts * (b2 + ts * b3)))
    ln_c += c0 * salinity_ppt * salinity_ppt
    return math.exp(ln_c)


def saturation_mg_l(
    temp_c: float,
    salinity_ppt: float = 0.0,
    pressure_kpa: float = _ATM_KPA,
) -> float:
    """Air-saturated dissolved oxygen in mg/L."""
    umol = oxygen_umol_per_kg(temp_c, salinity_ppt)
    density = seawater_density_kg_l(temp_c, salinity_ppt)
    at_one_atm = umol * (31.9988 / 1000.0) * density
    if pressure_kpa <= 0:
        raise ValueError("pressure_kpa must be positive")
    return at_one_atm * (pressure_kpa / _ATM_KPA)


def analog_do_mg_l(
    volts: float,
    *,
    v_zero: float,
    v_sat: float,
    temp_c: float,
    salinity_ppt: float,
    pressure_kpa: float = _ATM_KPA,
) -> float:
    """Two-point linear map from an analog galvanic DO board to mg/L.

    ``v_zero`` is the voltage in zero-oxygen solution. ``v_sat`` is the voltage
    in air-saturated water (or water-saturated air, per the probe manual) at
    ``temp_c``. The scale is the saturation concentration at that temperature,
    salinity, and barometric pressure.
    """
    span = v_sat - v_zero
    if abs(span) < 1e-6:
        raise ValueError("DO calibration span is zero")
    sat = saturation_mg_l(temp_c, salinity_ppt, pressure_kpa)
    return (volts - v_zero) / span * sat


def analog_ph(volts: float, *, v_ph7: float, v_ph4: float) -> float:
    """Two-point pH from a DFRobot-style analog board.

    On those boards the voltage rises as pH falls. ``v_ph7`` and ``v_ph4`` are
    the voltages measured in 7.00 and 4.00 buffers.
    """
    span = v_ph7 - v_ph4
    if abs(span) < 1e-6:
        raise ValueError("pH calibration span is zero")
    slope = (7.0 - 4.0) / span
    return 7.0 + (volts - v_ph7) * slope


def fahrenheit_to_celsius(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def celsius_to_fahrenheit(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0
