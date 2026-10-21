# Harvest traceability

A harvest batch is a signed summary of one tank from stocking day through harvest day. The hub builds it from logs it already has. A wallet you control anchors the hash on Solana. Buyers open a verify page, from a QR code on the label or the invoice, and check the hash against the transaction.

ShrimpCoin (`SHRMPCOIN`) is the farm's Token-2022 mint. It is not part of this step. Anchoring a memo does not need the token, the mint authority, or the update authority. Any everyday Phantom wallet can sign. The Mac that created the mint can also sign, through the Solana CLI, if that keypair is not imported into Phantom.

## What goes in a batch

- Batch ID, tank, stocking date, harvest date, pounds, and notes
- Min, mean, and max of dissolved oxygen, temperature, pH, TAN, nitrite, nitrate, and alkalinity
- Alert count for that tank and window
- Time in range: the share of elapsed time the previous dissolved-oxygen, temperature, and pH sample was inside the warning band
- Optional AMADEUS stats when you leave that box checked: animals, speed, distance, nearest-neighbor crowding, and approximate body length

Dates are farm-local (`America/Los_Angeles` unless the hub config says otherwise), from the start of stocking day through the end of harvest day.

The canonical JSON uses sorted keys and fixed 4-decimal numbers. The SHA-256 is of those exact UTF-8 bytes. The signature is stored beside the record, not inside it, so anchoring does not change the hash.

## IPFS

`ipfs.api_token` in the hub config is empty by default, and then the hub skips pinning. With a token it uploads the canonical JSON file.

Pinata:

```json
"ipfs": {
  "provider": "pinata",
  "api_url": "https://api.pinata.cloud/pinning/pinFileToIPFS",
  "api_token": "the pinata jwt"
}
```

A local Kubo node is the same shape with `"provider": "kubo"` and `"api_url": "http://127.0.0.1:5001/api/v0/add"`. Put any non-empty token in `api_token` so the hub knows you meant to pin. The CID in the memo is the CID of those bytes.

## Solana memo

The memo program is `MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr`. The text is:

```text
NCPP-harvest:<batchId>:<sha256>:<ipfs cid>
```

A missing CID is a single `-`. A memo is public, cheap, and visible on Solscan. A custom program would need a deploy and an upgrade key. The memo does not move ShrimpCoin.

Devnet is the default (`solana.cluster` and `solana.rpc_url` in the hub config). Choose Mainnet on the harvest form only when you mean to spend real SOL. The hub stores the cluster with the batch and will not accept a signature from the other cluster.

### Cost

A memo transaction has one signature. The network fee is 5000 lamports, which is 0.000005 SOL. That is a fraction of a cent at ordinary SOL prices. Devnet SOL comes from a faucet and costs nothing. The CLI path also moves 1 lamport to your own address so the memo has a normal transfer to attach to. That lamport comes back to the same account. The fee is the only net cost.

The hub does not pay the fee and does not hold a key.

### Phantom

Phantom is the signing path on the dashboard.

- On a computer, the Phantom extension injects a provider. **Sign with Phantom** connects and sends the memo. Phantom shows the transaction. You approve it. Nothing is signed if you reject it.
- On a phone, **Open in Phantom on this phone** uses Phantom's browse link, `https://phantom.com/ul/browse/<this page>?ref=<this hub>`. Phantom opens the dashboard in its in-app browser, where the same provider exists, and **Sign with Phantom** asks for the same approval.
- There is no auto-approve path. The page only calls `connect` and `signAndSendTransaction` on a provider with `isPhantom` set.

After Phantom returns a signature, the dashboard posts that signature to the hub. The hub reads the transaction from the cluster RPC, checks that the memo matches, and only then stores it.

### Solana CLI

The update authority for ShrimpCoin may live in `~/.config/solana/id.json` from the Solana CLI, not in Phantom. That is fine. From a checkout of this repo, on that Mac:

```bash
scripts/anchor_harvest.sh NCPP-20261021-nursery --hub http://192.168.1.10:8080
```

The Harvest card shows this command with the batch id and the hub URL filled in. The script:

1. Reads the memo and RPC URL from the hub.
2. Runs `solana address` and `solana transfer` with `--with-memo`, using the CLI's configured default keypair.
3. Posts the signature, the public address, and `source: solana-cli` back to the hub.

It never prints the keypair file and never sends the file to the hub. Do not pass a seed phrase as an argument. If `solana config` points at a key you did not mean to use, change that with the Solana CLI before running the script. Passing `--url` from the batch keeps a devnet batch off mainnet even if your CLI default is mainnet.

Anchoring does not require this key to be the token's update authority. A different Phantom wallet is an equally valid signer. The update authority only matters for ShrimpCoin metadata.

## Verify page

`/verify/<batchId>` is public and read-only. It shows the record, the canonical JSON, and the SHA-256. The page hashes those bytes again in the browser (this works on plain HTTP, which the farm LAN uses). It asks the hub to fetch the Solana transaction, and it also tries the cluster RPC itself. Both must see the same memo. The Solscan link is `https://solscan.io/tx/<signature>?cluster=devnet` on devnet, and the mainnet link has no cluster query.

`/verify/<batchId>/qr.svg` is the QR code for that page. Point `public_url` in the hub config at the address you want printed on packaging. A phone on the farm Wi-Fi can open a LAN URL. The symbol holds about 100 characters; keep `public_url` short enough that the full verify link fits.

## ShrimpCoin, later

| | |
| --- | --- |
| Symbol | SHRMPCOIN |
| Mint | `mntsByXQ5QyqMkknKzX1w4CEmErG3RjQbB4zuC6cwKP` |
| Program | Token-2022 |
| Supply | 100,000, fixed |
| Mint authority | revoked |
| Freeze authority | revoked |
| Update authority | `bosVqs1t59kXpyuwLFYZ1qf61qWygXEaZYSLGjf1TF4` |

`GET /api/shrimpcoin` returns those facts and `"redeem_for_prawns": {"implemented": false}`. A later phase could redeem SHRMPCOIN for prawns. This hub does not transfer, burn, or escrow the token.
