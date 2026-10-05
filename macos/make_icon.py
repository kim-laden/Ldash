"""Write laden.icns: a green mark on the Laden Ops background."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def png(size: int) -> bytes:
    raw = bytearray()
    for y in range(size):
        raw.append(0)
        for x in range(size):
            edge = x < max(2, size // 32) or y < max(2, size // 32) or x >= size - max(2, size // 32) or y >= size - max(2, size // 32)
            mark = abs(x - y) < max(2, size // 14) or abs(x + y - (size - 1)) < max(2, size // 14)
            if edge:
                raw += bytes((10, 18, 12, 255))
            elif mark:
                raw += bytes((157, 255, 0, 255))
            else:
                raw += bytes((7, 10, 14, 255))

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")


def icns(path: Path) -> None:
    images = {b"ic07": png(128), b"ic08": png(256), b"ic09": png(512)}
    body = b"".join(tag + struct.pack(">I", 8 + len(data)) + data for tag, data in images.items())
    path.write_bytes(b"icns" + struct.pack(">I", 8 + len(body)) + body)


if __name__ == "__main__":
    dest = Path(__file__).resolve().parent / "laden.icns"
    icns(dest)
    print(dest)
