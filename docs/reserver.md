# Phase 1 on a Seeed reServer x86

Phase 1 is dissolved oxygen, temperature, pH, water level, an overhead camera, the phone dashboard, and alerts. It runs on one Seeed Studio reServer (ODYSSEY Intel, x86_64 Linux). The nursery is the 225 gallon vannamei tank at NorCal Premier Prawn. The tank loop does not need the internet.

Phase 2 is [AMADEUS](amadeus.md) on a separate GPU PC. Do not install it here. The reServer has no NVIDIA GPU, and phase 1 does not use one. Moving this same hub onto that PC later is optional.

A Raspberry Pi can still be an extra tank node. Phase 1 does not need one. The Pi wiring notes stay in [wiring.md](wiring.md). Do not use the Pi node config on the reServer: the 40-pin header on an ODYSSEY board is the onboard SAMD21 coprocessor, not Linux GPIO, so the I2C and `RPi.GPIO` drivers will not see those pins.

## What you are aiming at

```text
USB DO, pH, temperature, level          USB camera or IP camera
        │                                        │
        ▼                                        ▼
 node on the reServer  ── 127.0.0.1:8090 ──►  hub on the same reServer :8080
                                                   │
                                                   ▼
                                            phone on the farm Wi-Fi
```

SQLite lives on the reServer. Email and SMS are optional and are the only parts that need the internet. If SMTP is blank, the alert is still on the dashboard.

## One command before the probes are plugged in

From a clone of this repo, on the reServer or any other x86 Linux machine:

```bash
docker compose up --build
```

The dashboard is `http://127.0.0.1:8080`. Both containers log to stdout (`docker compose logs -f`). The node is simulated: dissolved oxygen, temperature, pH, level, and the camera picture are stand-ins, which is what you want in CI and on the bench. `docker compose down` stops them and keeps the SQLite volumes. `docker compose down -v` deletes those volumes.

Without Docker:

```bash
./scripts/reserver-up.sh
```

That starts the same simulated node and hub in the foreground. Ctrl-C stops both. Check that path and exit:

```bash
./scripts/reserver-up.sh --check
```

You should see a line like `phase-1 reServer check ok: nursery 6.5 mg/L`. Python 3.11 or newer is required. Ubuntu 24.04 has it. Ubuntu 22.04 needs `sudo apt-get install python3.11 python3.11-venv`.

## Install on the reServer

Do this once, while the machine still has a network path for `apt`. After that the tank loop is local.

```bash
sudo ./scripts/setup-reserver.sh
```

The script installs Python, ffmpeg, and `v4l-utils`. It does not install CUDA, an NVIDIA driver, or AMADEUS. It copies starter configs to `/etc/prawn-monitor/` if those files are not there yet, adds your user to the `dialout` and `video` groups, and installs `prawn-monitor-reserver.service`.

Log out and back in so the new groups apply, then edit the two files:

- `/etc/prawn-monitor/node.json` — serial device paths and the camera
- `/etc/prawn-monitor/hub.json` — set `public_url` to `http://<reServer-lan-ip>:8080` so alert links point at this box. The nursery `node_url` stays `http://127.0.0.1:8090` on this machine. If an older hub file still points at a Pi, change that URL. The script does not overwrite a hub or node file that is already there.

Give the reServer a DHCP reservation. Plug one 2.5GbE port into the farm switch. Do not forward port 8080 on the router. Power the reServer from its own supply on a GFCI outlet, not from a pump relay.

When the paths in the node file match real devices:

```bash
sudo systemctl enable --now prawn-monitor-reserver.service
systemctl status prawn-monitor-reserver.service
journalctl -u prawn-monitor-reserver.service -f
```

The service runs `./scripts/reserver-up.sh --hardware`, which prefers `/etc/prawn-monitor/node.json` and `/etc/prawn-monitor/hub.json`. The dashboard on a phone on the same LAN is `http://<reServer-lan-ip>:8080`.

Until the probes are in the water, you can keep using `./scripts/reserver-up.sh` (simulated) and leave the service disabled. A missing dongle does not crash the node: that sensor comes back `ok: false`, and dissolved oxygen offline is a critical alert.

## USB probes

List stable names. `ttyUSB0` moves when you replug; the `by-id` path does not.

```bash
ls -l /dev/serial/by-id
```

Unplug one adapter, run it again, and see which name disappeared. Put that path in the config. Several FTDI adapters plus a camera may want a powered USB hub. The reServer's own ports are the right place; do not try to bit-bang the 40-pin header from this Linux install.

Two layouts are supported. Use one.

### Atlas EZO circuits on USB-serial

This is the layout in `config/node.reserver.usb.example.json`. Each EZO circuit is in UART mode (the protocol switch off I2C), 9600 8N1, talking to its own 3.3 V USB-serial adapter (FTDI or CP2102).

| EZO pin | USB-serial |
| --- | --- |
| TX | adapter RX |
| RX | adapter TX |
| GND | adapter GND |
| VCC | 3.3 V, not 5 V |

| Circuit | Example `by-id` role | Reading |
| --- | --- | --- |
| EZO-DO | `..._DO` | mg/L |
| EZO-pH | `..._PH` | pH |
| EZO-RTD | `..._RTD` | temperature, stored as °F |

Cross TX and RX. Share ground. Leave the BNC probe on the circuit, and keep the circuit in the dry box. The node sends temperature, salinity, and pressure compensation with the same commands as the Pi I2C driver. Calibration ops are unchanged: `cal_mid`, `cal_low`, `cal_high`, `cal_atm`, `cal_zero`. See [calibration.md](calibration.md). Default addresses in the UART config are ignored, because UART has one circuit per port.

`compensation.salinity_ppt` is the test-kit salinity (the nursery is often near 20 ppt). `compensation.pressure_kpa` stays 99 for Ukiah.

Water level is not an EZO reading. Put it on its own USB-serial port with the line protocol below. `low_cm` 25 and `high_cm` 55 match the nursery limits already used on the Pi: low water is critical, high water is a warning.

A DS18B20 that the kernel already exposes can replace the RTD. Set the temperature driver to `ds18b20` and, if you need to, `device_path` to that `w1_slave` file. A USB 1-wire adapter only works when `/sys/bus/w1/devices/28-*` actually appears.

### One USB bridge for all four readings

`config/node.reserver.usb-line.example.json` is the other layout: one serial port, one ASCII line per poll. The node writes `R` and a carriage return (change `query` if your bridge uses something else) and reads one line. Either shape is fine:

```text
do=6.42 temp_f=84.1 ph=7.91 level=ok level_cm=40.2
DO,6.42,TEMP_C,28.9,PH,7.91,LEVEL,ok,LEVEL_CM,40.2
```

| Key | Meaning |
| --- | --- |
| `do` | Dissolved oxygen, mg/L |
| `temp_f` or `temp_c` | Temperature. Fahrenheit is stored either way |
| `ph` | pH |
| `level` | `ok`, `low`, or `high` |
| `level_cm` | Water height in centimeters |

`level=low` or `level=high` wins. With only `level_cm`, the node uses `low_cm` and `high_cm`. There is no calibration HTTP command in this mode; calibrate the bridge, then confirm the dashboard against the test kit. A line that omits a key marks that one sensor offline and keeps the others.

Baud is 9600 unless you set `baud`. Timeout is 2 seconds.

## Camera

USB UVC, which is the example:

```json
"camera": {
  "id": "overhead",
  "driver": "v4l2",
  "device": "/dev/video0",
  "input_format": "mjpeg",
  "width": 1280,
  "height": 720,
  "fps": 15,
  "mm_per_pixel": 0.4,
  "exposure_us": 8000
}
```

`ls -l /dev/video*` after you plug the camera in. ffmpeg grabs the frames, so OpenCV is not required. If the picture is empty, drop `input_format` or set it to `yuyv422`. Width and height must be even.

Lock exposure before you trust a clip. Units differ by camera. Read the control list, then set manual exposure and confirm the preview does not brighten when a shrimp crosses:

```bash
v4l2-ctl -d /dev/video0 --list-ctrls
v4l2-ctl -d /dev/video0 -c auto_exposure=1
v4l2-ctl -d /dev/video0 -c exposure_time_absolute=80
```

On some cameras manual is `auto_exposure=1`. On others it is `3`. The dashboard reports `auto_exposure: null` until you have done this yourself.

An IP camera uses the same node. RTSP gets TCP transport by default. An HTTP MJPEG URL does not:

```json
"camera": {
  "id": "overhead",
  "driver": "ip",
  "url": "rtsp://192.168.1.50:554/stream1",
  "width": 1280,
  "height": 720,
  "fps": 15,
  "mm_per_pixel": 0.4
}
```

Lock exposure in that camera's own settings. The old `"driver": "usb"` path still imports OpenCV if you already use it. `"driver": "picamera2"` is the Raspberry Pi camera only.

`recording.enabled` is false in the examples so a missing camera does not throw on a schedule. When the snapshot looks right, set it to true. The example times are 08:00 and 16:00 Pacific, 180 seconds. Clips land in `var/reserver/node/clips/` as an MP4 plus a JSON sidecar. Phase 1 is done without ever copying those files anywhere.

Bolt the camera above the tank, looking straight down. The mount has to stay put if you later measure millimeters per pixel. See [calibration.md](calibration.md).

## What good looks like

- `curl -s http://127.0.0.1:8080/health` returns `"service": "prawn-hub"`
- The nursery tile is online, 225 gallons, and the four readings move about every 15 seconds (`poll_interval_s`)
- The camera tab shows a JPEG
- Unplugging the dissolved-oxygen adapter marks that sensor offline and raises a critical alert. pH, temperature, and level stay up
- `journalctl -u prawn-monitor-reserver.service -f` shows the hub line and the node line, and nothing about CUDA

The Pi quick start with two simulated tanks is still `./scripts/demo.sh --serve`. That is a laptop demo, not the reServer service.

## Docker with real devices

Prefer the systemd service on the farm. If you want containers to see the probes, edit `docker-compose.hardware.yml`, uncomment the device lines that exist, point `config/node.reserver.usb.example.json` at those paths, and run:

```bash
docker compose -f docker-compose.yml -f docker-compose.hardware.yml up --build
```

A device path that is not on the machine makes Docker refuse to start. The default `docker compose up` stays on the simulated node for that reason.

## Phase 2, and the RTX 4060 later

Phase 2 is optional. Copy a clip and its JSON to the GPU PC (USB stick or `rsync` on the LAN) and run AMADEUS there. Bring the CSV back and import it on the Activity tab. Notes for an RTX 4060 8 GB card are in [amadeus.md](amadeus.md). None of that has to be installed for the nursery to be monitored.

The same hub and node can move later to a Linux or Windows PC with that 4060. Phase 1 on Windows is Python 3.11, ffmpeg, and `pyserial`, with the same config shape. Do that when you want one box. Until then, leave the hub on the reServer.

## If something is quiet

| What you see | What to check |
| --- | --- |
| `pyserial is not installed` | `./scripts/setup-reserver.sh`, or `.venv/bin/pip install 'pyserial>=3.5'` |
| Sensor offline, port in the error | `ls -l /dev/serial/by-id`, and that your user is in `dialout` |
| Permission denied on the serial port | Log out after `usermod -aG dialout`. The service user is the one in the systemd unit |
| Snapshot 503, ffmpeg in the message | `sudo apt-get install ffmpeg` |
| `/dev/video0` missing | Camera unplugged, or it enumerated as `/dev/video1`. Your user needs the `video` group |
| Picture opens then goes black | Drop `input_format`, or set `yuyv422` |
| Node answers, hub tile stays offline | `node_url` in the hub file is `http://127.0.0.1:8090` on this box |
| Dashboard opens on the reServer and not on the phone | Phone on the farm LAN, `http://<lan-ip>:8080`, no guest Wi-Fi isolation |
| Probe drops off after it has been idle | USB autosuspend. Set that port's power control to `on` |
