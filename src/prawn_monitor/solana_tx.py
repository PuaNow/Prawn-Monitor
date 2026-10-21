"""Unsigned legacy transaction bytes for an SPL Memo instruction.

The browser wallet signs these bytes. Nothing in this module holds a key.
"""

from __future__ import annotations

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MEMO_PROGRAM_ID = "MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"
MEMO_LIMIT = 566


def b58decode(text: str) -> bytes:
    if not text:
        raise ValueError("base58 value is empty")
    number = 0
    for char in text:
        index = _ALPHABET.find(char)
        if index < 0:
            raise ValueError("invalid base58")
        number = number * 58 + index
    pad = 0
    for char in text:
        if char != "1":
            break
        pad += 1
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return b"\x00" * pad + raw


def b58encode(raw: bytes) -> str:
    pad = 0
    for byte in raw:
        if byte != 0:
            break
        pad += 1
    number = int.from_bytes(raw, "big")
    chars: list[str] = []
    while number:
        number, rem = divmod(number, 58)
        chars.append(_ALPHABET[rem])
    return "1" * pad + "".join(reversed(chars))


def compact_u16(value: int) -> bytes:
    if value < 0 or value > 0xFFFF:
        raise ValueError("compact-u16 out of range")
    if value < 0x80:
        return bytes((value,))
    if value < 0x4000:
        return bytes((value & 0x7F | 0x80, value >> 7))
    return bytes((value & 0x7F | 0x80, (value >> 7) & 0x7F | 0x80, value >> 14))


def _pubkey(text: str, label: str) -> bytes:
    raw = b58decode(text)
    if len(raw) != 32:
        raise ValueError(f"{label} must be 32 bytes")
    return raw


def build_unsigned_memo_tx(fee_payer: str, blockhash: str, memo: str) -> bytes:
    """Legacy transaction with one blank signature slot and one memo instruction.

    The fee payer is the only signer. The memo program is read-only. The
    signer is included in the instruction accounts so the memo is tied to
    that wallet. Phantom fills the blank signature when the user approves.
    """
    data = memo.encode("utf-8")
    if not data:
        raise ValueError("memo is empty")
    if len(data) > MEMO_LIMIT:
        raise ValueError("memo is longer than the SPL memo limit")
    payer = _pubkey(fee_payer, "fee payer")
    recent = _pubkey(blockhash, "blockhash")
    program = _pubkey(MEMO_PROGRAM_ID, "memo program")
    message = bytearray()
    message.append(1)  # required signatures
    message.append(0)  # read-only signed accounts
    message.append(1)  # read-only unsigned accounts
    message += compact_u16(2)
    message += payer
    message += program
    message += recent
    message += compact_u16(1)  # one instruction
    message.append(1)  # program id index
    message += compact_u16(1)  # one account index
    message.append(0)  # fee payer
    message += compact_u16(len(data))
    message += data
    return compact_u16(1) + bytes(64) + bytes(message)
