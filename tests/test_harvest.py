import hashlib
import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

import pytest

from prawn_monitor.harvest import canonical_dumps, local_window, memo_for, sha256_text, verify_transaction
from prawn_monitor.qrcode import qr_matrix
from prawn_monitor.hub import HubApp, get_json, post_json
from prawn_monitor.httputil import HttpError, json_response, serve
from prawn_monitor.shrimpcoin import MINT, SUPPLY, UPDATE_AUTHORITY
from prawn_monitor.solana_tx import b58encode, build_unsigned_memo_tx

ROOT = Path(__file__).resolve().parents[1]
PAYER = "11111111111111111111111111111111"
BLOCK = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"


def _dispatch(app, method, path, payload=None):
    body = b"" if payload is None else json.dumps(payload).encode()
    try:
        return app.handle(method, path, {}, body, {})
    except HttpError as exc:
        return json_response({"error": exc.message}, exc.status)


def _signature() -> str:
    return b58encode(b"\x11" + b"\x22" * 63)


def _tx(memo: str, *, failed: bool = False) -> dict:
    return {
        "slot": 42,
        "meta": {
            "err": {"InstructionError": [0, "Custom"]} if failed else None,
            "logMessages": [f'Program log: Memo (len {len(memo)}): "{memo}"'],
        },
        "transaction": {
            "message": {
                "accountKeys": [{"pubkey": PAYER, "signer": True, "writable": True}],
                "instructions": [{"program": "spl-memo", "parsed": memo}],
            }
        },
    }


def _reading(do, temp, ph, ok=True):
    return {
        "dissolved_oxygen": {"ok": ok, "value": do},
        "temperature": {"ok": ok, "value": temp},
        "ph": {"ok": ok, "value": ph},
        "water_level": {"ok": True, "value": "ok", "extra": {"level_cm": 40}},
    }


def _hub(tmp_path, solana_fetch=None, ipfs=None, ipfs_transport=None):
    config = {
        "data_dir": str(tmp_path / "hub"),
        "site": "NorCal Premier Prawn",
        "place": "Ukiah, CA",
        "timezone": "UTC",
        "public_url": "http://127.0.0.1:8080",
        "start_poller": False,
        "tanks": [
            {
                "id": "nursery",
                "name": "Nursery",
                "volume_gal": 225,
                "species": "Litopenaeus vannamei",
                "node_url": "http://127.0.0.1:9",
            }
        ],
        "solana": {"cluster": "devnet"},
        "ipfs": ipfs or {"api_token": ""},
    }
    return HubApp(config, solana_fetch=solana_fetch, ipfs_transport=ipfs_transport)


def _seed(app):
    app.db.insert_reading("nursery", "2026-08-01T00:00:00Z", _reading(1.0, 70, 6.0), {})
    app.db.insert_reading("nursery", "2026-09-01T00:00:00Z", _reading(6.0, 84, 8.0), {})
    app.db.insert_reading("nursery", "2026-09-01T01:00:00Z", _reading(3.0, 84, 8.0), {})
    app.db.insert_reading("nursery", "2026-09-01T02:00:00Z", _reading(6.5, 84, 8.0), {})
    app.db.insert_reading("nursery", "2026-09-01T01:30:00Z", _reading(0.1, 84, 8.0, ok=False), {})
    app.db.insert_manual("nursery", "2026-09-01T00:30:00Z", {"tan_mg_l": 0.2, "nitrite_mg_l": 0.1, "nitrate_mg_l": 10, "alkalinity_mg_l": 100})
    app.db.insert_manual("nursery", "2026-09-01T12:00:00Z", {"tan_mg_l": 1.2, "nitrate_mg_l": 40, "alkalinity_mg_l": 140})
    app.db.open_alert(
        tank_id="nursery", kind="dissolved_oxygen", severity="critical", message="low",
        value=3.0, unit="mg/L", opened_at="2026-09-01T01:00:00Z", delivery="{}",
    )
    app.db.open_alert(
        tank_id="nursery", kind="tan", severity="critical", message="high",
        value=1.2, unit="mg/L", opened_at="2026-09-01T12:00:00Z", delivery="{}",
    )
    app.db.open_alert(
        tank_id="nursery", kind="temperature", severity="warning", message="old",
        value=70, unit="F", opened_at="2026-08-01T00:00:00Z", delivery="{}",
    )


def _payload(**overrides):
    body = {
        "batch_id": "NCPP-20260901-nursery",
        "tank_id": "nursery",
        "stocking_date": "2026-09-01",
        "harvest_date": "2026-09-01",
        "pounds": 42.5,
        "notes": "first pull",
        "include_activity": False,
        "cluster": "devnet",
    }
    body.update(overrides)
    return body


def test_farm_dates_use_pacific_time():
    start, end = local_window("2026-09-01", "2026-09-01", "America/Los_Angeles")
    assert start == "2026-09-01T07:00:00Z"
    assert end == "2026-09-02T06:59:59Z"


def test_qr_symbol_is_stable_and_decodes():
    url = "http://127.0.0.1:8080/verify/NCPP-1"
    modules = qr_matrix(url)
    bits = "".join("1" if cell else "0" for row in modules for cell in row)
    assert hashlib.sha256(bits.encode()).hexdigest() == "11226012468fa0931a40ca8497e7cecd8afa1f26b4cb3f51006ff322c5b1c039"
    cv2 = pytest.importorskip("cv2")
    numpy = pytest.importorskip("numpy")
    quiet = 4
    size = len(modules) + quiet * 2
    image = numpy.full((size * 8, size * 8), 255, numpy.uint8)
    for row, line in enumerate(modules):
        for col, dark in enumerate(line):
            if dark:
                y = (row + quiet) * 8
                x = (col + quiet) * 8
                image[y : y + 8, x : x + 8] = 0
    text, _points, _straight = cv2.QRCodeDetector().detectAndDecode(image)
    assert text == url


def test_canonical_hash_is_stable_and_changes_with_the_record():
    record = {"schema": "ncpp.harvest.v1", "notes": "ok", "batch_id": "NCPP-1"}
    text = canonical_dumps(record)
    assert text == '{"batch_id":"NCPP-1","notes":"ok","schema":"ncpp.harvest.v1"}'
    assert canonical_dumps({"notes": "ok", "schema": "ncpp.harvest.v1", "batch_id": "NCPP-1"}) == text
    digest = sha256_text(text)
    assert digest == hashlib.sha256(text.encode()).hexdigest()
    assert sha256_text(text) == digest
    assert digest != sha256_text(canonical_dumps({**record, "notes": "changed"}))
    memo = memo_for("NCPP-1", digest, None)
    assert memo == f"NCPP-harvest:NCPP-1:{digest}:-"
    assert memo_for("NCPP-1", digest, "bafyCID").endswith(":bafyCID")


def test_record_built_from_simulated_logs(tmp_path):
    app = _hub(tmp_path)
    try:
        _seed(app)
        created = app.handle("POST", "/api/harvests", {}, json.dumps(_payload()).encode(), {})
        assert created.status == 201, created.body
        body = json.loads(created.body)
        record = body["record"]
        metrics = record["water_quality"]["metrics"]
        assert metrics["dissolved_oxygen_mg_l"]["min"] == "3.0000"
        assert metrics["dissolved_oxygen_mg_l"]["mean"] == "5.1667"
        assert metrics["dissolved_oxygen_mg_l"]["max"] == "6.5000"
        assert metrics["dissolved_oxygen_mg_l"]["n"] == 3
        assert metrics["temperature_f"]["mean"] == "84.0000"
        assert metrics["ph"]["mean"] == "8.0000"
        assert metrics["tan_mg_l"] == {"n": 2, "min": "0.2000", "mean": "0.7000", "max": "1.2000", "unit": "mg/L"}
        assert metrics["nitrite_mg_l"]["n"] == 1
        assert metrics["nitrate_mg_l"]["mean"] == "25.0000"
        assert metrics["alkalinity_mg_l"]["min"] == "100.0000"
        assert metrics["alkalinity_mg_l"]["max"] == "140.0000"
        quality = record["water_quality"]
        assert quality["alert_count"] == 2
        assert quality["time_in_range"]["fraction"] == "0.5000"
        assert quality["time_in_range"]["in_range_seconds"] == 3600
        assert quality["time_in_range"]["span_seconds"] == 7200
        assert record["activity"] is None
        assert record["volume_gal"] == "225.0000"
        assert record["pounds"] == "42.5000"
        assert body["sha256"] == sha256_text(body["canonical_json"])
        assert body["memo"].endswith(":-")
        assert body["ipfs_status"] == "skipped"
        assert "scripts/anchor_harvest.sh NCPP-20260901-nursery --hub http://127.0.0.1:8080" == body["cli_command"]
        assert body["cluster"] == "devnet"
        assert "devnet.solana.com" in body["rpc_url"]
    finally:
        app.close()


def test_activity_and_ipfs_pin_are_optional(tmp_path):
    calls = {}

    def transport(url, data, headers):
        calls["url"] = url
        calls["auth"] = headers["Authorization"]
        calls["body"] = data
        return 200, b'{"IpfsHash":"bafyharvestcid"}'

    app = _hub(
        tmp_path,
        ipfs={"provider": "pinata", "api_token": "test-token"},
        ipfs_transport=transport,
    )
    try:
        _seed(app)
        app.db.add_tracking_import(
            tank_id="nursery",
            imported_at="2026-09-01T03:00:00Z",
            filename="amadeus.csv",
            fps=30,
            mm_per_pixel=0.4,
            summary={
                "animals": 2,
                "mean_speed_mm_s": 50,
                "total_distance_mm": 90,
                "median_length_mm": 35,
            },
            samples=[{"frame": 1, "t_s": 0, "mean_speed_mm_s": 50, "cum_distance_mm": 90, "mean_nn_mm": 12, "crowded_fraction": 0.5, "mean_length_mm": 35}],
            csv_path="unused.csv",
        )
        created = app.handle("POST", "/api/harvests", {}, json.dumps(_payload(include_activity=True, batch_id="NCPP-act")).encode(), {})
        body = json.loads(created.body)
        assert body["record"]["activity"]["animals"] == 2
        assert body["record"]["activity"]["distance_mm"] == "90.0000"
        assert body["record"]["activity"]["mean_speed_mm_s"] == "50.0000"
        assert body["record"]["activity"]["mean_nearest_neighbor_mm"] == "12.0000"
        assert body["ipfs_cid"] == "bafyharvestcid"
        assert body["memo"].endswith(":bafyharvestcid")
        assert calls["auth"] == "Bearer test-token"
        assert b"ncpp.harvest.v1" in calls["body"]
    finally:
        app.close()


def test_mocked_solana_verification_and_secret_rejection(tmp_path):
    seen = {}

    def fetch(rpc, signature):
        seen["rpc"] = rpc
        seen["signature"] = signature
        row = app.db.get_harvest("NCPP-20260901-nursery")
        if signature == seen.get("bad"):
            return _tx("NCPP-harvest:other:abc:-")
        return _tx(row["memo"])

    app = _hub(tmp_path, solana_fetch=fetch)
    try:
        _seed(app)
        created = json.loads(_dispatch(app, "POST", "/api/harvests", _payload()).body)
        secret = _dispatch(
            app,
            "POST",
            "/api/harvests/" + created["batch_id"] + "/signature",
            {"signature": _signature(), "private_key": "nope"},
        )
        assert secret.status == 400
        assert b"private" in secret.body
        bad_sig = b58encode(b"\x33" * 64)
        seen["bad"] = bad_sig
        mismatch = _dispatch(
            app,
            "POST",
            "/api/harvests/" + created["batch_id"] + "/signature",
            {"signature": bad_sig, "source": "phantom"},
        )
        assert mismatch.status == 400
        assert app.db.get_harvest(created["batch_id"])["signature"] is None
        direct = verify_transaction(created["memo"], _tx("different"))
        assert direct["ok"] is False
        assert verify_transaction(created["memo"], _tx(created["memo"], failed=True))["ok"] is False
        good = _signature()
        stored = _dispatch(
            app,
            "POST",
            "/api/harvests/" + created["batch_id"] + "/signature",
            {"signature": good, "signer": PAYER, "cluster": "devnet", "source": "phantom"},
        )
        assert stored.status == 200, stored.body
        saved = json.loads(stored.body)
        assert saved["signature"] == good
        assert saved["signer"] == PAYER
        assert saved["anchor_source"] == "phantom"
        assert saved["solscan_url"].endswith("?cluster=devnet")
        assert seen["signature"] == good
        check = json.loads(_dispatch(app, "GET", "/api/harvests/" + created["batch_id"] + "/verification").body)
        assert check["ok"] is True
        assert check["memo_matches"] is True
        page = _dispatch(app, "GET", "/verify/" + created["batch_id"])
        assert page.status == 200
        html = page.body.decode()
        assert created["sha256"] in html
        assert "Verify this harvest" in html
        qr = _dispatch(app, "GET", "/verify/" + created["batch_id"] + "/qr.svg")
        assert qr.status == 200
        assert qr.body.startswith(b"<svg")
        coin = json.loads(_dispatch(app, "GET", "/api/shrimpcoin").body)
        assert coin["mint"] == MINT
        assert coin["update_authority"] == UPDATE_AUTHORITY
        assert coin["supply"] == SUPPLY
        assert coin["required_for_harvest_anchor"] is False
        assert coin["redeem_for_prawns"]["implemented"] is False
    finally:
        app.close()


def test_unsigned_memo_matches_the_browser_builder():
    memo = "NCPP-harvest:NCPP-1:" + ("ab" * 32) + ":-"
    raw = build_unsigned_memo_tx(PAYER, BLOCK, memo)
    assert raw[0] == 1
    assert raw[1:65] == bytes(64)
    assert memo.encode() in raw
    completed = subprocess.run(
        ["node", "tests/memo_tx.test.mjs", raw.hex(), PAYER, BLOCK, memo],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_cli_script_records_a_signature_without_a_keypair(tmp_path):
    signature = _signature()
    seen = {}

    def fetch(rpc, sent):
        seen["signature"] = sent
        row = app.db.get_harvest("NCPP-20260901-nursery")
        return _tx(row["memo"])

    app = _hub(tmp_path, solana_fetch=fetch)
    handle = serve(app, "127.0.0.1", 0)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "solana-args"
    fake = bin_dir / "solana"
    fake.write_text(
        textwrap.dedent(
            f"""\
            #!/bin/sh
            printf '%s\\n' "$*" >> "{log}"
            if [ "$1" = "address" ]; then
              echo "{PAYER}"
              exit 0
            fi
            echo '{{"signature":"{signature}"}}'
            """
        )
    )
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    try:
        _seed(app)
        post_json(
            f"http://127.0.0.1:{handle.port}/api/harvests",
            _payload(),
            timeout=5,
        )
        env = os.environ.copy()
        env["PATH"] = str(bin_dir) + os.pathsep + env.get("PATH", "")
        completed = subprocess.run(
            ["bash", str(ROOT / "scripts" / "anchor_harvest.sh"), "NCPP-20260901-nursery", "--hub", f"http://127.0.0.1:{handle.port}"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        assert completed.returncode == 0, completed.stderr + completed.stdout
        assert signature in completed.stdout
        args = log.read_text()
        assert "--keypair" not in args
        assert "--with-memo" in args
        assert "NCPP-harvest:" in args
        saved = get_json(f"http://127.0.0.1:{handle.port}/api/harvests/NCPP-20260901-nursery", timeout=5)
        assert saved["signature"] == signature
        assert saved["anchor_source"] == "solana-cli"
        assert saved["signer"] == PAYER
        assert seen["signature"] == signature
    finally:
        handle.close()
        app.close()
