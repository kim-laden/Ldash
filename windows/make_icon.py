"""Write a small green Laden Ops icon."""

from __future__ import annotations

import struct
from pathlib import Path


def dib(size: int) -> bytes:
    pixels = bytearray()
    for y in range(size - 1, -1, -1):
        for x in range(size):
            edge = x < 2 or y < 2 or x >= size - 2 or y >= size - 2
            mark = abs(x - y) < max(1, size // 16) or abs(x + y - (size - 1)) < max(1, size // 16)
            if edge:
                pixels += bytes((10, 18, 12, 255))
            elif mark:
                pixels += bytes((157, 255, 0, 255))
            else:
                pixels += bytes((7, 10, 14, 255))
    header = struct.pack(
        "<IiiHHIIiiII",
        40,
        size,
        size * 2,
        1,
        32,
        0,
        len(pixels),
        0,
        0,
        0,
        0,
    )
    mask_row = ((size + 31) // 32) * 4
    mask = bytes(mask_row * size)
    return header + bytes(pixels) + mask


def ico(path: Path) -> None:
    images = [(16, dib(16)), (32, dib(32)), (48, dib(48))]
    offset = 6 + 16 * len(images)
    entries = b""
    blob = b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,
            size if size < 256 else 0,
            0,
            0,
            1,
            32,
            len(data),
            offset,
        )
        offset += len(data)
        blob += data
    path.write_bytes(struct.pack("<HHH", 0, 1, len(images)) + entries + blob)


if __name__ == "__main__":
    dest = Path(__file__).resolve().parent / "laden.ico"
    ico(dest)
    print(dest)
