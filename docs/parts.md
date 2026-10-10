# Parts list

Prices are approximate retail in US dollars, checked in October 2026, before tax and shipping. Street prices for the Pi and camera move around. Confirm the manufacturer page before you order. One hub serves every tank. The parts below are for one tank node, plus the shared hub line at the end.

The nursery is 225 gallons. The same node parts fit the next tank; only the mount and the millimeters-per-pixel number change.

## Shared, either chemistry build

| Item | Approx. USD | Notes |
| --- | ---: | --- |
| Raspberry Pi 5, 4 GB | 80 | A Pi 4 with 2 GB is enough if you already have one (often nearer $45) |
| Official 27 W USB-C supply | 12 | Do not power the Pi from the pump circuit |
| 32 GB microSD, A1 | 10 | |
| Short Cat6 cable | 8 | Into the farm switch. The node does not need Wi-Fi |
| Waterproof DS18B20 plus 4.7 kΩ resistor | 8 | |
| Float switches, 2 | 8 | One low, one high |
| JSN-SR04T waterproof ultrasonic | 12 | Electronics stay dry; the transducer faces the water |
| Overhead camera | 30 | Pi Camera Module 3, or a UVC USB camera that can lock exposure |
| Enclosure, glands, proto board, wire | 40 | Keep it out of splash |
| **Shared subtotal** | **208** | |

## Atlas build

Continuous-immersion probes. The EZO circuits speak I2C and do their own temperature, salinity, and pressure compensation.

| Item | Approx. USD | Source price, October 2026 |
| --- | ---: | --- |
| EZO-DO circuit | 53.99 | [atlas-scientific.com](https://atlas-scientific.com/embedded-solutions/ezo-dissolved-oxygen-circuit/) |
| EZO-pH circuit | 45.99 | [EZO-pH](https://atlas-scientific.com/embedded-solutions/ezo-ph-circuit/) |
| Mini lab-grade DO probe ENV-20-DOX | 199.99 | [probe page](https://atlas-scientific.com/probes/mini-d-o-probe/). The full-size ENV-40-DOX is $259.99 |
| Lab-grade pH probe ENV-40-pH, or mini ENV-20-pH | 99.99 | [probe list](https://atlas-scientific.com/probes/) |
| pH 4 / 7 / 10 calibration sachets | 15 | |
| **Atlas chemistry** | **415** | |
| **Atlas node, with the shared parts** | **623** | |

The mini DO probe holds less electrolyte than the full-size probe, so it needs refilling sooner. Budget replacement membrane caps when you reorder. Power the EZO boards from the Pi 3.3 V rail so the I2C lines stay at 3.3 V. If a probe shell can be earthed through another instrument, use an isolated carrier instead of the plain EZO; check Atlas for the current isolated-board price.

## DFRobot build

Cheaper dissolved-oxygen kit, analog pH, and an ADS1115 because the Pi has no useful analog input.

| Item | Approx. USD | Source price, October 2026 |
| --- | ---: | --- |
| Gravity analog DO kit SEN0237-A | 169.00 | [DFRobot](https://www.dfrobot.com/product-1628.html) |
| Gravity analog pH kit V2 SEN0161-V2 | 39.50 | [DFRobot](https://www.dfrobot.com/product-1782.html) |
| ADS1115 breakout | 12 | I2C address 0x48 |
| Sodium sulfite for a zero-oxygen check | 10 | |
| **DFRobot chemistry** | **230** | |
| **DFRobot node, with the shared parts** | **438** | |

DFRobot's own page for SEN0161-V2 says that probe is for lab use and drifts if it is left in the water. For a probe that stays in the nursery, use their industrial analog pH kit (search "Gravity Industrial Analog pH") or the Atlas probe. The analog DO kit is a galvanic probe made for continuous reading; replace the membrane cap and filling solution on the schedule in the kit manual (on the order of a month for electrolyte, longer for the cap in clean water).

## Hub

| Item | Approx. USD | Notes |
| --- | ---: | --- |
| Shop PC you already have | 0 | Any always-on Linux machine on the LAN |
| Or a second Pi 5, supply, and card | 100 | Same class of board as a node |

The RTX 4060 8 GB machine is not part of this bill. It runs AMADEUS, which is a separate install. See [amadeus.md](amadeus.md).

## Adding a tank

Copy the node build. Give the new Pi its own `tank.id`, a static LAN address, and a line in the hub `tanks` array. The dashboard picks it up on the next poll. You do not rebuild the hub.
