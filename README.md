# Prawn Monitor

Prawn Monitor is a local water-quality and camera system for a small recirculating shrimp farm. The first tank is the 225 gallon nursery at NorCal Premier Prawn in Ukiah, California, growing Pacific whiteleg shrimp (`Litopenaeus vannamei`).

Phase 1 runs on one Seeed Studio reServer x86 (ODYSSEY Intel, Linux) at the tank: dissolved oxygen, temperature, pH, water level, an overhead camera, SQLite history, a phone dashboard, and alerts. The tank loop does not need the internet. Phase 2 is optional clip tracking with AMADEUS on a separate GPU PC. An RTX 4060 is the card planned for this farm. You can move this same hub onto that PC later. A Raspberry Pi can still be a tank node if you already have one. Phase 1 does not need a Pi, a GPU, or AMADEUS.

The layout follows [Sync Tank](https://github.com/looseleif/sync-tank) (MIT): one node per tank, a hub that polls those nodes, and tank identity that stays intact as you add tanks. Prawn Monitor is original code. It does not include Sync Tank's 3D habitat model, robotic camera rigs, or optional cloud image notes. See `NOTICE`.

## Phases

| Phase | Host | What runs |
| --- | --- | --- |
| 1 | Seeed reServer x86 | USB probes, USB or IP camera, hub, dashboard, alerts |
| 2, optional | Linux or Windows PC with a GPU | AMADEUS reads clips recorded in phase 1. Not installed with phase 1 |
| Later, optional | That same GPU PC | Move the hub and node there if you outgrow the reServer |

## How it is put together

```
test kit (by hand) ─┐
                    ▼
DO, pH, temperature, level          overhead USB or IP camera
        │                                  │
        ▼                                  ▼
 reServer x86 tank node ── localhost ──► hub on the same reServer
        │                                  │
        │ MP4, when you want a clip        │ email / SMS, if configured
        ▼                                  ▼
 phase 2: GPU PC runs AMADEUS        phone on the farm Wi-Fi
 (separate program, not required)    no internet required
        │
        ▼ tracking CSV, optional
      hub activity charts
```

A second tank can be another node on the LAN, on this reServer pattern or on a Raspberry Pi. Add it to the hub config. A node that is down does not hide the others.

## Quick start

Phase 1 with no probes, on the reServer or any other x86 Linux machine:

```bash
docker compose up --build
```

The dashboard is at `http://127.0.0.1:8080`. The node is simulated, so this is the path for CI and for the bench before USB is plugged in. Logs are `docker compose logs -f`.

Without Docker:

```bash
./scripts/reserver-up.sh
```

`./scripts/reserver-up.sh --check` starts that pair, confirms the nursery, and exits. Real probes, the camera, and systemd are in [docs/reserver.md](docs/reserver.md). Install on the box with `sudo ./scripts/setup-reserver.sh`. That install does not pull a GPU stack.

The two-tank laptop demo is still:

```bash
./scripts/demo.sh --serve
```

It plays a dissolved-oxygen drop and a test-kit entry, records a clip, and imports `examples/amadeus_sample.csv`. Details are in [docs/quickstart.md](docs/quickstart.md).

A Raspberry Pi node, if you add one later, still uses [docs/parts.md](docs/parts.md), [docs/wiring.md](docs/wiring.md), [docs/calibration.md](docs/calibration.md), then:

```bash
sudo ./scripts/setup-node.sh --interfaces    # on each tank Pi
sudo ./scripts/setup-hub.sh                  # only if the hub is not already the reServer
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

## AMADEUS (phase 2, optional)

Not part of phase 1. Do not install it on the reServer. The reServer has no GPU, and the dashboard, alerts, and camera work without it.

Clip recording locks exposure and writes an MP4 plus a JSON sidecar (tank, time, resolution, frame rate, exposure, millimeters per pixel). When you want tracking, copy those files to the GPU machine and run [AMADEUS](https://amadeus.jpmyrmecol.com/) there. AMADEUS is AGPL-3.0-only and is not included in this repository. The import step reads the CSV from its `results/` folder. Recording notes for an RTX 4060 8 GB card are in [docs/amadeus.md](docs/amadeus.md). Moving the hub itself onto that PC is optional and later. Phase 1 stays on the reServer.

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
./scripts/reserver-up.sh --check
```

Alert boundaries, cooldown, acknowledgement, and escalation are tested without hardware. `tests/test_e2e.py` runs the simulated farm: two nodes, the hub, a critical oxygen alert, a test-kit entry, a JPEG, an MP4, and an AMADEUS CSV.

## Docs

- [reServer x86, phase 1](docs/reserver.md)
- [Quick start](docs/quickstart.md)
- [Parts and prices](docs/parts.md)
- [Wiring](docs/wiring.md) (Raspberry Pi node; the reServer uses USB)
- [Calibration](docs/calibration.md)
- [AMADEUS recording](docs/amadeus.md) (phase 2, optional)

## License

MIT. Sync Tank is credited in `NOTICE`. AMADEUS stays a separate program under its own AGPL-3.0-only license.
