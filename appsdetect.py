"""Detect default browser/terminal and list installed apps for first-time setup.

Only reads app listings (.desktop, .app bundles, Start Menu shortcuts, launcher
intents). Never crawls home file contents. The UI keeps the list on-device until
the user confirms the vault.
"""
from __future__ import annotations

import configparser
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_MAX_APPS = 400
_TERM_CANDIDATES = (
    "konsole",
    "gnome-terminal",
    "xfce4-terminal",
    "wezterm",
    "kitty",
    "alacritty",
    "foot",
    "qterminal",
)


def detect_defaults() -> dict:
    if os.name == "nt":
        from winhost import detect_defaults as win_detect

        return win_detect()
    if sys.platform == "darwin":
        from machost import detect_defaults as mac_detect

        return mac_detect()
    return _linux_detect_defaults()


def list_installed_apps() -> dict:
    if os.name == "nt":
        from winhost import list_installed_apps as win_list

        return win_list()
    if sys.platform == "darwin":
        from machost import list_installed_apps as mac_list

        return mac_list()
    return _linux_list_installed_apps()


def _choice(cid: str, name: str, command: str) -> dict:
    return {"id": cid, "name": name, "command": command}


def _linux_detect_defaults() -> dict:
    browser_choices = _linux_browser_choices()
    terminal_choices = _linux_terminal_choices()
    browser = _linux_default_browser(browser_choices) or (browser_choices[0] if browser_choices else _choice("browser", "Browser", "xdg-open https://www.google.com"))
    terminal = _linux_default_terminal(terminal_choices) or (terminal_choices[0] if terminal_choices else _choice("terminal", "Terminal", "x-terminal-emulator"))
    return {
        "ok": True,
        "platform": "linux",
        "browser": browser,
        "terminal": terminal,
        "browserChoices": browser_choices,
        "terminalChoices": terminal_choices,
        "terminalAvailable": True,
    }


def _linux_list_installed_apps() -> dict:
    apps: list[dict] = []
    seen: set[str] = set()
    for directory in _linux_desktop_dirs():
        try:
            entries = sorted(directory.glob("*.desktop"))
        except OSError:
            continue
        for path in entries:
            if len(apps) >= _MAX_APPS:
                break
            row = _parse_desktop(path)
            if not row:
                continue
            key = row["id"]
            if key in seen:
                continue
            seen.add(key)
            apps.append(row)
    apps.sort(key=lambda r: r["name"].lower())
    return {"ok": True, "platform": "linux", "apps": apps, "suggestions": True}


def _linux_desktop_dirs() -> list[Path]:
    home = Path.home()
    dirs = [
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        home / ".local/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        home / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]
    xdg = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share")
    for part in xdg.split(":"):
        part = part.strip()
        if part:
            dirs.append(Path(part) / "applications")
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        key = str(d)
        if key in seen:
            continue
        seen.add(key)
        if d.is_dir():
            out.append(d)
    return out


def _parse_desktop(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # Only the [Desktop Entry] group — no file contents beyond the listing.
    section: dict[str, str] = {}
    in_entry = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            in_entry = line.lower() == "[desktop entry]"
            continue
        if not in_entry or "=" not in line:
            continue
        key, val = line.split("=", 1)
        section.setdefault(key.strip(), val.strip())
    if section.get("Type", "Application") != "Application":
        return None
    if section.get("NoDisplay", "").lower() == "true":
        return None
    if section.get("Hidden", "").lower() == "true":
        return None
    if "OnlyShowIn" in section and "NotShowIn" not in section:
        # Keep generic apps; skip DE-only when clearly restricted? Keep them.
        pass
    name = section.get("Name") or path.stem
    exec_line = section.get("Exec") or ""
    command = _desktop_exec_to_command(exec_line)
    if not command:
        return None
    try_exec = section.get("TryExec") or ""
    if try_exec and not shutil.which(try_exec) and not Path(try_exec).exists():
        # Flatpak TryExec may be relative; still keep if Exec looks runnable.
        if not command.startswith("flatpak ") and not shutil.which(command.split()[0]):
            return None
    icon = section.get("Icon") or ""
    return {
        "id": "desktop:" + path.name,
        "name": name[:80],
        "command": command[:400],
        "kind": "desktop",
        "icon": icon[:120] if icon and not icon.startswith("/") else None,
    }


def _desktop_exec_to_command(exec_line: str) -> str:
    text = str(exec_line or "").strip()
    if not text:
        return ""
    # Drop field codes.
    text = re.sub(r"\s+%[fFuUdDnNickvm]", "", text)
    text = re.sub(r"%[fFuUdDnNickvm]", "", text)
    try:
        parts = shlex.split(text, posix=True)
    except ValueError:
        parts = text.split()
    if not parts:
        return ""
    # env VAR=val cmd …
    if parts[0] == "env":
        i = 1
        while i < len(parts) and "=" in parts[i]:
            i += 1
        parts = parts[i:]
    if not parts:
        return ""
    return " ".join(shlex.quote(p) if re.search(r"[\s\"']", p) else p for p in parts)


def _linux_browser_choices() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for path in _linux_desktop_dirs():
        for desk in path.glob("*.desktop"):
            try:
                text = desk.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "x-scheme-handler/http" not in text and "WebBrowser" not in text and "browser" not in desk.name.lower():
                # Still include known browsers by name.
                if not re.search(r"(?i)(firefox|chrom|brave|opera|vivaldi|edge|epiphany|falkon|midori)", desk.name + text[:400]):
                    continue
            row = _parse_desktop(desk)
            if not row or row["id"] in seen:
                continue
            # Prefer opening a URL with the browser binary when possible.
            cmd = row["command"]
            if "http" not in cmd and not cmd.endswith("%u"):
                # leave as launchable app
                pass
            seen.add(row["id"])
            rows.append(_choice(row["id"], row["name"], cmd))
            if len(rows) >= 24:
                return rows
    # Always offer xdg-open fallback.
    rows.append(_choice("xdg-open", "System default (xdg-open)", "xdg-open https://www.google.com"))
    return rows


def _linux_default_browser(choices: list[dict]) -> dict | None:
    desktop_id = ""
    for argv in (
        ["xdg-settings", "get", "default-web-browser"],
        ["xdg-mime", "query", "default", "x-scheme-handler/http"],
    ):
        try:
            out = subprocess.check_output(argv, text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
        except (OSError, subprocess.SubprocessError):
            out = ""
        if out.endswith(".desktop"):
            desktop_id = out
            break
    if desktop_id:
        want = "desktop:" + desktop_id
        for row in choices:
            if row["id"] == want or row["id"].endswith(desktop_id):
                return row
        # Parse the desktop file directly.
        for directory in _linux_desktop_dirs():
            path = directory / desktop_id
            if path.is_file():
                parsed = _parse_desktop(path)
                if parsed:
                    return _choice(parsed["id"], parsed["name"], parsed["command"])
    env_browser = (os.environ.get("BROWSER") or "").strip()
    if env_browser and shutil.which(env_browser.split()[0]):
        return _choice("env-browser", Path(env_browser.split()[0]).name, env_browser)
    return None


def _linux_terminal_choices() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    def add(cid: str, name: str, command: str) -> None:
        if cid in seen or not command:
            return
        binary = command.split()[0]
        if binary not in ("x-terminal-emulator",) and not shutil.which(binary) and not Path(binary).exists():
            return
        seen.add(cid)
        rows.append(_choice(cid, name, command))

    env_term = (os.environ.get("TERMINAL") or "").strip()
    if env_term:
        add("env-terminal", Path(env_term.split()[0]).name, env_term)
    if shutil.which("x-terminal-emulator"):
        add("x-terminal-emulator", "x-terminal-emulator", "x-terminal-emulator")
    # KDE
    kde = _read_ini_key(Path.home() / ".config/kdeglobals", "General", "TerminalApplication")
    if kde:
        add("kde-terminal", kde, kde)
    # XFCE
    xfce = _read_ini_key(Path.home() / ".config/xfce4/helpers.rc", "DEFAULT", "TerminalEmulator")
    if not xfce:
        xfce = _read_ini_key(Path.home() / ".config/xfce4/helpers.rc", None, "TerminalEmulator")
    if xfce:
        add("xfce-terminal", xfce, xfce)
    # GNOME gsettings
    try:
        out = subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.default-applications.terminal", "exec"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip().strip("'\"")
        if out:
            add("gnome-terminal-setting", out, out)
    except (OSError, subprocess.SubprocessError):
        pass
    for name in _TERM_CANDIDATES:
        if shutil.which(name):
            add(name, name, name)
    return rows


def _linux_default_terminal(choices: list[dict]) -> dict | None:
    env_term = (os.environ.get("TERMINAL") or "").strip()
    if env_term:
        for row in choices:
            if row["command"] == env_term or row["id"] == "env-terminal":
                return row
        if shutil.which(env_term.split()[0]):
            return _choice("env-terminal", Path(env_term.split()[0]).name, env_term)
    if shutil.which("x-terminal-emulator"):
        for row in choices:
            if row["id"] == "x-terminal-emulator":
                return row
    kde = _read_ini_key(Path.home() / ".config/kdeglobals", "General", "TerminalApplication")
    if kde:
        for row in choices:
            if row["command"] == kde or row["name"] == kde:
                return row
    xfce = _read_ini_key(Path.home() / ".config/xfce4/helpers.rc", "DEFAULT", "TerminalEmulator") or _read_ini_key(
        Path.home() / ".config/xfce4/helpers.rc", None, "TerminalEmulator"
    )
    if xfce:
        for row in choices:
            if row["command"] == xfce or row["name"] == xfce:
                return row
    try:
        out = subprocess.check_output(
            ["gsettings", "get", "org.gnome.desktop.default-applications.terminal", "exec"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip().strip("'\"")
        if out:
            for row in choices:
                if row["command"] == out or row["name"] == out:
                    return row
    except (OSError, subprocess.SubprocessError):
        pass
    for name in _TERM_CANDIDATES:
        for row in choices:
            if row["id"] == name:
                return row
    return choices[0] if choices else None


def _read_ini_key(path: Path, section: str | None, key: str) -> str:
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    # kdeglobals / helpers.rc are INI-like; helpers.rc may be key=value without section.
    if section is None:
        for line in text.splitlines():
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip()
        return ""
    try:
        parser = configparser.ConfigParser()
        parser.optionxform = str  # type: ignore[assignment]
        parser.read_string(text)
        if parser.has_option(section, key):
            return parser.get(section, key).strip()
    except (configparser.Error, OSError):
        pass
    # Fallback loose scan under [section]
    in_sec = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            in_sec = s[1:-1] == section
            continue
        if in_sec and s.startswith(key + "="):
            return s.split("=", 1)[1].strip()
    return ""
