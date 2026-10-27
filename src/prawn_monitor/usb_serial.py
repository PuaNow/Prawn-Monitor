"""USB serial probes for an x86 host such as the Seeed reServer.

Two protocols share this module:

* ``usb_line`` — one adapter prints a single ASCII line with dissolved oxygen,
  temperature, pH, and water level. A small bridge or a USB meter can do this.
* ``atlas_uart`` — an Atlas EZO circuit in UART mode behind a USB-serial
  adapter. One circuit per port. The bytes match the EZO datasheet, so the
  existing calibration commands still apply.

Nothing here opens a port until a reading is requested. A missing dongle
marks that sensor offline and leaves the node running.
"""

from __future__ import annotations

import re
import threading
import time
from typing import Any, Callable

_KEY_MAP = {
    "do": "do",
    "dissolved_oxygen": "do",
    "mg_l": "do",
    "temp_f": "temp_f",
    "temperature_f": "temp_f",
    "temp_c": "temp_c",
    "temperature_c": "temp_c",
    "ph": "ph",
    "level": "level",
    "water_level": "level",
    "level_cm": "level_cm",
    "cm": "level_cm",
}

_UNITS = {
    "dissolved_oxygen": "mg/L",
    "temperature": "F",
    "ph": "pH",
    "water_level": "state",
}

_KINDS = ("dissolved_oxygen", "temperature", "ph", "water_level")


def parse_usb_line(text: str) -> dict[str, str]:
    """Parse one probe line into canonical keys.

    Accepts ``do=6.42 temp_f=84.1 ph=7.91 level=ok level_cm=40.2`` and the
    comma form ``DO,6.42,TEMP_C,28.9,PH,7.91,LEVEL,ok,LEVEL_CM,40.2``.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("empty usb line")
    if "=" not in raw and ":" not in raw:
        parts = [part.strip() for part in raw.split(",") if part.strip()]
        if len(parts) < 2 or len(parts) % 2:
            raise ValueError(f"usb line has no key=value fields: {raw[:120]}")
        tokens = [f"{parts[i]}={parts[i + 1]}" for i in range(0, len(parts), 2)]
    else:
        tokens = [token for token in re.split(r"[\s,]+", raw) if token]
    found: dict[str, str] = {}
    for token in tokens:
        if "=" in token:
            key, value = token.split("=", 1)
        elif ":" in token:
            key, value = token.split(":", 1)
        else:
            continue
        canonical = _KEY_MAP.get(key.strip().lower())
        if canonical and value.strip() != "":
            found[canonical] = value.strip()
    if not found:
        raise ValueError(f"usb line has no known fields: {raw[:120]}")
    return found


def parse_atlas_uart_frame(raw: bytes) -> tuple[int, str]:
    """Split an EZO UART reply into a status code and ASCII payload.

    The first byte is the status (1 success, 2 failed, 254 pending, 255 no
    data), then the text, then CR. A leading echoed command is skipped.
    """
    data = raw.replace(b"\x00", b"")
    status_prefixes = (b"\x01", b"\x02", bytes((254,)), bytes((255,)))
    if data[:1] not in status_prefixes and b"\r" in data:
        _echo, data = data.split(b"\r", 1)
        data = data.lstrip(b"\r\n")
    if not data:
        raise RuntimeError("empty atlas uart frame")
    code = data[0]
    text = data[1:].split(b"\r", 1)[0].decode("ascii", "replace").strip()
    return int(code), text


def _pyserial_open(port: str, baud: int, timeout_s: float) -> Any:
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError(
            "pyserial is not installed. On the reServer run: pip install 'pyserial>=3.5'"
        ) from exc
    try:
        return serial.Serial(port, baudrate=baud, timeout=timeout_s, exclusive=True)
    except TypeError:
        return serial.Serial(port, baudrate=baud, timeout=timeout_s)


class SerialLink:
    """One USB-serial port. Opens on the first transaction, not at startup."""

    def __init__(
        self,
        port: str,
        baud: int = 9600,
        timeout_s: float = 2.0,
        opener: Callable[..., Any] | None = None,
    ):
        self.port = port
        self.baud = baud
        self.timeout_s = timeout_s
        self._opener = opener or _pyserial_open
        self._ser: Any = None
        self._lock = threading.Lock()

    def _open(self) -> Any:
        if self._ser is not None and getattr(self._ser, "is_open", True):
            return self._ser
        self._ser = self._opener(self.port, self.baud, self.timeout_s)
        return self._ser

    def _drop(self) -> None:
        ser = self._ser
        self._ser = None
        if ser is None:
            return
        close = getattr(ser, "close", None)
        if close:
            try:
                close()
            except Exception:
                return

    def transact_line(self, query: str) -> str:
        with self._lock:
            try:
                ser = self._open()
                reset = getattr(ser, "reset_input_buffer", None)
                if reset:
                    reset()
                payload = query.encode("ascii")
                if not payload.endswith((b"\n", b"\r")):
                    payload += b"\r"
                ser.write(payload)
                raw = ser.readline()
                if not raw:
                    raise TimeoutError(f"no reply from {self.port}")
                return raw.decode("ascii", "replace").strip()
            except Exception:
                self._drop()
                raise

    def transact_atlas(self, command: str, delay_s: float) -> tuple[int, str]:
        with self._lock:
            try:
                ser = self._open()
                if hasattr(ser, "timeout"):
                    ser.timeout = max(self.timeout_s, float(delay_s) + 0.5)
                reset = getattr(ser, "reset_input_buffer", None)
                if reset:
                    reset()
                ser.write((command + "\r").encode("ascii"))
                raw = ser.read_until(b"\r")
                if not raw:
                    raise TimeoutError(f"no atlas reply from {self.port}")
                code, text = parse_atlas_uart_frame(raw)
                if code == 254:
                    time.sleep(max(float(delay_s), 0.3))
                    raw = ser.read_until(b"\r")
                    if not raw:
                        raise TimeoutError(f"atlas still processing on {self.port}")
                    code, text = parse_atlas_uart_frame(raw)
                return code, text
            except Exception:
                self._drop()
                raise

    def close(self) -> None:
        with self._lock:
            self._drop()


class AtlasUartTransport:
    """EZO UART transport with the same ``transact`` shape as the I2C driver.

    ``address`` is ignored. UART has one circuit on the port.
    """

    def __init__(self, port: str, baud: int = 9600, timeout_s: float = 2.0, link: SerialLink | None = None):
        self.link = link or SerialLink(port, baud, timeout_s)

    def transact(self, _address: int, command: str, delay_s: float) -> tuple[int, str]:
        return self.link.transact_atlas(command, delay_s)

    def close(self) -> None:
        self.link.close()


def _offline_set(error: str) -> dict[str, dict[str, Any]]:
    from prawn_monitor.sensors import sensor_result

    return {
        kind: sensor_result(
            kind,
            ok=False,
            value=None,
            unit=unit,
            driver="usb_line",
            error=error,
        )
        for kind, unit in _UNITS.items()
    }


def readings_from_usb_fields(
    fields: dict[str, str],
    *,
    low_cm: float | None,
    high_cm: float | None,
    raw: str = "",
) -> dict[str, dict[str, Any]]:
    """Turn parsed keys into the four readings the hub already stores."""
    from prawn_monitor.chemistry import celsius_to_fahrenheit, fahrenheit_to_celsius
    from prawn_monitor.sensors import classify_level, sensor_result

    note = raw.strip()[:200]
    out: dict[str, dict[str, Any]] = {}

    if "do" not in fields:
        out["dissolved_oxygen"] = sensor_result(
            "dissolved_oxygen",
            ok=False,
            value=None,
            unit="mg/L",
            driver="usb_line",
            error="missing from usb line",
        )
    else:
        try:
            out["dissolved_oxygen"] = sensor_result(
                "dissolved_oxygen",
                ok=True,
                value=float(fields["do"]),
                unit="mg/L",
                driver="usb_line",
                extra={"raw": note} if note else {},
            )
        except ValueError:
            out["dissolved_oxygen"] = sensor_result(
                "dissolved_oxygen",
                ok=False,
                value=None,
                unit="mg/L",
                driver="usb_line",
                error=f"bad do value {fields['do']!r}",
            )

    if "temp_f" in fields or "temp_c" in fields:
        try:
            if "temp_f" in fields:
                temp_f = float(fields["temp_f"])
                celsius = fahrenheit_to_celsius(temp_f)
            else:
                celsius = float(fields["temp_c"])
                temp_f = celsius_to_fahrenheit(celsius)
            out["temperature"] = sensor_result(
                "temperature",
                ok=True,
                value=temp_f,
                unit="F",
                driver="usb_line",
                extra={"celsius": celsius, "raw": note} if note else {"celsius": celsius},
            )
        except ValueError:
            out["temperature"] = sensor_result(
                "temperature",
                ok=False,
                value=None,
                unit="F",
                driver="usb_line",
                error="bad temperature value",
            )
    else:
        out["temperature"] = sensor_result(
            "temperature",
            ok=False,
            value=None,
            unit="F",
            driver="usb_line",
            error="missing from usb line",
        )

    if "ph" not in fields:
        out["ph"] = sensor_result(
            "ph",
            ok=False,
            value=None,
            unit="pH",
            driver="usb_line",
            error="missing from usb line",
        )
    else:
        try:
            out["ph"] = sensor_result(
                "ph",
                ok=True,
                value=float(fields["ph"]),
                unit="pH",
                driver="usb_line",
                extra={"raw": note} if note else {},
            )
        except ValueError:
            out["ph"] = sensor_result(
                "ph",
                ok=False,
                value=None,
                unit="pH",
                driver="usb_line",
                error=f"bad ph value {fields['ph']!r}",
            )

    level_cm = None
    level_error = None
    if "level_cm" in fields:
        try:
            level_cm = float(fields["level_cm"])
        except ValueError:
            level_error = f"bad level_cm value {fields['level_cm']!r}"
    low_wet = None
    high_wet = None
    if level_error is None and "level" in fields:
        token = fields["level"].lower()
        if token == "high":
            high_wet = True
        elif token == "low":
            low_wet = False
        elif token == "ok":
            low_wet = True
            high_wet = False
        else:
            level_error = f"bad level value {fields['level']!r}"
    if level_error:
        out["water_level"] = sensor_result(
            "water_level",
            ok=False,
            value=None,
            unit="state",
            driver="usb_line",
            error=level_error,
        )
    elif "level" not in fields and level_cm is None:
        out["water_level"] = sensor_result(
            "water_level",
            ok=False,
            value=None,
            unit="state",
            driver="usb_line",
            error="missing from usb line",
        )
    else:
        state = classify_level(
            low_wet=low_wet,
            high_wet=high_wet,
            level_cm=level_cm,
            low_cm=low_cm,
            high_cm=high_cm,
        )
        out["water_level"] = sensor_result(
            "water_level",
            ok=True,
            value=state,
            unit="state",
            driver="usb_line",
            extra={"level_cm": level_cm, "source": "usb_line", "raw": note},
        )
    return out


class UsbLineBank:
    """One serial port that answers for every probe."""

    driver_name = "usb_line"

    def __init__(
        self,
        exchange: Callable[[str], str],
        *,
        low_cm: float | None = None,
        high_cm: float | None = None,
        query: str = "R",
        closer: Callable[[], None] | None = None,
    ):
        self.exchange = exchange
        self.low_cm = None if low_cm is None else float(low_cm)
        self.high_cm = None if high_cm is None else float(high_cm)
        self.query = query
        self._closer = closer

    def readings(self) -> dict[str, dict[str, Any]]:
        try:
            line = self.exchange(self.query)
            fields = parse_usb_line(line)
        except Exception as exc:
            return _offline_set(str(exc))
        return readings_from_usb_fields(fields, low_cm=self.low_cm, high_cm=self.high_cm, raw=line)

    def inventory(self) -> list[dict[str, str]]:
        return [{"kind": kind, "driver": self.driver_name} for kind in _KINDS]

    def command(self, _kind: str, _op: str, _body: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": False,
            "error": (
                "usb_line has no calibration command. Calibrate the bridge, or use "
                "atlas_uart for an EZO circuit (same cal ops as atlas_ezo)."
            ),
        }

    def close(self) -> None:
        if self._closer:
            self._closer()
            self._closer = None


class UsbFieldDriver:
    """One sensor on its own USB-serial port, still speaking the line protocol."""

    driver_name = "usb_line"

    def __init__(
        self,
        kind: str,
        link: SerialLink,
        *,
        query: str,
        low_cm: float | None,
        high_cm: float | None,
    ):
        self.kind = kind
        self.link = link
        self.bank = UsbLineBank(
            link.transact_line,
            low_cm=low_cm,
            high_cm=high_cm,
            query=query,
            closer=link.close,
        )

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        del temp_c
        return self.bank.readings()[self.kind]

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.bank.command(kind, op, body)

    def close(self) -> None:
        self.bank.close()


class AtlasTemperature:
    """EZO-RTD reports Celsius. The hub stores Fahrenheit."""

    driver_name = "atlas_uart"

    def __init__(self, inner: Any):
        self.inner = inner
        self.driver_name = getattr(inner, "driver_name", "atlas_uart")

    def read(self, temp_c: float | None = None) -> dict[str, Any]:
        del temp_c
        reading = self.inner.read()
        reading["unit"] = "F"
        if reading.get("ok") and reading.get("value") is not None:
            from prawn_monitor.chemistry import celsius_to_fahrenheit

            celsius = float(reading["value"])
            reading["value"] = celsius_to_fahrenheit(celsius)
            extra = dict(reading.get("extra") or {})
            extra["celsius"] = celsius
            reading["extra"] = extra
        return reading

    def command(self, kind: str, op: str, body: dict[str, Any]) -> dict[str, Any]:
        return self.inner.command(kind, op, body)

    def close(self) -> None:
        close = getattr(self.inner, "close", None)
        if close:
            close()


def usb_line_bank_from_config(sensors_cfg: dict[str, Any]) -> UsbLineBank:
    cfg = dict(sensors_cfg.get("usb_line") or {})
    port = cfg.get("port")
    if not port:
        raise ValueError(
            "sensors.usb_line.port is required when every probe uses usb_line. "
            "Point it at /dev/serial/by-id/..."
        )
    link = SerialLink(str(port), int(cfg.get("baud", 9600)), float(cfg.get("timeout_s", 2)))
    level = dict(sensors_cfg.get("water_level") or {})
    return UsbLineBank(
        link.transact_line,
        low_cm=level.get("low_cm", cfg.get("low_cm")),
        high_cm=level.get("high_cm", cfg.get("high_cm")),
        query=str(cfg.get("query", "R")),
        closer=link.close,
    )


def _atlas_driver(
    kind: str,
    unit: str,
    cfg: dict[str, Any],
    salinity: float,
    pressure: float,
    driver_cls: type,
) -> Any:
    port = cfg.get("port")
    if not port:
        raise ValueError(f"{kind} atlas_uart requires port (/dev/serial/by-id/...)")
    delay = cfg.get("delay_s", 0.9 if kind == "ph" else 0.6)
    transport = AtlasUartTransport(
        port=str(port),
        baud=int(cfg.get("baud", 9600)),
        timeout_s=float(cfg.get("timeout_s", 2)),
    )
    return driver_cls(
        kind=kind,
        unit=unit,
        address=int(cfg.get("address", 0)),
        delay_s=float(delay),
        transport=transport,
        salinity_ppt=salinity,
        pressure_kpa=pressure,
        driver_name="atlas_uart",
    )


def build_usb_sensors(config: dict[str, Any], data_dir: Any) -> Any:
    """Build the phase-1 sensor group for ``sensors.mode`` = ``usb``.

    ``data_dir`` is unused. USB probes keep their calibration on the circuit
    or on the bridge, not in a file next to the node.
    """
    del data_dir
    from prawn_monitor.sensors import AtlasEzoDriver, Ds18b20Driver, SensorGroup

    sensors_cfg = dict(config.get("sensors") or {})
    compensation = dict(config.get("compensation") or {})
    salinity = float(compensation.get("salinity_ppt", 20.0))
    pressure = float(compensation.get("pressure_kpa", 99.0))
    blocks = {
        kind: dict(sensors_cfg.get(kind) or {})
        for kind in _KINDS
    }
    names = {kind: blocks[kind].get("driver", "usb_line") for kind in _KINDS}
    if names["water_level"] == "atlas_uart":
        raise ValueError("water_level does not use atlas_uart; use usb_line")
    if set(names.values()) == {"usb_line"}:
        return SensorGroup({}, combined=usb_line_bank_from_config(sensors_cfg))

    level_cfg = blocks["water_level"]
    drivers: dict[str, Any] = {}
    for kind, name in names.items():
        cfg = blocks[kind]
        if name == "usb_line":
            merged = dict(sensors_cfg.get("usb_line") or {})
            merged.update({key: value for key, value in cfg.items() if value is not None})
            if not merged.get("port"):
                raise ValueError(f"{kind} usb_line requires a port")
            link = SerialLink(
                str(merged["port"]),
                int(merged.get("baud", 9600)),
                float(merged.get("timeout_s", 2)),
            )
            drivers[kind] = UsbFieldDriver(
                kind,
                link,
                query=str(merged.get("query", "R")),
                low_cm=level_cfg.get("low_cm", merged.get("low_cm")),
                high_cm=level_cfg.get("high_cm", merged.get("high_cm")),
            )
        elif name == "atlas_uart":
            if kind == "water_level":
                raise ValueError("water_level does not use atlas_uart; use usb_line")
            unit = {"dissolved_oxygen": "mg/L", "ph": "pH", "temperature": "C"}[kind]
            inner = _atlas_driver(kind, unit, cfg, salinity, pressure, AtlasEzoDriver)
            drivers[kind] = AtlasTemperature(inner) if kind == "temperature" else inner
        elif name == "ds18b20" and kind == "temperature":
            drivers[kind] = Ds18b20Driver(
                device_path=cfg.get("device_path"),
                w1_root=cfg.get("w1_root", "/sys/bus/w1/devices"),
            )
        else:
            raise ValueError(f"unknown {kind} driver {name} for usb mode")
    return SensorGroup(drivers)
