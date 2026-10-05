"""Build a flat macOS installer package on Linux."""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import shutil
import struct
import subprocess
import sys
import threading
import zlib
from pathlib import Path


def run(cmd: list[str], cwd: Path | None = None) -> None:
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def cpio_gz(root: Path, dest: Path) -> None:
    listing = subprocess.check_output(["find", ".", "-print"], cwd=root, text=True)
    payload = ("\n".join(sorted(listing.splitlines())) + "\n").encode()
    proc = subprocess.Popen(
        ["cpio", "-o", "--format", "odc", "--owner", "0:80"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        cwd=root,
    )

    def feed() -> None:
        assert proc.stdin is not None
        proc.stdin.write(payload)
        proc.stdin.close()

    thread = threading.Thread(target=feed)
    thread.start()
    assert proc.stdout is not None
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, compresslevel=9) as gz:
        shutil.copyfileobj(proc.stdout, gz)
    proc.stdout.close()
    thread.join()
    code = proc.wait()
    if code != 0:
        raise SystemExit(f"cpio exited {code}")
    dest.write_bytes(raw.getvalue())


def count_tree(root: Path) -> tuple[int, int]:
    files = 0
    size = 0
    for dirpath, dirnames, filenames in os.walk(root):
        files += 1 + len(filenames)
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink() or path.is_file():
                size += path.lstat().st_size
    return files, max(1, (size + 1023) // 1024)


def package_info(count: int, kbytes: int) -> bytes:
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<pkg-info format-version="2" identifier="org.laden.OpsDash.installer" version="1.0.0" install-location="/" auth="root" overwrite-permissions="true">
  <payload numberOfFiles="{count}" installKBytes="{kbytes}"/>
  <scripts>
    <postinstall file="./postinstall"/>
  </scripts>
</pkg-info>
"""
    return xml.encode("utf-8")


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def write_xar(path: Path, files: list[tuple[str, bytes]]) -> None:
    """Uncompressed xar. The checksum is the SHA-1 of the TOC."""
    offset = 20
    parts: list[str] = []
    blob = bytearray()
    for index, (name, data) in enumerate(files, start=1):
        digest = sha1(data)
        parts.append(
            f"""  <file id="{index}">
    <name>{name}</name>
    <type>file</type>
    <mode>0644</mode>
    <data>
      <length>{len(data)}</length>
      <offset>{offset}</offset>
      <size>{len(data)}</size>
      <encoding style="application/octet-stream"/>
      <archived-checksum style="sha1">{digest}</archived-checksum>
      <extracted-checksum style="sha1">{digest}</extracted-checksum>
    </data>
  </file>
"""
        )
        blob += data
        offset += len(data)
    toc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<xar>\n <toc>\n'
        "  <checksum style=\"sha1\">\n   <offset>0</offset>\n   <size>20</size>\n  </checksum>\n"
        + "".join(parts)
        + " </toc>\n</xar>\n"
    ).encode("utf-8")
    digest = hashlib.sha1(toc).digest()
    comp = zlib.compress(toc)
    header = struct.pack(">IHHQQI", 0x78617221, 28, 1, len(comp), len(toc), 1)
    path.write_bytes(header + comp + digest + blob)


def main() -> None:
    arm = Path(sys.argv[1]).resolve()
    intel = Path(sys.argv[2]).resolve()
    out = Path(sys.argv[3]).resolve()
    postinstall = Path(sys.argv[4]).resolve()
    mkbom = Path(sys.argv[5]).resolve()
    work = Path(sys.argv[6]).resolve()
    if work.exists():
        shutil.rmtree(work)
    root = work / "root"
    stage_arm = root / "Library" / "Application Support" / "laden-ops-setup" / "arm64"
    stage_x64 = root / "Library" / "Application Support" / "laden-ops-setup" / "x64"
    stage_arm.mkdir(parents=True)
    stage_x64.mkdir(parents=True)
    print("staging Apple Silicon app", flush=True)
    shutil.copytree(arm, stage_arm / "Laden Ops.app", symlinks=True)
    print("staging Intel app", flush=True)
    shutil.copytree(intel, stage_x64 / "Laden Ops.app", symlinks=True)
    for dirpath, dirnames, filenames in os.walk(root):
        os.chmod(dirpath, 0o755)
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_symlink():
                continue
            mode = path.stat().st_mode
            if mode & 0o111:
                os.chmod(path, 0o755)
            else:
                os.chmod(path, 0o644)
    count, kbytes = count_tree(root)
    flat = work / "flat"
    flat.mkdir()
    print(f"payload files={count} kbytes={kbytes}", flush=True)
    cpio_gz(root, flat / "Payload")
    run([str(mkbom), str(root), str(flat / "Bom")])
    bom = (flat / "Bom").read_bytes()
    if not bom.startswith(b"BOMStore"):
        raise SystemExit("mkbom did not write a BOMStore file")
    scripts = work / "scripts"
    scripts.mkdir()
    script = scripts / "postinstall"
    shutil.copy(postinstall, script)
    os.chmod(script, 0o755)
    cpio_gz(scripts, flat / "Scripts")
    (flat / "PackageInfo").write_bytes(package_info(count, kbytes))
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    write_xar(
        out,
        [
            ("Payload", (flat / "Payload").read_bytes()),
            ("PackageInfo", (flat / "PackageInfo").read_bytes()),
            ("Bom", (flat / "Bom").read_bytes()),
            ("Scripts", (flat / "Scripts").read_bytes()),
        ],
    )
    print(f"wrote {out} ({out.stat().st_size} bytes)", flush=True)


if __name__ == "__main__":
    main()
