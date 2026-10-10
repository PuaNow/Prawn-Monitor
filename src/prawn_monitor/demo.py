"""Simulated nursery plus a second tank, on localhost, with no hardware."""

from __future__ import annotations

import shutil
import threading
import urllib.request
from pathlib import Path
from typing import Any

from prawn_monitor.hub import HubApp, get_json, post_json
from prawn_monitor.httputil import serve
from prawn_monitor.node import NodeApp

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_CSV = REPO_ROOT / "examples" / "amadeus_sample.csv"


def _node_config(tank: dict[str, Any], data_dir: Path) -> dict[str, Any]:
    return {
        "host": "127.0.0.1",
        "port": 0,
        "data_dir": str(data_dir),
        "tank": tank,
        "sensors": {"mode": "simulated"},
        "camera": {
            "id": "overhead",
            "driver": "simulated",
            "width": 320,
            "height": 240,
            "fps": 10,
            "mm_per_pixel": 0.4,
            "exposure": "fixed",
            "exposure_us": 8000,
        },
        "recording": {"enabled": False, "timezone": "America/Los_Angeles"},
        "compensation": {"salinity_ppt": 20, "pressure_kpa": 99.0},
    }


class DemoStack:
    def __init__(self, root: Path, sink: list[dict[str, Any]] | None = None):
        self.root = root
        self.sink = sink if sink is not None else []
        self.nodes: list[tuple[NodeApp, Any]] = []
        self.hub: HubApp | None = None
        self.hub_http = None
        self.urls: dict[str, str] = {}

    def start(self) -> None:
        # Each demo replays the same story, so a previous acknowledgement
        # cannot sit inside the cooldown and hide the oxygen alert.
        if self.root.exists():
            shutil.rmtree(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        tanks = [
            {
                "id": "nursery",
                "name": "Nursery",
                "volume_gal": 225,
                "species": "Litopenaeus vannamei",
                "site": "NorCal Premier Prawn, Ukiah CA",
            },
            {
                "id": "growout-1",
                "name": "Grow-out 1",
                "volume_gal": None,
                "species": "Litopenaeus vannamei",
                "site": "NorCal Premier Prawn, Ukiah CA",
            },
        ]
        node_urls = {}
        for tank in tanks:
            app = NodeApp(_node_config(tank, self.root / tank["id"]))
            http = serve(app, "127.0.0.1", 0)
            self.nodes.append((app, http))
            node_urls[tank["id"]] = f"http://127.0.0.1:{http.port}"
        hub_tanks = []
        for tank in tanks:
            hub_tanks.append({**tank, "node_url": node_urls[tank["id"]], "mm_per_pixel": 0.4})
        config = {
            "host": "127.0.0.1",
            "port": 0,
            "data_dir": str(self.root / "hub"),
            "site": "NorCal Premier Prawn",
            "place": "Ukiah, CA",
            "timezone": "America/Los_Angeles",
            "poll_interval_s": 2,
            "timeout_s": 10,
            "start_poller": False,
            "tanks": hub_tanks,
            "notify": {"sms": {"mode": "off"}, "smtp": {"host": "", "to": []}},
        }

        def collect(alert, notify_cfg, hub_url=""):
            self.sink.append({"alert": alert, "hub_url": hub_url})
            return {"email": [{"skipped": True}], "sms": [{"skipped": True}], "logged": True}

        self.hub = HubApp(config, deliver=collect)
        self.hub_http = serve(self.hub, "127.0.0.1", 0)
        self.urls["hub"] = f"http://127.0.0.1:{self.hub_http.port}"
        self.hub.public_url = self.urls["hub"]
        self.urls.update(node_urls)

    def stop(self) -> None:
        if self.hub_http:
            self.hub_http.close()
        if self.hub:
            self.hub.close()
        for app, http in self.nodes:
            http.close()
            app.close()

    def scenario(self) -> dict[str, Any]:
        assert self.hub is not None
        hub = self.urls["hub"]
        nursery = self._node("nursery")
        post_json(
            nursery + "/api/simulate",
            {"dissolved_oxygen": 6.5, "temperature_f": 83.0, "ph": 7.9, "level": "ok", "level_cm": 40},
            timeout=5,
        )
        self.hub.poll_once()
        quiet = get_json(hub + "/api/alerts", timeout=5)
        post_json(
            nursery + "/api/simulate",
            {"dissolved_oxygen": 3.6, "temperature_f": 85.5, "ph": 7.9, "level": "ok", "level_cm": 40},
            timeout=5,
        )
        self.hub.poll_once()
        post_json(
            hub + "/api/tanks/nursery/manual",
            {
                "tan_mg_l": 1.2,
                "nitrite_mg_l": 0.7,
                "nitrate_mg_l": 40,
                "alkalinity_mg_l": 90,
                "salinity_ppt": 20,
                "notes": "Demo test kit",
            },
            timeout=5,
        )
        inbox = get_json(hub + "/api/alerts", timeout=5)["alerts"]
        kinds = {(row["tank_id"], row["kind"], row["severity"]) for row in inbox}
        do_alerts = [row for row in inbox if row["kind"] == "dissolved_oxygen"]
        if not do_alerts:
            raise RuntimeError(f"dissolved oxygen alert was not opened: {inbox}")
        do_alert = do_alerts[0]
        post_json(hub + f"/api/alerts/{do_alert['id']}/ack", {"by": "demo"}, timeout=5)
        after_ack = get_json(hub + "/api/alerts?tank=nursery", timeout=5)["alerts"]
        history = get_json(hub + "/api/tanks/nursery/history?hours=168", timeout=5)
        overview = get_json(hub + "/api/overview", timeout=5)
        with urllib.request.urlopen(hub + "/api/tanks/nursery/camera/snapshot.jpg", timeout=20) as response:
            jpeg = response.read()
        clip = post_json(hub + "/api/tanks/nursery/clips", {"duration_s": 0.4}, timeout=30)
        with urllib.request.urlopen(hub + "/api/tanks/nursery/clips/" + clip["id"] + "/file", timeout=20) as response:
            mp4 = response.read(16)
        csv_text = SAMPLE_CSV.read_text(encoding="utf-8")
        imported = post_json(
            hub + "/api/tanks/nursery/tracking",
            {"csv": csv_text, "filename": "amadeus_sample.csv", "fps": 10, "mm_per_pixel": 1, "crowding_mm": 40},
            timeout=10,
        )
        page = urllib.request.urlopen(hub + "/", timeout=5).read().decode("utf-8")
        growout_alerts = [row for row in inbox if row["tank_id"] == "growout-1"]
        return {
            "hub": hub,
            "quiet_before_fault": quiet["alerts"],
            "kinds": sorted(kinds),
            "acked_dissolved_oxygen_still_open": any(
                row["id"] == do_alert["id"] for row in after_ack
            ),
            "history_points": len(history["points"]),
            "tanks": [tank["id"] for tank in overview["tanks"]],
            "nursery_volume": next(tank["volume_gal"] for tank in overview["tanks"] if tank["id"] == "nursery"),
            "jpeg": jpeg[:3],
            "clip": {key: clip.get(key) for key in ("id", "exposure", "auto_exposure", "container", "mm_per_pixel")},
            "mp4": mp4,
            "tracking": imported["summary"],
            "growout_alerts": growout_alerts,
            "notifications": len(self.sink),
            "dashboard_ok": "Prawn Monitor" in page and "Test kit" in page,
            "do_status": next(item["status"]["dissolved_oxygen"] for item in overview["tanks"] if item["id"] == "nursery"),
            "temp_status": next(item["status"]["temperature"] for item in overview["tanks"] if item["id"] == "nursery"),
        }

    def _node(self, tank_id: str) -> str:
        return self.urls[tank_id]


def assess(report: dict[str, Any]) -> dict[str, Any]:
    problems = []
    if report["quiet_before_fault"]:
        problems.append("healthy water raised an alert")
    expected = {
        ("nursery", "dissolved_oxygen", "critical"),
        ("nursery", "temperature_swing", "warning"),
        ("nursery", "tan", "critical"),
        ("nursery", "nitrite", "warning"),
        ("nursery", "alkalinity", "warning"),
    }
    if set(map(tuple, report["kinds"])) != expected:
        problems.append(f"alert set was {report['kinds']}")
    if report["acked_dissolved_oxygen_still_open"]:
        problems.append("acknowledged alert stayed in the inbox")
    if report["history_points"] < 2:
        problems.append("history did not keep both polls")
    if report["tanks"] != ["growout-1", "nursery"] and set(report["tanks"]) != {"growout-1", "nursery"}:
        problems.append("both tanks were not listed")
    if report["nursery_volume"] != 225:
        problems.append("nursery volume was not 225 gallons")
    if report["jpeg"] != b"\xff\xd8\xff":
        problems.append("snapshot was not a JPEG")
    if report["clip"]["exposure"] != "fixed" or report["clip"]["auto_exposure"] is not False:
        problems.append("clip exposure was not locked")
    if b"ftyp" not in report["mp4"]:
        problems.append("clip was not an MP4")
    summary = report["tracking"]
    if summary["animals"] != 2 or abs(summary["total_distance_mm"] - 90) > 0.01:
        problems.append(f"tracking summary was {summary}")
    if report["growout_alerts"]:
        problems.append("grow-out received the nursery alert")
    if not report["dashboard_ok"]:
        problems.append("dashboard HTML was incomplete")
    if report["notifications"] < 1:
        problems.append("alerter was not called")
    if report["do_status"] != "critical":
        problems.append(f"dissolved oxygen tile was {report['do_status']} after the drop")
    if report["temp_status"] != "warning":
        problems.append(f"temperature tile was {report['temp_status']} after the swing")
    report["problems"] = problems
    report["ok"] = not problems
    return report


def run_check(root: Path) -> dict[str, Any]:
    stack = DemoStack(root)
    stack.start()
    try:
        report = stack.scenario()
    finally:
        stack.stop()
    return assess(report)


def format_report(report: dict[str, Any]) -> str:
    lines = [
        f"Hub {report['hub']}",
        f"Tanks: {', '.join(report['tanks'])}",
        f"Alerts: {', '.join(':'.join(item) for item in report['kinds'])}",
        f"History points: {report['history_points']}",
        f"Clip: {report['clip']['id']} exposure={report['clip']['exposure']}",
        (
            "Activity: "
            f"{report['tracking']['animals']} animals, "
            f"distance {report['tracking']['total_distance_mm']:.1f} mm, "
            f"mean speed {report['tracking']['mean_speed_mm_s']:.1f} mm/s, "
            f"median length {report['tracking']['median_length_mm']:.1f} mm"
        ),
        "Result: " + ("ok" if report["ok"] else "failed"),
    ]
    for problem in report["problems"]:
        lines.append(f"- {problem}")
    return "\n".join(lines)


def serve_until_interrupt(root: Path) -> None:
    stack = DemoStack(root)
    stack.start()
    try:
        report = assess(stack.scenario())
        print(format_report(report))
        print(f"Dashboard {stack.urls['hub']}")
        print("Leave this running and open the dashboard. Ctrl-C stops it.")
        stop = threading.Event()
        while not stop.wait(3600):
            pass
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        stack.stop()
