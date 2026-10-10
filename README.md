# Prawn Monitor

Prawn Monitor is a local water-quality and camera system for a small recirculating shrimp farm. The first tank is the 225 gallon nursery at NorCal Premier Prawn in Ukiah, California, growing Pacific whiteleg shrimp (`Litopenaeus vannamei`). Each tank gets its own Raspberry Pi. A hub on the same LAN stores readings in SQLite and serves a phone dashboard. The farm keeps working when the internet does not.

The layout follows [Sync Tank](https://github.com/looseleif/sync-tank) (MIT): one node per tank, a hub that polls those nodes, and tank identity that stays intact as you add tanks. Prawn Monitor is original code. It does not include Sync Tank's 3D habitat model, robotic camera rigs, or optional cloud image notes. See `NOTICE`.

## How it is put together

```
test kit (by hand) ─┐
                    ▼
DO, pH, temperature, level          overhead camera
        │                                  │
        ▼                                  ▼
 Raspberry Pi tank node  ── LAN ──►  hub (SQLite + dashboard)
        │                                  │
        │ MP4 + JSON metadata              │ email / SMS
        ▼                                  ▼
 GPU PC runs AMADEUS                 phone on the farm Wi-Fi
 (separate program, AGPL)            no internet required
        │
        ▼ tracking CSV
      hub activity charts
```

Add a tank by running another node and appending it to the hub config. A node that is down does not hide the others.

## Quick start

No Pi and no probes:

```bash
./scripts/demo.sh --serve
```

That starts a simulated 225 gallon nursery and a second tank, plays a dissolved-oxygen drop and a test-kit entry, records a fixed-exposure clip, imports `examples/amadeus_sample.csv`, and prints the dashboard URL. `./scripts/demo.sh` (or `./scripts/demo.sh --check`) runs the same path and exits. Details are in [docs/quickstart.md](docs/quickstart.md).

With hardware, use [docs/parts.md](docs/parts.md), [docs/wiring.md](docs/wiring.md), [docs/calibration.md](docs/calibration.md), then:

```bash
sudo ./scripts/setup-node.sh --interfaces    # on each tank Pi
sudo ./scripts/setup-hub.sh                  # on the always-on hub
```

## What the dashboard shows

- Current dissolved oxygen, temperature, pH, and water level for each tank
- History charts, 7 days by default, or 24 hours, 30 days, or a custom range
- Overhead snapshot, live MJPEG, and the clip list
- A test-kit form for TAN, nitrite, nitrate, alkalinity (mg/L as CaCO3), and salinity
- Alerts with acknowledgement, plus per-tank thresholds
- Activity charts after an AMADEUS CSV import: speed, distance, crowding, approximate body length

## Alert defaults

These are the vannamei nursery limits. A reading on the limit is still inside the band. Change them in the dashboard or in the hub config. Low water is critical because the nursery can uncover the biofilter intake; high water is a warning. Both severities are configurable.

| Check | Warning | Critical |
| --- | --- | --- |
| Dissolved oxygen | under 5 mg/L | under 4 mg/L |
| Temperature | outside 82–86 °F | |
| Temperature swing | more than 2 °F in 24 h | |
| pH | outside 7.5–8.3 | |
| TAN | over 0.5 mg/L | over 1 mg/L |
| Nitrite | over 0.5 mg/L | |
| Nitrate | over 100 mg/L | |
| Alkalinity | under 120 mg/L as CaCO3 | |
| Water level | high | low |
| Node offline | | critical, after 120 s |
| Sensor offline | other probes | dissolved oxygen |

Warning alerts wait 60 minutes before they can be raised again. Critical alerts wait 15 minutes. Acknowledgement starts that cooldown. A jump from warning to critical notifies immediately. Salinity is stored and shown, and it has no default alert.

Email uses SMTP. SMS uses the same SMTP server with a carrier email-to-SMS address, or Twilio when you set `sms.mode` to `twilio`. If SMTP is empty, the alert is still stored on the dashboard.

This is a decision aid for people who already know the tank. It is not a life-support controller. A dissolved-oxygen crash can kill shrimp faster than an email round trip.

## AMADEUS

Clip recording locks exposure and writes an MP4 plus a JSON sidecar (tank, time, resolution, frame rate, exposure, millimeters per pixel). Copy those files to the GPU machine and run [AMADEUS](https://amadeus.jpmyrmecol.com/) there. AMADEUS is AGPL-3.0-only and is not included in this repository. The import step reads the CSV from its `results/` folder. Recording notes for an RTX 4060 8 GB card are in [docs/amadeus.md](docs/amadeus.md).

## Node API

The hub polls these. You can poll them too.

| Method | Path | Role |
| --- | --- | --- |
| GET | `/api/inventory` | Tank identity, drivers, camera links |
| GET | `/api/readings` | Latest probe sample |
| GET | `/camera/{id}/snapshot.jpg` | One JPEG |
| GET | `/camera/{id}/stream.mjpg` | MJPEG |
| POST | `/camera/{id}/clips` | Record a fixed-exposure MP4 |
| POST | `/api/sensors/{kind}/command` | Calibration commands |
| POST | `/api/simulate` | Set simulated values when `sensors.mode` is `simulated` |

`kind` is `dissolved_oxygen`, `temperature`, `ph`, or `water_level`.

## Tests

```bash
python3 -m venv .venv
.venv/bin/pip install pytest
PYTHONPATH=src .venv/bin/pytest
```

Alert boundaries, cooldown, acknowledgement, and escalation are tested without hardware. `tests/test_e2e.py` runs the simulated farm: two nodes, the hub, a critical oxygen alert, a test-kit entry, a JPEG, an MP4, and an AMADEUS CSV.

## Docs

- [Quick start](docs/quickstart.md)
- [Parts and prices](docs/parts.md)
- [Wiring](docs/wiring.md)
- [Calibration](docs/calibration.md)
- [AMADEUS recording](docs/amadeus.md)

## License

MIT. Sync Tank is credited in `NOTICE`. AMADEUS stays a separate program under its own AGPL-3.0-only license.
