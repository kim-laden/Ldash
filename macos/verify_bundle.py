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
    for native in natives:
        if not has_cpu(native, expect):
            raise SystemExit(f"{native} has no {arch} slice")
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
    if "app.js?v=25" not in index:
        raise SystemExit("web cache is not v=25")
    plist = (app / "Contents" / "Info.plist").read_text(encoding="utf-8")
    if "Laden AS" not in plist:
        raise SystemExit("publisher is not Laden AS")
    if not (app / "Contents" / "Resources" / "laden.icns").is_file():
        raise SystemExit("icon missing")
    print(f"bundle ok {arch} python={target.name} photino={len(natives)}")


if __name__ == "__main__":
    main()
