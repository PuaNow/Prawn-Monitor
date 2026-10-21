"""Public read-only page for one harvest batch."""

from __future__ import annotations

import html
import json
from typing import Any


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _metric_rows(record: dict[str, Any]) -> str:
    labels = {
        "dissolved_oxygen_mg_l": "Dissolved oxygen",
        "temperature_f": "Temperature",
        "ph": "pH",
        "tan_mg_l": "TAN",
        "nitrite_mg_l": "Nitrite",
        "nitrate_mg_l": "Nitrate",
        "alkalinity_mg_l": "Alkalinity",
    }
    metrics = (record.get("water_quality") or {}).get("metrics") or {}
    rows = []
    for key, label in labels.items():
        metric = metrics.get(key)
        if not metric:
            rows.append(f"<tr><td>{label}</td><td colspan='4'>No samples in this period</td></tr>")
            continue
        rows.append(
            "<tr>"
            f"<td>{label}</td>"
            f"<td>{_esc(metric['min'])}</td>"
            f"<td>{_esc(metric['mean'])}</td>"
            f"<td>{_esc(metric['max'])}</td>"
            f"<td>{_esc(metric['unit'])} · n={_esc(metric['n'])}</td>"
            "</tr>"
        )
    return "".join(rows)


def _activity_block(record: dict[str, Any]) -> str:
    activity = record.get("activity")
    if not activity:
        return "<p class='hint'>AMADEUS activity was not included in this record.</p>"
    if not activity.get("imports"):
        return "<p class='hint'>No AMADEUS imports fell in this stocking-to-harvest window.</p>"
    return (
        "<ul>"
        f"<li>Imports {_esc(activity.get('imports'))}, animals {_esc(activity.get('animals'))}</li>"
        f"<li>Mean speed {_esc(activity.get('mean_speed_mm_s'))} mm/s</li>"
        f"<li>Distance {_esc(activity.get('distance_mm'))} mm</li>"
        f"<li>Mean nearest neighbor {_esc(activity.get('mean_nearest_neighbor_mm'))} mm</li>"
        f"<li>Approximate median length {_esc(activity.get('median_length_mm'))} mm</li>"
        "</ul>"
    )


def render_verify_page(batch: dict[str, Any], verify_url: str) -> str:
    record = batch["record"]
    quality = record.get("water_quality") or {}
    span = quality.get("time_in_range") or {}
    fraction = span.get("fraction") or "0.0000"
    try:
        percent = f"{float(fraction) * 100:.1f}%"
    except ValueError:
        percent = fraction
    signature = batch.get("signature")
    if signature:
        banner = "Anchored on Solana. This page recomputes the hash and checks the memo."
        banner_class = "pending"
    else:
        banner = "Not anchored yet. The record and hash are saved. A wallet still has to sign the memo."
        banner_class = "pending"
    solscan = ""
    if batch.get("solscan_url"):
        solscan = f"<p><a href='{_esc(batch['solscan_url'])}'>View the transaction on Solscan</a></p>"
    ipfs = ""
    if batch.get("ipfs_cid"):
        ipfs = (
            "<p>IPFS CID <code>"
            + _esc(batch["ipfs_cid"])
            + "</code>. The memo commits to this CID. "
            + f"<a href='https://ipfs.io/ipfs/{_esc(batch['ipfs_cid'])}'>Public gateway</a></p>"
        )
    payload = json.dumps(batch, ensure_ascii=False).replace("<", "\\u003c")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Verify {_esc(batch['batch_id'])}</title>
<style>
  body {{ margin: 0; font-family: "Segoe UI", "Helvetica Neue", sans-serif; background: #efe6d6; color: #1c1915; }}
  main {{ max-width: 760px; margin: 0 auto; padding: 16px 16px 40px; }}
  h1 {{ font-size: 1.45rem; margin: 0 0 4px; }}
  .sub, .hint {{ color: #6d645b; }}
  .card {{ background: #fffdf8; border: 1px solid #ddcfc0; border-radius: 14px; padding: 12px; margin-top: 12px; }}
  .banner {{ padding: 10px 12px; border-radius: 10px; font-weight: 650; }}
  .banner.pending {{ background: #f8ecd4; color: #8a5a00; }}
  .banner.ok {{ background: #e5f0dc; color: #3f6b2a; }}
  .banner.bad {{ background: #f8e4ea; color: #8f1d3a; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 0.92rem; }}
  th, td {{ text-align: left; padding: 6px 4px; border-bottom: 1px solid #ddcfc0; vertical-align: top; }}
  code, pre {{ word-break: break-all; white-space: pre-wrap; font-size: 0.82rem; }}
  img.qr {{ width: min(280px, 100%); background: white; border-radius: 12px; }}
  a {{ color: #0c5c5c; }}
</style>
</head>
<body>
<main>
  <h1>Verify this harvest</h1>
  <p class="sub">{_esc(record.get('site'))} · {_esc(record.get('place'))}</p>
  <div id="banner" class="banner {banner_class}">{banner}</div>
  <section class="card">
    <h2>{_esc(batch['batch_id'])}</h2>
    <p>{_esc(record.get('tank_name'))} · {_esc(record.get('species'))} · {_esc(record.get('volume_gal') or '—')} gal</p>
    <p>Stocked {_esc(record.get('stocking_date'))} · harvested {_esc(record.get('harvest_date'))} · {_esc(record.get('pounds'))} lb</p>
    <p>{_esc(record.get('notes') or '')}</p>
    <p>Alerts in this period: {_esc(quality.get('alert_count'))}. Time in range for dissolved oxygen, temperature, and pH: {percent} ({_esc(span.get('in_range_seconds'))} of {_esc(span.get('span_seconds'))} seconds).</p>
  </section>
  <section class="card">
    <h2>Water quality</h2>
    <table>
      <thead><tr><th>Measure</th><th>Min</th><th>Mean</th><th>Max</th><th></th></tr></thead>
      <tbody>{_metric_rows(record)}</tbody>
    </table>
  </section>
  <section class="card">
    <h2>Activity</h2>
    {_activity_block(record)}
  </section>
  <section class="card">
    <h2>Hash</h2>
    <p>SHA-256 of the canonical JSON:</p>
    <p><code id="stored-hash">{_esc(batch['sha256'])}</code></p>
    <p id="hash-check" class="hint">Recomputing the hash in this browser…</p>
    <details>
      <summary>Canonical JSON</summary>
      <pre id="canonical">{_esc(batch['canonical_json'])}</pre>
    </details>
  </section>
  <section class="card">
    <h2>Solana memo</h2>
    <p><code>{_esc(batch['memo'])}</code></p>
    <p>Cluster: {_esc(batch['cluster'])}. Signer: {_esc(batch.get('signer') or '—')}. Signature: <code>{_esc(signature or '—')}</code></p>
    {solscan}
    {ipfs}
    <p id="chain-check" class="hint">Checking the transaction…</p>
    <p id="browser-check" class="hint"></p>
  </section>
  <section class="card">
    <h2>Packaging QR</h2>
    <p class="hint">This code opens this page: {_esc(verify_url)}</p>
    <img class="qr" alt="QR code for this verify page" src="/verify/{_esc(batch['batch_id'])}/qr.svg">
    <p class="hint">ShrimpCoin (SHRMPCOIN) is not required to anchor or to verify a harvest. A later phase could redeem the token for prawns. This page does not move tokens.</p>
  </section>
</main>
<script id="harvest-data" type="application/json">{payload}</script>
<script src="/static/memo_tx.js"></script>
<script>
const data = JSON.parse(document.getElementById("harvest-data").textContent);
const banner = document.getElementById("banner");
const hashCheck = document.getElementById("hash-check");
function paint(node, ok, text) {{
  node.textContent = text;
  node.className = ok ? "banner ok" : "banner bad";
}}
if (!window.prawnMemoTx) {{
  hashCheck.textContent = "The hash checker did not load.";
}} else {{
  const again = window.prawnMemoTx.sha256Hex(data.canonical_json);
  if (again === data.sha256) {{
    paint(hashCheck, true, "Hash recomputed in this browser matches the record.");
  }} else {{
    paint(hashCheck, false, "Hash recomputed in this browser does not match. Treat this record as altered.");
    banner.className = "banner bad";
    banner.textContent = "The hash does not match the record on this page.";
  }}
}}
fetch("/api/harvests/" + encodeURIComponent(data.batch_id) + "/verification")
  .then((response) => response.json())
  .then((check) => {{
    const chain = document.getElementById("chain-check");
    if (!data.signature) {{
      chain.textContent = "No Solana signature is stored yet.";
      return;
    }}
    paint(chain, !!check.ok, check.reason || "Hub finished the Solana check.");
    if (check.ok && hashCheck.className.indexOf("bad") < 0) {{
      banner.className = "banner ok";
      banner.textContent = "Verified. The hash matches the record, and the Solana memo matches the hash.";
    }} else if (!check.ok) {{
      banner.className = "banner bad";
      banner.textContent = check.reason || "The Solana transaction does not match this record.";
    }}
  }})
  .catch(() => {{
    document.getElementById("chain-check").textContent = "The hub could not be asked to recheck Solana.";
  }});
if (data.signature && window.prawnMemoTx) {{
  fetch(data.rpc_url, {{
    method: "POST",
    headers: {{ "Content-Type": "application/json" }},
    body: JSON.stringify({{
      jsonrpc: "2.0",
      id: 1,
      method: "getTransaction",
      params: [data.signature, {{ encoding: "jsonParsed", commitment: "confirmed", maxSupportedTransactionVersion: 0 }}],
    }}),
  }})
    .then((response) => response.json())
    .then((body) => {{
      const tx = body && body.result;
      const memos = tx ? window.prawnMemoTx.memosFromTransaction(tx) : [];
      const node = document.getElementById("browser-check");
      if (!tx) {{
        node.textContent = "This browser could not read the transaction from the Solana RPC. The hub check above still applies.";
        return;
      }}
      if (memos.indexOf(data.memo) >= 0) {{
        paint(node, true, "This browser read the Solana transaction and the memo matches.");
      }} else {{
        paint(node, false, "This browser read the Solana transaction and the memo does not match.");
      }}
    }})
    .catch(() => {{
      document.getElementById("browser-check").textContent = "This browser could not reach the Solana RPC. The hub check above still applies.";
    }});
}}
</script>
</body>
</html>
"""
