#!/usr/bin/env bash
# Anchor one harvest memo with the local solana CLI.
# Uses the CLI's default keypair. The hub only receives the signature
# and the public address. This script never reads a key file into the hub.
set -euo pipefail

BATCH=""
HUB="${PRAWN_HUB:-http://127.0.0.1:8080}"

usage() {
  echo "Usage: scripts/anchor_harvest.sh <batchId> [--hub URL]" >&2
  echo "The Solana CLI default keypair signs the memo. No key is sent to the hub." >&2
}

if [[ $# -eq 0 || "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  usage
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --hub)
      HUB="${2:?--hub needs a URL}"
      shift 2
      ;;
    --)
      shift
      break
      ;;
    -*)
      echo "Unknown option $1" >&2
      usage
      exit 1
      ;;
    *)
      if [[ -n "$BATCH" ]]; then
        echo "Unexpected extra argument $1" >&2
        exit 1
      fi
      BATCH="$1"
      shift
      ;;
  esac
done

if [[ -z "$BATCH" ]]; then
  usage
  exit 1
fi
HUB="${HUB%/}"

if ! command -v solana >/dev/null 2>&1; then
  echo "The solana CLI is not on PATH. Install it and leave the keypair in its default config." >&2
  exit 1
fi

eval "$(HUB="$HUB" BATCH="$BATCH" python3 - <<'PY'
import json, os, shlex, urllib.error, urllib.request
hub = os.environ["HUB"].rstrip("/")
batch = os.environ["BATCH"]
url = hub + "/api/harvests/" + batch
try:
    with urllib.request.urlopen(url, timeout=20) as response:
        body = json.load(response)
except urllib.error.HTTPError as exc:
    raise SystemExit(f"hub returned HTTP {exc.code} for {url}") from exc
memo = str(body.get("memo") or "")
if not memo.startswith("NCPP-harvest:"):
    raise SystemExit("refusing to sign a memo that is not an NCPP harvest")
print("MEMO=" + shlex.quote(memo))
print("RPC=" + shlex.quote(str(body["rpc_url"])))
print("CLUSTER=" + shlex.quote(str(body["cluster"])))
print("EXISTING=" + shlex.quote(body.get("signature") or ""))
PY
)"

if [[ -n "${EXISTING}" ]]; then
  echo "Already anchored: ${EXISTING}"
  exit 0
fi

PAYER="$(solana address)"
echo "Signing harvest ${BATCH} with ${PAYER} on ${CLUSTER}."
echo "The key stays in the Solana CLI config. Only the signature is posted back."

RESULT="$(solana --url "$RPC" --output json transfer --with-memo "$MEMO" "$PAYER" 0.000000001)"

SIG="$(python3 -c 'import json,sys
text = sys.stdin.read()
start = text.find("{")
end = text.rfind("}")
if start >= 0 and end > start:
    print(json.loads(text[start:end + 1])["signature"])
    raise SystemExit(0)
for line in text.splitlines():
    if "Signature" in line and ":" in line:
        print(line.split(":", 1)[1].strip())
        raise SystemExit(0)
raise SystemExit("solana did not print a signature")' <<<"$RESULT")"

HUB="$HUB" BATCH="$BATCH" SIG="$SIG" PAYER="$PAYER" CLUSTER="$CLUSTER" python3 - <<'PY'
import json, os, urllib.request
payload = json.dumps({
    "signature": os.environ["SIG"],
    "signer": os.environ["PAYER"],
    "cluster": os.environ["CLUSTER"],
    "source": "solana-cli",
}).encode("utf-8")
request = urllib.request.Request(
    os.environ["HUB"].rstrip("/") + "/api/harvests/" + os.environ["BATCH"] + "/signature",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request, timeout=40) as response:
    body = json.load(response)
print(body["signature"])
PY
