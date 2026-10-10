# Calibration

Do this with the tank at its real salinity and with the DS18B20 already in the water. The node feeds that temperature into the dissolved-oxygen and pH readings. Set `compensation.salinity_ppt` to the test-kit salinity (the nursery is often near 20 ppt) and `compensation.pressure_kpa` to the local barometer. Ukiah's elevation makes 99 kPa a better default than 101.3.

Commands go to the node:

```bash
curl -s -X POST http://192.168.1.21:8090/api/sensors/dissolved_oxygen/command \
  -H 'Content-Type: application/json' \
  -d '{"op":"cal_atm"}'
```

Simulated mode refuses these commands on purpose. There is no probe to calibrate.

## DS18B20

These are factory calibrated. Once a year, put the tip in an ice bath and confirm the dashboard is near 32 °F. A reading several degrees off is usually a bad crimp or the wrong `device_path`, not a math problem.

## Atlas pH

Use fresh 4.00, 7.00, and 10.00 buffers at the same temperature as the tank if you can. Rinse the probe with tank water between buffers, not with distilled water if you can avoid a long soak in it.

1. `{"op":"cal_clear"}` if you are starting over.
2. Mid point in 7.00: `{"op":"cal_mid","value":7.00}`
3. Low point in 4.00: `{"op":"cal_low","value":4.00}`
4. High point in 10.00: `{"op":"cal_high","value":10.00}`

The circuit stores the calibration. Recalibrate when the probe sits in a buffer and the dashboard disagrees by more than about 0.05, or on the probe maker's interval. Temperature compensation is the `T,<celsius>` command, which the node sends from the DS18B20 when the temperature moves by 0.2 °C.

## Atlas dissolved oxygen

Galvanic probes do not need a polarization wait.

1. Let the probe sit in the tank until the reading stops drifting.
2. Atmospheric calibration, probe in air that is humid (a bottle with a damp sponge, or just above the water, following the probe card): `{"op":"cal_atm"}`.
3. Optional zero: in sodium sulfite solution, `{"op":"cal_zero"}`.

Salinity and pressure:

```bash
curl -s -X POST .../api/sensors/dissolved_oxygen/command \
  -H 'Content-Type: application/json' -d '{"op":"salinity_ppt","value":20}'
curl -s -X POST .../api/sensors/dissolved_oxygen/command \
  -H 'Content-Type: application/json' -d '{"op":"pressure_kpa","value":99}'
```

The running node also sends those from `compensation` on its own. Refill electrolyte and replace the membrane cap on the interval printed on the probe. A mini probe uses its fill faster than a full-size probe.

## DFRobot analog pH

The board's voltage rises as pH falls. You need two buffers.

1. Stir the probe in 7.00 buffer until the voltage settles.
2. `{"op":"capture_ph7"}` on the `ph` sensor. The node stores that voltage in `calibration.json`.
3. Rinse, then 4.00 buffer, then `{"op":"capture_ph4"}`.

You can also write the voltages you measured with a meter:

```json
{"op":"set_calibration","v_ph7":1.50,"v_ph4":2.05}
```

The conversion is a straight line through those two points. Check it in 10.00 buffer. If 10.00 is off by more than about 0.1, the probe is tired or the board is not the Gravity V2 slope this driver expects.

SEN0161-V2 is a lab probe. If it lives in the nursery, plan on frequent checks against buffers, or switch to an industrial probe. See [parts.md](parts.md).

## DFRobot analog dissolved oxygen

The driver maps voltage with two points:

`DO = (V - Vzero) / (Vsat - Vzero) × saturation(temperature, salinity, pressure)`

Saturation uses the Garcia and Gordon 1992 combined fit, converted to mg/L, then scaled by barometric pressure. At 25 °C in freshwater at 1 atm that is about 8.26 mg/L. At nursery salinity it is lower, which is what you want.

1. Optional zero: probe in sodium sulfite solution, wait until the voltage stops falling, `{"op":"capture_zero"}` on `dissolved_oxygen`. If you skip this, the driver uses `v_zero` from config or 0 V.
2. Air calibration: probe in humid air or in air-saturated water, at a known temperature, `{"op":"capture_sat"}`.
3. Put the probe back in the tank. The next reading uses the DS18B20 temperature.

`capture_sat` assumes the probe is at 100% saturation at the current temperature. Do not capture it in a bucket of stagnant water.

## Level

Float switches need a position, not a formula. Lift each float by hand and confirm the dashboard changes to `low` or `high`.

For the ultrasonic sensor, measure `mount_height_cm` with a tape from the transducer face to your zero. Then set `low_cm` and `high_cm` to the water heights where you want alerts. Compare `level_cm` on the dashboard with a ruler in the tank. A consistent offset means the mount height is wrong. A reading stuck near the minimum means the transducer is inside its blind zone.

## Millimeters per pixel

AMADEUS reports pixels. The hub converts them with one number: millimeters per pixel at the depth where the shrimp actually are.

1. Put a ruler, or a tile of known length, at that depth under the overhead camera.
2. Record a snapshot.
3. Count pixels across a known span.
4. `mm_per_pixel = known_mm / pixel_span`.
5. Store it on the tank in the hub config and on the node camera config.

A ruler on the tank floor is wrong if the animals cruise higher in the column. Recalibrate when you move the camera or change the water depth a lot. Refraction through the lid window is folded into this number, so measure through the same window you record through.

## Test kit

The probes do not measure TAN, nitrite, nitrate, or alkalinity. Enter those on the Test kit tab. Alkalinity is mg/L as CaCO3. If the kit reports dKH, multiply by 17.9. The latest number for each parameter is the one the alerter uses, so a bad TAN stays alarming until a newer test comes back inside the limit.
