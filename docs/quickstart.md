# Simulated quick start

You can run the whole farm on a laptop. Nothing here needs a Raspberry Pi, a probe, or a network route off the machine.

```bash
git clone <this repo>
cd prawn-monitor
./scripts/demo.sh --serve
```

The script creates `.venv` if you do not already have one, clears the demo data directory, starts two simulated nodes, and starts the hub. The nursery is the 225 gallon tank. Grow-out 1 is a second node with no volume filled in, so the hub is already shaped for the next tank.

The scenario then:

1. Polls both nodes while the water is inside the vannamei band.
2. Drops nursery dissolved oxygen to 3.6 mg/L and moves temperature from 83 °F to 85.5 °F.
3. Saves a test-kit row: TAN 1.2, nitrite 0.7, nitrate 40, alkalinity 90 mg/L as CaCO3, salinity 20 ppt.
4. Acknowledges the dissolved-oxygen alert.
5. Grabs a JPEG, records a short fixed-exposure MP4, and imports `examples/amadeus_sample.csv`.

Open the printed `http://127.0.0.1:<port>` address on your phone or computer. Use Now, History (7 days is the default range), Camera, Test kit, Alerts, and Activity. Ctrl-C stops the processes.

To run that path in CI or before a commit, without leaving servers up:

```bash
./scripts/demo.sh
```

The same check is `tests/test_e2e.py`.

## Point a real hub at a simulated node

```bash
PYTHONPATH=src .venv/bin/python -m prawn_monitor node --config config/node.simulated.example.json
```

In another shell, set that URL as `node_url` in a copy of `config/hub.example.json` and start the hub. `POST /api/simulate` on the node accepts `dissolved_oxygen`, `temperature_f`, `ph`, `level` (`ok`, `low`, or `high`), `level_cm`, and `offline` (a list of sensor names).

## What you should see

- Nursery volume 225 gallons, both tanks listed separately
- A critical dissolved-oxygen alert and a temperature-swing warning
- Critical TAN, warning nitrite, warning alkalinity
- Grow-out 1 stays quiet
- A JPEG snapshot and an MP4 whose metadata says exposure is fixed
- Activity numbers from the sample CSV: 2 animals, 90 mm traveled, 50 mm/s mean speed, 35 mm median length at 1 mm per pixel and 10 fps

SMTP is left blank in the demo, so delivery is recorded as skipped and the alerts still show on the dashboard.
