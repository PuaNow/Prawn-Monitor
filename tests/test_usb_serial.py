import pytest

from prawn_monitor.sensors import build_sensors
from prawn_monitor.usb_serial import (
    SerialLink,
    UsbLineBank,
    parse_atlas_uart_frame,
    parse_usb_line,
    readings_from_usb_fields,
)


def test_parse_usb_line_words_and_csv_pairs():
    words = parse_usb_line("do=6.42 temp_f=84.1 ph=7.91 level=ok level_cm=40.2\r\n")
    assert words == {
        "do": "6.42",
        "temp_f": "84.1",
        "ph": "7.91",
        "level": "ok",
        "level_cm": "40.2",
    }
    pairs = parse_usb_line("DO,6.42,TEMP_C,28.9,PH,7.91,LEVEL,high,LEVEL_CM,60")
    assert pairs["do"] == "6.42"
    assert pairs["temp_c"] == "28.9"
    assert pairs["level"] == "high"
    try:
        parse_usb_line("hello")
    except ValueError:
        pass
    else:
        raise AssertionError("garbage line should fail")


def test_atlas_uart_frame_status_and_echo():
    assert parse_atlas_uart_frame(b"\x016.25,80.1\r") == (1, "6.25,80.1")
    assert parse_atlas_uart_frame(b"R\r\x016.25\r") == (1, "6.25")
    assert parse_atlas_uart_frame(b"\xfe\r") == (254, "")


def test_line_fields_convert_celsius_and_classify_level():
    low = readings_from_usb_fields(
        {"do": "6.1", "temp_c": "0", "ph": "7.8", "level_cm": "10"},
        low_cm=25,
        high_cm=55,
    )
    assert low["temperature"]["value"] == 32
    assert low["temperature"]["unit"] == "F"
    assert low["water_level"]["value"] == "low"
    assert low["water_level"]["extra"]["level_cm"] == 10
    high = readings_from_usb_fields(
        {"do": "6.1", "temp_f": "84", "ph": "7.8", "level": "high", "level_cm": "40"},
        low_cm=25,
        high_cm=55,
    )
    assert high["water_level"]["value"] == "high"
    broken = readings_from_usb_fields(
        {"do": "nope", "temp_f": "84", "ph": "7.8", "level": "ok"},
        low_cm=25,
        high_cm=55,
    )
    assert broken["dissolved_oxygen"]["ok"] is False
    assert broken["ph"]["ok"] is True
    assert broken["ph"]["value"] == 7.8


def test_missing_port_marks_every_probe_offline():
    def offline(_query: str) -> str:
        raise TimeoutError("no reply from /dev/ttyUSB0")

    bank = UsbLineBank(offline)
    readings = bank.readings()
    assert set(readings) == {"dissolved_oxygen", "temperature", "ph", "water_level"}
    assert all(row["ok"] is False for row in readings.values())
    assert bank.command("ph", "cal_mid", {"value": 7})["ok"] is False


def test_serial_link_uses_injected_port():
    class FakeSerial:
        def __init__(self):
            self.written = []
            self.is_open = True
            self.timeout = 1
            self.reads = [b"\xfe\r", b"\x017.10\r"]

        def reset_input_buffer(self):
            return None

        def write(self, data):
            self.written.append(data)
            return len(data)

        def readline(self):
            return b"do=6.5 temp_f=84 ph=7.9 level=ok level_cm=40\n"

        def read_until(self, expected=b"\r", size=None):
            return self.reads.pop(0)

        def close(self):
            self.is_open = False

    serial = FakeSerial()
    link = SerialLink("/dev/ttyUSB0", opener=lambda *_args: serial)
    line = link.transact_line("R")
    assert line.startswith("do=6.5")
    assert serial.written[0] == b"R\r"
    code, text = link.transact_atlas("R", 0)
    assert (code, text) == (1, "7.10")
    link.close()
    assert serial.is_open is False


def test_build_usb_line_and_mixed_atlas(monkeypatch, tmp_path):
    class FakeLink:
        def __init__(self, port, baud=9600, timeout_s=2.0, opener=None):
            self.port = port

        def transact_line(self, query):
            if self.port.endswith("LEVEL"):
                return "level=ok level_cm=40"
            return "do=6.42 temp_c=28 ph=7.91 level=low level_cm=10"

        def transact_atlas(self, command, delay_s):
            if command != "R":
                return 1, ""
            if self.port.endswith("DO"):
                return 1, "6.50"
            if self.port.endswith("PH"):
                return 1, "7.90"
            return 1, "28.50"

        def close(self):
            return None

    monkeypatch.setattr("prawn_monitor.usb_serial.SerialLink", FakeLink)
    line = build_sensors(
        {
            "sensors": {
                "mode": "usb",
                "usb_line": {"port": "/dev/serial/by-id/bridge"},
                "water_level": {"low_cm": 25, "high_cm": 55},
            }
        },
        tmp_path,
    )
    sample = line.readings()
    assert sample["dissolved_oxygen"]["value"] == 6.42
    assert sample["dissolved_oxygen"]["driver"] == "usb_line"
    assert sample["temperature"]["extra"]["celsius"] == 28
    assert sample["water_level"]["value"] == "low"
    assert line.inventory()[0]["driver"] == "usb_line"
    line.close()

    mixed = build_sensors(
        {
            "compensation": {"salinity_ppt": 20, "pressure_kpa": 99},
            "sensors": {
                "mode": "usb",
                "dissolved_oxygen": {"driver": "atlas_uart", "port": "/dev/serial/by-id/DO"},
                "ph": {"driver": "atlas_uart", "port": "/dev/serial/by-id/PH"},
                "temperature": {"driver": "atlas_uart", "port": "/dev/serial/by-id/RTD"},
                "water_level": {
                    "driver": "usb_line",
                    "port": "/dev/serial/by-id/LEVEL",
                    "low_cm": 25,
                    "high_cm": 55,
                },
            },
        },
        tmp_path,
    )
    readings = mixed.readings()
    assert readings["dissolved_oxygen"]["value"] == 6.5
    assert readings["dissolved_oxygen"]["driver"] == "atlas_uart"
    assert readings["ph"]["value"] == 7.9
    assert readings["temperature"]["value"] == pytest.approx(83.3)
    assert readings["temperature"]["unit"] == "F"
    assert readings["temperature"]["extra"]["celsius"] == 28.5
    assert readings["water_level"]["value"] == "ok"
    assert readings["water_level"]["extra"]["level_cm"] == 40
    names = {row["kind"]: row["driver"] for row in mixed.inventory()}
    assert names["dissolved_oxygen"] == "atlas_uart"
    assert names["water_level"] == "usb_line"
    mixed.close()

    simulated = build_sensors({"sensors": {"mode": "simulated"}}, tmp_path)
    assert simulated.readings()["dissolved_oxygen"]["driver"] == "simulated"

    with pytest.raises(ValueError, match="water_level"):
        build_sensors(
            {
                "sensors": {
                    "mode": "usb",
                    "dissolved_oxygen": {"driver": "atlas_uart", "port": "/dev/serial/by-id/DO"},
                    "water_level": {"driver": "atlas_uart", "port": "/dev/serial/by-id/LEVEL"},
                }
            },
            tmp_path,
        )
