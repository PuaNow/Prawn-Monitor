"""Pluggable tank sensors.

Simulated drivers run anywhere. Hardware drivers talk to I2C, 1-Wire, and GPIO
when those stacks are present and stay quiet, with ``ok: false``, when they
are not. A third driver can be dropped in by registering ``build``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from prawn_monitor.chemistry import (
    analog_do_mg_l,
    analog_ph,
    celsius_to_fahrenheit,
    fahrenheit_to_celsius,
    saturation_mg_l,
)


def sensor_result(
    kind: str,
    *,
    ok: bool,
    value: Any,
    unit: str,
    driver: str,
    extra: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "kind": kind,
        "ok": ok,
        "value": value,
        "unit": unit,
        "driver": driver,
        "extra": extra or {},
        "error": error,
    }


def parse_w1_slave(text: str) -> float:
    """Return Celsius from a DS18B20 ``w1_slave`` file. Raises ValueError if bad."""
    if "YES" not in text:
        raise ValueError("CRC check failed")
    marker = text.rfind("t=")
    if marker < 0:
        raise ValueError("temperature missing")
    raw = text[marker + 2 :].strip().split()[0]
    return int(raw) / 1000.0


def classify_level(
    *,
    low_wet: bool | None,
    high_wet: bool | None,
    level_cm: float | None,
    low_cm: float | None,
    high_cm: float | None,
) -> str:
    """Combine float switches and an optional ultrasonic depth.

    A closed high float wins. An open low float means the water is below the
    safe line. Ultrasonic thresholds are used when a switch is not fitted.
    """
    if high_wet is True:
        return "high"
    if low_wet is False:
        return "low"
    if low_wet is True and high_wet is not True:
        if level_cm is not None and high_cm is not None and level_cm > high_cm:
            return "high"
        return "ok"
    if level_cm is None:
        return "unknown"
    if low_cm is not None and level_cm < low_cm:
        return "low"
    if high_cm is not None and level_cm > high_cm:
        return "high"
    return "ok"


def counts_to_volts(counts: int) -> float:
    """ADS1115 signed conversion at PGA ±4.096 V."""
    if counts & 0x8000:
        counts -= 0x10000
    return counts * 4.096 / 32768.0


def echo_us_to_cm(echo_us: float, sound_m_s: float = 343.0) -> float:
    """Round-trip microseconds to distance in centimeters."""
    return echo_us * (sound_m_s / 10_000.0) / 2.0


def parse_atlas_payload(code: int, payload: str) -> float:
    if code == 254:
        raise TimeoutError("atlas device still processing")
    if code == 255:
        raise RuntimeError("atlas device had no data")
    if code != 1:
        raise RuntimeError(f"atlas device rejected the command ({payload or code})")
    first = payload.split(",")[0].strip()
    return float(first)


class SimulatedBank:
    """One stand-in for every probe. The node exposes it only in simulated mode."""

    driver_name = "simulated"

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[str, Any] = {
            "dissolved_oxygen": 6.5,
            "temperature_f": 84.0,
            "ph": 7.9,
            "level": "ok",
            "level_cm": 40.0,
            "offline": set(),
        }

    def update(self, payload: dict[str, Any]) -> None:
        with self.lock:
            for key in ("dissolved_oxygen", "temperature_f", "ph", "level", "level_cm"):
                if key in payload and payload[key] is not None:
                    self.values[key] = payload[key]
            if "offline" in payload:
                self.values["offline"] = set(payload.get("offline") or [])

    def _offline(self, kind: str) -> bool:
        return kind in self.values["offline"]

    def readings(self) -> dict[str, dict[str, Any]]:
        with self.lock:
            offline = set(self.values["offline"])
            do = self.values["dissolved_oxygen"]
            temp_f = self.values["temperature_f"]
            ph = self.values["ph"]
            level = self.values["level"]
            level_cm = self.values["level_cm"]
        return {
            "dissolved_oxygen": sensor_result(
                "dissolved_oxygen",
                ok="dissolved_oxygen" not in offline,
                value=None if "dissolved_oxygen" in offline else float(do),
                unit="mg/L",
                driver=self.driver_name,
                error="simulated offline" if "dissolved_oxygen" in offline else None,
            ),
            "temperature": sensor_result(
                "temperature",
                ok="temperature" not in offline,
                value=None if "temperature" in offline else float(temp_f),
                unit="F",
                driver=self.driver_name,
                extra={}
                if "temperature" in offline
                else {"celsius": fahrenheit_to_celsius(float(temp_f))},
                error="simulated offline" if "temperature" in offline else None,
            ),
            "ph": sensor_result(
                "ph",
                ok="ph" not in offline,
                value=None if "ph" in offline else float(ph),
                unit="pH",
                driver=self.driver_name,
                error="simulated offline" if "ph" in offline else None,
            ),
            "water_level": sensor_result(
                "water_level",
                ok="water_level" not in offline,
                value=None if "water_level" in offline else level,
                unit="state",
                driver=self.driver_name,
                extra={} if "water_level" in offline else {"level_cm": float(level_cm), "source": "simulated"},
                error="simulated offline" if "water_level" in offline else None,
            ),
        }

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "error": "simulated mode has no probe to calibrate; set the driver to atlas or dfrobot on the Pi",
        }

    def close(self) -> None:
        return


class AtlasTransport:
    """I2C transport for an Atlas EZO circuit. Tests pass a fake with ``transact``."""

    def __init__(self, bus_num: int = 1):
        import smbus2

        self.bus = smbus2.SMBus(bus_num)

    def transact(self, address: int, command: str, delay_s: float) -> tuple[int, str]:
        import smbus2

        payload = list((command + "\x00").encode("ascii"))
        self.bus.i2c_rdwr(smbus2.i2c_msg.write(address, payload))
        time.sleep(delay_s)
        read = smbus2.i2c_msg.read(address, 31)
        self.bus.i2c_rdwr(read)
        raw = bytes(read)
        text = raw[1:].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        if raw[0] == 254:
            time.sleep(delay_s)
            read = smbus2.i2c_msg.read(address, 31)
            self.bus.i2c_rdwr(read)
            raw = bytes(read)
            text = raw[1:].split(b"\x00", 1)[0].decode("ascii", "replace").strip()
        return raw[0], text

    def close(self) -> None:
        close = getattr(self.bus, "close", None)
        if close:
            close()


class AtlasEzoDriver:
    def __init__(
        self,
        *,
        kind: str,
        unit: str,
        address: int,
        delay_s: float,
        transport: AtlasTransport | Any,
        salinity_ppt: float = 0.0,
        pressure_kpa: float = 101.325,
    ):
        self.kind = kind
        self.unit = unit
        self.address = address
        self.delay_s = delay_s
        self.transport = transport
        self.driver_name = "atlas_ezo"
        self.salinity_ppt = salinity_ppt
        self.pressure_kpa = pressure_kpa
        self._last_temp_c: float | None = None
        self._comp_sent = False

    def _query(self, command: str) -> tuple[int, str]:
        return self.transport.transact(self.address, command, self.delay_s)

    def apply_compensation(self, temp_c: float | None) -> None:
        if temp_c is None:
            return
        if self._last_temp_c is not None and abs(temp_c - self._last_temp_c) < 0.2 and self._comp_sent:
            return
        self._query(f"T,{temp_c:.2f}")
        if self.kind == "dissolved_oxygen":
            self._query(f"S,{self.salinity_ppt:.2f}")
            self._query(f"P,{self.pressure_kpa:.2f}")
        self._last_temp_c = temp_c
        self._comp_sent = True

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        try:
            self.apply_compensation(temp_c)
            code, payload = self._query("R")
            value = parse_atlas_payload(code, payload)
            return sensor_result(
                self.kind,
                ok=True,
                value=value,
                unit=self.unit,
                driver=self.driver_name,
                extra={"address": self.address, "raw": payload},
            )
        except Exception as exc:
            return sensor_result(
                self.kind,
                ok=False,
                value=None,
                unit=self.unit,
                driver=self.driver_name,
                error=str(exc),
            )

    def command(self, _kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        mapping = {
            "cal_clear": "Cal,clear",
            "cal_mid": "Cal,mid,{value}",
            "cal_low": "Cal,low,{value}",
            "cal_high": "Cal,high,{value}",
            "cal_atm": "Cal",
            "cal_zero": "Cal,0",
            "temperature_c": "T,{value}",
            "salinity_ppt": "S,{value}",
            "pressure_kpa": "P,{value}",
        }
        if op not in mapping:
            return {"ok": False, "error": f"unsupported op {op}"}
        if "{value}" in mapping[op]:
            if "value" not in body:
                return {"ok": False, "error": "value is required"}
            command = mapping[op].format(value=body["value"])
        else:
            command = mapping[op]
        try:
            code, payload = self._query(command)
            if code not in (1, 254):
                return {"ok": False, "error": payload or f"code {code}"}
            return {"ok": True, "command": command, "response": payload}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close(self) -> None:
        close = getattr(self.transport, "close", None)
        if close:
            close()


class Ads1115:
    """Minimal ADS1115 single-shot reader over SMBus. PGA ±4.096 V."""

    _MUX = {0: 0xC3, 1: 0xD3, 2: 0xE3, 3: 0xF3}

    def __init__(self, bus_num: int = 1, address: int = 0x48):
        import smbus2

        self.bus = smbus2.SMBus(bus_num)
        self.address = address
        self._closed = False

    def volts(self, channel: int) -> float:
        if channel not in self._MUX:
            raise ValueError(f"ADS1115 channel {channel} is out of range")
        high = self._MUX[channel]
        low = 0x83  # 128 SPS, comparator disabled
        self.bus.write_i2c_block_data(self.address, 0x01, [high, low])
        time.sleep(0.01)
        for _ in range(20):
            config = self.bus.read_i2c_block_data(self.address, 0x01, 2)
            if config[0] & 0x80:
                break
            time.sleep(0.01)
        raw = self.bus.read_i2c_block_data(self.address, 0x00, 2)
        return counts_to_volts((raw[0] << 8) | raw[1])

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.bus, "close", None)
        if close:
            close()


class AnalogChemistry:
    """DFRobot analog DO and pH through one ADS1115."""

    driver_name = "dfrobot_ads1115"

    def __init__(self, adc: Any, config: dict[str, Any], calibration_path: Path):
        self.adc = adc
        self.config = config
        self.calibration_path = calibration_path
        self.calibration = self._load()
        self._closed = False

    def _load(self) -> dict[str, Any]:
        stored: dict[str, Any] = {}
        if self.calibration_path.exists():
            stored = json.loads(self.calibration_path.read_text(encoding="utf-8"))
        base = dict(self.config.get("calibration") or {})
        base.update(stored)
        return base

    def _save(self) -> None:
        self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
        self.calibration_path.write_text(json.dumps(self.calibration, indent=2), encoding="utf-8")

    def _volts(self, channel_key: str, default_channel: int) -> float:
        channel = int(self.config.get(channel_key, default_channel))
        return float(self.adc.volts(channel))

    def read_do(self, temp_c: float | None) -> dict[str, Any]:
        try:
            volts = self._volts("do_channel", 0)
            cal = self.calibration
            if "v_sat" not in cal:
                raise RuntimeError("DO board needs a saturation calibration (capture_sat)")
            temp = float(cal.get("fallback_temp_c", 28.0) if temp_c is None else temp_c)
            value = analog_do_mg_l(
                volts,
                v_zero=float(cal.get("v_zero", 0.0)),
                v_sat=float(cal["v_sat"]),
                temp_c=temp,
                salinity_ppt=float(cal.get("salinity_ppt", self.config.get("salinity_ppt", 0.0))),
                pressure_kpa=float(cal.get("pressure_kpa", self.config.get("pressure_kpa", 101.325))),
            )
            return sensor_result(
                "dissolved_oxygen",
                ok=True,
                value=value,
                unit="mg/L",
                driver=self.driver_name,
                extra={"volts": volts, "temp_c": temp},
            )
        except Exception as exc:
            return sensor_result(
                "dissolved_oxygen",
                ok=False,
                value=None,
                unit="mg/L",
                driver=self.driver_name,
                error=str(exc),
            )

    def read_ph(self, temp_c: float | None) -> dict[str, Any]:
        try:
            volts = self._volts("ph_channel", 1)
            cal = self.calibration
            if "v_ph7" not in cal or "v_ph4" not in cal:
                raise RuntimeError("pH board needs pH 7 and pH 4 calibration")
            value = analog_ph(volts, v_ph7=float(cal["v_ph7"]), v_ph4=float(cal["v_ph4"]))
            return sensor_result(
                "ph",
                ok=True,
                value=value,
                unit="pH",
                driver=self.driver_name,
                extra={"volts": volts, "temp_c": temp_c},
            )
        except Exception as exc:
            return sensor_result("ph", ok=False, value=None, unit="pH", driver=self.driver_name, error=str(exc))

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        captures = {
            "capture_sat": ("dissolved_oxygen", "do_channel", 0, "v_sat"),
            "capture_zero": ("dissolved_oxygen", "do_channel", 0, "v_zero"),
            "capture_ph7": ("ph", "ph_channel", 1, "v_ph7"),
            "capture_ph4": ("ph", "ph_channel", 1, "v_ph4"),
        }
        try:
            if op in captures:
                expect, channel_key, default_channel, field = captures[op]
                if kind != expect:
                    return {"ok": False, "error": f"{op} belongs on {expect}"}
                volts = self._volts(channel_key, default_channel)
                self.calibration[field] = volts
                self._save()
                return {"ok": True, "op": op, field: volts}
            if op == "set_calibration":
                for key, value in body.items():
                    if key == "op":
                        continue
                    self.calibration[key] = value
                self._save()
                return {"ok": True, "calibration": self.calibration}
            return {"ok": False, "error": f"unsupported op {op}"}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        close = getattr(self.adc, "close", None)
        if close:
            close()


class Ds18b20Driver:
    driver_name = "ds18b20"

    def __init__(self, device_path: str | Path | None = None, w1_root: str | Path = "/sys/bus/w1/devices"):
        self.device_path = Path(device_path) if device_path else None
        self.w1_root = Path(w1_root)

    def _slave_file(self) -> Path:
        if self.device_path:
            return self.device_path
        matches = sorted(self.w1_root.glob("28-*"))
        if not matches:
            raise FileNotFoundError(f"no DS18B20 under {self.w1_root}")
        return matches[0] / "w1_slave"

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        try:
            text = self._slave_file().read_text(encoding="utf-8")
            celsius = parse_w1_slave(text)
            return sensor_result(
                "temperature",
                ok=True,
                value=celsius_to_fahrenheit(celsius),
                unit="F",
                driver=self.driver_name,
                extra={"celsius": celsius},
            )
        except Exception as exc:
            return sensor_result(
                "temperature",
                ok=False,
                value=None,
                unit="F",
                driver=self.driver_name,
                error=str(exc),
            )

    def command(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "DS18B20 has no field calibration; check it in an ice bath"}

    def close(self) -> None:
        return


class WaterLevelHardware:
    driver_name = "level"

    def __init__(
        self,
        *,
        read_low: Callable[[], bool | None] | None,
        read_high: Callable[[], bool | None] | None,
        read_distance_cm: Callable[[], float | None] | None,
        low_cm: float | None,
        high_cm: float | None,
        mount_height_cm: float | None,
    ):
        self.read_low = read_low
        self.read_high = read_high
        self.read_distance_cm = read_distance_cm
        self.low_cm = low_cm
        self.high_cm = high_cm
        self.mount_height_cm = mount_height_cm

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        errors = []
        low_wet = None
        high_wet = None
        level_cm = None
        try:
            if self.read_low:
                low_wet = self.read_low()
            if self.read_high:
                high_wet = self.read_high()
        except Exception as exc:
            errors.append(f"float: {exc}")
        try:
            if self.read_distance_cm:
                distance = self.read_distance_cm()
                if distance is not None and self.mount_height_cm is not None:
                    level_cm = self.mount_height_cm - distance
                elif distance is not None:
                    level_cm = distance
        except Exception as exc:
            errors.append(f"ultrasonic: {exc}")
        if low_wet is None and high_wet is None and level_cm is None:
            return sensor_result(
                "water_level",
                ok=False,
                value=None,
                unit="state",
                driver=self.driver_name,
                error="; ".join(errors) or "no level sensor configured",
            )
        state = classify_level(
            low_wet=low_wet,
            high_wet=high_wet,
            level_cm=level_cm,
            low_cm=self.low_cm,
            high_cm=self.high_cm,
        )
        return sensor_result(
            "water_level",
            ok=True,
            value=state,
            unit="state",
            driver=self.driver_name,
            extra={
                "level_cm": level_cm,
                "low_wet": low_wet,
                "high_wet": high_wet,
                "notes": errors,
            },
        )

    def command(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"ok": False, "error": "level sensors are calibrated by mounting height and switch position"}

    def close(self) -> None:
        return


def measure_echo_us(trigger: Callable[[bool], None], echo: Callable[[], bool], timeout_s: float = 0.03) -> float:
    """Time a JSN-SR04T echo. ``trigger`` and ``echo`` are GPIO callables."""
    trigger(False)
    time.sleep(2e-6)
    trigger(True)
    time.sleep(10e-6)
    trigger(False)
    deadline = time.perf_counter() + timeout_s
    while echo() is False:
        if time.perf_counter() > deadline:
            raise TimeoutError("ultrasonic echo did not start")
    start = time.perf_counter()
    while echo() is True:
        if time.perf_counter() > deadline:
            raise TimeoutError("ultrasonic echo did not end")
    return (time.perf_counter() - start) * 1_000_000.0


class _Gpio:
    def __init__(self) -> None:
        try:
            import RPi.GPIO as gpio
        except ImportError as exc:
            raise RuntimeError("RPi.GPIO is not installed") from exc
        self.gpio = gpio
        gpio.setmode(gpio.BCM)
        gpio.setwarnings(False)

    def input_pullup(self, pin: int) -> Callable[[], bool]:
        self.gpio.setup(pin, self.gpio.IN, pull_up_down=self.gpio.PUD_UP)

        def read() -> bool:
            # Closed-when-wet switch pulls the pin to ground.
            return self.gpio.input(pin) == self.gpio.LOW

        return read

    def output(self, pin: int) -> Callable[[bool], None]:
        self.gpio.setup(pin, self.gpio.OUT)

        def write(level: bool) -> None:
            self.gpio.output(pin, self.gpio.HIGH if level else self.gpio.LOW)

        return write

    def input(self, pin: int) -> Callable[[], bool]:
        self.gpio.setup(pin, self.gpio.IN)

        def read() -> bool:
            return self.gpio.input(pin) == self.gpio.HIGH

        return read


class SensorGroup:
    def __init__(self, drivers: dict[str, Any], simulated: SimulatedBank | None = None):
        self.drivers = drivers
        self.simulated = simulated

    def readings(self) -> dict[str, dict[str, Any]]:
        if self.simulated is not None:
            return self.simulated.readings()
        temp = self.drivers["temperature"].read()
        temp_c = None
        if temp.get("ok") and temp.get("value") is not None:
            temp_c = fahrenheit_to_celsius(float(temp["value"]))
        do_driver = self.drivers["dissolved_oxygen"]
        ph_driver = self.drivers["ph"]
        do = do_driver.read(temp_c=temp_c) if _accepts_temp(do_driver) else do_driver.read()
        ph = ph_driver.read(temp_c=temp_c) if _accepts_temp(ph_driver) else ph_driver.read()
        level = self.drivers["water_level"].read()
        return {
            "dissolved_oxygen": do,
            "temperature": temp,
            "ph": ph,
            "water_level": level,
        }

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        if self.simulated is not None:
            return self.simulated.command(kind, op, body)
        driver = self.drivers.get(kind)
        if driver is None:
            return {"ok": False, "error": f"no driver for {kind}"}
        return driver.command(kind, op, body)

    def inventory(self) -> list[dict[str, str]]:
        if self.simulated is not None:
            return [
                {"kind": kind, "driver": "simulated"}
                for kind in ("dissolved_oxygen", "temperature", "ph", "water_level")
            ]
        return [
            {"kind": kind, "driver": getattr(driver, "driver_name", driver.__class__.__name__)}
            for kind, driver in self.drivers.items()
        ]

    def close(self) -> None:
        if self.simulated is not None:
            self.simulated.close()
            return
        for driver in self.drivers.values():
            close = getattr(driver, "close", None)
            if close:
                close()


def _accepts_temp(driver: Any) -> bool:
    read = getattr(driver, "read")
    try:
        code = read.__func__ if hasattr(read, "__func__") else read
        return "temp_c" in getattr(code, "__code__", type("c", (), {"co_varnames": ()})()).co_varnames
    except Exception:
        return True


def build_sensors(config: dict[str, Any], data_dir: Path) -> SensorGroup:
    sensors_cfg = dict(config.get("sensors") or {})
    mode = sensors_cfg.get("mode", "simulated")
    if mode == "simulated":
        return SensorGroup({}, SimulatedBank())
    compensation = dict(config.get("compensation") or {})
    salinity = float(compensation.get("salinity_ppt", 20.0))
    pressure = float(compensation.get("pressure_kpa", 99.0))
    drivers: dict[str, Any] = {}
    do_cfg = dict(sensors_cfg.get("dissolved_oxygen") or {})
    ph_cfg = dict(sensors_cfg.get("ph") or {})
    do_name = do_cfg.get("driver", "atlas_ezo")
    ph_name = ph_cfg.get("driver", "atlas_ezo")
    analog: AnalogChemistry | None = None
    if do_name == "dfrobot_ads1115" or ph_name == "dfrobot_ads1115":
        ads_cfg = dict(sensors_cfg.get("ads1115") or {})
        adc = Ads1115(bus_num=int(ads_cfg.get("bus", 1)), address=int(ads_cfg.get("address", 0x48)))
        merged = {"calibration": {}, "salinity_ppt": salinity, "pressure_kpa": pressure}
        merged.update(do_cfg)
        merged["calibration"] = {
            **dict(do_cfg.get("calibration") or {}),
            **dict(ph_cfg.get("calibration") or {}),
            "salinity_ppt": salinity,
            "pressure_kpa": pressure,
        }
        merged["do_channel"] = do_cfg.get("channel", 0)
        merged["ph_channel"] = ph_cfg.get("channel", 1)
        analog = AnalogChemistry(adc, merged, data_dir / "calibration.json")

    if do_name == "atlas_ezo":
        drivers["dissolved_oxygen"] = AtlasEzoDriver(
            kind="dissolved_oxygen",
            unit="mg/L",
            address=int(do_cfg.get("address", 0x61)),
            delay_s=float(do_cfg.get("delay_s", 0.6)),
            transport=AtlasTransport(bus_num=int(do_cfg.get("bus", 1))),
            salinity_ppt=salinity,
            pressure_kpa=pressure,
        )
    elif do_name == "dfrobot_ads1115":
        assert analog is not None
        drivers["dissolved_oxygen"] = _AnalogDo(analog)
    else:
        raise ValueError(f"unknown dissolved oxygen driver {do_name}")

    temp_cfg = dict(sensors_cfg.get("temperature") or {})
    temp_name = temp_cfg.get("driver", "ds18b20")
    if temp_name != "ds18b20":
        raise ValueError(f"unknown temperature driver {temp_name}")
    drivers["temperature"] = Ds18b20Driver(
        device_path=temp_cfg.get("device_path"),
        w1_root=temp_cfg.get("w1_root", "/sys/bus/w1/devices"),
    )

    if ph_name == "atlas_ezo":
        drivers["ph"] = AtlasEzoDriver(
            kind="ph",
            unit="pH",
            address=int(ph_cfg.get("address", 0x63)),
            delay_s=float(ph_cfg.get("delay_s", 0.9)),
            transport=AtlasTransport(bus_num=int(ph_cfg.get("bus", 1))),
        )
    elif ph_name == "dfrobot_ads1115":
        assert analog is not None
        drivers["ph"] = _AnalogPh(analog)
    else:
        raise ValueError(f"unknown pH driver {ph_name}")

    level_cfg = dict(sensors_cfg.get("water_level") or {})
    gpio = None
    read_low = None
    read_high = None
    read_distance = None
    floats = dict(level_cfg.get("floats") or {})
    ultrasonic = dict(level_cfg.get("ultrasonic") or {})
    if floats.get("enabled", True) and (floats.get("low_pin") is not None or floats.get("high_pin") is not None):
        gpio = _Gpio()
        if floats.get("low_pin") is not None:
            read_low = gpio.input_pullup(int(floats["low_pin"]))
        if floats.get("high_pin") is not None:
            read_high = gpio.input_pullup(int(floats["high_pin"]))
    if ultrasonic.get("enabled", False):
        if gpio is None:
            gpio = _Gpio()
        trigger = gpio.output(int(ultrasonic.get("trigger_pin", 23)))
        echo = gpio.input(int(ultrasonic.get("echo_pin", 24)))

        def read_distance() -> float:
            return echo_us_to_cm(measure_echo_us(trigger, echo))

    drivers["water_level"] = WaterLevelHardware(
        read_low=read_low,
        read_high=read_high,
        read_distance_cm=read_distance,
        low_cm=level_cfg.get("low_cm", ultrasonic.get("low_cm")),
        high_cm=level_cfg.get("high_cm", ultrasonic.get("high_cm")),
        mount_height_cm=ultrasonic.get("mount_height_cm"),
    )
    return SensorGroup(drivers)


class _AnalogDo:
    driver_name = "dfrobot_ads1115"

    def __init__(self, analog: AnalogChemistry):
        self.analog = analog

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        return self.analog.read_do(temp_c)

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.analog.command(kind, op, body)

    def close(self) -> None:
        self.analog.close()


class _AnalogPh:
    driver_name = "dfrobot_ads1115"

    def __init__(self, analog: AnalogChemistry):
        self.analog = analog

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        return self.analog.read_ph(temp_c)

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.analog.command(kind, op, body)

    def close(self) -> None:
        self.analog.close()


def saturation_note(temp_c: float, salinity_ppt: float, pressure_kpa: float) -> float:
    """Helper for calibration docs and tests."""
    return saturation_mg_l(temp_c, salinity_ppt, pressure_kpa)
