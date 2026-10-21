"""Harvest batch records, SHA-256, optional IPFS, and Solana memo checks.

The canonical JSON is hashed before any signature exists. The memo commits
to that hash. A wallet signs the memo. This module never asks for a key.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable
from zoneinfo import ZoneInfo

from prawn_monitor.timeutil import parse_iso, to_iso

SCHEMA = "ncpp.harvest.v1"
MEMO_PREFIX = "NCPP-harvest"
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
BATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
SIGNATURE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{80,100}$")
SIGNER = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,48}$")
_SECRET_PARTS = ("private", "secret", "seed", "mnemonic", "keypair")
_MEMO_LOG = re.compile(r'^Program log: Memo \(len \d+\): "(.*)"$')

CLUSTERS = {
    "devnet": "https://api.devnet.solana.com",
    "mainnet": "https://api.mainnet-beta.solana.com",
}

METRIC_ORDER = (
    "dissolved_oxygen_mg_l",
    "temperature_f",
    "ph",
    "tan_mg_l",
    "nitrite_mg_l",
    "nitrate_mg_l",
    "alkalinity_mg_l",
)
METRIC_UNITS = {
    "dissolved_oxygen_mg_l": "mg/L",
    "temperature_f": "F",
    "ph": "pH",
    "tan_mg_l": "mg/L",
    "nitrite_mg_l": "mg/L",
    "nitrate_mg_l": "mg/L",
    "alkalinity_mg_l": "mg/L as CaCO3",
}

Transport = Callable[[str, bytes, dict[str, str]], tuple[int, bytes]]
SolanaFetch = Callable[[str, str], dict[str, Any] | None]


def fmt_num(value: float, places: int = 4) -> str:
    quantum = Decimal("1").scaleb(-places)
    decimal = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    return format(decimal, f".{places}f")


def canonical_dumps(record: dict[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_cluster(name: str | None) -> str:
    raw = (name or "devnet").strip().lower()
    if raw == "devnet":
        return "devnet"
    if raw in ("mainnet", "mainnet-beta"):
        return "mainnet"
    raise ValueError("cluster must be devnet or mainnet")


def resolve_endpoint(solana_config: dict[str, Any] | None, requested: str | None) -> tuple[str, str]:
    config = solana_config or {}
    default_cluster = normalize_cluster(config.get("cluster"))
    cluster = normalize_cluster(requested) if requested else default_cluster
    configured = str(config.get("rpc_url") or "").strip()
    if configured and cluster == default_cluster:
        return cluster, configured
    return cluster, CLUSTERS[cluster]


def reject_secrets(payload: dict[str, Any]) -> None:
    for key in payload:
        lowered = str(key).lower().replace("-", "_")
        if any(part in lowered for part in _SECRET_PARTS):
            raise ValueError("the hub does not accept private keys or seed phrases")


def memo_for(batch_id: str, digest: str, cid: str | None) -> str:
    if not BATCH_ID.fullmatch(batch_id):
        raise ValueError("batch id must be letters, numbers, dot, underscore, or hyphen")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("sha256 must be 64 lowercase hex characters")
    cid_part = cid or "-"
    if ":" in cid_part or any(ch.isspace() for ch in cid_part):
        raise ValueError("ipfs cid cannot contain spaces or colons")
    text = f"{MEMO_PREFIX}:{batch_id}:{digest}:{cid_part}"
    if len(text.encode("utf-8")) > 566:
        raise ValueError("memo is longer than the SPL memo limit")
    return text


def solscan_url(signature: str, cluster: str) -> str:
    if cluster == "devnet":
        return f"https://solscan.io/tx/{signature}?cluster=devnet"
    return f"https://solscan.io/tx/{signature}"


def local_window(stocking_date: str, harvest_date: str, timezone_name: str) -> tuple[str, str]:
    start = _local_date(stocking_date, timezone_name, end=False)
    end = _local_date(harvest_date, timezone_name, end=True)
    if end < start:
        raise ValueError("harvest date is before the stocking date")
    return to_iso(start), to_iso(end)


def _local_date(text: str, timezone_name: str, *, end: bool) -> datetime:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text or ""):
        raise ValueError("dates must be YYYY-MM-DD")
    year, month, day = (int(part) for part in text.split("-"))
    try:
        zone = ZoneInfo(timezone_name)
        if end:
            local = datetime(year, month, day, 23, 59, 59, tzinfo=zone)
        else:
            local = datetime(year, month, day, 0, 0, 0, tzinfo=zone)
    except Exception as exc:
        raise ValueError(f"could not use timezone {timezone_name}") from exc
    return local


def _stats(values: list[float]) -> dict[str, Any] | None:
    if not values:
        return None
    return {
        "n": len(values),
        "min": fmt_num(min(values)),
        "mean": fmt_num(sum(values) / len(values)),
        "max": fmt_num(max(values)),
        "unit": "",
    }


def _in_range_probe(reading: dict[str, Any], limits: dict[str, Any]) -> bool | None:
    checks: list[bool] = []
    if reading.get("do_ok") and reading.get("do_mg_l") is not None:
        checks.append(float(reading["do_mg_l"]) >= float(limits["do_mg_l"]["warn_below"]))
    if reading.get("temp_ok") and reading.get("temp_f") is not None:
        temp = float(reading["temp_f"])
        checks.append(float(limits["temp_f"]["low"]) <= temp <= float(limits["temp_f"]["high"]))
    if reading.get("ph_ok") and reading.get("ph") is not None:
        ph = float(reading["ph"])
        checks.append(float(limits["ph"]["low"]) <= ph <= float(limits["ph"]["high"]))
    if not checks:
        return None
    return all(checks)


def time_in_range(readings: list[dict[str, Any]], limits: dict[str, Any]) -> dict[str, Any]:
    """Share of elapsed time the previous DO, temperature, and pH sample was inside the warning band.

    A sample with no usable probe is left out. Kit values are not part of this
    fraction because they are sparse. One sample has no elapsed time.
    """
    points: list[tuple[datetime, bool]] = []
    for reading in readings:
        flag = _in_range_probe(reading, limits)
        if flag is None:
            continue
        points.append((parse_iso(reading["ts"]), flag))
    samples_in = sum(1 for _ts, flag in points if flag)
    empty = {
        "fraction": "0.0000",
        "in_range_seconds": 0,
        "span_seconds": 0,
        "samples": 0,
        "samples_in_range": 0,
    }
    if not points:
        return empty
    if len(points) == 1:
        return {
            "fraction": "1.0000" if points[0][1] else "0.0000",
            "in_range_seconds": 0,
            "span_seconds": 0,
            "samples": 1,
            "samples_in_range": samples_in,
        }
    span = 0
    inside = 0
    for (start, flag), (end, _next) in zip(points, points[1:]):
        delta = int((end - start).total_seconds())
        if delta < 0:
            continue
        span += delta
        if flag:
            inside += delta
    fraction = "0.0000" if span == 0 else fmt_num(inside / span)
    return {
        "fraction": fraction,
        "in_range_seconds": inside,
        "span_seconds": span,
        "samples": len(points),
        "samples_in_range": samples_in,
    }


def _bands(limits: dict[str, Any]) -> dict[str, Any]:
    return {
        "dissolved_oxygen_mg_l": {"in_range_at_or_above": fmt_num(float(limits["do_mg_l"]["warn_below"]))},
        "temperature_f": {
            "low": fmt_num(float(limits["temp_f"]["low"])),
            "high": fmt_num(float(limits["temp_f"]["high"])),
        },
        "ph": {
            "low": fmt_num(float(limits["ph"]["low"])),
            "high": fmt_num(float(limits["ph"]["high"])),
        },
        "tan_mg_l": {"in_range_at_or_below": fmt_num(float(limits["tan_mg_l"]["warn_above"]))},
        "nitrite_mg_l": {"in_range_at_or_below": fmt_num(float(limits["nitrite_mg_l"]["warn_above"]))},
        "nitrate_mg_l": {"in_range_at_or_below": fmt_num(float(limits["nitrate_mg_l"]["warn_above"]))},
        "alkalinity_mg_l": {"in_range_at_or_above": fmt_num(float(limits["alkalinity_mg_l"]["warn_below"]))},
    }


def _metric(values: list[float], unit: str) -> dict[str, Any] | None:
    stats = _stats(values)
    if stats is None:
        return None
    stats["unit"] = unit
    return stats


def build_record(
    *,
    batch_id: str,
    site: str,
    place: str,
    tank: dict[str, Any],
    stocking_date: str,
    harvest_date: str,
    period_start: str,
    period_end: str,
    pounds: float,
    notes: str,
    readings: list[dict[str, Any]],
    manual_logs: list[dict[str, Any]],
    alert_count: int,
    limits: dict[str, Any],
    activity: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics = {
        "dissolved_oxygen_mg_l": _metric(
            [float(row["do_mg_l"]) for row in readings if row.get("do_ok") and row.get("do_mg_l") is not None],
            METRIC_UNITS["dissolved_oxygen_mg_l"],
        ),
        "temperature_f": _metric(
            [float(row["temp_f"]) for row in readings if row.get("temp_ok") and row.get("temp_f") is not None],
            METRIC_UNITS["temperature_f"],
        ),
        "ph": _metric(
            [float(row["ph"]) for row in readings if row.get("ph_ok") and row.get("ph") is not None],
            METRIC_UNITS["ph"],
        ),
        "tan_mg_l": _metric(_manual_values(manual_logs, "tan_mg_l"), METRIC_UNITS["tan_mg_l"]),
        "nitrite_mg_l": _metric(_manual_values(manual_logs, "nitrite_mg_l"), METRIC_UNITS["nitrite_mg_l"]),
        "nitrate_mg_l": _metric(_manual_values(manual_logs, "nitrate_mg_l"), METRIC_UNITS["nitrate_mg_l"]),
        "alkalinity_mg_l": _metric(
            _manual_values(manual_logs, "alkalinity_mg_l"),
            METRIC_UNITS["alkalinity_mg_l"],
        ),
    }
    volume = tank.get("volume_gal")
    return {
        "schema": SCHEMA,
        "batch_id": batch_id,
        "site": site,
        "place": place,
        "species": tank.get("species") or "Litopenaeus vannamei",
        "tank_id": tank["id"],
        "tank_name": tank.get("name") or tank["id"],
        "volume_gal": None if volume is None else fmt_num(float(volume)),
        "stocking_date": stocking_date,
        "harvest_date": harvest_date,
        "period_start": period_start,
        "period_end": period_end,
        "pounds": fmt_num(float(pounds)),
        "notes": notes,
        "bands": _bands(limits),
        "water_quality": {
            "alert_count": int(alert_count),
            "time_in_range": time_in_range(readings, limits),
            "metrics": metrics,
        },
        "activity": activity,
    }


def _manual_values(logs: list[dict[str, Any]], key: str) -> list[float]:
    values = []
    for row in logs:
        if row.get(key) is not None:
            values.append(float(row[key]))
    return values


def activity_from_imports(imports: list[dict[str, Any]], samples_for) -> dict[str, Any]:
    if not imports:
        return {
            "imports": 0,
            "animals": 0,
            "mean_speed_mm_s": None,
            "distance_mm": fmt_num(0),
            "mean_nearest_neighbor_mm": None,
            "mean_crowded_fraction": None,
            "median_length_mm": None,
        }
    animals = 0
    speeds: list[float] = []
    lengths: list[float] = []
    distance = 0.0
    nearest: list[float] = []
    crowded: list[float] = []
    for item in imports:
        summary = item.get("summary") or {}
        animals = max(animals, int(summary.get("animals") or 0))
        if summary.get("mean_speed_mm_s") is not None:
            speeds.append(float(summary["mean_speed_mm_s"]))
        if summary.get("median_length_mm") is not None:
            lengths.append(float(summary["median_length_mm"]))
        distance += float(summary.get("total_distance_mm") or 0)
        for sample in samples_for(int(item["id"])):
            if sample.get("mean_nn_mm") is not None:
                nearest.append(float(sample["mean_nn_mm"]))
            if sample.get("crowded_fraction") is not None:
                crowded.append(float(sample["crowded_fraction"]))
    return {
        "imports": len(imports),
        "animals": animals,
        "mean_speed_mm_s": None if not speeds else fmt_num(sum(speeds) / len(speeds)),
        "distance_mm": fmt_num(distance),
        "mean_nearest_neighbor_mm": None if not nearest else fmt_num(sum(nearest) / len(nearest)),
        "mean_crowded_fraction": None if not crowded else fmt_num(sum(crowded) / len(crowded)),
        "median_length_mm": None if not lengths else fmt_num(sum(lengths) / len(lengths)),
    }


def clean_notes(value: Any) -> str:
    text = unicodedata.normalize("NFC", str(value or "")).strip()
    if len(text) > 1000:
        raise ValueError("notes must be 1000 characters or fewer")
    return text


def clean_pounds(value: Any) -> float:
    try:
        pounds = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("pounds must be a number") from exc
    if pounds < 0 or pounds > 100000:
        raise ValueError("pounds must be between 0 and 100000")
    return pounds


class PinResult:
    def __init__(self, cid: str | None, status: str, detail: str):
        self.cid = cid
        self.status = status
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {"cid": self.cid, "status": self.status, "detail": self.detail}


def pin_canonical_json(
    text: str,
    ipfs_config: dict[str, Any] | None,
    transport: Transport | None = None,
) -> PinResult:
    config = ipfs_config or {}
    token = str(config.get("api_token") or "").strip()
    if not token:
        return PinResult(None, "skipped", "no IPFS API token is set")
    url = str(config.get("api_url") or "").strip()
    provider = str(config.get("provider") or "").strip().lower()
    if not provider:
        provider = "kubo" if url.rstrip("/").endswith("/api/v0/add") else "pinata"
    if provider == "pinata" and not url:
        url = "https://api.pinata.cloud/pinning/pinFileToIPFS"
    if not url:
        return PinResult(None, "failed", "IPFS api_url is empty")
    boundary = "prawn" + uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="harvest.json"\r\n'
        "Content-Type: application/json; charset=utf-8\r\n\r\n"
    ).encode("utf-8") + text.encode("utf-8") + f"\r\n--{boundary}--\r\n".encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
    }
    send = transport or urllib_transport
    try:
        status, raw = send(url, body, headers)
    except Exception as exc:
        return PinResult(None, "failed", f"IPFS pin failed: {exc}")
    if status < 200 or status >= 300:
        detail = raw.decode("utf-8", errors="replace")[:300]
        return PinResult(None, "failed", f"IPFS pin returned HTTP {status}: {detail}")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return PinResult(None, "failed", "IPFS pin returned a response that is not JSON")
    cid = payload.get("IpfsHash") or payload.get("Hash") or payload.get("cid")
    if not cid:
        return PinResult(None, "failed", "IPFS pin response did not include a CID")
    return PinResult(str(cid), "pinned", "pinned the canonical JSON bytes")


def urllib_transport(url: str, data: bytes, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def fetch_transaction(rpc_url: str, signature: str, transport: Transport | None = None) -> dict[str, Any] | None:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "getTransaction",
        "params": [
            signature,
            {"encoding": "jsonParsed", "commitment": "confirmed", "maxSupportedTransactionVersion": 0},
        ],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    send = transport or urllib_transport
    status, raw = send(rpc_url, body, headers)
    if status < 200 or status >= 300:
        raise RuntimeError(f"Solana RPC returned HTTP {status}")
    parsed = json.loads(raw.decode("utf-8"))
    if parsed.get("error"):
        raise RuntimeError(str(parsed["error"]))
    result = parsed.get("result")
    if result is None:
        return None
    if not isinstance(result, dict):
        raise RuntimeError("Solana RPC returned an unexpected transaction")
    return result


def memos_in_transaction(tx: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def add(value: str | None) -> None:
        if value and value not in found:
            found.append(value)

    for instruction in _instructions(tx):
        program = instruction.get("program")
        program_id = instruction.get("programId")
        parsed = instruction.get("parsed")
        if program == "spl-memo" or program_id == MEMO_PROGRAM_ID:
            if isinstance(parsed, str):
                add(parsed)
            elif isinstance(parsed, dict):
                memo = parsed.get("memo")
                if isinstance(memo, str):
                    add(memo)
            data = instruction.get("data")
            if isinstance(data, str) and program != "spl-memo":
                add(_maybe_b64(data))
    for line in (tx.get("meta") or {}).get("logMessages") or []:
        match = _MEMO_LOG.match(str(line))
        if match:
            add(match.group(1))
    return found


def _maybe_b64(data: str) -> str | None:
    import base64

    try:
        raw = base64.b64decode(data, validate=True)
    except Exception:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _instructions(tx: dict[str, Any]):
    message = (tx.get("transaction") or {}).get("message") or {}
    yield from message.get("instructions") or []
    for group in (tx.get("meta") or {}).get("innerInstructions") or []:
        yield from group.get("instructions") or []


def fee_payer(tx: dict[str, Any]) -> str | None:
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    if not keys:
        return None
    first = keys[0]
    if isinstance(first, dict):
        return first.get("pubkey")
    if isinstance(first, str):
        return first
    return None


def transaction_slot(tx: dict[str, Any]) -> int | None:
    slot = tx.get("slot")
    if slot is None:
        return None
    return int(slot)


def verify_transaction(expected_memo: str, tx: dict[str, Any] | None) -> dict[str, Any]:
    if tx is None:
        return {"ok": False, "reason": "transaction not found", "memos": []}
    err = (tx.get("meta") or {}).get("err")
    if err:
        return {"ok": False, "reason": "transaction failed on Solana", "memos": []}
    memos = memos_in_transaction(tx)
    if expected_memo not in memos:
        return {"ok": False, "reason": "memo does not match this harvest", "memos": memos}
    return {"ok": True, "reason": "memo matches this harvest", "memos": memos}


def public_harvest(row: dict[str, Any]) -> dict[str, Any]:
    signature = row.get("signature")
    cluster = row["cluster"]
    cid = row.get("ipfs_cid")
    if cid:
        ipfs_status = "pinned"
    elif row.get("ipfs_error"):
        ipfs_status = "failed"
    else:
        ipfs_status = "skipped"
    return {
        "batch_id": row["batch_id"],
        "tank_id": row["tank_id"],
        "stocking_date": row["stocking_date"],
        "harvest_date": row["harvest_date"],
        "pounds": row["pounds"],
        "notes": row["notes"] or "",
        "record": json.loads(row["canonical_json"]),
        "canonical_json": row["canonical_json"],
        "sha256": row["sha256"],
        "ipfs_cid": cid,
        "ipfs_error": row.get("ipfs_error"),
        "ipfs_status": ipfs_status,
        "memo": row["memo"],
        "cluster": cluster,
        "rpc_url": row["rpc_url"],
        "signature": signature,
        "signer": row.get("signer"),
        "slot": row.get("slot"),
        "anchored_at": row.get("anchored_at"),
        "anchor_source": row.get("anchor_source"),
        "created_at": row["created_at"],
        "solscan_url": solscan_url(signature, cluster) if signature else None,
        "verify_path": f"/verify/{row['batch_id']}",
    }


def anchor_source(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("phantom", "solana-cli"):
        return text
    return "wallet"
