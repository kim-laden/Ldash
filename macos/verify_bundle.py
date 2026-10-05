"""Check a staged Laden Ops.app without launching it."""

from __future__ import annotations

import sys
from pathlib import Path

ARM64 = 0x0100000C
X64 = 0x01000007


def macho_cpu(path: Path) -> int | None:
    data = path.read_bytes()[:8]
    if len(data) < 8:
        return None
    magic = int.from_bytes(data[:4], "little")
    if magic != 0xFEEDFACF:
        return None
    return int.from_bytes(data[4:8], "little")


def has_cpu(path: Path, expect: int) -> bool:
    data = path.read_bytes()
    if macho_cpu(path) == expect:
        return True
    if int.from_bytes(data[:4], "big") != 0xCAFEBABE or len(data) < 8:
        return False
    count = int.from_bytes(data[4:8], "big")
    for index in range(count):
        off = 8 + index * 20
        if off + 20 > len(data):
            return False
        cputype = int.from_bytes(data[off : off + 4], "big")
        slice_off = int.from_bytes(data[off + 8 : off + 12], "big")
        if cputype == expect and int.from_bytes(data[slice_off : slice_off + 4], "little") == 0xFEEDFACF:
            return True
    return False


def _slices(data: bytes) -> list[bytes]:
    if int.from_bytes(data[:4], "big") in (0xCAFEBABE,):
        count = int.from_bytes(data[4:8], "big")
        out = []
        for index in range(count):
            off = 8 + index * 20
            start = int.from_bytes(data[off + 8 : off + 12], "big")
            size = int.from_bytes(data[off + 12 : off + 16], "big")
            out.append(data[start : start + size])
        return out
    return [data]


def min_macos(path: Path) -> list[tuple[int, int]]:
    """(major, minor) minimum macOS per Mach-O slice, from LC_BUILD_VERSION or LC_VERSION_MIN_MACOSX."""
    found = []
    for mach in _slices(path.read_bytes()):
        if int.from_bytes(mach[:4], "little") != 0xFEEDFACF:
            continue
        ncmds = int.from_bytes(mach[16:20], "little")
        off = 32
        for _ in range(ncmds):
            cmd = int.from_bytes(mach[off : off + 4], "little")
            size = int.from_bytes(mach[off + 4 : off + 8], "little")
            ver = None
            if cmd == 0x32:  # LC_BUILD_VERSION: platform, minos, sdk
                ver = int.from_bytes(mach[off + 12 : off + 16], "little")
            elif cmd == 0x24:  # LC_VERSION_MIN_MACOSX: version, sdk
                ver = int.from_bytes(mach[off + 8 : off + 12], "little")
            if ver is not None:
                found.append((ver >> 16, (ver >> 8) & 0xFF))
                break
            off += size
    return found


def main() -> None:
    app = Path(sys.argv[1])
    arch = sys.argv[2]
    expect = ARM64 if arch == "arm64" else X64
    mac = app / "Contents" / "MacOS"
    exe = mac / "LadenOps"
    if not has_cpu(exe, expect):
        raise SystemExit(f"{exe} is not a {arch} Mach-O")
    natives = list(mac.rglob("Photino.Native.dylib"))
    if not natives:
        raise SystemExit("Photino.Native.dylib missing")
    plist_text = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    import re

    m = re.search(r"<key>LSMinimumSystemVersion</key>\s*<string>(\d+)\.(\d+)", plist_text)
    floor = (int(m.group(1)), int(m.group(2))) if m else (12, 0)
    for native in natives:
        if not has_cpu(native, expect):
            raise SystemExit(f"{native} has no {arch} slice")
        mins = min_macos(native)
        if not mins or max(mins) > floor:
            raise SystemExit(f"{native.name} needs macOS {max(mins) if mins else '?'}; the app promises {floor[0]}.{floor[1]}")
    photino_min = max(min_macos(natives[0]))
    runtime = mac / "runtime" / arch
    python = runtime / "bin" / "python3"
    if not python.exists():
        python = runtime / "bin" / "python3.12"
    if not python.exists():
        raise SystemExit(f"python missing under {runtime}")
    target = python.resolve()
    if not has_cpu(target, expect):
        raise SystemExit(f"{target} is not a {arch} Mach-O")
    serve = (mac / "runtime" / "serve.py").read_text(encoding="utf-8")
    if "def main" not in serve or "127.0.0.1" not in serve:
        raise SystemExit("serve.py is not the dashboard server")
    for name in ("host.py", "machost.py", "cacert.pem"):
        if not (mac / "runtime" / name).is_file():
            raise SystemExit(f"missing runtime/{name}")
    index = (mac / "web" / "index.html").read_text(encoding="utf-8")
    if "app.js?v=27" not in index:
        raise SystemExit("web cache is not v=26")
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    if "Laden AS" not in plist:
        raise SystemExit("publisher is not Laden AS")
    if not (app / "Contents" / "Resources" / "laden.icns").is_file():
        raise SystemExit("icon missing")
    print(f"bundle ok {arch} python={target.name} photino={len(natives)} photino-minos={photino_min[0]}.{photino_min[1]}")


if __name__ == "__main__":
    main()
