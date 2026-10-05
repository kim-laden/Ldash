"""Write the iPhone icon set: a green mark on the Laden Ops background."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path


def png(size: int) -> bytes:
    raw = bytearray()
    edge_px = max(2, size // 32)
    mark_px = max(2, size // 14)
    for y in range(size):
        raw.append(0)
        for x in range(size):
            edge = x < edge_px or y < edge_px or x >= size - edge_px or y >= size - edge_px
            mark = abs(x - y) < mark_px or abs(x + y - (size - 1)) < mark_px
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


def main() -> None:
    dest = Path(__file__).resolve().parent / "LadenOps" / "Assets.xcassets" / "AppIcon.appiconset"
    dest.mkdir(parents=True, exist_ok=True)
    for size in (40, 58, 60, 80, 87, 120, 180, 1024):
        file = dest / f"Icon-{size}.png"
        file.write_bytes(png(size))
        print(file)


if __name__ == "__main__":
    main()
