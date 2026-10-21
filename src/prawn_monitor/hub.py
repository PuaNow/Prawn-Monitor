"""Local hub. Polls tank nodes, stores SQLite history, and serves the dashboard.

One process can own any number of tanks. Each tank keeps the node URL and the
name it was given; a missing node does not drop the others.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable

from prawn_monitor import __version__
from prawn_monitor.alerts import PriorAlert, evaluate_conditions, plan_alert_actions
from prawn_monitor.db import Database
from prawn_monitor.harvest import (
    BATCH_ID,
    SIGNATURE,
    SIGNER,
    activity_from_imports,
    anchor_source,
    build_record,
    canonical_dumps,
    clean_notes,
    clean_pounds,
    fee_payer,
    fetch_transaction,
    local_window,
    memo_for,
    normalize_cluster,
    pin_canonical_json,
    public_harvest,
    reject_secrets,
    resolve_endpoint,
    sha256_text,
    transaction_slot,
    verify_transaction,
)
from prawn_monitor.history import bucket_readings
from prawn_monitor.httputil import HttpError, Response, Router, json_response, parse_json_body, query_value, serve
from prawn_monitor.notify import deliver_alert
from prawn_monitor.qrcode import qr_svg
from prawn_monitor.shrimpcoin import describe as describe_shrimpcoin
from prawn_monitor.thresholds import effective_thresholds
from prawn_monitor.timeutil import parse_iso, to_iso, utc_now
from prawn_monitor.tracking import analyze_tracks, parse_tracking_csv
from prawn_monitor.verify_page import render_verify_page

log = logging.getLogger("prawn.hub")

_DASHBOARD = Path(__file__).with_name("dashboard.html")
_STATIC = Path(__file__).with_name("static")


def get_json(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, payload: dict[str, Any], timeout: float) -> Any:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def sensors_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "dissolved_oxygen": {"ok": bool(row["do_ok"]), "value": row["do_mg_l"], "unit": "mg/L"},
        "temperature": {"ok": bool(row["temp_ok"]), "value": row["temp_f"], "unit": "F"},
        "ph": {"ok": bool(row["ph_ok"]), "value": row["ph"], "unit": "pH"},
        "water_level": {
            "ok": bool(row["level_ok"]),
            "value": row["level_state"],
            "unit": "state",
            "extra": {"level_cm": row["level_cm"]},
        },
    }


class HubApp:
    max_body = 8 * 1024 * 1024

    def __init__(
        self,
        config: dict[str, Any],
        db: Database | None = None,
        deliver: Callable | None = None,
        solana_fetch: Callable[[str, str], dict[str, Any] | None] | None = None,
        ipfs_transport: Callable[[str, bytes, dict[str, str]], tuple[int, bytes]] | None = None,
    ):
        self.config = config
        self.tanks = [dict(tank) for tank in config.get("tanks") or []]
        self.by_id = {tank["id"]: tank for tank in self.tanks}
        self.data_dir = Path(config.get("data_dir") or "var/hub")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(self.data_dir / "prawn.sqlite")
        self.deliver = deliver or deliver_alert
        self.timeout_s = float(config.get("timeout_s") or 10)
        self.poll_interval_s = float(config.get("poll_interval_s") or 15)
        self.site = config.get("site") or "NorCal Premier Prawn"
        self.place = config.get("place") or "Ukiah, CA"
        self.timezone = config.get("timezone") or "America/Los_Angeles"
        self.public_url = config.get("public_url") or ""
        self.solana = dict(config.get("solana") or {})
        self.ipfs = dict(config.get("ipfs") or {})
        self.solana_fetch = solana_fetch
        self.ipfs_transport = ipfs_transport
        self.cameras: dict[str, str] = {}
        self._stop = threading.Event()
        self._poller: threading.Thread | None = None
        self.router = Router()
        self._mount()
        self._seed()
        if config.get("start_poller", True):
            self._poller = threading.Thread(target=self._poll_loop, name="poller", daemon=True)
            self._poller.start()

    def log_exception(self, exc: BaseException) -> None:
        log.exception("hub request failed: %s", exc)

    def close(self) -> None:
        self._stop.set()
        if self._poller:
            self._poller.join(timeout=2)
        self.db.close()

    def _seed(self) -> None:
        now = to_iso(utc_now())
        for tank in self.tanks:
            self.db.upsert_tank({**tank, "site": self.site, "updated_at": now})
        if self.config.get("thresholds") and not self.db.thresholds_for("global"):
            self.db.save_thresholds("global", self.config["thresholds"])

    def handle(self, method: str, path: str, query: dict, body: bytes, headers: Any) -> Response:
        found = self.router.lookup(method, path)
        if not found:
            return json_response({"error": "not found", "path": path}, 404)
        fn, params = found
        return fn(params, query, body, headers)

    def _mount(self) -> None:
        self.router.route("GET", "/")(self._dashboard)
        self.router.route("GET", "/health")(self._health)
        self.router.route("GET", "/favicon.ico")(self._favicon)
        self.router.route("GET", "/api/overview")(self._overview)
        self.router.route("POST", "/api/poll")(self._poll)
        self.router.route("GET", "/api/tanks/{tank_id}/history")(self._history)
        self.router.route("GET", "/api/tanks/{tank_id}/manual")(self._manual_list)
        self.router.route("POST", "/api/tanks/{tank_id}/manual")(self._manual_post)
        self.router.route("GET", "/api/alerts")(self._alerts)
        self.router.route("POST", "/api/alerts/{alert_id}/ack")(self._ack)
        self.router.route("GET", "/api/thresholds")(self._thresholds_get)
        self.router.route("PUT", "/api/thresholds")(self._thresholds_put)
        self.router.route("GET", "/api/tanks/{tank_id}/clips")(self._clips)
        self.router.route("POST", "/api/tanks/{tank_id}/clips")(self._record)
        self.router.route("GET", "/api/tanks/{tank_id}/camera/snapshot.jpg")(self._snapshot)
        self.router.route("GET", "/api/tanks/{tank_id}/camera/stream.mjpg")(self._camera_stream)
        self.router.route("GET", "/api/tanks/{tank_id}/clips/{clip_id}/file")(self._clip_file)
        self.router.route("POST", "/api/tanks/{tank_id}/tracking")(self._tracking_post)
        self.router.route("GET", "/api/tanks/{tank_id}/tracking")(self._tracking_list)
        self.router.route("GET", "/api/tracking/{import_id}")(self._tracking_get)
        self.router.route("GET", "/api/shrimpcoin")(self._shrimpcoin)
        self.router.route("GET", "/api/harvests")(self._harvest_list)
        self.router.route("POST", "/api/harvests")(self._harvest_create)
        self.router.route("GET", "/api/harvests/{batch_id}")(self._harvest_get)
        self.router.route("POST", "/api/harvests/{batch_id}/signature")(self._harvest_signature)
        self.router.route("GET", "/api/harvests/{batch_id}/verification")(self._harvest_verification)
        self.router.route("GET", "/verify/{batch_id}/qr.svg")(self._harvest_qr)
        self.router.route("GET", "/verify/{batch_id}")(self._harvest_page)
        self.router.route("GET", "/static/{name}")(self._static_file)

    def _dashboard(self, _params, _query, _body, _headers) -> Response:
        if not _DASHBOARD.is_file():
            raise HttpError(500, "dashboard.html is missing")
        return Response(
            body=_DASHBOARD.read_text(encoding="utf-8").encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )

    def _favicon(self, _params, _query, _body, _headers) -> Response:
        return Response(status=204, body=b"", content_type="image/x-icon")

    def _health(self, _params, _query, _body, _headers) -> Response:
        return json_response({"ok": True, "service": "prawn-hub", "version": __version__, "tanks": len(self.tanks)})

    def _tank(self, tank_id: str) -> dict[str, Any]:
        tank = self.by_id.get(tank_id)
        if tank is None:
            raise HttpError(404, f"unknown tank {tank_id}")
        return tank

    def _overview(self, _params, _query, _body, _headers) -> Response:
        alerts = self.db.list_alerts(inbox_only=True, limit=200)
        tanks = []
        for tank in self.tanks:
            status = self.db.node_status(tank["id"]) or {}
            latest = self.db.latest_reading(tank["id"])
            manual = {
                key: {"value": value, "ts": ts}
                for key, (value, ts) in self.db.latest_manual_values(tank["id"]).items()
            }
            online = bool(status.get("online"))
            tanks.append(
                {
                    "id": tank["id"],
                    "name": tank.get("name") or tank["id"],
                    "volume_gal": tank.get("volume_gal"),
                    "species": tank.get("species"),
                    "mm_per_pixel": tank.get("mm_per_pixel"),
                    "online": online,
                    "last_ok_at": status.get("last_ok_at"),
                    "last_error": status.get("last_error"),
                    "reading": _public_reading(latest),
                    "manual": manual,
                    "status": self._tile_status(tank, latest, online),
                }
            )
        return json_response(
            {
                "site": self.site,
                "place": self.place,
                "timezone": self.timezone,
                "time": to_iso(utc_now()),
                "tanks": tanks,
                "alerts": alerts,
            }
        )

    def _poll(self, _params, _query, _body, _headers) -> Response:
        results = self.poll_once()
        return json_response({"ok": True, "results": results})

    def _history(self, params, query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        now = utc_now()
        start_text = query_value(query, "from")
        end_text = query_value(query, "to")
        if start_text and end_text:
            start = parse_iso(start_text)
            end = parse_iso(end_text)
        else:
            hours = float(query_value(query, "hours", "168") or 168)
            end = now
            start = now - timedelta(hours=hours)
        rows = self.db.readings_between(tank["id"], to_iso(start), to_iso(end))
        return json_response(
            {
                "tank_id": tank["id"],
                "from": to_iso(start),
                "to": to_iso(end),
                "points": bucket_readings(rows),
            }
        )

    def _manual_list(self, params, _query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        return json_response({"logs": self.db.manual_logs(tank["id"])})

    def _manual_post(self, params, _query, body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        payload = parse_json_body(body)
        fields = _manual_fields(payload)
        if not any(fields.get(key) is not None for key in fields if key != "notes") and not fields.get("notes"):
            raise HttpError(400, "enter a test result or a note")
        ts = to_iso(utc_now())
        log_id = self.db.insert_manual(tank["id"], ts, fields)
        self.evaluate_tank(tank["id"])
        return json_response({"id": log_id, "ts": ts, **fields}, 201)

    def _alerts(self, _params, query, _body, _headers) -> Response:
        tank_id = query_value(query, "tank")
        inbox = query_value(query, "status", "inbox") != "all"
        return json_response({"alerts": self.db.list_alerts(tank_id=tank_id, inbox_only=inbox, limit=200)})

    def _ack(self, params, _query, body, _headers) -> Response:
        try:
            alert_id = int(params["alert_id"])
        except ValueError as exc:
            raise HttpError(400, "alert id must be an integer") from exc
        payload = parse_json_body(body)
        who = str(payload.get("by") or "operator")[:80]
        if not self.db.ack_alert(alert_id, to_iso(utc_now()), who):
            existing = self.db.get_alert(alert_id)
            if existing is None:
                raise HttpError(404, "alert not found")
            raise HttpError(409, "alert is already acknowledged")
        return json_response({"ok": True, "id": alert_id, "acked_by": who})

    def _thresholds_get(self, _params, query, _body, _headers) -> Response:
        tank_id = query_value(query, "tank")
        global_layer = self.db.thresholds_for("global")
        tank_layer = self.db.thresholds_for(tank_id) if tank_id else {}
        return json_response(
            {
                "tank": tank_id,
                "global": global_layer,
                "override": tank_layer,
                "effective": effective_thresholds(global_layer, tank_layer),
            }
        )

    def _thresholds_put(self, _params, query, body, _headers) -> Response:
        payload = parse_json_body(body)
        tank_id = query_value(query, "tank")
        scope = tank_id or "global"
        if tank_id:
            self._tank(tank_id)
        self.db.save_thresholds(scope, payload)
        for tank in self.tanks:
            self.evaluate_tank(tank["id"])
        return self._thresholds_get({}, query, b"", {})

    def _clips(self, params, _query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        return json_response({"clips": self.db.list_clips(tank["id"])})

    def _record(self, params, _query, body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        payload = parse_json_body(body)
        camera_id = self.cameras.get(tank["id"], "overhead")
        url = tank["node_url"].rstrip("/") + f"/camera/{camera_id}/clips"
        duration = float(payload.get("duration_s") or 10)
        try:
            meta = post_json(url, {"duration_s": duration}, timeout=duration + 30)
        except Exception as exc:
            raise HttpError(502, f"node recording failed: {exc}") from exc
        meta["tank_id"] = tank["id"]
        self.db.upsert_clip(meta)
        return json_response(meta, 201)

    def _snapshot(self, params, _query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        camera_id = self.cameras.get(tank["id"], "overhead")
        url = tank["node_url"].rstrip("/") + f"/camera/{camera_id}/snapshot.jpg"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout_s + 10) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "image/jpeg")
        except Exception as exc:
            raise HttpError(502, f"snapshot failed: {exc}") from exc
        return Response(body=body, content_type=content_type.split(";")[0])

    def _camera_stream(self, params, _query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        camera_id = self.cameras.get(tank["id"], "overhead")
        url = tank["node_url"].rstrip("/") + f"/camera/{camera_id}/stream.mjpg"
        try:
            upstream = urllib.request.urlopen(url, timeout=self.timeout_s)
        except Exception as exc:
            raise HttpError(502, f"stream failed: {exc}") from exc
        content_type = upstream.headers.get("Content-Type", "multipart/x-mixed-replace; boundary=prawnframe")

        def stream(wfile: Any) -> None:
            try:
                while True:
                    chunk = upstream.read(8192)
                    if not chunk:
                        break
                    wfile.write(chunk)
                    wfile.flush()
            finally:
                upstream.close()

        return Response(content_type=content_type, stream=stream, headers={"Connection": "close"})

    def _clip_file(self, params, _query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        camera_id = self.cameras.get(tank["id"], "overhead")
        clip_id = params["clip_id"]
        url = tank["node_url"].rstrip("/") + f"/camera/{camera_id}/clips/{clip_id}/file"
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                body = response.read()
        except Exception as exc:
            raise HttpError(502, f"clip download failed: {exc}") from exc
        return Response(body=body, content_type="video/mp4")

    def _tracking_post(self, params, _query, body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        payload = parse_json_body(body)
        csv_text = payload.get("csv")
        if not csv_text or not isinstance(csv_text, str):
            raise HttpError(400, "csv text is required")
        try:
            fps = float(payload.get("fps") or 30)
            mm = float(payload.get("mm_per_pixel") or tank.get("mm_per_pixel") or 0)
            crowding = float(payload.get("crowding_mm") or 40)
            frames = parse_tracking_csv(csv_text)
            analyzed = analyze_tracks(frames, fps=fps, mm_per_pixel=mm, crowding_mm=crowding)
        except (TypeError, ValueError) as exc:
            raise HttpError(400, str(exc)) from exc
        stamp = utc_now().strftime("%Y%m%dT%H%M%SZ")
        folder = self.data_dir / "tracking"
        folder.mkdir(parents=True, exist_ok=True)
        filename = Path(str(payload.get("filename") or "amadeus.csv")).name
        csv_path = folder / f"{tank['id']}-{stamp}.csv"
        csv_path.write_text(csv_text, encoding="utf-8")
        import_id = self.db.add_tracking_import(
            tank_id=tank["id"],
            imported_at=to_iso(utc_now()),
            filename=filename,
            fps=fps,
            mm_per_pixel=mm,
            summary=analyzed["summary"],
            samples=analyzed["samples"],
            csv_path=str(csv_path),
        )
        return json_response({"id": import_id, "summary": analyzed["summary"]}, 201)

    def _tracking_list(self, params, _query, _body, _headers) -> Response:
        tank = self._tank(params["tank_id"])
        return json_response({"imports": self.db.list_imports(tank["id"])})

    def _tracking_get(self, params, _query, _body, _headers) -> Response:
        try:
            import_id = int(params["import_id"])
        except ValueError as exc:
            raise HttpError(400, "import id must be an integer") from exc
        row = self.db.tracking_import(import_id)
        if row is None:
            raise HttpError(404, "import not found")
        if len(row["samples"]) > 800:
            row["samples"] = bucket_series(row["samples"], 800)
        return json_response(row)

    def _shrimpcoin(self, _params, _query, _body, _headers) -> Response:
        return json_response(describe_shrimpcoin())

    def _static_file(self, params, _query, _body, _headers) -> Response:
        name = params["name"]
        if "/" in name or name.startswith("."):
            raise HttpError(404, "not found")
        file = _STATIC / name
        if not file.is_file():
            raise HttpError(404, "not found")
        content_type = "text/javascript; charset=utf-8" if name.endswith(".js") else "application/octet-stream"
        return Response(body=file.read_bytes(), content_type=content_type)

    def _harvest_list(self, _params, query, _body, _headers) -> Response:
        tank_id = query_value(query, "tank")
        if tank_id:
            self._tank(tank_id)
        base = self._public_base(_headers)
        rows = [self._present_harvest(row, base) for row in self.db.list_harvests(tank_id)]
        return json_response({"harvests": rows})

    def _harvest_get(self, params, _query, _body, headers) -> Response:
        return json_response(self._present_harvest(self._harvest_row(params["batch_id"]), self._public_base(headers)))

    def _harvest_create(self, _params, _query, body, headers) -> Response:
        payload = parse_json_body(body)
        try:
            reject_secrets(payload)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        batch_id = str(payload.get("batch_id") or "").strip()
        if not BATCH_ID.fullmatch(batch_id):
            raise HttpError(400, "batch id must be letters, numbers, dot, underscore, or hyphen")
        tank = self._tank(str(payload.get("tank_id") or ""))
        try:
            stocking = str(payload.get("stocking_date") or "")
            harvest_date = str(payload.get("harvest_date") or "")
            period_start, period_end = local_window(stocking, harvest_date, self.timezone)
            pounds = clean_pounds(payload.get("pounds"))
            notes = clean_notes(payload.get("notes"))
            cluster, rpc_url = resolve_endpoint(self.solana, payload.get("cluster"))
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        include_activity = payload.get("include_activity", True)
        if not isinstance(include_activity, bool):
            raise HttpError(400, "include_activity must be true or false")
        readings = self.db.readings_between(tank["id"], period_start, period_end)
        manuals = self.db.manual_logs_between(tank["id"], period_start, period_end)
        alerts = self.db.count_alerts_between(tank["id"], period_start, period_end)
        activity = None
        if include_activity:
            imports = self.db.tracking_imports_between(tank["id"], period_start, period_end)
            activity = activity_from_imports(imports, self.db.tracking_samples)
        record = build_record(
            batch_id=batch_id,
            site=self.site,
            place=self.place,
            tank=tank,
            stocking_date=stocking,
            harvest_date=harvest_date,
            period_start=period_start,
            period_end=period_end,
            pounds=pounds,
            notes=notes,
            readings=readings,
            manual_logs=manuals,
            alert_count=alerts,
            limits=self.thresholds_for(tank["id"]),
            activity=activity,
        )
        canonical = canonical_dumps(record)
        digest = sha256_text(canonical)
        pinned = pin_canonical_json(canonical, self.ipfs, self.ipfs_transport)
        try:
            memo = memo_for(batch_id, digest, pinned.cid)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        row = {
            "batch_id": batch_id,
            "tank_id": tank["id"],
            "stocking_date": stocking,
            "harvest_date": harvest_date,
            "pounds": pounds,
            "notes": notes,
            "canonical_json": canonical,
            "sha256": digest,
            "ipfs_cid": pinned.cid,
            "ipfs_error": None if pinned.status != "failed" else pinned.detail[:300],
            "memo": memo,
            "cluster": cluster,
            "rpc_url": rpc_url,
            "created_at": to_iso(utc_now()),
        }
        try:
            self.db.insert_harvest(row)
        except sqlite3.IntegrityError as exc:
            raise HttpError(409, "batch id already exists") from exc
        stored = self.db.get_harvest(batch_id)
        body_out = self._present_harvest(stored, self._public_base(headers))
        body_out["ipfs"] = pinned.as_dict()
        return json_response(body_out, 201)

    def _harvest_signature(self, params, _query, body, headers) -> Response:
        payload = parse_json_body(body)
        try:
            reject_secrets(payload)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        row = self._harvest_row(params["batch_id"])
        signature = str(payload.get("signature") or "").strip()
        if not SIGNATURE.fullmatch(signature):
            raise HttpError(400, "signature must be the Solana transaction signature")
        if payload.get("cluster"):
            try:
                asked = normalize_cluster(str(payload.get("cluster")))
            except ValueError as exc:
                raise HttpError(400, str(exc)) from exc
            if asked != row["cluster"]:
                raise HttpError(400, "cluster does not match this batch")
        signer = str(payload.get("signer") or "").strip() or None
        if signer and not SIGNER.fullmatch(signer):
            raise HttpError(400, "signer must be a public address")
        if row.get("signature"):
            if row["signature"] != signature:
                raise HttpError(409, "this batch is already anchored with a different signature")
            return json_response(self._present_harvest(row, self._public_base(headers)))
        try:
            tx = self._load_transaction(row["rpc_url"], signature)
            checked = verify_transaction(row["memo"], tx)
        except Exception as exc:
            raise HttpError(502, f"could not read the Solana transaction: {exc}") from exc
        if not checked["ok"]:
            raise HttpError(400, checked["reason"])
        self.db.anchor_harvest(
            row["batch_id"],
            signature=signature,
            signer=signer or (fee_payer(tx) if tx else None),
            slot=transaction_slot(tx) if tx else None,
            anchored_at=to_iso(utc_now()),
            source=anchor_source(payload.get("source")),
        )
        stored = self.db.get_harvest(row["batch_id"])
        return json_response(self._present_harvest(stored, self._public_base(headers)))

    def _harvest_verification(self, params, _query, _body, _headers) -> Response:
        row = self._harvest_row(params["batch_id"])
        digest_ok = sha256_text(row["canonical_json"]) == row["sha256"]
        if not row.get("signature"):
            return json_response(
                {
                    "ok": False,
                    "anchored": False,
                    "sha256_matches_record": digest_ok,
                    "memo_matches": None,
                    "reason": "not anchored yet",
                    "signature": None,
                    "cluster": row["cluster"],
                    "solscan_url": None,
                }
            )
        try:
            tx = self._load_transaction(row["rpc_url"], row["signature"])
            checked = verify_transaction(row["memo"], tx)
        except Exception as exc:
            return json_response(
                {
                    "ok": False,
                    "anchored": True,
                    "sha256_matches_record": digest_ok,
                    "memo_matches": None,
                    "reason": f"could not read the Solana transaction: {exc}",
                    "signature": row["signature"],
                    "cluster": row["cluster"],
                    "solscan_url": public_harvest(row)["solscan_url"],
                }
            )
        ok = bool(checked["ok"] and digest_ok)
        reason = checked["reason"] if digest_ok else "stored hash does not match the canonical record"
        return json_response(
            {
                "ok": ok,
                "anchored": True,
                "sha256_matches_record": digest_ok,
                "memo_matches": checked["ok"],
                "reason": reason,
                "signature": row["signature"],
                "cluster": row["cluster"],
                "solscan_url": public_harvest(row)["solscan_url"],
                "memos": checked.get("memos") or [],
            }
        )

    def _harvest_page(self, params, _query, _body, headers) -> Response:
        row = self._harvest_row(params["batch_id"])
        base = self._public_base(headers)
        presented = self._present_harvest(row, base)
        page = render_verify_page(presented, presented["verify_url"])
        return Response(body=page.encode("utf-8"), content_type="text/html; charset=utf-8")

    def _harvest_qr(self, params, _query, _body, headers) -> Response:
        row = self._harvest_row(params["batch_id"])
        url = self._present_harvest(row, self._public_base(headers))["verify_url"]
        try:
            svg = qr_svg(url)
        except ValueError as exc:
            raise HttpError(400, str(exc)) from exc
        return Response(body=svg.encode("utf-8"), content_type="image/svg+xml")

    def _harvest_row(self, batch_id: str) -> dict[str, Any]:
        row = self.db.get_harvest(batch_id)
        if row is None:
            raise HttpError(404, "unknown harvest")
        return row

    def _present_harvest(self, row: dict[str, Any] | None, base: str) -> dict[str, Any]:
        if row is None:
            raise HttpError(404, "unknown harvest")
        body = public_harvest(row)
        body["verify_url"] = f"{base}/verify/{row['batch_id']}"
        body["cli_command"] = f"scripts/anchor_harvest.sh {row['batch_id']} --hub {base}"
        return body

    def _public_base(self, headers: Any) -> str:
        configured = str(self.public_url or "").rstrip("/")
        if configured:
            return configured
        host = ""
        if headers is not None:
            host = headers.get("Host") or ""
        return "http://" + (host or "127.0.0.1")

    def _load_transaction(self, rpc_url: str, signature: str) -> dict[str, Any] | None:
        if self.solana_fetch is not None:
            return self.solana_fetch(rpc_url, signature)
        return fetch_transaction(rpc_url, signature)

    def poll_once(self) -> list[dict[str, Any]]:
        results = []
        for tank in self.tanks:
            results.append(self._poll_tank(tank))
        return results

    def _poll_tank(self, tank: dict[str, Any]) -> dict[str, Any]:
        now = utc_now()
        base = tank["node_url"].rstrip("/")
        try:
            inventory = get_json(base + "/api/inventory", self.timeout_s)
            reading = get_json(base + "/api/readings", self.timeout_s)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
            self._mark_offline(tank, now, str(exc))
            return {"tank_id": tank["id"], "ok": False, "error": str(exc)}
        cameras = inventory.get("cameras") or []
        if cameras:
            self.cameras[tank["id"]] = cameras[0].get("id") or "overhead"
        remote_tank = inventory.get("tank") or {}
        self.db.upsert_tank(
            {
                "id": tank["id"],
                "name": tank.get("name") or remote_tank.get("name") or tank["id"],
                "volume_gal": tank.get("volume_gal", remote_tank.get("volume_gal")),
                "species": tank.get("species") or remote_tank.get("species"),
                "node_url": tank["node_url"],
                "mm_per_pixel": tank.get("mm_per_pixel"),
                "site": self.site,
                "updated_at": to_iso(now),
            }
        )
        ts = reading.get("ts") or to_iso(now)
        sensors = reading.get("sensors") or {}
        self.db.insert_reading(tank["id"], ts, sensors, reading)
        self.db.set_node_status(tank["id"], online=True, last_ok_at=ts, error=None)
        self._sync_clips(tank)
        self.evaluate_tank(tank["id"], sensors=sensors, node_online=True, now=parse_iso(ts))
        return {"tank_id": tank["id"], "ok": True, "ts": ts}

    def _sync_clips(self, tank: dict[str, Any]) -> None:
        camera_id = self.cameras.get(tank["id"], "overhead")
        url = tank["node_url"].rstrip("/") + f"/camera/{camera_id}/clips"
        try:
            payload = get_json(url, self.timeout_s)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            return
        for clip in payload.get("clips") or []:
            if clip.get("id"):
                clip["tank_id"] = tank["id"]
                self.db.upsert_clip(clip)

    def _mark_offline(self, tank: dict[str, Any], now, error: str) -> None:
        status = self.db.node_status(tank["id"]) or {}
        last_ok = status.get("last_ok_at")
        limits = self.thresholds_for(tank["id"])
        grace = float(limits["offline"]["node_s"])
        aged = True
        if last_ok:
            aged = (now - parse_iso(last_ok)).total_seconds() >= grace
        self.db.set_node_status(tank["id"], online=not aged, last_ok_at=None, error=error)
        if aged:
            self.evaluate_tank(tank["id"], sensors=None, node_online=False, now=now)

    def _tile_status(self, tank: dict[str, Any], latest: dict[str, Any] | None, online: bool) -> dict[str, str]:
        """Current band for each probe. Acknowledgement does not clear this."""
        tiles = ("dissolved_oxygen", "temperature", "ph", "water_level")
        if not online or latest is None:
            return {kind: "offline" for kind in tiles}
        moment = parse_iso(latest["ts"])
        limits = self.thresholds_for(tank["id"])
        window_start = to_iso(moment - timedelta(hours=float(limits["temp_f"]["swing_hours"])))
        conditions = evaluate_conditions(
            tank_name=str(tank.get("name") or tank["id"]),
            now=moment,
            node_online=True,
            sensors=sensors_from_row(latest),
            temp_history=self.db.temp_history(tank["id"], window_start),
            manual=self.db.latest_manual_values(tank["id"]),
            thresholds=limits,
        )
        rank = {"ok": 0, "warning": 1, "critical": 2}
        status = {kind: "ok" for kind in tiles}
        for condition in conditions:
            kind = "temperature" if condition.kind == "temperature_swing" else condition.kind
            if kind.startswith("sensor_offline:"):
                kind = kind.split(":", 1)[1]
            if kind not in status:
                continue
            if rank[condition.severity] > rank[status[kind]]:
                status[kind] = condition.severity
        return status

    def thresholds_for(self, tank_id: str) -> dict[str, Any]:
        return effective_thresholds(self.db.thresholds_for("global"), self.db.thresholds_for(tank_id))

    def evaluate_tank(
        self,
        tank_id: str,
        *,
        sensors: dict[str, Any] | None = None,
        node_online: bool | None = None,
        now=None,
    ) -> None:
        tank = self._tank(tank_id)
        moment = now or utc_now()
        limits = self.thresholds_for(tank_id)
        if node_online is None:
            status = self.db.node_status(tank_id) or {}
            node_online = bool(status.get("online"))
        if sensors is None and node_online:
            latest = self.db.latest_reading(tank_id)
            sensors = sensors_from_row(latest) if latest else {}
        window_start = to_iso(moment - timedelta(hours=float(limits["temp_f"]["swing_hours"])))
        history = self.db.temp_history(tank_id, window_start)
        manual = self.db.latest_manual_values(tank_id)
        conditions = evaluate_conditions(
            tank_name=str(tank.get("name") or tank_id),
            now=moment,
            node_online=bool(node_online),
            sensors=sensors,
            temp_history=history,
            manual=manual,
            thresholds=limits,
        )
        rows = self.db.latest_alerts_by_kind(tank_id)
        priors = [_prior(row) for row in rows]
        ids = {row["kind"]: row["id"] for row in rows}
        actions = plan_alert_actions(conditions, priors, moment, limits["cooldown_s"])
        stamp = to_iso(moment)
        for action in actions:
            if action.op == "resolve":
                self.db.resolve_alert(ids[action.kind], stamp)
                continue
            payload = {
                "tank_id": tank_id,
                "tank_name": tank.get("name") or tank_id,
                "kind": action.kind,
                "severity": action.severity,
                "message": action.message,
                "value": action.value,
                "unit": action.unit,
            }
            try:
                delivery = self.deliver(payload, self.config.get("notify"), hub_url=self.public_url)
            except Exception as exc:
                delivery = {"error": str(exc)}
            encoded = json.dumps(delivery, sort_keys=True)
            if action.op == "open":
                self.db.open_alert(
                    tank_id=tank_id,
                    kind=action.kind,
                    severity=action.severity,
                    message=action.message,
                    value=action.value,
                    unit=action.unit,
                    opened_at=stamp,
                    delivery=encoded,
                )
            elif action.op == "escalate":
                self.db.escalate_alert(
                    ids[action.kind],
                    severity=action.severity,
                    message=action.message,
                    value=action.value,
                    unit=action.unit,
                    notified_at=stamp,
                    delivery=encoded,
                )

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.poll_once()
            except Exception:
                log.exception("poll loop failed")
            self._stop.wait(self.poll_interval_s)


def _prior(row: dict[str, Any]) -> PriorAlert:
    return PriorAlert(
        kind=row["kind"],
        severity=row["severity"],
        opened_at=parse_iso(row["opened_at"]),
        acked_at=parse_iso(row["acked_at"]) if row["acked_at"] else None,
        resolved_at=parse_iso(row["resolved_at"]) if row["resolved_at"] else None,
        notified_at=parse_iso(row["notified_at"]) if row["notified_at"] else None,
    )


def _public_reading(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "ts": row["ts"],
        "dissolved_oxygen": {"value": row["do_mg_l"], "ok": bool(row["do_ok"]), "unit": "mg/L"},
        "temperature": {"value": row["temp_f"], "ok": bool(row["temp_ok"]), "unit": "F"},
        "ph": {"value": row["ph"], "ok": bool(row["ph_ok"]), "unit": "pH"},
        "water_level": {
            "value": row["level_state"],
            "ok": bool(row["level_ok"]),
            "unit": "state",
            "level_cm": row["level_cm"],
        },
    }


_MANUAL_KEYS = ("tan_mg_l", "nitrite_mg_l", "nitrate_mg_l", "alkalinity_mg_l", "salinity_ppt")


def _manual_fields(payload: dict[str, Any]) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for key in _MANUAL_KEYS:
        raw = payload.get(key)
        if raw is None or raw == "":
            fields[key] = None
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise HttpError(400, f"{key} must be a number") from exc
        if value < 0:
            raise HttpError(400, f"{key} cannot be negative")
        fields[key] = value
    notes = payload.get("notes")
    fields["notes"] = (str(notes).strip()[:500] if notes else None)
    return fields


def bucket_series(samples: list[dict[str, Any]], max_points: int) -> list[dict[str, Any]]:
    if len(samples) <= max_points:
        return samples
    size = len(samples) / max_points
    out = []
    index = 0
    keys = ("mean_speed_mm_s", "cum_distance_mm", "mean_nn_mm", "crowded_fraction", "mean_length_mm")
    while index < len(samples):
        end = min(len(samples), int(index + size))
        if end <= index:
            end = index + 1
        chunk = samples[index:end]
        point: dict[str, Any] = {"frame": chunk[-1]["frame"], "t_s": chunk[-1]["t_s"]}
        for key in keys:
            values = [row[key] for row in chunk if row.get(key) is not None]
            point[key] = (sum(values) / len(values)) if values else None
        # Cumulative distance should stay monotonic: keep the bucket's last value.
        point["cum_distance_mm"] = chunk[-1].get("cum_distance_mm")
        out.append(point)
        index = end
    return out


def load_config(path: str | Path) -> dict[str, Any]:
    file = Path(path)
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{file} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{file} must contain a JSON object")
    return data
