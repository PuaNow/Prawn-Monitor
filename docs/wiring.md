# Wiring

One Raspberry Pi per tank. The Pi, the ADC, and the camera stay dry. Probes go through cable glands. Power the Pi from its own supply on a GFCI outlet, not from a pump relay.

BCM numbers below match `config/node.atlas.example.json` and `config/node.dfrobot.example.json`.

## Common bus

| Pi physical pin | BCM | Function |
| --- | --- | --- |
| 1 | 3.3 V | Probe electronics and the ADS1115 |
| 6 | GND | Common ground for sensors only |
| 3 | GPIO 2 | I2C SDA |
| 5 | GPIO 3 | I2C SCL |
| 7 | GPIO 4 | DS18B20 data, with 4.7 kΩ to 3.3 V |
| 11 | GPIO 17 | Low float switch |
| 13 | GPIO 27 | High float switch |
| 16 | GPIO 23 | JSN-SR04T trigger |
| 18 | GPIO 24 | JSN-SR04T echo, through a divider |

Enable the buses once:

```bash
sudo ./scripts/setup-node.sh --interfaces
sudo reboot
i2cdetect -y 1
```

You should see `0x61` and `0x63` for Atlas, or `0x48` for the ADS1115. DS18B20 devices show up as `/sys/bus/w1/devices/28-*`.

```
                    3.3 V
                      │
                     4.7k
                      │
Pi GPIO4 ─────────────┴──────── DS18B20 data
Pi GND ───────────────────────── DS18B20 GND
Pi 3.3 V ─────────────────────── DS18B20 VCC

Pi SDA/SCL ──┬── Atlas EZO-DO  (0x61)   or   ADS1115 (0x48)
             └── Atlas EZO-pH  (0x63)
```

Do not power an EZO board at 5 V and then tie its data pins to the Pi. At 3.3 V the EZO I2C lines are safe directly.

## Atlas EZO

Both circuits share SDA, SCL, 3.3 V, and GND. Default addresses are 97 (`0x61`) for dissolved oxygen and 99 (`0x63`) for pH. The BNC jack faces the probe. Keep the circuit inside the dry box.

The node sends temperature from the DS18B20, plus the salinity and pressure in `compensation`. Ukiah is about 190 m above sea level, so the example pressure is 99 kPa rather than 101.3.

## DFRobot analog boards and the ADS1115

| ADS1115 pin | Connect |
| --- | --- |
| VDD, GND, SCL, SDA | Pi 3.3 V, ground, GPIO 3, GPIO 2 |
| ADDR | GND (address 0x48, which is `72` in the JSON file) |
| A0 | DO signal converter analog out |
| A1 | pH signal converter analog out |

Power the Gravity boards from 3.3 V as well. Their analog output is specified as 0–3.0 V, which stays inside what an ADS1115 running at 3.3 V can measure. If you later power a board from 5 V, do not let that output exceed the ADS1115 supply.

The DO and pH boards each have a BNC jack for their probe. They do not speak I2C. The Pi reads voltage and applies the calibration in [calibration.md](calibration.md).

## Float switches

Use switches that close when the float rises. Wire one side of each switch to ground and the other side to the GPIO. The Pi enables its internal pull-up, so a wet switch reads low.

- Low switch at the lowest water level you will accept. For the nursery, that is still above the biofilter intake.
- High switch below the overflow.

If you only fit one of them, leave the other pin out of the config.

## JSN-SR04T

The transducer can live in the humid lid. The small circuit board cannot. Mount the transducer at least 25 cm above the highest water line; this sensor is blind inside about 20 cm.

`mount_height_cm` is the distance from the transducer face down to the tank's zero (the bottom, or whatever you call zero). Water height is mount height minus the measured air gap. `low_cm` and `high_cm` are water heights, not air gaps.

The trigger pin can be driven at 3.3 V. The echo pin on most of these boards is 5 V. Do not connect echo directly to the Pi. Divide it so the GPIO sees at most 3.3 V. A 10 kΩ resistor from echo to the GPIO and an 18 kΩ resistor from the GPIO to ground lands near 3.2 V.

Some JSN-SR04T boards are 5 V only on VCC. Power that board from 5 V, keep its ground common with the Pi, and still divide the echo.

## Camera

Fix the camera above the tank, looking straight down, and do not let the mount move between the clip you track and the clip you record tomorrow. Splash on the lens becomes a false animal. A lid window or a short hood is enough.

- Pi Camera Module 3: `"driver": "picamera2"`. The node turns auto-exposure off and sets `exposure_us`.
- USB UVC camera: `"driver": "usb"`. V4L2 exposure units are not microseconds on every camera. Set `exposure` to the manual value your camera understands, and confirm the preview does not brighten and darken on its own.

## Power and water

Keep mains and the Pi on opposite sides of the enclosure. Do not share the probe ground with a heater or a pump ground. If a probe reading jumps when a pump starts, you have a ground problem, not a calibration problem.

The hub is another computer on the same Ethernet switch. Give every Pi a DHCP reservation. The dashboard is only as private as that LAN: do not forward port 8080 to the router.
