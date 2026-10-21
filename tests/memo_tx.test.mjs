import fs from "node:fs";
import vm from "node:vm";
import { TextEncoder } from "node:util";

const hex = process.argv[2];
const payer = process.argv[3];
const blockhash = process.argv[4];
const memo = process.argv[5];
if (!globalThis.TextEncoder) globalThis.TextEncoder = TextEncoder;
const code = fs.readFileSync(new URL("../src/prawn_monitor/static/memo_tx.js", import.meta.url), "utf8");
vm.runInThisContext(code);
const api = globalThis.prawnMemoTx;
if (!api) throw new Error("prawnMemoTx was not exported");

if (api.sha256Hex("abc") !== "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad") {
  throw new Error("sha256 of abc did not match");
}
const bytes = api.buildUnsignedMemoTx(payer, blockhash, memo);
const got = Buffer.from(bytes).toString("hex");
if (got !== hex) {
  throw new Error("unsigned memo transaction does not match Python");
}
if (!got.includes(Buffer.from(memo).toString("hex"))) {
  throw new Error("memo bytes missing from the transaction");
}

const page = "http://192.168.1.10:8080/#harvest";
const origin = "http://192.168.1.10:8080";
const browse = api.phantomBrowseUrl(page, origin);
if (!browse.startsWith("https://phantom.com/ul/browse/")) throw new Error("browse link origin");
if (!browse.includes(encodeURIComponent(page))) throw new Error("browse link target");
if (!browse.includes("ref=" + encodeURIComponent(origin))) throw new Error("browse link ref");

const provider = { isPhantom: true };
if (api.planPhantomAction({ provider, userAgent: "Mozilla iPhone", pageUrl: page, origin }).type !== "sign") {
  throw new Error("injected Phantom should sign, including inside the phone browser");
}
const mobile = api.planPhantomAction({ provider: null, userAgent: "Mozilla/5.0 (iPhone)", pageUrl: page, origin });
if (mobile.type !== "browse" || mobile.url !== browse) throw new Error("phone without Phantom should open the browse link");
if (api.planPhantomAction({ provider: null, userAgent: "Mozilla/5.0 (X11)", pageUrl: page, origin }).type !== "missing") {
  throw new Error("desktop without Phantom should not pretend to sign");
}

let captured = null;
const wallet = {
  isPhantom: true,
  connect: async () => ({ publicKey: { toString: () => payer } }),
  signAndSendTransaction: async (tx) => {
    captured = Buffer.from(tx.serialize());
    return { signature: "5".repeat(88) };
  },
};
const signed = await api.signMemoWithPhantom(wallet, {
  rpcUrl: "http://rpc.test",
  memo,
  fetchImpl: async () => ({
    json: async () => ({ result: { value: { blockhash } } }),
  }),
});
if (signed.signature !== "5".repeat(88) || signed.signer !== payer) throw new Error("signature was not returned");
if (!captured.equals(Buffer.from(bytes))) throw new Error("Phantom was not given the memo transaction");

let refused = false;
try {
  await api.signMemoWithPhantom({ isPhantom: false, signAndSendTransaction: async () => ({ signature: "nope" }) }, {
    rpcUrl: "http://rpc.test",
    memo,
    fetchImpl: async () => ({ json: async () => ({ result: { value: { blockhash } } }) }),
  });
} catch (error) {
  refused = String(error.message).includes("Phantom");
}
if (!refused) throw new Error("a non-Phantom provider must be refused");

const tx = {
  meta: { err: null, logMessages: [`Program log: Memo (len ${memo.length}): "${memo}"`] },
  transaction: { message: { instructions: [{ program: "spl-memo", parsed: memo }] } },
};
const memos = api.memosFromTransaction(tx);
if (memos.length !== 1 || memos[0] !== memo) throw new Error("memo parse failed");
console.log("ok");
