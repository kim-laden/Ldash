"""Local host bridge: metrics, processes, KWin windows, launch, HTTP."""

from __future__ import annotations

import base64
import getpass
import json
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

def write_private_kit(directory: Path, payload: str) -> dict:
    """Write the dashboard kit for this user only. Do not log the payload."""
    text = str(payload or "")
    raw = text.encode("utf-8")
    if not text.strip():
        return {"ok": False, "error": "Nothing to save"}
    if len(raw) > KIT_MAX_BYTES:
        return {"ok": False, "error": "The kit is too large to save"}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "The kit is not valid"}
    if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
        return {"ok": False, "error": "The kit is not valid"}
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / "kit.json"
    tmp = directory / ".kit.json.tmp"
    tmp.write_bytes(raw)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return {
        "ok": True,
        "bytes": len(raw),
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def write_vault_conf(directory: Path, payload: str) -> dict:
    """Write vault.conf for this user only. Do not log the payload."""
    text = str(payload or "")
    raw = text.encode("utf-8")
    if not text.strip():
        return {"ok": False, "error": "Nothing to save"}
    if len(raw) > KIT_MAX_BYTES:
        return {"ok": False, "error": "The kit is too large to save"}
    try:
        start = text.find("{")
        parsed = json.loads(text[start:] if start >= 0 else text)
    except json.JSONDecodeError:
        return {"ok": False, "error": "The kit is not valid"}
    if not isinstance(parsed, dict) or parsed.get("kind") != "laden.vault.conf":
        return {"ok": False, "error": "The kit is not valid"}
    directory.mkdir(parents=True, exist_ok=True)
    dest = directory / "vault.conf"
    tmp = directory / ".vault.conf.tmp"
    tmp.write_bytes(raw)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(dest)
    try:
        os.chmod(dest, 0o600)
    except OSError:
        pass
    return {
        "ok": True,
        "bytes": len(raw),
        "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }


def _config_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(root) / "laden-ops"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "laden-ops"
    return Path.home() / ".config" / "laden-ops"


CONFIG_DIR = _config_dir()
VAULT_WORK = CONFIG_DIR / "vault-workspace"
KWIN_SCRIPT = CONFIG_DIR / "kwin.js"
SHORTCUT_FILE = CONFIG_DIR / "shortcut"
PAUSE_MODE_FILE = CONFIG_DIR / "pause-mode"
KIT_MAX_BYTES = 24 * 1024 * 1024
BUS_NAME = "org.laden.OpsDash.Bridge"
BUS_PATH = "/org/laden/OpsDash/Bridge"
BUS_IFACE = "org.laden.OpsDash.Bridge"
SCRIPT_PLUGIN = "opsdash"


def _pdf_text(value: str) -> str:
    raw = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    for src, dst in (
        ("\u2014", "-"),
        ("\u2013", "-"),
        ("\u2018", "'"),
        ("\u2019", "'"),
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2026", "..."),
        ("\u00b7", " - "),
        ("\u2022", "-"),
    ):
        raw = raw.replace(src, dst)
    return raw.encode("latin-1", "replace").decode("latin-1")


def _pdf_escape(value: str) -> str:
    return _pdf_text(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _wrap_pdf(text: str, limit: int) -> list[str]:
    lines: list[str] = []
    for para in _pdf_text(text).split("\n"):
        if not para.strip():
            lines.append("")
            continue
        current = ""
        for word in para.split():
            while len(word) > limit:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:limit])
                word = word[limit:]
            trial = word if not current else current + " " + word
            if len(trial) <= limit:
                current = trial
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines or [""]


def _dump_pdf_text(dumps: list) -> str:
    blocks: list[str] = []
    for item in dumps:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "dump").strip()[:80] or "dump"
        body = str(item.get("body") or "").strip()[:700]
        blocks.append(name + ("\n" + body if body else ""))
    return "\n\n".join(blocks) if blocks else "(empty)"


def render_case_pdf(path: Path, title: str, scope: str, summary: str, case_body: str, dumps: list) -> None:
    """Printable case sheet. Dark header band, light page, green section rules."""
    render_sheet(
        path,
        title or "case",
        [
            ("SCOPE", scope or "(none)"),
            ("CALEB'S THOUGHTS", summary or "(no summary yet)"),
            ("CASE", case_body or "(no analysed intel yet)"),
            ("DUMP", _dump_pdf_text(dumps)),
        ],
        banner="CASE",
    )


def render_sheet(path: Path, title: str, sections: list, banner: str = "CASE") -> None:
    """One printable sheet. `sections` are (heading, body) pairs."""
    page_w, page_h = 595.0, 842.0
    items: list[tuple[str, str]] = [("title", title or "sheet")]
    for heading, body in sections:
        items.append(("h", str(heading or "SECTION")[:40]))
        for line in _wrap_pdf(str(body or ""), 92):
            items.append(("b", line))
        items.append(("gap", ""))

    pages: list[list[str]] = []
    commands: list[str] = []
    y = 774.0

    def new_page() -> None:
        nonlocal commands, y
        if commands:
            pages.append(commands)
        commands = []
        y = 774.0

    new_page()
    for kind, text in items:
        step = {"title": 28.0, "h": 20.0, "b": 13.0, "gap": 6.0}[kind]
        if y - step < 54:
            new_page()
        if kind == "title":
            commands.append(f"BT /F2 16 Tf 0.05 0.07 0.09 rg 48 {y:.1f} Td ({_pdf_escape(text)}) Tj ET")
        elif kind == "h":
            commands.append(f"0 0.62 0.38 rg 48 {y - 3:.1f} 499 1.2 re f")
            commands.append(f"BT /F2 10 Tf 0 0.45 0.28 rg 48 {y:.1f} Td ({_pdf_escape(text)}) Tj ET")
        elif kind == "b":
            shown = text if text else " "
            commands.append(f"BT /F1 10 Tf 0.08 0.10 0.14 rg 48 {y:.1f} Td ({_pdf_escape(shown)}) Tj ET")
        y -= step
    if commands:
        pages.append(commands)
    if not pages:
        pages.append([])

    objects: list[bytes] = []

    def add(data: str | bytes) -> int:
        objects.append(data if isinstance(data, bytes) else data.encode("latin-1", "replace"))
        return len(objects)

    font_regular = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    font_bold = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    page_ids: list[int] = []
    content_ids: list[int] = []
    total = len(pages)
    for index, content in enumerate(pages, start=1):
        header = [
            "0.02 0.027 0.039 rg",
            f"0 {page_h - 36:.1f} {page_w:.1f} 36 re f",
            "0 1 0.616 rg",
            f"0 {page_h - 38:.1f} {page_w:.1f} 2 re f",
            f"BT /F2 9 Tf 0 1 0.616 rg 48 {page_h - 22:.1f} Td (LADEN OPS) Tj ET",
            f"BT /F2 9 Tf 0.90 0.945 1 rg 118 {page_h - 22:.1f} Td ({_pdf_escape(banner)}) Tj ET",
            f"BT /F1 8 Tf 0.55 0.61 0.70 rg 48 32 Td ({_pdf_escape(title[:60])}  ·  {index} / {total}) Tj ET",
        ]
        payload = ("\n".join(header + content) + "\n").encode("latin-1", "replace")
        content_ids.append(add(f"<< /Length {len(payload)} >>\nstream\n".encode("latin-1") + payload + b"endstream"))
        page_ids.append(0)
    pages_id = add("<< /Type /Pages /Count " + str(total) + " /Kids [" + " ".join(f"{pid} 0 R" for pid in range(len(objects) + 1, len(objects) + 1 + total)) + "] >>")
    # page objects must follow the pages tree id calculation above, so append them now
    # and patch Kids to the real ids.
    first_page = len(objects) + 1
    kids = " ".join(f"{first_page + i} 0 R" for i in range(total))
    objects[pages_id - 1] = f"<< /Type /Pages /Count {total} /Kids [{kids}] >>".encode("latin-1")
    for offset, content_id in enumerate(content_ids):
        page_ids[offset] = add(
            "<< /Type /Page /Parent "
            + f"{pages_id} 0 R "
            + f"/MediaBox [0 0 {page_w:.0f} {page_h:.0f}] "
            + "/Resources << /Font << "
            + f"/F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >> "
            + f"/Contents {content_id} 0 R >>"
        )
    catalog = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out.extend(f"{number} 0 obj\n".encode("ascii"))
        out.extend(obj)
        out.extend(b"\nendobj\n")
    xref = len(out)
    out.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        out.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    out.extend(
        f"trailer << /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(out)


# Product default. A saved shortcut is per machine and must not replace this.
PRODUCT_SHORTCUT = "Ctrl+`"
# Windows registers both English backtick and Norwegian pipe.
WIN_PRODUCT_SHORTCUT = "Ctrl+`,Ctrl+|"
# KGlobalAccel::GlobalShortcutLoading::NoAutoloading. On the daemon this changes
# the active shortcut and leaves the default alone (IsDefault is 8, SetPresent is 2).
_KGA_NOAUTOLOADING = 4
_KGA_ISDEFAULT = 8
_KGA_ACTION = ("kwin", "OpsDashToggle", "KWin", "Toggle Laden Ops")


def default_shortcut() -> str:
    try:
        text = SHORTCUT_FILE.read_text(encoding="utf-8").strip()
        if text:
            line = text.splitlines()[0].strip()
            if line:
                if os.name == "nt" and line == PRODUCT_SHORTCUT:
                    return WIN_PRODUCT_SHORTCUT
                return line
    except OSError:
        pass
    if os.name == "nt":
        return WIN_PRODUCT_SHORTCUT
    return PRODUCT_SHORTCUT


def save_shortcut(accel: str) -> str:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fallback = WIN_PRODUCT_SHORTCUT if os.name == "nt" else PRODUCT_SHORTCUT
    cleaned = (accel or fallback).strip()
    cleaned = cleaned.replace("CommandOrControl", "Ctrl").replace("Control", "Ctrl")
    if os.name == "nt" and cleaned == PRODUCT_SHORTCUT:
        cleaned = WIN_PRODUCT_SHORTCUT
    SHORTCUT_FILE.write_text(cleaned + "\n", encoding="utf-8")
    return cleaned


def default_pause_mode() -> bool:
    try:
        text = PAUSE_MODE_FILE.read_text(encoding="utf-8").strip().lower()
        return text in ("1", "true", "on", "yes")
    except OSError:
        return False


def save_pause_mode(enabled: bool) -> bool:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    flag = bool(enabled)
    PAUSE_MODE_FILE.write_text("1\n" if flag else "0\n", encoding="utf-8")
    return flag


def primary_shortcut(accel: str) -> str:
    parts = [p.strip() for p in (accel or "").split(",") if p.strip()]
    return parts[0] if parts else PRODUCT_SHORTCUT


def _qt_shortcut_code(accel: str) -> int | None:
    """Qt combined key for a portable shortcut string. None if it cannot be encoded."""
    raw = (accel or "").strip()
    if not raw or raw.lower() == "none":
        return None
    parts = raw.split("+")
    tokens: list[str] = []
    index = 0
    while index < len(parts):
        piece = parts[index]
        if piece == "":
            tokens.append("+")
            index += 1
            while index < len(parts) and parts[index] == "":
                index += 1
            continue
        tokens.append(piece.strip())
        index += 1
    if not tokens or any(not token for token in tokens):
        return None
    mods = {
        "ctrl": 0x04000000,
        "control": 0x04000000,
        "alt": 0x08000000,
        "option": 0x08000000,
        "shift": 0x02000000,
        "meta": 0x10000000,
        "super": 0x10000000,
        "win": 0x10000000,
        "cmd": 0x10000000,
        "command": 0x10000000,
        "num": 0x20000000,
        "keypad": 0x20000000,
    }
    names = {
        "space": 0x20,
        "esc": 0x01000000,
        "escape": 0x01000000,
        "tab": 0x01000001,
        "backtab": 0x01000002,
        "backspace": 0x01000003,
        "return": 0x01000004,
        "enter": 0x01000005,
        "ins": 0x01000006,
        "insert": 0x01000006,
        "del": 0x01000007,
        "delete": 0x01000007,
        "pause": 0x01000008,
        "print": 0x01000009,
        "sysreq": 0x0100000A,
        "clear": 0x0100000B,
        "home": 0x01000010,
        "end": 0x01000011,
        "left": 0x01000012,
        "up": 0x01000013,
        "right": 0x01000014,
        "down": 0x01000015,
        "pgup": 0x01000016,
        "page up": 0x01000016,
        "pageup": 0x01000016,
        "pgdown": 0x01000017,
        "page down": 0x01000017,
        "pagedown": 0x01000017,
        "capslock": 0x01000024,
        "numlock": 0x01000025,
        "scrolllock": 0x01000026,
        "menu": 0x01000055,
        "help": 0x01000058,
        "back": 0x01000061,
        "forward": 0x01000062,
        "stop": 0x01000063,
        "refresh": 0x01000064,
        "volume down": 0x01000070,
        "volume mute": 0x01000071,
        "volume up": 0x01000072,
        "media play": 0x01000080,
        "media stop": 0x01000081,
        "media previous": 0x01000082,
        "media next": 0x01000083,
        "media pause": 0x01000085,
    }
    for number in range(1, 36):
        names[f"f{number}"] = 0x01000030 + number - 1
    mask = 0
    key = None
    for index, token in enumerate(tokens):
        low = token.lower()
        last = index == len(tokens) - 1
        if not last and low in mods:
            mask |= mods[low]
            continue
        if not last:
            return None
        if low in names:
            key = names[low]
        elif len(token) == 1:
            char = token.upper() if token.isalpha() else token
            code = ord(char)
            if 0x20 <= code <= 0x7E:
                key = code
        else:
            return None
    if key is None:
        return None
    combined = mask | key
    if combined in (0, 0x01FFFFFF):
        return None
    return combined


def _kglobal_call(method: str, *args):
    import dbus

    bus = dbus.SessionBus()
    iface = dbus.Interface(bus.get_object("org.kde.kglobalaccel", "/kglobalaccel"), "org.kde.KGlobalAccel")
    action = dbus.Array(list(_KGA_ACTION), signature="s")
    return getattr(iface, method)(action, *args)


def _kglobal_code(value) -> int | None:
    try:
        return int(value[0][0][0])
    except (TypeError, ValueError, IndexError):
        return None


def _kglobal_set(code: int, flags: int) -> bool:
    try:
        import dbus
    except ImportError:
        return False
    try:
        keys = dbus.Array(
            [dbus.Struct((dbus.Array([dbus.Int32(code), dbus.Int32(0), dbus.Int32(0), dbus.Int32(0)], signature="i"),))],
            signature="(ai)",
        )
        result = _kglobal_call("setShortcutKeys", keys, dbus.UInt32(flags))
    except Exception:
        return False
    return _kglobal_code(result) == code


def _kglobal_read() -> tuple[int | None, int | None]:
    try:
        return _kglobal_code(_kglobal_call("shortcutKeys")), _kglobal_code(_kglobal_call("defaultShortcutKeys"))
    except Exception:
        return None, None


def _patch_kglobalshortcutsrc(active: str, default: str) -> bool:
    """Rewrite only the kwin OpsDashToggle line. Leave every other shortcut alone."""
    path = Path.home() / ".config" / "kglobalshortcutsrc"
    try:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError:
        return False
    section = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and "=" not in stripped:
            section = stripped[1:-1]
            continue
        if section != "kwin" or not stripped.startswith("OpsDashToggle="):
            continue
        _active, _default, desc = (stripped.split("=", 1)[1].split(",", 2) + ["", ""])[:3]
        newline = "\n" if line.endswith("\n") else ""
        lines[index] = f"OpsDashToggle={active},{default},{desc}{newline}"
        try:
            path.write_text("".join(lines), encoding="utf-8")
        except OSError:
            return False
        return True
    return False


def _reload_kglobalaccel() -> bool:
    """Restart a standalone kglobalaccel only. Never restart KWin or an inactive unit."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return False
    try:
        state = subprocess.run(
            [systemctl, "--user", "is-active", "plasma-kglobalaccel.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if (state.stdout or "").strip() != "active":
        return False
    try:
        restarted = subprocess.run(
            [systemctl, "--user", "restart", "plasma-kglobalaccel.service"],
            check=False,
            capture_output=True,
            text=True,
            timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return restarted.returncode == 0


def _apply_saved_shortcut(saved: str) -> str | None:
    """Point the live OpsDashToggle key at `saved`. The default stays Ctrl+`."""
    active_code = _qt_shortcut_code(saved)
    default_code = _qt_shortcut_code(PRODUCT_SHORTCUT)
    if active_code is not None and default_code is not None:
        # loadScript registers the action asynchronously. Retry until it exists.
        for _attempt in range(8):
            if _kglobal_set(default_code, _KGA_ISDEFAULT) and _kglobal_set(active_code, _KGA_NOAUTOLOADING):
                active, default = _kglobal_read()
                if active == active_code and default == default_code:
                    return None
            time.sleep(0.1)
    if not _patch_kglobalshortcutsrc(saved, PRODUCT_SHORTCUT):
        return "could not set the KDE shortcut"
    if not _reload_kglobalaccel():
        return "saved the shortcut file, but KDE did not apply it"
    return None


def _clk_tck() -> int:
    try:
        out = subprocess.check_output(["getconf", "CLK_TCK"], text=True, timeout=2).strip()
        return int(out) or 100
    except (OSError, subprocess.SubprocessError, ValueError):
        return 100


def _read_mem() -> tuple[int, int]:
    total = avail = 0
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                total = int(line.split()[1]) * 1024
            elif line.startswith("MemAvailable:"):
                avail = int(line.split()[1]) * 1024
    except OSError:
        pass
    return total, avail


# --- Temperature / GPU autodetection (Linux) -------------------------------
# Probed once, then cached. Works with no setup on AMD (k10temp/zenpower,
# amdgpu), Intel (coretemp, i915/xe) and NVIDIA (nouveau hwmon or nvidia-smi).
# Anything that cannot be read gives None, which the board shows as "–".

_TEMP_MIN_C = 1.0
_TEMP_MAX_C = 125.0
# CPU hwmon drivers, best first. Labels are tried in order inside each chip.
_CPU_HWMON = (
    ("zenpower", ("Tdie", "Tctl")),
    ("k10temp", ("Tdie", "Tctl", "Tccd1")),
    ("coretemp", ("Package id 0", "Package id 1")),
    ("cpu_thermal", ()),
    ("cpu-thermal", ()),
    ("soc_thermal", ()),
    ("thinkpad", ("CPU",)),
)
# GPU hwmon drivers, best first.
_GPU_HWMON = (
    ("amdgpu", ("edge", "junction")),
    ("radeon", ()),
    ("nouveau", ()),
    ("xe", ("pkg", "package")),
    ("i915", ()),
)
_CPU_ZONE_TYPES = ("x86_pkg_temp", "cpu-thermal", "cpu_thermal", "soc_thermal", "tcpu", "k10temp", "acpitz")


def _valid_temp(value: float | None) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return round(value, 1) if _TEMP_MIN_C <= value <= _TEMP_MAX_C else None


def _read_milli_c(path: Path) -> float | None:
    try:
        raw = path.read_text(encoding="ascii").strip()
        num = float(raw)
    except (OSError, ValueError):
        return None
    return _valid_temp(num / 1000.0 if abs(num) > 200 else num)


def _parse_nvidia_smi(out: str) -> tuple[float | None, float | None]:
    """First GPU line of `nvidia-smi --query-gpu=utilization.gpu,temperature.gpu`."""
    lines = [line for line in (out or "").strip().splitlines() if line.strip()]
    if not lines:
        return None, None
    parts = [p.strip() for p in lines[0].split(",")]
    util = temp = None
    try:
        util = max(0.0, min(100.0, float(parts[0])))
    except (ValueError, IndexError):
        util = None
    try:
        temp = _valid_temp(float(parts[1]))
    except (ValueError, IndexError):
        temp = None
    return util, temp


def _hwmon_temp_inputs(chip: Path) -> list[tuple[str, Path]]:
    """(label, temp*_input path) for one hwmon chip, in index order."""
    found: list[tuple[int, str, Path]] = []
    try:
        entries = list(chip.glob("temp*_input"))
    except OSError:
        return []
    for inp in entries:
        stem = inp.name[: -len("_input")]
        try:
            index = int(stem[4:])
        except ValueError:
            index = 999
        label = ""
        try:
            label = (chip / f"{stem}_label").read_text(encoding="utf-8").strip()
        except OSError:
            label = ""
        found.append((index, label, inp))
    found.sort(key=lambda item: item[0])
    return [(label, path) for _i, label, path in found]


def _pick_inputs(inputs: list[tuple[str, Path]], labels: tuple[str, ...], fallback_all: bool) -> list[Path]:
    for want in labels:
        hit = [path for label, path in inputs if label.lower() == want.lower()]
        if hit:
            return hit
    if not inputs:
        return []
    if fallback_all:
        return [path for _label, path in inputs]
    return [inputs[0][1]]


class LinuxSensors:
    """Finds CPU/GPU temperature sources once and keeps reading them."""

    REPROBE_S = 60.0

    def __init__(self, sys_root: str | Path = "/sys", nvidia_smi: str | None = "auto", runner=None) -> None:
        self.root = Path(sys_root)
        self._nvidia_arg = nvidia_smi
        self._run = runner or subprocess.check_output
        self._probed_at = 0.0
        self.cpu_paths: list[Path] = []
        self.cpu_source = "none"
        self.gpu_temp_paths: list[Path] = []
        self.gpu_busy_path: Path | None = None
        self.gpu_source = "none"
        self.nvidia: str | None = None
        self._nvidia_fail = 0
        self._lock = threading.Lock()

    # probing -----------------------------------------------------------
    def _chips(self) -> list[tuple[str, Path]]:
        base = self.root / "class" / "hwmon"
        chips: list[tuple[str, Path]] = []
        try:
            entries = sorted(base.iterdir(), key=lambda p: p.name)
        except OSError:
            return []
        for chip in entries:
            try:
                name = (chip / "name").read_text(encoding="utf-8").strip()
            except OSError:
                continue
            chips.append((name, chip))
        return chips

    def probe(self) -> None:
        self._probed_at = time.monotonic()
        chips = self._chips()
        self.cpu_paths, self.cpu_source = [], "none"
        for driver, labels in _CPU_HWMON:
            for name, chip in chips:
                if name != driver:
                    continue
                inputs = _hwmon_temp_inputs(chip)
                # coretemp without a package line: use the hottest core.
                paths = _pick_inputs(inputs, labels, fallback_all=(driver == "coretemp"))
                paths = [p for p in paths if _read_milli_c(p) is not None]
                if paths:
                    self.cpu_paths.extend(paths)
                    self.cpu_source = f"hwmon:{driver}"
            if self.cpu_paths:
                break
        if not self.cpu_paths:
            self._probe_thermal_zones()

        self.gpu_temp_paths, self.gpu_busy_path, self.gpu_source = [], None, "none"
        for driver, labels in _GPU_HWMON:
            for name, chip in chips:
                if name != driver:
                    continue
                paths = [p for p in _pick_inputs(_hwmon_temp_inputs(chip), labels, False) if _read_milli_c(p) is not None]
                busy = chip / "device" / "gpu_busy_percent"
                busy_ok = self._read_pct(busy) is not None
                if paths or busy_ok:
                    self.gpu_temp_paths = paths
                    self.gpu_busy_path = busy if busy_ok else None
                    self.gpu_source = f"hwmon:{driver}"
                    break
            if self.gpu_source != "none":
                break

        if self._nvidia_arg == "auto":
            self.nvidia = shutil.which("nvidia-smi")
        else:
            self.nvidia = self._nvidia_arg
        self._nvidia_fail = 0

    def _probe_thermal_zones(self) -> None:
        base = self.root / "class" / "thermal"
        zones: list[tuple[str, Path]] = []
        try:
            entries = sorted(base.glob("thermal_zone*"), key=lambda p: p.name)
        except OSError:
            entries = []
        for zone in entries:
            try:
                kind = (zone / "type").read_text(encoding="utf-8").strip().lower()
            except OSError:
                kind = ""
            temp = zone / "temp"
            if _read_milli_c(temp) is not None:
                zones.append((kind, temp))
        for want in _CPU_ZONE_TYPES:
            hit = [path for kind, path in zones if kind == want]
            if hit:
                self.cpu_paths = hit
                self.cpu_source = f"thermal:{want}"
                return
        hit = [path for kind, path in zones if "cpu" in kind or "pkg" in kind or "soc" in kind]
        if hit:
            self.cpu_paths, self.cpu_source = hit, "thermal:cpu"
            return
        if zones:
            # Unknown zone names: keep the old behaviour (hottest zone).
            self.cpu_paths, self.cpu_source = [path for _k, path in zones], "thermal:any"

    def _ensure(self) -> None:
        if not self._probed_at:
            self.probe()

    def _maybe_reprobe(self) -> None:
        if time.monotonic() - self._probed_at >= self.REPROBE_S:
            self.probe()

    @staticmethod
    def _read_pct(path: Path | None) -> float | None:
        if path is None:
            return None
        try:
            value = float(path.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            return None
        return max(0.0, min(100.0, value)) if value == value else None

    # reading -----------------------------------------------------------
    def cpu_temp(self) -> float | None:
        with self._lock:
            try:
                self._ensure()
                values = [v for v in (_read_milli_c(p) for p in self.cpu_paths) if v is not None]
                if not values:
                    self._maybe_reprobe()
                    values = [v for v in (_read_milli_c(p) for p in self.cpu_paths) if v is not None]
                return max(values) if values else None
            except Exception:
                return None

    def gpu(self) -> tuple[float | None, float | None]:
        """(utilization %, temperature °C). None for anything unknown."""
        with self._lock:
            try:
                self._ensure()
                util = temp = None
                if self.nvidia and self._nvidia_fail < 3:
                    try:
                        out = self._run(
                            [self.nvidia, "--query-gpu=utilization.gpu,temperature.gpu", "--format=csv,noheader,nounits"],
                            text=True,
                            timeout=2,
                            stderr=subprocess.DEVNULL,
                        )
                        util, temp = _parse_nvidia_smi(out)
                        self._nvidia_fail = 0 if (util is not None or temp is not None) else self._nvidia_fail + 1
                    except (OSError, subprocess.SubprocessError, ValueError):
                        self._nvidia_fail += 1
                if util is None and temp is None:
                    temps = [v for v in (_read_milli_c(p) for p in self.gpu_temp_paths) if v is not None]
                    temp = max(temps) if temps else None
                    util = self._read_pct(self.gpu_busy_path)
                    if temp is None and util is None and (self.gpu_temp_paths or self.gpu_busy_path):
                        self._maybe_reprobe()
                return util, temp
            except Exception:
                return None, None

    def sources(self) -> dict:
        gpu = self.gpu_source
        if self.nvidia and self._nvidia_fail < 3:
            gpu = "nvidia-smi" if gpu == "none" else "nvidia-smi+" + gpu
        return {"cpu": self.cpu_source, "gpu": gpu}


def _cpu_temp() -> float | None:
    return _LINUX_SENSORS.cpu_temp()


def _gpu() -> tuple[float | None, float | None]:
    return _LINUX_SENSORS.gpu()


_LINUX_SENSORS = LinuxSensors()


def _pam_login(user: str, password: str) -> bool:
    """Authenticate against the login PAM service. The password is not logged."""
    import ctypes

    libc = ctypes.CDLL(None)
    libc.malloc.restype = ctypes.c_void_p
    libc.malloc.argtypes = [ctypes.c_size_t]
    pam = ctypes.CDLL("libpam.so.0")
    PAM_PROMPT_ECHO_OFF = 1
    PAM_SUCCESS = 0

    class PamMessage(ctypes.Structure):
        _fields_ = [("msg_style", ctypes.c_int), ("msg", ctypes.c_char_p)]

    class PamResponse(ctypes.Structure):
        _fields_ = [("resp", ctypes.c_char_p), ("resp_retcode", ctypes.c_int)]

    conv_func = ctypes.CFUNCTYPE(
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.POINTER(PamMessage)),
        ctypes.POINTER(ctypes.POINTER(PamResponse)),
        ctypes.c_void_p,
    )

    class PamConv(ctypes.Structure):
        _fields_ = [("conv", conv_func), ("appdata_ptr", ctypes.c_void_p)]

    secret = password.encode("utf-8")

    def alloc(data: bytes) -> int:
        buf = libc.malloc(len(data) + 1)
        if not buf:
            raise OSError("malloc failed")
        ctypes.memmove(buf, data + b"\0", len(data) + 1)
        return buf

    @conv_func
    def converse(count, messages, responses, _app):
        if count < 1:
            return 19
        size = count * ctypes.sizeof(PamResponse)
        raw = libc.malloc(size)
        if not raw:
            return 19
        ctypes.memset(raw, 0, size)
        rows = ctypes.cast(raw, ctypes.POINTER(PamResponse))
        for i in range(count):
            style = messages[i].contents.msg_style
            if style == PAM_PROMPT_ECHO_OFF:
                rows[i].resp = ctypes.cast(alloc(secret), ctypes.c_char_p)
            elif style == 2:
                rows[i].resp = ctypes.cast(alloc(user.encode("utf-8")), ctypes.c_char_p)
            else:
                rows[i].resp = None
            rows[i].resp_retcode = 0
        responses[0] = rows
        return PAM_SUCCESS

    pam.pam_start.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.POINTER(PamConv), ctypes.POINTER(ctypes.c_void_p)]
    pam.pam_start.restype = ctypes.c_int
    pam.pam_authenticate.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_authenticate.restype = ctypes.c_int
    pam.pam_end.argtypes = [ctypes.c_void_p, ctypes.c_int]
    pam.pam_end.restype = ctypes.c_int

    handle = ctypes.c_void_p()
    conv = PamConv(converse, None)
    started = pam.pam_start(b"login", user.encode("utf-8"), ctypes.byref(conv), ctypes.byref(handle))
    if started != PAM_SUCCESS:
        raise OSError("pam_start failed")
    try:
        return pam.pam_authenticate(handle, 0) == PAM_SUCCESS
    finally:
        pam.pam_end(handle, 0)


def _post_json(url: str, payload: dict, headers: dict, timeout: int = 45) -> tuple[int, str]:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return getattr(res, "status", 200), res.read(200_000).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(80_000).decode("utf-8", "replace")


def _ai_text(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    direct = data.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    chunks: list[str] = []
    for item in data.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                chunks.append(part["text"])
    if chunks:
        return "\n".join(chunks).strip()
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"].strip()
    return ""


class Host:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.windows: list[dict] = []
        self._cpu_prev: tuple[int, int] | None = None
        self._proc_prev: dict[int, tuple[int, float]] = {}
        self._clk = _clk_tck()
        self._gpu_at = 0.0
        self._gpu: tuple[float | None, float | None] = (None, None)
        self._xfer: dict[str, dict] = {}

    def sample_system(self) -> dict:
        if os.name == "nt":
            from winhost import sample_system

            return sample_system(self)
        if sys.platform == "darwin":
            from machost import sample_system

            return sample_system(self)
        total_jiffies = idle = 0
        try:
            parts = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
            nums = [int(x) for x in parts]
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            total_jiffies = sum(nums)
        except (OSError, ValueError, IndexError):
            nums = []
        cpu_pct = None
        if self._cpu_prev and total_jiffies:
            dt = total_jiffies - self._cpu_prev[0]
            di = idle - self._cpu_prev[1]
            if dt > 0:
                cpu_pct = max(0.0, min(100.0, 100.0 * (1 - di / dt)))
        if total_jiffies:
            self._cpu_prev = (total_jiffies, idle)

        mem_total, mem_avail = _read_mem()
        mem_used = max(0, mem_total - mem_avail)
        mem_pct = (mem_used / mem_total * 100) if mem_total else 0.0
        load1, load5, load15 = os.getloadavg()
        now = time.time()
        if now - self._gpu_at > 3:
            self._gpu = _gpu()
            self._gpu_at = now
        gpu_pct, gpu_temp = self._gpu
        return {
            "ok": True,
            "hostname": os.uname().nodename,
            "cpuCount": os.cpu_count() or 1,
            "cpuPct": round(cpu_pct, 1) if cpu_pct is not None else None,
            "cpuTempC": _cpu_temp(),
            "memTotal": mem_total,
            "memUsed": mem_used,
            "memPct": round(mem_pct, 1),
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
            "gpuPct": gpu_pct,
            "gpuTempC": gpu_temp,
            "sensorSource": _LINUX_SENSORS.sources(),
        }

    def _scan_procs(self) -> list[dict]:
        with self.lock:
            return self._scan_procs_locked()

    def _scan_procs_locked(self) -> list[dict]:
        if os.name == "nt":
            from winhost import scan_processes

            return scan_processes(self)
        if sys.platform == "darwin":
            from machost import scan_processes

            return scan_processes(self)
        now = time.time()
        clk = self._clk
        mem_total, _avail = _read_mem()
        page = os.sysconf("SC_PAGE_SIZE") or 4096
        found: list[dict] = []
        seen: set[int] = set()
        proc_root = Path("/proc")
        for entry in proc_root.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                stat = (entry / "stat").read_text(encoding="utf-8")
                lparen = stat.find("(")
                rparen = stat.rfind(")")
                comm = stat[lparen + 1 : rparen]
                rest = stat[rparen + 2 :].split()
                state = rest[0]
                utime = int(rest[11])
                stime = int(rest[12])
                threads = int(rest[17])
                rss_pages = int(rest[21])
                cmd_raw = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
            except (OSError, ValueError, IndexError):
                continue
            if not cmd_raw:
                continue
            ticks = utime + stime
            prev = self._proc_prev.get(pid)
            cpu = 0.0
            if prev:
                dt = now - prev[1]
                if dt > 0.05:
                    cpu = max(0.0, ((ticks - prev[0]) / clk / dt) * 100 / max(1, os.cpu_count() or 1))
            self._proc_prev[pid] = (ticks, now)
            seen.add(pid)
            rss = rss_pages * page
            found.append(
                {
                    "pid": pid,
                    "comm": comm,
                    "cmdline": cmd_raw[:180],
                    "state": state,
                    "threads": threads,
                    "cpuPct": round(cpu, 1),
                    "rssMb": round(rss / (1024 * 1024), 1),
                    "memPct": round((rss / mem_total) * 100, 2) if mem_total else 0.0,
                }
            )
        self._proc_prev = {pid: val for pid, val in self._proc_prev.items() if pid in seen}
        return found

    def process_groups(self) -> dict:
        procs = self._scan_procs()
        groups: dict[str, dict] = {}
        for proc in procs:
            key = proc["comm"] or "?"
            group = groups.get(key)
            if group is None:
                groups[key] = {
                    "comm": key,
                    "count": 1,
                    "cpuPct": proc["cpuPct"],
                    "memPct": proc["memPct"],
                    "rssMb": proc["rssMb"],
                    "pid": proc["pid"],
                    "state": proc["state"],
                    "threads": proc["threads"],
                    "cmdline": proc["cmdline"],
                }
            else:
                group["count"] += 1
                group["cpuPct"] = round(group["cpuPct"] + proc["cpuPct"], 1)
                group["memPct"] = round(group["memPct"] + proc["memPct"], 2)
                group["rssMb"] = round(group["rssMb"] + proc["rssMb"], 1)
                group["threads"] += proc["threads"]
                if proc["cpuPct"] >= group.get("_top", -1):
                    group["pid"] = proc["pid"]
                    group["state"] = proc["state"]
                    group["cmdline"] = proc["cmdline"]
                    group["_top"] = proc["cpuPct"]
        rows = sorted(groups.values(), key=lambda g: g["cpuPct"], reverse=True)[:50]
        for row in rows:
            row.pop("_top", None)
        return {"ok": True, "groups": rows}

    def watch_app(self, query: str) -> dict:
        needle = (query or "").strip().lower()
        procs = self._scan_procs()
        matched = [
            p
            for p in procs
            if needle and (needle in p["comm"].lower() or needle in p["cmdline"].lower())
        ]
        if not matched:
            return {
                "ok": True,
                "query": query,
                "count": 0,
                "cpuPct": 0,
                "memPct": 0,
                "rssMb": 0,
                "pid": None,
                "state": "",
                "threads": 0,
                "comm": query,
            }
        top = max(matched, key=lambda p: p["cpuPct"])
        return {
            "ok": True,
            "query": query,
            "count": len(matched),
            "cpuPct": round(sum(p["cpuPct"] for p in matched), 1),
            "memPct": round(sum(p["memPct"] for p in matched), 2),
            "rssMb": round(sum(p["rssMb"] for p in matched), 1),
            "pid": top["pid"],
            "state": top["state"],
            "threads": sum(p["threads"] for p in matched),
            "comm": top["comm"],
        }

    def set_windows(self, payload: str) -> None:
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            data = []
        if not isinstance(data, list):
            data = []
        clean = []
        for item in data[:80]:
            if not isinstance(item, dict):
                continue
            clean.append(
                {
                    "caption": str(item.get("caption") or "")[:180],
                    "cls": str(item.get("cls") or ""),
                    "name": str(item.get("name") or ""),
                    "pid": int(item.get("pid") or 0),
                    "active": bool(item.get("active")),
                    "minimized": bool(item.get("minimized")),
                    "w": int(item.get("w") or 0),
                    "h": int(item.get("h") or 0),
                }
            )
        with self.lock:
            self.windows = clean

    def get_windows(self) -> dict:
        if os.name == "nt":
            try:
                from winhost import collect_windows

                collect_windows(self)
            except OSError:
                pass
        elif sys.platform == "darwin":
            try:
                from machost import collect_windows

                collect_windows(self)
            except OSError:
                pass
        with self.lock:
            rows = list(self.windows)
        return {"ok": True, "windows": rows}

    def launch(self, command: str) -> dict:
        command = (command or "").strip()
        if not command:
            return {"ok": False, "error": "empty command"}
        if os.name == "nt":
            from winhost import launch as win_launch

            return win_launch(command)
        if sys.platform == "darwin":
            from machost import launch as mac_launch

            return mac_launch(command)
        try:
            if command.startswith("http://") or command.startswith("https://"):
                subprocess.Popen(["xdg-open", command], start_new_session=True)
            else:
                subprocess.Popen(command, shell=True, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def fetch(self, url: str) -> dict:
        url = (url or "").strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            return {"ok": False, "error": "only http(s) URLs"}
        req = urllib.request.Request(url, headers={"User-Agent": "laden-ops"})
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                raw = res.read(200_000)
                status = getattr(res, "status", 200)
        except urllib.error.HTTPError as exc:
            raw = exc.read(80_000)
            status = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        text = raw.decode("utf-8", "replace")
        body: object
        try:
            body = json.loads(text)
        except json.JSONDecodeError:
            body = text[:4000]
        return {"ok": True, "status": status, "body": body}

    def local_user(self) -> dict:
        uid = 0 if os.name == "nt" else os.getuid()
        return {
            "ok": True,
            "username": getpass.getuser(),
            "homedir": str(Path.home()),
            "uid": uid,
        }

    def verify_password(self, password: str) -> dict:
        """Check the login password. Linux uses libpam. macOS uses PAM. Windows uses LogonUser."""
        user = getpass.getuser()
        if not password:
            return {"ok": False, "method": "none"}
        if os.name == "nt":
            from winhost import check_password

            return check_password(user, password)
        if sys.platform == "darwin":
            from machost import check_password

            return check_password(user, password)
        try:
            ok = _pam_login(user, password)
        except OSError:
            return {"ok": password == user, "method": "username-fallback"}
        return {"ok": ok, "method": "pam"}

    def _vault_dir(self, rel: str) -> Path:
        root = VAULT_WORK.resolve()
        raw = str(rel or "").replace("\\", "/")
        if ".." in raw or raw.startswith("/"):
            raise ValueError("path escapes vault workspace")
        safe = re.sub(r"[^A-Za-z0-9_./-]+", "_", raw).strip("/")[:180]
        dest = (root / (safe or "_root")).resolve()
        if dest != root and root not in dest.parents:
            raise ValueError("path escapes vault workspace")
        return dest

    def save_kit(self, payload: str) -> dict:
        """Persist the open kit beside account.json. The text is not logged."""
        try:
            return write_private_kit(CONFIG_DIR, payload)
        except OSError:
            return {"ok": False, "error": "Could not write the kit"}

    def save_vault_conf(self, payload: str) -> dict:
        """Persist vault.conf beside account.json. The text is not logged."""
        try:
            return write_vault_conf(CONFIG_DIR, payload)
        except OSError:
            return {"ok": False, "error": "Could not write vault.conf"}

    def load_vault_conf(self) -> dict:
        """Read this device's vault.conf. The text is not logged."""
        path = CONFIG_DIR / "vault.conf"
        if not path.is_file():
            return {"ok": True, "exists": False}
        try:
            raw = path.read_bytes()
        except OSError:
            return {"ok": False, "error": "Could not read vault.conf"}
        if len(raw) > KIT_MAX_BYTES:
            return {"ok": False, "error": "vault.conf is too large"}
        return {"ok": True, "exists": True, "text": raw.decode("utf-8", "replace")}

    def account_fetch(self, url: str, method: str = "GET", body: str = "", token: str = "") -> dict:
        """Call the Laden cloud vault service. The body and token are not logged."""
        target = (url or "").strip()
        verb = (method or "GET").strip().upper()
        if verb not in ("GET", "POST", "PUT"):
            return {"ok": False, "error": "method not allowed"}
        parsed = urllib.parse.urlparse(target)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("https", "http") or not host:
            return {"ok": False, "error": "only http(s) URLs"}
        if parsed.scheme == "http" and host not in ("127.0.0.1", "localhost"):
            return {"ok": False, "error": "the cloud copy needs https"}
        data = None
        headers = {"User-Agent": "laden-ops", "Accept": "application/json, text/plain"}
        if token:
            headers["Authorization"] = "Bearer " + str(token)
        if verb in ("POST", "PUT"):
            raw = str(body or "").encode("utf-8")
            if len(raw) > 8_000_000:
                return {"ok": False, "error": "the kit is too large to send"}
            data = raw
            headers["Content-Type"] = "text/plain; charset=utf-8"
        req = urllib.request.Request(target, data=data, headers=headers, method=verb)
        try:
            with urllib.request.urlopen(req, timeout=8) as res:
                raw = res.read(8_000_000)
                status = getattr(res, "status", 200)
        except urllib.error.HTTPError as exc:
            raw = exc.read(8_000_000)
            status = exc.code
        except (urllib.error.URLError, TimeoutError, OSError):
            return {"ok": False, "error": "Could not reach the cloud copy"}
        return {"ok": True, "status": status, "body": raw.decode("utf-8", "replace")}

    def prepare_user_folder(self, email: str, username: str) -> dict:
        """Create the account folder, config, and vault. The password is not written here."""
        mail = str(email or "").strip()
        name = str(username or "").strip()
        if "@" not in mail or len(mail) > 120:
            return {"ok": False, "error": "Need an email address"}
        if not name or len(name) > 32:
            return {"ok": False, "error": "Need a username"}
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            VAULT_WORK.mkdir(parents=True, exist_ok=True)
            for folder in ("legal", "credentials", "engagements"):
                made = self.vault_ensure_dir(folder)
                if not made.get("ok"):
                    return made
            account = {
                "product": "Ldash",
                "version": "1.0.0",
                "email": mail,
                "username": name,
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            (CONFIG_DIR / "account.json").write_text(json.dumps(account, indent=2) + "\n", encoding="utf-8")
            return {"ok": True, "path": str(CONFIG_DIR), "vault": str(VAULT_WORK)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def vault_ensure_dir(self, rel: str) -> dict:
        try:
            dest = self._vault_dir(rel)
            dest.mkdir(parents=True, exist_ok=True)
            readme = dest / "NOTES.md"
            if not readme.exists():
                readme.write_text(
                    f"# Vault workspace\n\nAuthorized engagement notes only.\nPath: {dest}\n",
                    encoding="utf-8",
                )
            return {"ok": True, "path": str(dest)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def export_case_pdf(
        self,
        rel: str,
        title: str,
        scope: str,
        summary: str,
        case_body: str,
        dumps: list | None = None,
        launch: bool = True,
    ) -> dict:
        """Write a printable case PDF into the scope's vault folder and open it."""
        try:
            dest = self._vault_dir(rel)
            dest.mkdir(parents=True, exist_ok=True)
            file = dest / "case.pdf"
            render_case_pdf(
                file,
                title=str(title or "case")[:120],
                scope=str(scope or "")[:400],
                summary=str(summary or "")[:8000],
                case_body=str(case_body or "")[:20000],
                dumps=list(dumps or [])[:40],
            )
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        opened = {"ok": True, "path": str(file)}
        if launch:
            opened = self.open_path(str(file))
            opened["path"] = str(file)
        return opened

    def export_budget_pdf(self, rel: str, title: str, sections: list | None = None, launch: bool = True) -> dict:
        """Write the monthly budget sheet into the vault workspace and open it."""
        try:
            clean: list[tuple[str, str]] = []
            for item in list(sections or [])[:8]:
                if not isinstance(item, dict):
                    continue
                heading = str(item.get("heading") or "SECTION")[:40]
                body = str(item.get("body") or "")[:12000]
                clean.append((heading, body))
            if not clean:
                clean = [("MONTH", "(empty)")]
            dest = self._vault_dir(rel or "economy")
            dest.mkdir(parents=True, exist_ok=True)
            file = dest / "budget.pdf"
            render_sheet(file, str(title or "month")[:120], clean, banner="BOOKS")
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        opened = {"ok": True, "path": str(file)}
        if launch:
            opened = self.open_path(str(file))
            opened["path"] = str(file)
        return opened

    def vault_write_file(self, rel: str, filename: str, data_b64: str) -> dict:
        """Store an uploaded case document. The file is written and never executed."""
        import base64
        try:
            payload = str(data_b64 or "")
            if len(payload) > 12_000_000:
                return {"ok": False, "error": "file is over 8 MB"}
            raw = base64.b64decode(payload, validate=False)
            if len(raw) > 8_000_000:
                return {"ok": False, "error": "file is over 8 MB"}
            dest = self._vault_dir(rel)
            dest.mkdir(parents=True, exist_ok=True)
            name = self._upload_name(filename)
            file = dest / name
            file.write_bytes(raw)
            return {"ok": True, "path": str(file)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _upload_name(self, filename: str) -> str:
        name = Path(str(filename or "file")).name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "file"
        if name in (".", ".."):
            return "file"
        return name

    def _export_name(self, filename: str) -> str:
        name = Path(str(filename or "laden.vault.conf")).name
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")[:80] or "laden.vault.conf"
        if name in (".", ".."):
            return "laden.vault.conf"
        return name

    def _xfer_prune(self) -> None:
        now = time.time()
        dead = [key for key, item in self._xfer.items() if now - float(item.get("at") or 0) > 600]
        for key in dead:
            self._xfer.pop(key, None)
        if len(self._xfer) > 8:
            oldest = sorted(self._xfer, key=lambda key: float(self._xfer[key].get("at") or 0))
            for key in oldest[: len(self._xfer) - 8]:
                self._xfer.pop(key, None)

    def vault_read_open(self, rel: str, filename: str) -> dict:
        """Open a vault file for chunked reading into a vault.conf. The bytes are not executed."""
        try:
            dest = self._vault_dir(rel)
            file = dest / self._upload_name(filename)
            if not file.is_file():
                return {"ok": False, "error": "file not found"}
            raw = file.read_bytes()
            if len(raw) > 8_000_000:
                return {"ok": False, "error": "file is over 8 MB"}
            data = base64.b64encode(raw).decode("ascii")
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
        with self.lock:
            self._xfer_prune()
            token = secrets.token_hex(8)
            self._xfer[token] = {"kind": "read", "at": time.time(), "data": data}
        return {"ok": True, "token": token, "length": len(data)}

    def vault_read_chunk(self, token: str, offset: int) -> dict:
        with self.lock:
            item = self._xfer.get(str(token or ""))
            if not item or item.get("kind") != "read":
                return {"ok": False, "error": "transfer expired"}
            data = str(item.get("data") or "")
            start = max(0, int(offset or 0))
            chunk = data[start : start + 240_000]
            nxt = start + len(chunk)
            done = nxt >= len(data)
            if done:
                self._xfer.pop(str(token), None)
            else:
                item["at"] = time.time()
        return {"ok": True, "data": chunk, "next": nxt, "done": done}

    def vault_write_open(self, rel: str, filename: str) -> dict:
        with self.lock:
            self._xfer_prune()
            token = secrets.token_hex(8)
            self._xfer[token] = {
                "kind": "write",
                "at": time.time(),
                "rel": str(rel or ""),
                "filename": str(filename or "file"),
                "parts": [],
                "size": 0,
            }
        return {"ok": True, "token": token}

    def vault_write_chunk(self, token: str, data: str) -> dict:
        piece = str(data or "")
        with self.lock:
            item = self._xfer.get(str(token or ""))
            if not item or item.get("kind") != "write":
                return {"ok": False, "error": "transfer expired"}
            item["size"] = int(item.get("size") or 0) + len(piece)
            if item["size"] > 12_000_000:
                self._xfer.pop(str(token), None)
                return {"ok": False, "error": "file is over 8 MB"}
            item["parts"].append(piece)
            item["at"] = time.time()
        return {"ok": True}

    def vault_write_finish(self, token: str) -> dict:
        with self.lock:
            item = self._xfer.pop(str(token or ""), None)
        if not item or item.get("kind") != "write":
            return {"ok": False, "error": "transfer expired"}
        return self.vault_write_file(str(item.get("rel") or ""), str(item.get("filename") or "file"), "".join(item.get("parts") or []))

    def save_text_open(self, filename: str) -> dict:
        with self.lock:
            self._xfer_prune()
            token = secrets.token_hex(8)
            self._xfer[token] = {"kind": "text", "at": time.time(), "filename": self._export_name(filename), "parts": [], "size": 0}
        return {"ok": True, "token": token}

    def save_text_chunk(self, token: str, data: str) -> dict:
        piece = str(data or "")
        with self.lock:
            item = self._xfer.get(str(token or ""))
            if not item or item.get("kind") != "text":
                return {"ok": False, "error": "transfer expired"}
            item["size"] = int(item.get("size") or 0) + len(piece)
            if item["size"] > 96_000_000:
                self._xfer.pop(str(token), None)
                return {"ok": False, "error": "vault.conf is too large"}
            item["parts"].append(piece)
            item["at"] = time.time()
        return {"ok": True}

    def save_text_finish(self, token: str) -> dict:
        """Write an exported vault.conf. Desktop leaves it in the exports folder."""
        with self.lock:
            item = self._xfer.pop(str(token or ""), None)
        if not item or item.get("kind") != "text":
            return {"ok": False, "error": "transfer expired"}
        try:
            dest = CONFIG_DIR / "exports"
            dest.mkdir(parents=True, exist_ok=True)
            file = dest / str(item.get("filename") or "laden.vault.conf")
            file.write_text("".join(item.get("parts") or []), encoding="utf-8")
            return {"ok": True, "path": str(file)}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def vault_write_note(self, rel: str, filename: str, body: str) -> dict:
        try:
            dest = self._vault_dir(rel)
            dest.mkdir(parents=True, exist_ok=True)
            name = re.sub(r"[^\w.\-]+", "_", str(filename or "note.md"))[:80] or "note.md"
            file = dest / name
            file.write_text(str(body or ""), encoding="utf-8")
            return {"ok": True, "path": str(file)}
        except (OSError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def open_terminal(self, cwd: str | None, command: str | None) -> dict:
        if os.name == "nt":
            from winhost import open_terminal as win_terminal

            return win_terminal(cwd, command)
        if sys.platform == "darwin":
            from machost import open_terminal as mac_terminal

            return mac_terminal(cwd, command)
        work = str(Path.home())
        cwd_text = str(cwd or "").strip()
        if cwd_text:
            cand = Path(cwd_text).expanduser()
            if cand.is_dir():
                work = str(cand)
        extra = " ".join(str(command or "").split())[:400]
        inner = f"{extra}; exec bash" if extra else None
        runners: list[tuple[str, list[str]]] = [
            ("wezterm", ["wezterm", "start", "--cwd", work] + (["--", "bash", "-lc", inner] if inner else [])),
            ("kitty", ["kitty", "--directory", work] + (["--", "bash", "-lc", inner] if inner else [])),
            ("alacritty", ["alacritty", "--working-directory", work] + (["-e", "bash", "-lc", inner] if inner else [])),
            ("konsole", ["konsole", "--workdir", work] + (["-e", "bash", "-lc", inner] if inner else [])),
            ("gnome-terminal", ["gnome-terminal", f"--working-directory={work}"] + (["--", "bash", "-lc", inner] if inner else [])),
            ("xterm", ["xterm", "-e", "bash", "-lc", f"cd {shlex.quote(work)}; {inner or 'exec bash'}"]),
        ]
        for binary, argv in runners:
            if not shutil.which(binary):
                continue
            try:
                subprocess.Popen(argv, start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"ok": True, "cwd": work}
            except OSError:
                continue
        return {"ok": False, "error": "no terminal found"}

    def open_path(self, target: str) -> dict:
        target = (target or "").strip()
        if not target:
            return {"ok": False, "error": "empty path"}
        if target.startswith("http://") or target.startswith("https://"):
            return self.launch(target)
        path = Path(target).expanduser()
        if not path.is_absolute():
            path = Path.home() / path
        if not path.exists():
            return {"ok": False, "error": "path not found"}
        if os.name == "nt":
            from winhost import open_path as win_open

            return win_open(str(path))
        if sys.platform == "darwin":
            from machost import open_path as mac_open

            return mac_open(str(path))
        try:
            subprocess.Popen(["xdg-open", str(path)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"ok": True, "path": str(path)}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def ai_chat(self, provider: str, api_key: str, system: str, user: str, model: str | None = None) -> dict:
        key = (api_key or "").strip()
        if not key:
            return {"ok": False, "error": "missing api key"}
        which = provider if provider in ("xai", "groq", "openrouter") else "xai"
        system = str(system or "")[:4000]
        user = str(user or "")[:12000]
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json", "User-Agent": "laden-ops"}
        if which == "xai":
            url = "https://api.x.ai/v1/responses"
            payload = {
                "model": model or "grok-4.7",
                "input": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        else:
            url = (
                "https://api.groq.com/openai/v1/chat/completions"
                if which == "groq"
                else "https://openrouter.ai/api/v1/chat/completions"
            )
            if which == "openrouter":
                headers["HTTP-Referer"] = "https://laden.no"
                headers["X-Title"] = "Laden Ops Journal"
            payload = {
                "model": model or ("llama-3.3-70b-versatile" if which == "groq" else "openrouter/free"),
                "temperature": 0.4,
                "max_tokens": 700,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            }
        try:
            status, text = _post_json(url, payload, headers)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return {"ok": False, "error": str(exc)}
        if status >= 400:
            return {"ok": False, "error": f"{which} {status}: {text[:180]}"}
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return {"ok": False, "error": "bad AI JSON"}
        content = _ai_text(data)
        if not content:
            return {"ok": False, "error": "empty AI response"}
        return {"ok": True, "text": content}

    def install_kwin(self, shortcut: str) -> dict:
        shortcut = save_shortcut(shortcut)
        if os.name == "nt" or sys.platform == "darwin":
            return {"ok": True, "shortcut": shortcut, "backend": "hotkey"}
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        # Always register the product default. registerShortcut will not replace
        # an existing binding, and it must not record the saved key as the default.
        script = KWIN_SCRIPT_JS.replace("__SHORTCUT__", json.dumps(PRODUCT_SHORTCUT))
        KWIN_SCRIPT.write_text(script, encoding="utf-8")
        qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
        if not qdbus:
            return {"ok": False, "shortcut": shortcut, "error": "qdbus6 not found"}
        commands = [
            [qdbus, "org.kde.KWin", "/Scripting", "unloadScript", SCRIPT_PLUGIN],
            [qdbus, "org.kde.KWin", "/Scripting", "loadScript", str(KWIN_SCRIPT), SCRIPT_PLUGIN],
            [qdbus, "org.kde.KWin", "/Scripting", "start"],
        ]
        try:
            for cmd in commands:
                subprocess.run(cmd, check=False, timeout=4, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "shortcut": shortcut, "error": str(exc)}
        # KDE takes one binding; comma-separated Windows dual shortcuts use the first.
        error = _apply_saved_shortcut(primary_shortcut(shortcut))
        if error:
            return {"ok": False, "shortcut": shortcut, "default": PRODUCT_SHORTCUT, "error": error}
        return {"ok": True, "shortcut": shortcut, "default": PRODUCT_SHORTCUT, "backend": "kwin"}

    def set_pause_mode(self, enabled: bool) -> dict:
        flag = save_pause_mode(enabled)
        return {"ok": True, "enabled": flag}

    def pause_mode(self) -> dict:
        return {"ok": True, "enabled": default_pause_mode()}


KWIN_SCRIPT_JS = r"""
var SHORTCUT = __SHORTCUT__;
function dump() {
  var list = workspace.windowList();
  var out = [];
  for (var i = 0; i < list.length; i++) {
    var w = list[i];
    if (!w || !w.caption) continue;
    if (w.desktopWindow) continue;
    out.push({
      caption: String(w.caption).slice(0, 180),
      cls: String(w.resourceClass || ""),
      name: String(w.resourceName || ""),
      pid: Number(w.pid || 0),
      active: !!w.active,
      minimized: !!w.minimized,
      w: Math.round(Number(w.width || 0)),
      h: Math.round(Number(w.height || 0))
    });
  }
  try {
    callDBus("org.laden.OpsDash.Bridge", "/org/laden/OpsDash/Bridge", "org.laden.OpsDash.Bridge", "SetWindows", JSON.stringify(out));
  } catch (e) {}
}
function onToggle() {
  try {
    callDBus("org.laden.OpsDash.Bridge", "/org/laden/OpsDash/Bridge", "org.laden.OpsDash.Bridge", "Toggle");
  } catch (e) {
    print("OPS_DASH toggle " + e);
  }
}
try {
  registerShortcut("OpsDashToggle", "Toggle Laden Ops", SHORTCUT, onToggle);
} catch (e) {
  print("OPS_DASH shortcut " + e);
}
dump();
var opsTimer = new QTimer();
opsTimer.timeout.connect(dump);
opsTimer.start(2000);
"""
