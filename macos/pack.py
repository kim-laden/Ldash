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
import time
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
<pkg-info format-version="2" identifier="{PKG_ID}" version="{PKG_VERSION}" install-location="/" auth="root" overwrite-permissions="true">
  <payload numberOfFiles="{count}" installKBytes="{kbytes}"/>
  <scripts>
    <postinstall file="./postinstall"/>
  </scripts>
</pkg-info>
"""
    return xml.encode("utf-8")


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


PKG_ID = "org.laden.OpsDash.installer"
PKG_VERSION = "1.0.0"
COMPONENT = "Ldash.pkg"


def distribution(kbytes: int) -> bytes:
    """productbuild-style Distribution for a product archive with one component."""
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<installer-gui-script minSpecVersion="2">
  <title>Ldash</title>
  <options customize="never" require-scripts="false" rootVolumeOnly="true" hostArchitectures="x86_64,arm64"/>
  <domains enable_anywhere="false" enable_currentUserHome="false" enable_localSystem="true"/>
  <volume-check>
    <allowed-os-versions>
      <os-version min="12.0"/>
    </allowed-os-versions>
  </volume-check>
  <choices-outline>
    <line choice="default">
      <line choice="{PKG_ID}"/>
    </line>
  </choices-outline>
  <choice id="default"/>
  <choice id="{PKG_ID}" visible="false">
    <pkg-ref id="{PKG_ID}"/>
  </choice>
  <pkg-ref id="{PKG_ID}" version="{PKG_VERSION}" onConclusion="none" installKBytes="{kbytes}">#{COMPONENT}</pkg-ref>
</installer-gui-script>
"""
    return xml.encode("utf-8")


# A tree entry is (name, bytes) for a file or (name, [entries]) for a directory.
Entry = tuple


def write_xar(path: Path, entries: list[Entry]) -> None:
    """Write an uncompressed-data xar (the format pkgutil/Installer read).

    Layout: 28-byte header, zlib TOC, heap. The TOC checksum is SHA-1 over the
    *compressed* TOC bytes and is stored at heap offset 0 (20 bytes), so file
    data starts at heap offset 20. Data is stored as-is
    (application/octet-stream), so archived and extracted checksums are equal.
    """
    blob = bytearray()
    offset = 20
    next_id = 1
    ctime = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def render(items: list[Entry], depth: int) -> str:
        nonlocal offset, next_id
        pad = "  " * depth
        out = []
        for name, value in items:
            fid = next_id
            next_id += 1
            if isinstance(value, list):
                out.append(
                    f"{pad}<file id=\"{fid}\">\n"
                    f"{pad}  <name>{name}</name>\n"
                    f"{pad}  <type>directory</type>\n"
                    f"{pad}  <mode>0755</mode>\n"
                    + render(value, depth + 1)
                    + f"{pad}</file>\n"
                )
                continue
            data = bytes(value)
            digest = sha1(data)
            out.append(
                f"{pad}<file id=\"{fid}\">\n"
                f"{pad}  <name>{name}</name>\n"
                f"{pad}  <type>file</type>\n"
                f"{pad}  <mode>0644</mode>\n"
                f"{pad}  <data>\n"
                f"{pad}    <length>{len(data)}</length>\n"
                f"{pad}    <offset>{offset}</offset>\n"
                f"{pad}    <size>{len(data)}</size>\n"
                f"{pad}    <encoding style=\"application/octet-stream\"/>\n"
                f"{pad}    <archived-checksum style=\"sha1\">{digest}</archived-checksum>\n"
                f"{pad}    <extracted-checksum style=\"sha1\">{digest}</extracted-checksum>\n"
                f"{pad}  </data>\n"
                f"{pad}</file>\n"
            )
            blob.extend(data)
            offset += len(data)
        return "".join(out)

    body = render(entries, 2)
    toc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n<xar>\n <toc>\n'
        f"  <creation-time>{ctime}</creation-time>\n"
        '  <checksum style="sha1">\n   <offset>0</offset>\n   <size>20</size>\n  </checksum>\n'
        + body
        + " </toc>\n</xar>\n"
    ).encode("utf-8")
    comp = zlib.compress(toc, 9)
    # The TOC checksum covers the compressed TOC exactly as stored on disk.
    toc_digest = hashlib.sha1(comp).digest()
    # magic 'xar!', header size 28, version 1, toc lengths, cksum_alg 1 = SHA-1
    header = struct.pack(">IHHQQI", 0x78617221, 28, 1, len(comp), len(toc), 1)
    assert len(header) == 28 and len(toc_digest) == 20
    path.write_bytes(header + comp + toc_digest + bytes(blob))


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
    # mkbom (bomutils) skips dotfiles, so the Bom must not miss any payload
    # entry: drop empty ".empty" placeholders and refuse any other dotfile.
    for dot in sorted(root.rglob(".*")):
        if dot.name == ".empty" and dot.is_file() and dot.stat().st_size == 0:
            dot.unlink()
        else:
            raise SystemExit(f"dotfile would be missing from the Bom: {dot}")
    count, kbytes = count_tree(root)
    flat = work / "flat"
    flat.mkdir()
    print(f"payload files={count} kbytes={kbytes}", flush=True)
    cpio_gz(root, flat / "Payload")
    run([str(mkbom), "-u", "0", "-g", "80", str(root), str(flat / "Bom")])
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
            ("Distribution", distribution(kbytes)),
            (
                COMPONENT,
                [
                    ("Bom", (flat / "Bom").read_bytes()),
                    ("Payload", (flat / "Payload").read_bytes()),
                    ("Scripts", (flat / "Scripts").read_bytes()),
                    ("PackageInfo", (flat / "PackageInfo").read_bytes()),
                ],
            ),
        ],
    )
    print(f"wrote {out} ({out.stat().st_size} bytes)", flush=True)


if __name__ == "__main__":
    main()
