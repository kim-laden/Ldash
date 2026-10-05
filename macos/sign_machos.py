"""Ad-hoc sign every Mach-O in an .app so Apple Silicon will launch it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def kind(path: Path) -> str | None:
    if path.is_symlink() or not path.is_file():
        return None
    data = path.read_bytes()[:4]
    if len(data) < 4:
        return None
    le = int.from_bytes(data, "little")
    be = int.from_bytes(data, "big")
    if le in (0xFEEDFACF, 0xFEEDFACE) or be in (0xCAFEBABE, 0xCAFEBABF):
        return "macho"
    return None


def main() -> None:
    rcodesign = Path(sys.argv[1])
    roots = [Path(arg) for arg in sys.argv[2:]]
    targets: list[Path] = []
    for root in roots:
        for path in root.rglob("*"):
            if kind(path):
                targets.append(path)
    print(f"signing {len(targets)} Mach-O files", flush=True)
    for path in targets:
        proc = subprocess.run([str(rcodesign), "-C", "/dev/null", "sign", str(path)], text=True, capture_output=True)
        if proc.returncode != 0:
            sys.stderr.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            raise SystemExit(f"sign failed: {path}")
    print("signed", flush=True)


if __name__ == "__main__":
    main()
