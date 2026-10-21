/* Phantom signing helpers for a harvest memo.
   The hub never signs. This file builds an unsigned memo transaction and
   asks an injected Phantom provider to sign and send it. */
(function (root) {
  const ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
  const MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr";

  function b58decode(text) {
    if (!text) throw new Error("base58 value is empty");
    let zeros = 0;
    while (text[zeros] === "1") zeros += 1;
    let value = 0n;
    for (const char of text) {
      const index = ALPHABET.indexOf(char);
      if (index < 0) throw new Error("invalid base58");
      value = value * 58n + BigInt(index);
    }
    let hex = value.toString(16);
    if (hex.length % 2) hex = "0" + hex;
    if (value === 0n) hex = "";
    const body = hex === "" ? new Uint8Array(0) : Uint8Array.from(hex.match(/../g).map((byte) => parseInt(byte, 16)));
    const out = new Uint8Array(zeros + body.length);
    out.set(body, zeros);
    return out;
  }

  function compactU16(value) {
    if (value < 0x80) return Uint8Array.of(value);
    if (value < 0x4000) return Uint8Array.of((value & 0x7f) | 0x80, value >> 7);
    return Uint8Array.of((value & 0x7f) | 0x80, ((value >> 7) & 0x7f) | 0x80, value >> 14);
  }

  function concat(parts) {
    const length = parts.reduce((sum, part) => sum + part.length, 0);
    const out = new Uint8Array(length);
    let offset = 0;
    for (const part of parts) {
      out.set(part, offset);
      offset += part.length;
    }
    return out;
  }

  function pubkey(text, label) {
    const raw = b58decode(text);
    if (raw.length !== 32) throw new Error(label + " must be 32 bytes");
    return raw;
  }

  function buildUnsignedMemoTx(feePayer, blockhash, memo) {
    const data = new TextEncoder().encode(memo);
    if (!data.length) throw new Error("memo is empty");
    if (data.length > 566) throw new Error("memo is longer than the SPL memo limit");
    const message = concat([
      Uint8Array.of(1, 0, 1),
      compactU16(2),
      pubkey(feePayer, "fee payer"),
      pubkey(MEMO_PROGRAM_ID, "memo program"),
      pubkey(blockhash, "blockhash"),
      compactU16(1),
      Uint8Array.of(1),
      compactU16(1),
      Uint8Array.of(0),
      compactU16(data.length),
      data,
    ]);
    return concat([compactU16(1), new Uint8Array(64), message]);
  }

  function pubkeyLike(text) {
    const bytes = pubkey(text, "public key");
    return {
      toBase58: () => text,
      toString: () => text,
      toBytes: () => bytes,
      toBuffer: () => bytes,
    };
  }

  function buildMemoTransaction(feePayer, blockhash, memo) {
    const bytes = buildUnsignedMemoTx(feePayer, blockhash, memo);
    return {
      recentBlockhash: blockhash,
      feePayer: pubkeyLike(feePayer),
      serialize: function () {
        return bytes;
      },
    };
  }

  function phantomProvider(scope) {
    const target = scope || root;
    const injected = target.phantom && target.phantom.solana;
    if (injected && injected.isPhantom) return injected;
    if (target.solana && target.solana.isPhantom) return target.solana;
    return null;
  }

  function phantomBrowseUrl(pageUrl, origin) {
    return "https://phantom.com/ul/browse/" + encodeURIComponent(pageUrl) + "?ref=" + encodeURIComponent(origin);
  }

  function planPhantomAction(options) {
    const provider = options.provider;
    const pageUrl = options.pageUrl;
    const origin = options.origin;
    const url = phantomBrowseUrl(pageUrl, origin);
    if (provider && provider.isPhantom === true) return { type: "sign", url: url };
    const mobile = /Android|iPhone|iPad|iPod/i.test(options.userAgent || "");
    if (mobile) return { type: "browse", url: url };
    return { type: "missing", url: url };
  }

  async function latestBlockhash(rpcUrl, fetchImpl) {
    const response = await fetchImpl(rpcUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        jsonrpc: "2.0",
        id: 1,
        method: "getLatestBlockhash",
        params: [{ commitment: "confirmed" }],
      }),
    });
    const body = await response.json();
    const hash = body && body.result && body.result.value && body.result.value.blockhash;
    if (!hash) throw new Error("Could not fetch a recent blockhash from " + rpcUrl);
    return hash;
  }

  function signatureOf(result) {
    if (!result) return "";
    if (typeof result === "string") return result;
    if (typeof result.signature === "string") return result.signature;
    if (result.signature && result.signature.toString) return result.signature.toString();
    return "";
  }

  async function signMemoWithPhantom(provider, options) {
    if (!provider || provider.isPhantom !== true) {
      throw new Error("Phantom is not available in this browser. The hub cannot sign.");
    }
    const fetchImpl = options.fetchImpl || root.fetch;
    const connected = await provider.connect();
    const key = (connected && connected.publicKey) || provider.publicKey;
    if (!key || !key.toString) throw new Error("Phantom did not return a public address");
    const feePayer = key.toString();
    const blockhash = await latestBlockhash(options.rpcUrl, fetchImpl);
    const transaction = buildMemoTransaction(feePayer, blockhash, options.memo);
    const sent = await provider.signAndSendTransaction(transaction);
    const signature = signatureOf(sent);
    if (!signature) throw new Error("Phantom did not return a signature");
    return { signature: signature, signer: feePayer };
  }

  const K = new Uint32Array([
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
  ]);

  function rotr(value, bits) {
    return (value >>> bits) | (value << (32 - bits));
  }

  function sha256Hex(input) {
    const msg = typeof input === "string" ? new TextEncoder().encode(input) : input;
    const bitLen = msg.length * 8;
    const padded = new Uint8Array(((msg.length + 9 + 63) >> 6) << 6);
    padded.set(msg);
    padded[msg.length] = 0x80;
    const view = new DataView(padded.buffer);
    view.setUint32(padded.length - 8, Math.floor(bitLen / 0x100000000));
    view.setUint32(padded.length - 4, bitLen >>> 0);
    let h0 = 0x6a09e667, h1 = 0xbb67ae85, h2 = 0x3c6ef372, h3 = 0xa54ff53a;
    let h4 = 0x510e527f, h5 = 0x9b05688c, h6 = 0x1f83d9ab, h7 = 0x5be0cd19;
    const w = new Uint32Array(64);
    for (let offset = 0; offset < padded.length; offset += 64) {
      for (let i = 0; i < 16; i++) w[i] = view.getUint32(offset + i * 4);
      for (let i = 16; i < 64; i++) {
        const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
        const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let a = h0, b = h1, c = h2, d = h3, e = h4, f = h5, g = h6, h = h7;
      for (let i = 0; i < 64; i++) {
        const s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
        const ch = (e & f) ^ (~e & g);
        const t1 = (h + s1 + ch + K[i] + w[i]) >>> 0;
        const s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
        const maj = (a & b) ^ (a & c) ^ (b & c);
        const t2 = (s0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0;
      h4 = (h4 + e) >>> 0; h5 = (h5 + f) >>> 0; h6 = (h6 + g) >>> 0; h7 = (h7 + h) >>> 0;
    }
    return [h0, h1, h2, h3, h4, h5, h6, h7].map((part) => part.toString(16).padStart(8, "0")).join("");
  }

  function memosFromTransaction(tx) {
    const found = [];
    function add(value) {
      if (value && found.indexOf(value) < 0) found.push(value);
    }
    const message = tx && tx.transaction && tx.transaction.message;
    const groups = [];
    if (message && message.instructions) groups.push(message.instructions);
    const inner = tx && tx.meta && tx.meta.innerInstructions;
    if (inner) {
      for (const group of inner) {
        if (group.instructions) groups.push(group.instructions);
      }
    }
    for (const list of groups) {
      for (const instruction of list) {
        if (instruction.program === "spl-memo" || instruction.programId === MEMO_PROGRAM_ID) {
          if (typeof instruction.parsed === "string") add(instruction.parsed);
          else if (instruction.parsed && typeof instruction.parsed.memo === "string") add(instruction.parsed.memo);
        }
      }
    }
    const logs = (tx && tx.meta && tx.meta.logMessages) || [];
    for (const line of logs) {
      const match = /^Program log: Memo \(len \d+\): "(.*)"$/.exec(String(line));
      if (match) add(match[1]);
    }
    return found;
  }

  root.prawnMemoTx = {
    MEMO_PROGRAM_ID: MEMO_PROGRAM_ID,
    b58decode: b58decode,
    buildUnsignedMemoTx: buildUnsignedMemoTx,
    buildMemoTransaction: buildMemoTransaction,
    phantomProvider: phantomProvider,
    phantomBrowseUrl: phantomBrowseUrl,
    planPhantomAction: planPhantomAction,
    signMemoWithPhantom: signMemoWithPhantom,
    sha256Hex: sha256Hex,
    memosFromTransaction: memosFromTransaction,
  };
})(typeof globalThis !== "undefined" ? globalThis : this);
