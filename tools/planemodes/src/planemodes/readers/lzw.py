"""Decoder for Unix `compress` (.Z) files, which ODB++ archives may contain.

The format: a 3-byte header (1F 9D, flags: max code width and block mode),
then LZW codes packed little-endian from 9 bits up to the max width. In block
mode code 256 clears the table; codes are read in groups of 8, so after a
clear or a width change the reader skips to the end of the current group.
"""


def decompress(data: bytes) -> bytes:
    if len(data) < 3 or data[0] != 0x1F or data[1] != 0x9D:
        raise ValueError('not a .Z (compress) stream')
    maxbits, block = data[2] & 0x1F, bool(data[2] & 0x80)
    out = bytearray()
    table = [bytes([i]) for i in range(256)] + ([b''] if block else [])
    bits, pos, nbits = 9, 3 * 8, len(data) * 8
    prev, group_start = None, pos
    while pos + bits <= nbits:
        # read one code of `bits` bits, least significant bit first
        byte, off = pos >> 3, pos & 7
        chunk = int.from_bytes(data[byte:byte + 3], 'little')
        code = (chunk >> off) & ((1 << bits) - 1)
        pos += bits
        if block and code == 256:              # clear: restart the table
            table = table[:257]
            pos = group_start + (((pos - group_start) + bits * 8 - 1) // (bits * 8)) * bits * 8
            bits, prev, group_start = 9, None, pos
            continue
        if code < len(table):
            entry = table[code]
            if prev is not None:
                table.append(prev + entry[:1])
        elif code == len(table) and prev is not None:   # the KwKwK case
            entry = prev + prev[:1]
            table.append(entry)
        else:
            raise ValueError('corrupt .Z stream')
        out += entry
        prev = entry
        if len(table) >= (1 << bits) and bits < maxbits:   # widen the codes
            pos = group_start + (((pos - group_start) + bits * 8 - 1) // (bits * 8)) * bits * 8
            bits += 1
            group_start = pos
    return bytes(out)
