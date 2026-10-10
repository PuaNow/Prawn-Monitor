"""SQLite storage for readings, alerts, clips, and AMADEUS imports."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from prawn_monitor.timeutil import to_iso, utc_now


SCHEMA = """
CREATE TABLE IF NOT EXISTS tanks (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  volume_gal REAL,
  species TEXT,
  node_url TEXT,
  mm_per_pixel REAL,
  site TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY,
  tank_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  do_mg_l REAL,
  do_ok INTEGER,
  temp_f REAL,
  temp_ok INTEGER,
  ph REAL,
  ph_ok INTEGER,
  level_state TEXT,
  level_cm REAL,
  level_ok INTEGER,
  payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_readings_tank_ts ON readings(tank_id, ts);
CREATE TABLE IF NOT EXISTS manual_logs (
  id INTEGER PRIMARY KEY,
  tank_id TEXT NOT NULL,
  ts TEXT NOT NULL,
  tan_mg_l REAL,
  nitrite_mg_l REAL,
  nitrate_mg_l REAL,
  alkalinity_mg_l REAL,
  salinity_ppt REAL,
  notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_manual_tank_ts ON manual_logs(tank_id, ts);
CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY,
  tank_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  severity TEXT NOT NULL,
  message TEXT NOT NULL,
  value REAL,
  unit TEXT,
  opened_at TEXT NOT NULL,
  notified_at TEXT,
  acked_at TEXT,
  acked_by TEXT,
  resolved_at TEXT,
  delivery TEXT
);
CREATE INDEX IF NOT EXISTS idx_alerts_tank ON alerts(tank_id, id);
CREATE TABLE IF NOT EXISTS thresholds (
  scope TEXT PRIMARY KEY,
  body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS clips (
  id TEXT PRIMARY KEY,
  tank_id TEXT NOT NULL,
  camera_id TEXT,
  started_at TEXT,
  duration_s REAL,
  metadata TEXT
);
CREATE TABLE IF NOT EXISTS tracking_imports (
  id INTEGER PRIMARY KEY,
  tank_id TEXT NOT NULL,
  imported_at TEXT NOT NULL,
  filename TEXT,
  fps REAL,
  mm_per_pixel REAL,
  animal_count INTEGER,
  summary TEXT,
  csv_path TEXT
);
CREATE TABLE IF NOT EXISTS tracking_samples (
  id INTEGER PRIMARY KEY,
  import_id INTEGER NOT NULL,
  frame INTEGER,
  t_s REAL,
  mean_speed_mm_s REAL,
  cum_distance_mm REAL,
  mean_nn_mm REAL,
  crowded_fraction REAL,
  mean_length_mm REAL
);
CREATE INDEX IF NOT EXISTS idx_samples_import ON tracking_samples(import_id, frame);
CREATE TABLE IF NOT EXISTS node_status (
  tank_id TEXT PRIMARY KEY,
  last_ok_at TEXT,
  last_error TEXT,
  online INTEGER
);
"""


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=15)
        self._conn.row_factory = sqlite3.Row
        with self.lock:
            self._conn.executescript(SCHEMA)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.commit()

    def close(self) -> None:
        with self.lock:
            self._conn.close()

    def upsert_tank(self, row: dict[str, Any]) -> None:
        with self.lock:
            self._conn.execute(
                """
                INSERT INTO tanks (id, name, volume_gal, species, node_url, mm_per_pixel, site, updated_at)
                VALUES (:id, :name, :volume_gal, :species, :node_url, :mm_per_pixel, :site, :updated_at)
                ON CONFLICT(id) DO UPDATE SET
                  name=excluded.name,
                  volume_gal=excluded.volume_gal,
                  species=excluded.species,
                  node_url=excluded.node_url,
                  mm_per_pixel=excluded.mm_per_pixel,
                  site=excluded.site,
                  updated_at=excluded.updated_at
                """,
                {
                    "id": row["id"],
                    "name": row.get("name") or row["id"],
                    "volume_gal": row.get("volume_gal"),
                    "species": row.get("species"),
                    "node_url": row.get("node_url"),
                    "mm_per_pixel": row.get("mm_per_pixel"),
                    "site": row.get("site"),
                    "updated_at": row.get("updated_at") or to_iso(utc_now()),
                },
            )
            self._conn.commit()

    def list_tanks(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._conn.execute("SELECT * FROM tanks ORDER BY id").fetchall()
        return [dict(row) for row in rows]

    def insert_reading(self, tank_id: str, ts: str, sensors: dict[str, Any], payload: dict[str, Any]) -> int:
        def grab(kind: str, field: str, default=None):
            row = sensors.get(kind) or {}
            return row.get(field, default)

        level_extra = (sensors.get("water_level") or {}).get("extra") or {}
        with self.lock:
            cur = self._conn.execute(
                """
                INSERT INTO readings (
                  tank_id, ts, do_mg_l, do_ok, temp_f, temp_ok, ph, ph_ok,
                  level_state, level_cm, level_ok, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tank_id,
                    ts,
                    grab("dissolved_oxygen", "value"),
                    1 if grab("dissolved_oxygen", "ok") else 0,
                    grab("temperature", "value"),
                    1 if grab("temperature", "ok") else 0,
                    grab("ph", "value"),
                    1 if grab("ph", "ok") else 0,
                    grab("water_level", "value"),
                    level_extra.get("level_cm"),
                    1 if grab("water_level", "ok") else 0,
                    json.dumps(payload),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def readings_between(self, tank_id: str, start: str, end: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._conn.execute(
                """
                SELECT ts, do_mg_l, temp_f, ph, level_state, level_cm,
                       do_ok, temp_ok, ph_ok, level_ok
                FROM readings
                WHERE tank_id=? AND ts>=? AND ts<=?
                ORDER BY ts
                """,
                (tank_id, start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def temp_history(self, tank_id: str, since: str) -> list[tuple[str, float]]:
        with self.lock:
            rows = self._conn.execute(
                """
                SELECT ts, temp_f FROM readings
                WHERE tank_id=? AND ts>=? AND temp_ok=1 AND temp_f IS NOT NULL
                ORDER BY ts
                """,
                (tank_id, since),
            ).fetchall()
        return [(row["ts"], float(row["temp_f"])) for row in rows]

    def latest_reading(self, tank_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self._conn.execute(
                "SELECT * FROM readings WHERE tank_id=? ORDER BY ts DESC, id DESC LIMIT 1",
                (tank_id,),
            ).fetchone()
        return dict(row) if row else None

    def insert_manual(self, tank_id: str, ts: str, fields: dict[str, Any]) -> int:
        with self.lock:
            cur = self._conn.execute(
                """
                INSERT INTO manual_logs (
                  tank_id, ts, tan_mg_l, nitrite_mg_l, nitrate_mg_l,
                  alkalinity_mg_l, salinity_ppt, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tank_id,
                    ts,
                    fields.get("tan_mg_l"),
                    fields.get("nitrite_mg_l"),
                    fields.get("nitrate_mg_l"),
                    fields.get("alkalinity_mg_l"),
                    fields.get("salinity_ppt"),
                    fields.get("notes"),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def manual_logs(self, tank_id: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._conn.execute(
                """
                SELECT * FROM manual_logs WHERE tank_id=?
                ORDER BY ts DESC, id DESC LIMIT ?
                """,
                (tank_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_manual_values(self, tank_id: str) -> dict[str, tuple[float, str]]:
        """Latest non-null test-kit value per field. Salinity is included for display."""
        logs = self.manual_logs(tank_id, limit=200)
        found: dict[str, tuple[float, str]] = {}
        fields = (
            "tan_mg_l",
            "nitrite_mg_l",
            "nitrate_mg_l",
            "alkalinity_mg_l",
            "salinity_ppt",
        )
        for row in logs:
            for field in fields:
                if field in found or row.get(field) is None:
                    continue
                found[field] = (float(row[field]), row["ts"])
            if len(found) == len(fields):
                break
        return found

    def set_node_status(self, tank_id: str, *, online: bool, last_ok_at: str | None, error: str | None) -> None:
        with self.lock:
            current = self._conn.execute(
                "SELECT last_ok_at FROM node_status WHERE tank_id=?",
                (tank_id,),
            ).fetchone()
            kept_ok = last_ok_at
            if kept_ok is None and current is not None:
                kept_ok = current["last_ok_at"]
            self._conn.execute(
                """
                INSERT INTO node_status (tank_id, last_ok_at, last_error, online)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tank_id) DO UPDATE SET
                  last_ok_at=excluded.last_ok_at,
                  last_error=excluded.last_error,
                  online=excluded.online
                """,
                (tank_id, kept_ok, error, 1 if online else 0),
            )
            self._conn.commit()

    def node_status(self, tank_id: str) -> dict[str, Any] | None:
        with self.lock:
            row = self._conn.execute(
                "SELECT * FROM node_status WHERE tank_id=?",
                (tank_id,),
            ).fetchone()
        return dict(row) if row else None

    def save_thresholds(self, scope: str, body: dict[str, Any]) -> None:
        with self.lock:
            self._conn.execute(
                """
                INSERT INTO thresholds (scope, body) VALUES (?, ?)
                ON CONFLICT(scope) DO UPDATE SET body=excluded.body
                """,
                (scope, json.dumps(body)),
            )
            self._conn.commit()

    def thresholds_for(self, scope: str) -> dict[str, Any]:
        with self.lock:
            row = self._conn.execute(
                "SELECT body FROM thresholds WHERE scope=?",
                (scope,),
            ).fetchone()
        if row is None:
            return {}
        return json.loads(row["body"])

    def open_alert(
        self,
        *,
        tank_id: str,
        kind: str,
        severity: str,
        message: str,
        value: float | None,
        unit: str,
        opened_at: str,
        delivery: str,
    ) -> int:
        with self.lock:
            cur = self._conn.execute(
                """
                INSERT INTO alerts (
                  tank_id, kind, severity, message, value, unit,
                  opened_at, notified_at, delivery
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (tank_id, kind, severity, message, value, unit, opened_at, opened_at, delivery),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def escalate_alert(
        self,
        alert_id: int,
        *,
        severity: str,
        message: str,
        value: float | None,
        unit: str,
        notified_at: str,
        delivery: str,
    ) -> None:
        with self.lock:
            self._conn.execute(
                """
                UPDATE alerts
                SET severity=?, message=?, value=?, unit=?, notified_at=?, delivery=?
                WHERE id=?
                """,
                (severity, message, value, unit, notified_at, delivery, alert_id),
            )
            self._conn.commit()

    def resolve_alert(self, alert_id: int, resolved_at: str) -> None:
        with self.lock:
            self._conn.execute(
                "UPDATE alerts SET resolved_at=? WHERE id=? AND resolved_at IS NULL",
                (resolved_at, alert_id),
            )
            self._conn.commit()

    def ack_alert(self, alert_id: int, acked_at: str, acked_by: str) -> bool:
        with self.lock:
            cur = self._conn.execute(
                """
                UPDATE alerts SET acked_at=?, acked_by=?
                WHERE id=? AND acked_at IS NULL
                """,
                (acked_at, acked_by, alert_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def latest_alerts_by_kind(self, tank_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._conn.execute(
                "SELECT * FROM alerts WHERE tank_id=? ORDER BY id DESC",
                (tank_id,),
            ).fetchall()
        seen: set[str] = set()
        latest: list[dict[str, Any]] = []
        for row in rows:
            if row["kind"] in seen:
                continue
            seen.add(row["kind"])
            latest.append(dict(row))
        return latest

    def list_alerts(self, *, tank_id: str | None = None, inbox_only: bool = False, limit: int = 100) -> list[dict[str, Any]]:
        clauses = []
        args: list[Any] = []
        if tank_id:
            clauses.append("tank_id=?")
            args.append(tank_id)
        if inbox_only:
            clauses.append("acked_at IS NULL")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        args.append(limit)
        with self.lock:
            rows = self._conn.execute(
                f"SELECT * FROM alerts {where} ORDER BY id DESC LIMIT ?",
                args,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_alert(self, alert_id: int) -> dict[str, Any] | None:
        with self.lock:
            row = self._conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        return dict(row) if row else None

    def upsert_clip(self, clip: dict[str, Any]) -> None:
        with self.lock:
            self._conn.execute(
                """
                INSERT INTO clips (id, tank_id, camera_id, started_at, duration_s, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                  metadata=excluded.metadata,
                  duration_s=excluded.duration_s
                """,
                (
                    clip["id"],
                    clip["tank_id"],
                    clip.get("camera_id"),
                    clip.get("started_at"),
                    clip.get("duration_s"),
                    json.dumps(clip),
                ),
            )
            self._conn.commit()

    def list_clips(self, tank_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._conn.execute(
                "SELECT metadata FROM clips WHERE tank_id=? ORDER BY started_at DESC",
                (tank_id,),
            ).fetchall()
        return [json.loads(row["metadata"]) for row in rows]

    def add_tracking_import(
        self,
        *,
        tank_id: str,
        imported_at: str,
        filename: str,
        fps: float,
        mm_per_pixel: float,
        summary: dict[str, Any],
        samples: list[dict[str, Any]],
        csv_path: str,
    ) -> int:
        with self.lock:
            cur = self._conn.execute(
                """
                INSERT INTO tracking_imports (
                  tank_id, imported_at, filename, fps, mm_per_pixel,
                  animal_count, summary, csv_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    tank_id,
                    imported_at,
                    filename,
                    fps,
                    mm_per_pixel,
                    summary.get("animals"),
                    json.dumps(summary),
                    csv_path,
                ),
            )
            import_id = int(cur.lastrowid)
            self._conn.executemany(
                """
                INSERT INTO tracking_samples (
                  import_id, frame, t_s, mean_speed_mm_s, cum_distance_mm,
                  mean_nn_mm, crowded_fraction, mean_length_mm
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        import_id,
                        sample["frame"],
                        sample["t_s"],
                        sample["mean_speed_mm_s"],
                        sample["cum_distance_mm"],
                        sample["mean_nn_mm"],
                        sample["crowded_fraction"],
                        sample["mean_length_mm"],
                    )
                    for sample in samples
                ],
            )
            self._conn.commit()
            return import_id

    def list_imports(self, tank_id: str) -> list[dict[str, Any]]:
        with self.lock:
            rows = self._conn.execute(
                """
                SELECT id, tank_id, imported_at, filename, fps, mm_per_pixel,
                       animal_count, summary
                FROM tracking_imports WHERE tank_id=? ORDER BY id DESC
                """,
                (tank_id,),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["summary"] = json.loads(item["summary"])
            out.append(item)
        return out

    def tracking_samples(self, import_id: int) -> list[dict[str, Any]]:
        with self.lock:
            row = self._conn.execute(
                "SELECT * FROM tracking_imports WHERE id=?",
                (import_id,),
            ).fetchone()
            if row is None:
                return []
            samples = self._conn.execute(
                """
                SELECT frame, t_s, mean_speed_mm_s, cum_distance_mm,
                       mean_nn_mm, crowded_fraction, mean_length_mm
                FROM tracking_samples WHERE import_id=? ORDER BY frame
                """,
                (import_id,),
            ).fetchall()
        return [dict(sample) for sample in samples]

    def tracking_import(self, import_id: int) -> dict[str, Any] | None:
        with self.lock:
            row = self._conn.execute(
                "SELECT * FROM tracking_imports WHERE id=?",
                (import_id,),
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["summary"] = json.loads(item["summary"])
        item["samples"] = self.tracking_samples(import_id)
        return item
