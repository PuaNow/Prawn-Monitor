from prawn_monitor.chemistry import analog_do_mg_l, analog_ph, saturation_mg_l
from prawn_monitor.sensors import (
    AtlasEzoDriver,
    Ds18b20Driver,
    classify_level,
    counts_to_volts,
    echo_us_to_cm,
    parse_atlas_payload,
    parse_w1_slave,
)


def test_saturation_matches_freshwater_tables():
    assert saturation_mg_l(0, 0) == pytest_approx(14.62, 0.08)
    assert saturation_mg_l(20, 0) == pytest_approx(9.09, 0.05)
    assert saturation_mg_l(25, 0) == pytest_approx(8.26, 0.05)
    salty = saturation_mg_l(25, 20, 99.0)
    assert salty < saturation_mg_l(25, 0)
    assert salty == pytest_approx(saturation_mg_l(25, 20) * 99.0 / 101.325, 0.001)


def pytest_approx(value, tolerance):
    import pytest

    return pytest.approx(value, abs=tolerance)


def test_analog_calibration_math():
    sat = saturation_mg_l(25, 0)
    assert analog_do_mg_l(1.0, v_zero=0.0, v_sat=2.0, temp_c=25, salinity_ppt=0) == pytest_approx(sat / 2, 0.001)
    assert analog_ph(1.5, v_ph7=1.5, v_ph4=2.0) == pytest_approx(7.0, 0.001)
    assert analog_ph(2.0, v_ph7=1.5, v_ph4=2.0) == pytest_approx(4.0, 0.001)
    assert analog_ph(1.75, v_ph7=1.5, v_ph4=2.0) == pytest_approx(5.5, 0.001)


def test_atlas_payload_and_driver_compensation():
    assert parse_atlas_payload(1, "6.25,80.1") == 6.25
    assert parse_atlas_payload(1, "7.812") == 7.812

    class Fake:
        def __init__(self):
            self.commands = []

        def transact(self, address, command, delay):
            self.commands.append((address, command))
            if command == "R":
                return 1, "6.25,80.1"
            return 1, ""

    fake = Fake()
    driver = AtlasEzoDriver(
        kind="dissolved_oxygen",
        unit="mg/L",
        address=0x61,
        delay_s=0,
        transport=fake,
        salinity_ppt=20,
        pressure_kpa=99,
    )
    reading = driver.read(temp_c=28.2)
    assert reading["ok"] is True
    assert reading["value"] == 6.25
    commands = [item[1] for item in fake.commands]
    assert commands[0].startswith("T,")
    assert any(item.startswith("S,20") for item in commands)
    assert any(item.startswith("P,99") for item in commands)


def test_ds18b20_file_and_level_rules(tmp_path):
    slave = tmp_path / "w1_slave"
    slave.write_text("72 01 4b 46 7f ff 0c 10 1e : crc=1e YES\n72 01 4b 46 7f ff 0c 10 1e t=23125\n")
    reading = Ds18b20Driver(device_path=slave).read()
    assert reading["ok"] is True
    assert reading["extra"]["celsius"] == 23.125
    assert reading["value"] == pytest_approx(73.625, 0.001)
    try:
        parse_w1_slave("NO\nt=1000\n")
    except ValueError:
        pass
    else:
        raise AssertionError("crc failure should raise")
    assert classify_level(low_wet=True, high_wet=True, level_cm=10, low_cm=20, high_cm=50) == "high"
    assert classify_level(low_wet=False, high_wet=False, level_cm=40, low_cm=20, high_cm=50) == "low"
    assert classify_level(low_wet=True, high_wet=False, level_cm=40, low_cm=20, high_cm=50) == "ok"
    assert classify_level(low_wet=None, high_wet=None, level_cm=10, low_cm=20, high_cm=50) == "low"
    assert classify_level(low_wet=None, high_wet=None, level_cm=None, low_cm=20, high_cm=50) == "unknown"
    assert echo_us_to_cm(1000) == pytest_approx(17.15, 0.001)
    assert counts_to_volts(0x2000) == pytest_approx(1.024, 0.0001)
    assert counts_to_volts(0xE000) < 0
