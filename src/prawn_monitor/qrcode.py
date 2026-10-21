"""QR images for harvest verify URLs.

Byte mode, error correction M, versions 1 through 6, mask 0. Mask 0 is
declared in the format bits, so scanners accept it. Version 6 holds a
verify URL of about 100 characters, which covers a LAN address and batch id.
"""

from __future__ import annotations

_EXP = [0] * 512
_LOG = [0] * 256


def _build_gf() -> None:
    value = 1
    for index in range(255):
        _EXP[index] = value
        _LOG[value] = index
        value <<= 1
        if value & 0x100:
            value ^= 0x11D
    for index in range(255, 512):
        _EXP[index] = _EXP[index - 255]


_build_gf()


def _mul(left: int, right: int) -> int:
    if left == 0 or right == 0:
        return 0
    return _EXP[_LOG[left] + _LOG[right]]


def _generator(length: int) -> list[int]:
    poly = [1]
    for index in range(length):
        poly = _mul_poly(poly, [1, _EXP[index]])
    return poly


def _mul_poly(left: list[int], right: list[int]) -> list[int]:
    out = [0] * (len(left) + len(right) - 1)
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            out[i + j] ^= _mul(a, b)
    return out


def _rs_block(data: list[int], ecc_len: int) -> list[int]:
    gen = _generator(ecc_len)
    window = list(data) + [0] * ecc_len
    for i in range(len(data)):
        coef = window[i]
        if coef == 0:
            continue
        for j in range(ecc_len + 1):
            window[i + j] ^= _mul(gen[j], coef)
    return window[-ecc_len:]


# ECC M: error symbols per block, (blocks, data bytes per block) groups, remainder bits, alignment centers.
_VERSIONS = {
    1: (10, ((1, 16),), 0, ()),
    2: (16, ((1, 28),), 7, (6, 18)),
    3: (26, ((1, 44),), 7, (6, 22)),
    4: (18, ((2, 32),), 7, (6, 26)),
    5: (24, ((2, 43),), 7, (6, 30)),
    6: (16, ((4, 27),), 7, (6, 34)),
}


def _bits_of(value: int, length: int) -> list[int]:
    return [(value >> shift) & 1 for shift in range(length - 1, -1, -1)]


def _data_codewords(payload: bytes, capacity: int) -> list[int]:
    bits: list[int] = []
    bits += _bits_of(0b0100, 4)
    bits += _bits_of(len(payload), 8)
    for byte in payload:
        bits += _bits_of(byte, 8)
    spare = capacity * 8 - len(bits)
    bits += [0] * min(4, max(0, spare))
    while len(bits) % 8:
        bits.append(0)
    toggle = 0
    while len(bits) + 8 <= capacity * 8:
        bits += _bits_of(0xEC if toggle % 2 == 0 else 0x11, 8)
        toggle += 1
    if len(bits) != capacity * 8:
        raise ValueError("QR payload does not fill the chosen version")
    out = []
    for index in range(0, len(bits), 8):
        value = 0
        for bit in bits[index : index + 8]:
            value = (value << 1) | bit
        out.append(value)
    return out


def _choose_version(length: int) -> int:
    for version, (ecc_len, groups, _remainder, _align) in _VERSIONS.items():
        capacity = sum(count * size for count, size in groups)
        # 4 mode bits + 8 count bits, then the payload, plus up to 4 terminator bits.
        if length <= (capacity * 8 - 12) // 8:
            return version
    raise ValueError("verify URL is too long for a QR code; shorten public_url")


def _codeword_bits(payload: bytes) -> tuple[int, list[int]]:
    version = _choose_version(len(payload))
    ecc_len, groups, remainder, _align = _VERSIONS[version]
    capacity = sum(count * size for count, size in groups)
    data = _data_codewords(payload, capacity)
    blocks: list[list[int]] = []
    ecc: list[list[int]] = []
    offset = 0
    for count, size in groups:
        for _ in range(count):
            block = data[offset : offset + size]
            offset += size
            blocks.append(block)
            ecc.append(_rs_block(block, ecc_len))
    stream: list[int] = []
    longest = max(len(block) for block in blocks)
    for index in range(longest):
        for block in blocks:
            if index < len(block):
                stream.append(block[index])
    for index in range(ecc_len):
        for block in ecc:
            stream.append(block[index])
    bits: list[int] = []
    for byte in stream:
        bits += _bits_of(byte, 8)
    bits += [0] * remainder
    return version, bits


def _format_bits() -> int:
    # ECC M is 00, mask 0 is 000. The BCH remainder of zero is then masked with 0x5412.
    return 0x5412


def _blank(size: int) -> tuple[list[list[bool]], list[list[bool]]]:
    matrix = [[False] * size for _ in range(size)]
    reserved = [[False] * size for _ in range(size)]
    return matrix, reserved


def _finder(matrix: list[list[bool]], reserved: list[list[bool]], top: int, left: int) -> None:
    size = len(matrix)
    for row in range(top - 1, top + 8):
        for col in range(left - 1, left + 8):
            if not (0 <= row < size and 0 <= col < size):
                continue
            reserved[row][col] = True
            local_r = row - top
            local_c = col - left
            if local_r < 0 or local_c < 0 or local_r > 6 or local_c > 6:
                matrix[row][col] = False
            else:
                edge = local_r in (0, 6) or local_c in (0, 6)
                core = 2 <= local_r <= 4 and 2 <= local_c <= 4
                matrix[row][col] = edge or core


def _alignment(matrix: list[list[bool]], reserved: list[list[bool]], row: int, col: int) -> None:
    size = len(matrix)
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            rr, cc = row + dr, col + dc
            if not (0 <= rr < size and 0 <= cc < size):
                return
            if reserved[rr][cc]:
                return
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            rr, cc = row + dr, col + dc
            reserved[rr][cc] = True
            matrix[rr][cc] = max(abs(dr), abs(dc)) != 1


def _functions(version: int) -> tuple[list[list[bool]], list[list[bool]]]:
    size = 17 + 4 * version
    matrix, reserved = _blank(size)
    _finder(matrix, reserved, 0, 0)
    _finder(matrix, reserved, 0, size - 7)
    _finder(matrix, reserved, size - 7, 0)
    centers = _VERSIONS[version][3]
    for row in centers:
        for col in centers:
            _alignment(matrix, reserved, row, col)
    for index in range(size):
        if not reserved[6][index]:
            matrix[6][index] = index % 2 == 0
            reserved[6][index] = True
        if not reserved[index][6]:
            matrix[index][6] = index % 2 == 0
            reserved[index][6] = True
    dark = 4 * version + 9
    matrix[dark][8] = True
    reserved[dark][8] = True
    for row, col in _format_spots(size):
        reserved[row][col] = True
    return matrix, reserved


def _format_spots(size: int) -> list[tuple[int, int]]:
    spots = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    for index in range(8):
        spots.append((8, size - 1 - index))
    for index in range(7):
        spots.append((size - 7 + index, 8))
    return spots


def _place_bits(matrix: list[list[bool]], reserved: list[list[bool]], bits: list[int]) -> None:
    size = len(matrix)
    index = 0
    col = size - 1
    upward = True
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(size - 1, -1, -1) if upward else range(size)
        for row in rows:
            for current in (col, col - 1):
                if reserved[row][current]:
                    continue
                bit = bits[index] if index < len(bits) else 0
                index += 1
                matrix[row][current] = bool(bit)
        upward = not upward
        col -= 2
    if index < len(bits):
        raise ValueError("QR data did not fit the symbol")


def _mask(matrix: list[list[bool]], reserved: list[list[bool]]) -> None:
    size = len(matrix)
    for row in range(size):
        for col in range(size):
            if reserved[row][col]:
                continue
            if (row + col) % 2 == 0:
                matrix[row][col] = not matrix[row][col]


def _place_format(matrix: list[list[bool]]) -> None:
    """Place ECC-M mask-0 format bits the way ISO/IEC 18004 lays them out.

    Bit 14 (MSB) starts at column 0 of row 8. Bit 0 (LSB) starts at row 0 of
    column 8. The copy along the right and bottom edges mirrors that. The
    dark module on column 8 stays black.
    """
    info = _format_bits()
    size = len(matrix)
    voffset = 0
    hoffset = 0
    for index in range(8):
        low = (info >> index) & 1
        high = (info >> (14 - index)) & 1
        if index == 6:
            voffset += 1
            hoffset = 1
        matrix[index + voffset][8] = bool(low)
        matrix[8][index + hoffset] = bool(high)
        matrix[8][size - 1 - index] = bool(low)
        matrix[size - 1 - index][8] = bool(high)
    matrix[size - 8][8] = True


def qr_matrix(text: str) -> list[list[bool]]:
    payload = text.encode("utf-8")
    version, bits = _codeword_bits(payload)
    matrix, reserved = _functions(version)
    _place_bits(matrix, reserved, bits)
    _mask(matrix, reserved)
    _place_format(matrix)
    return matrix


def qr_svg(text: str) -> str:
    modules = qr_matrix(text)
    quiet = 4
    size = len(modules) + quiet * 2
    commands: list[str] = []
    for row, line in enumerate(modules):
        for col, dark in enumerate(line):
            if dark:
                commands.append(f"M{col + quiet} {row + quiet}h1v1h-1z")
    path = "".join(commands)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" '
        f'shape-rendering="crispEdges" role="img">'
        f'<rect width="{size}" height="{size}" fill="#fffdf8"/>'
        f'<path d="{path}" fill="#1c1915"/></svg>'
    )
