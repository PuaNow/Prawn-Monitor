"""Tank node: sensors and an overhead camera on one Raspberry Pi.

The hub polls this process over the LAN. The node keeps its own clip files and
a machine-readable inventory so another tank can be added without changing the
hub's code, the same shape Sync Tank uses for tank ownership.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from prawn_monitor import __version__
from prawn_monitor.camera import build_camera, ffmpeg_bin, jpeg_part, rgb_to_jpeg, stream_mjpeg, write_mp4
from prawn_monitor.httputil import HttpError, Response, Router, json_response, parse_json_body, serve
from prawn_monitor.schedule import slot_key, slots_due
from prawn_monitor.sensors import build_sensors
from prawn_monitor.timeutil import to_iso, utc_now

log = logging.getLogger("prawn.node")

_CLIP_ID = __import__("re").compile(r"^[A-Za-z0-9._-]+$")


class NodeApp:
    max_body = 1024 * 1024

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.tank = dict(config["tank"])
        self.tank_id = str(self.tank["id"])
        self.data_dir = Path(config.get("data_dir") or "var/node")
        self.clip_dir = self.data_dir / "clips"
        self.clip_dir.mkdir(parents=True, exist_ok=True)
        self.sensors = build_sensors(config, self.data_dir)
        label = str(self.tank.get("name") or self.tank_id)
        self.camera = build_camera(config, label=label)
        self.camera_id = str((config.get("camera") or {}).get("id") or "overhead")
        self.record_lock = threading.Lock()
        self._stop = threading.Event()
        self._scheduler: threading.Thread | None = None
        self.fired = self._load_fired()
        self.router = Router()
        self._mount()
        if (self.config.get("recording") or {}).get("enabled"):
            self._scheduler = threading.Thread(target=self._schedule_loop, name="recorder", daemon=True)
            self._scheduler.start()

    def log_exception(self, exc: BaseException) -> None:
        log.exception("node request failed: %s", exc)

    def close(self) -> None:
        self._stop.set()
        if self._scheduler:
            self._scheduler.join(timeout=2)
        for obj in (self.camera, self.sensors):
            close = getattr(obj, "close", None)
            if close:
                close()

    def handle(self, method: str, path: str, query: dict, body: bytes, headers: Any) -> Response:
        found = self.router.lookup(method, path)
        if not found:
            return json_response({"error": "not found", "path": path}, 404)
        fn, params = found
        return fn(params, query, body, headers)

    def _mount(self) -> None:
        self.router.route("GET", "/health")(self._health)
        self.router.route("GET", "/api/inventory")(self._inventory)
        self.router.route("GET", "/api/readings")(self._readings)
        self.router.route("POST", "/api/simulate")(self._simulate)
        self.router.route("POST", "/api/sensors/{kind}/command")(self._command)
        self.router.route("GET", "/camera/{camera_id}/snapshot.jpg")(self._snapshot)
        self.router.route("GET", "/camera/{camera_id}/stream.mjpg")(self._stream)
        self.router.route("POST", "/camera/{camera_id}/clips")(self._record)
        self.router.route("GET", "/camera/{camera_id}/clips")(self._clip_list)
        self.router.route("GET", "/camera/{camera_id}/clips/{clip_id}")(self._clip_meta)
        self.router.route("GET", "/camera/{camera_id}/clips/{clip_id}/file")(self._clip_file)

    def _health(self, _params, _query, _body, _headers) -> Response:
        return json_response({"ok": True, "service": "prawn-node", "version": __version__, "tank_id": self.tank_id})

    def _inventory(self, _params, _query, _body, _headers) -> Response:
        described = {}
        try:
            described = self.camera.describe()
        except Exception as exc:  # a camera fault should not hide the probes
            described = {"driver": "unavailable", "error": str(exc)}
        return json_response(
            {
                "node_id": self.tank_id,
                "tank": self.tank,
                "sensors": self.sensors.inventory(),
                "cameras": [
                    {
                        "id": self.camera_id,
                        "snapshot": f"/camera/{self.camera_id}/snapshot.jpg",
                        "stream": f"/camera/{self.camera_id}/stream.mjpg",
                        **described,
                    }
                ],
                "mode": (self.config.get("sensors") or {}).get("mode", "simulated"),
                "time": to_iso(utc_now()),
            }
        )

    def _readings(self, _params, _query, _body, _headers) -> Response:
        return json_response(
            {
                "node_id": self.tank_id,
                "tank_id": self.tank_id,
                "ts": to_iso(utc_now()),
                "sensors": self.sensors.readings(),
            }
        )

    def _simulate(self, _params, _query, body, _headers) -> Response:
        bank = self.sensors.simulated
        if bank is None:
            raise HttpError(403, "simulate is only available when sensors.mode is simulated")
        bank.update(parse_json_body(body))
        return self._readings({}, {}, b"", {})

    def _command(self, params, _query, body, _headers) -> Response:
        payload = parse_json_body(body)
        op = payload.get("op")
        if not op:
            raise HttpError(400, "op is required")
        result = self.sensors.command(params["kind"], str(op), payload)
        status = 200 if result.get("ok") else 400
        return json_response(result, status)

    def _require_camera(self, camera_id: str) -> None:
        if camera_id != self.camera_id:
            raise HttpError(404, f"unknown camera {camera_id}")

    def _snapshot(self, params, _query, _body, _headers) -> Response:
        self._require_camera(params["camera_id"])
        try:
            frame = self.camera.frame_rgb()
            jpeg = rgb_to_jpeg(frame, self.camera.width, self.camera.height)
        except Exception as exc:
            raise HttpError(503, str(exc)) from exc
        return Response(body=jpeg, content_type="image/jpeg")

    def _stream(self, params, _query, _body, _headers) -> Response:
        self._require_camera(params["camera_id"])
        if not ffmpeg_bin():
            raise HttpError(503, "ffmpeg is required for the MJPEG stream")

        def stream(wfile: Any) -> None:
            stream_mjpeg(
                wfile,
                self.camera.frame_rgb,
                self.camera.width,
                self.camera.height,
                float(self.camera.fps),
            )

        return Response(
            content_type="multipart/x-mixed-replace; boundary=prawnframe",
            stream=stream,
            headers={"Connection": "close"},
        )

    def _record(self, params, _query, body, _headers) -> Response:
        self._require_camera(params["camera_id"])
        payload = parse_json_body(body)
        recording = dict(self.config.get("recording") or {})
        duration = float(payload.get("duration_s") or recording.get("duration_s") or 10)
        max_duration = float(recording.get("max_duration_s") or 600)
        if duration <= 0 or duration > max_duration:
            raise HttpError(400, f"duration_s must be between 0 and {max_duration:g}")
        try:
            metadata = self.record_clip(duration)
        except Exception as exc:
            raise HttpError(500, str(exc)) from exc
        return json_response(metadata, 201)

    def record_clip(self, duration_s: float) -> dict[str, Any]:
        with self.record_lock:
            started = utc_now()
            fps = float(self.camera.fps)
            count = max(1, int(round(duration_s * fps)))
            frames = [self.camera.frame_rgb() for _ in range(count)]
            clip_id = (
                f"{self.tank_id}-{self.camera_id}-"
                f"{started.strftime('%Y%m%dT%H%M%S')}{started.microsecond:06d}Z"
            )
            if not _CLIP_ID.match(clip_id):
                raise RuntimeError("generated clip id was rejected")
            path = self.clip_dir / f"{clip_id}.mp4"
            write_mp4(path, frames, self.camera.width, self.camera.height, fps)
            described = self.camera.describe()
            metadata = {
                "id": clip_id,
                "tank_id": self.tank_id,
                "camera_id": self.camera_id,
                "started_at": to_iso(started),
                "duration_s": count / fps,
                "frames": count,
                "file": f"/camera/{self.camera_id}/clips/{clip_id}/file",
                "codec": "libx264",
                "container": "mp4",
                "notes": "Fixed overhead camera with exposure locked, for AMADEUS tracking.",
                "software": f"prawn-monitor {__version__}",
                **described,
            }
            (self.clip_dir / f"{clip_id}.json").write_text(
                json.dumps(metadata, indent=2),
                encoding="utf-8",
            )
            return metadata

    def _clip_list(self, params, _query, _body, _headers) -> Response:
        self._require_camera(params["camera_id"])
        clips = []
        for path in sorted(self.clip_dir.glob("*.json"), reverse=True):
            clips.append(json.loads(path.read_text(encoding="utf-8")))
        return json_response({"clips": clips})

    def _clip_record(self, clip_id: str) -> dict[str, Any]:
        if not _CLIP_ID.match(clip_id):
            raise HttpError(400, "invalid clip id")
        path = self.clip_dir / f"{clip_id}.json"
        if not path.is_file():
            raise HttpError(404, "clip not found")
        return json.loads(path.read_text(encoding="utf-8"))

    def _clip_meta(self, params, _query, _body, _headers) -> Response:
        self._require_camera(params["camera_id"])
        return json_response(self._clip_record(params["clip_id"]))

    def _clip_file(self, params, _query, _body, _headers) -> Response:
        self._require_camera(params["camera_id"])
        meta = self._clip_record(params["clip_id"])
        path = self.clip_dir / f"{meta['id']}.mp4"
        if not path.is_file():
            raise HttpError(404, "clip file missing")
        return Response(
            body=path.read_bytes(),
            content_type="video/mp4",
            headers={"Content-Disposition": f'inline; filename="{path.name}"'},
        )

    def _load_fired(self) -> set[str]:
        path = self.data_dir / "schedule-state.json"
        if not path.exists():
            return set()
        try:
            return set(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            return set()

    def _save_fired(self) -> None:
        path = self.data_dir / "schedule-state.json"
        # Keep the set from growing without bound across years of daily slots.
        recent = sorted(self.fired)[-400:]
        path.write_text(json.dumps(recent), encoding="utf-8")
        self.fired = set(recent)

    def _schedule_loop(self) -> None:
        while not self._stop.wait(15):
            try:
                self._schedule_once(datetime.now(self._timezone()))
            except Exception:
                log.exception("scheduled recording failed")

    def _timezone(self) -> ZoneInfo:
        name = (self.config.get("recording") or {}).get("timezone") or "America/Los_Angeles"
        return ZoneInfo(name)

    def _schedule_once(self, now_local: datetime) -> None:
        recording = dict(self.config.get("recording") or {})
        due = slots_due(now_local, list(recording.get("times") or []), self.fired)
        for hhmm in due:
            self.fired.add(slot_key(now_local, hhmm))
            self._save_fired()
            self.record_clip(float(recording.get("duration_s") or 30))


def jpeg_boundary_part(jpeg: bytes) -> bytes:
    return jpeg_part(jpeg)
