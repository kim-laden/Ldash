#!/usr/bin/env python3
"""Laden Ops — personal dashboard. GTK shell, laden.no visual language."""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import dbus
import dbus.service
import gi
from dbus.mainloop.glib import DBusGMainLoop

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gdk, Gio, GLib, Gtk, WebKit

from host import BUS_IFACE, BUS_NAME, BUS_PATH, Host, default_shortcut

ROOT = Path(__file__).resolve().parent
INDEX = ROOT / "web" / "index.html"
GEOM_PATH = Path.home() / ".config" / "laden-ops" / "window.json"
EDGE_PX = 8
TOP_EDGE_PX = 6
CORNER_PX = 8

BRIDGE_JS = r"""
(() => {
  const pending = new Map();
  let n = 0;
  window.laden = {
    call(method, params) {
      const id = ++n;
      return new Promise((resolve) => {
        pending.set(id, resolve);
        window.webkit.messageHandlers.host.postMessage(
          JSON.stringify({ id, method, params: params || {} })
        );
      });
    }
  };
  window.__ladenReply = (id, result) => {
    const fn = pending.get(id);
    if (!fn) return;
    pending.delete(id);
    fn(result);
  };
  window.addEventListener("error", (ev) => {
    document.title = "ERR " + (ev.message || "script");
  });
})();
"""


def js_text(message) -> str:
    value = message
    if hasattr(value, "get_js_value"):
        try:
            value = value.get_js_value()
        except Exception:
            pass
    if hasattr(value, "to_string"):
        try:
            return value.to_string()
        except Exception:
            pass
    return str(value)


def stored_window_size() -> tuple[int, int]:
    try:
        data = json.loads(GEOM_PATH.read_text(encoding="utf-8"))
        width = int(data["w"])
        height = int(data["h"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return 1180, 780
    return max(680, min(width, 3840)), max(460, min(height, 2160))


def stored_window_snap() -> str:
    try:
        data = json.loads(GEOM_PATH.read_text(encoding="utf-8"))
        mode = str(data.get("snap") or "right").strip().lower()
        if mode in ("left", "right", "off"):
            return mode
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return "right"


def save_geom(width: int, height: int, snap: str | None = None) -> None:
    mode = stored_window_snap() if snap is None else snap
    if mode not in ("left", "right", "off"):
        mode = "right"
    try:
        GEOM_PATH.parent.mkdir(parents=True, exist_ok=True)
        GEOM_PATH.write_text(
            json.dumps({"w": int(width), "h": int(height), "snap": mode}),
            encoding="utf-8",
        )
    except OSError:
        pass


def monitor_workarea(win: Gtk.Window | None) -> tuple[int, int, int, int]:
    display = Gdk.Display.get_default()
    monitor = None
    if win is not None:
        surface = win.get_surface()
        if surface is not None:
            monitor = display.get_monitor_at_surface(surface)
    if monitor is None:
        list_model = display.get_monitors()
        if list_model.get_n_items():
            monitor = list_model.get_item(0)
    if monitor is None:
        return 0, 0, 1920, 1080
    geom = monitor.get_workarea() if hasattr(monitor, "get_workarea") else monitor.get_geometry()
    return int(geom.x), int(geom.y), int(geom.width), int(geom.height)


def _x11_move_resize(win: Gtk.Window, x: int, y: int, w: int, h: int) -> bool:
    try:
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except Exception:
        return False
    surface = win.get_surface()
    if surface is None or not isinstance(surface, GdkX11.X11Surface):
        return False
    try:
        import ctypes
        from ctypes.util import find_library
        lib = find_library("X11")
        if not lib:
            return False
        x11 = ctypes.CDLL(lib)
        display = GdkX11.X11Display.get_xdisplay(surface.get_display())
        xid = surface.get_xid()
        x11.XMoveResizeWindow(display, ctypes.c_ulong(xid), int(x), int(y), int(w), int(h))
        x11.XFlush(display)
        return True
    except Exception:
        return False


def _kwin_move_resize(x: int, y: int, w: int, h: int) -> bool:
    """Best-effort Plasma snap via a one-shot KWin script."""
    import shutil
    import subprocess
    import tempfile
    qdbus = shutil.which("qdbus6") or shutil.which("qdbus")
    if not qdbus:
        return False
    script = f"""
var targets = ["Laden Ops", "Ldash"];
var list = workspace.windowList();
for (var i = 0; i < list.length; i++) {{
  var win = list[i];
  if (!win || !win.caption) continue;
  var title = String(win.caption);
  var hit = false;
  for (var t = 0; t < targets.length; t++) {{
    if (title.indexOf(targets[t]) === 0 || title === targets[t]) hit = true;
  }}
  if (!hit) continue;
  try {{
    win.frameGeometry = {{ x: {int(x)}, y: {int(y)}, width: {int(w)}, height: {int(h)} }};
  }} catch (e) {{
    try {{ win.geometry = {{ x: {int(x)}, y: {int(y)}, width: {int(w)}, height: {int(h)} }}; }} catch (e2) {{}}
  }}
}}
"""
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="ldash-snap-", suffix=".js")
        import os
        os.close(fd)
        Path(path).write_text(script, encoding="utf-8")
        plugin = "opsdash-snap"
        for cmd in (
            [qdbus, "org.kde.KWin", "/Scripting", "unloadScript", plugin],
            [qdbus, "org.kde.KWin", "/Scripting", "loadScript", path, plugin],
            [qdbus, "org.kde.KWin", "/Scripting", "start"],
        ):
            subprocess.run(cmd, check=False, timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run([qdbus, "org.kde.KWin", "/Scripting", "unloadScript", plugin], check=False, timeout=3, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False
    finally:
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass


def _wmctrl_move_resize(x: int, y: int, w: int, h: int) -> bool:
    import shutil
    import subprocess
    wmctrl = shutil.which("wmctrl")
    if not wmctrl:
        return False
    try:
        subprocess.run(
            [wmctrl, "-r", "Laden Ops", "-e", f"0,{int(x)},{int(y)},{int(w)},{int(h)}"],
            check=False,
            timeout=2,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def add_resize_edges(overlay: Gtk.Overlay, on_press) -> None:
    places = (
        ("top", Gdk.SurfaceEdge.NORTH, "n-resize"),
        ("bottom", Gdk.SurfaceEdge.SOUTH, "s-resize"),
        ("left", Gdk.SurfaceEdge.WEST, "w-resize"),
        ("right", Gdk.SurfaceEdge.EAST, "e-resize"),
        ("top-left", Gdk.SurfaceEdge.NORTH_WEST, "nw-resize"),
        ("top-right", Gdk.SurfaceEdge.NORTH_EAST, "ne-resize"),
        ("bottom-left", Gdk.SurfaceEdge.SOUTH_WEST, "sw-resize"),
        ("bottom-right", Gdk.SurfaceEdge.SOUTH_EAST, "se-resize"),
    )
    for place, edge, cursor_name in places:
        box = Gtk.Box()
        box.add_css_class("resize-edge")
        box.set_name("resize-" + place)
        box.set_cursor(Gdk.Cursor.new_from_name(cursor_name))
        box.set_can_target(True)
        if place == "top":
            box.set_valign(Gtk.Align.START)
            box.set_halign(Gtk.Align.FILL)
            box.set_hexpand(True)
            box.set_size_request(-1, TOP_EDGE_PX)
            box.set_margin_start(CORNER_PX)
            box.set_margin_end(CORNER_PX)
        elif place == "bottom":
            box.set_valign(Gtk.Align.END)
            box.set_halign(Gtk.Align.FILL)
            box.set_hexpand(True)
            box.set_size_request(-1, EDGE_PX)
            box.set_margin_start(CORNER_PX)
            box.set_margin_end(CORNER_PX)
        elif place == "left":
            box.set_halign(Gtk.Align.START)
            box.set_valign(Gtk.Align.FILL)
            box.set_vexpand(True)
            box.set_size_request(EDGE_PX, -1)
            box.set_margin_top(CORNER_PX)
            box.set_margin_bottom(CORNER_PX)
        elif place == "right":
            box.set_halign(Gtk.Align.END)
            box.set_valign(Gtk.Align.FILL)
            box.set_vexpand(True)
            box.set_size_request(EDGE_PX, -1)
            box.set_margin_top(CORNER_PX)
            box.set_margin_bottom(CORNER_PX)
        else:
            box.set_size_request(CORNER_PX, CORNER_PX)
            box.set_halign(Gtk.Align.START if "left" in place else Gtk.Align.END)
            box.set_valign(Gtk.Align.START if "top" in place else Gtk.Align.END)
        gesture = Gtk.GestureClick.new()
        gesture.set_button(1)
        gesture.connect("pressed", on_press, place, edge)
        box.add_controller(gesture)
        overlay.add_overlay(box)


class Bridge(dbus.service.Object):
    def __init__(self, bus: dbus.Bus, app: "OpsApp") -> None:
        super().__init__(bus, BUS_PATH)
        self.app = app

    @dbus.service.method(BUS_IFACE, in_signature="s", out_signature="")
    def SetWindows(self, payload: str) -> None:
        self.app.host.set_windows(str(payload))

    @dbus.service.method(BUS_IFACE, in_signature="", out_signature="")
    def Toggle(self) -> None:
        GLib.idle_add(self.app.toggle)


class OpsApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="org.laden.OpsDash", flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.host = Host()
        self.window: Gtk.ApplicationWindow | None = None
        self.webview: WebKit.WebView | None = None
        self._visible = False
        self._last_toggle = 0.0
        self._bridge: Bridge | None = None
        self._geom = (0, 0)
        self._snap = stored_window_snap()
        self._snap_sig = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        bus = dbus.SessionBus()
        self._bus_name = dbus.service.BusName(BUS_NAME, bus)
        self._bridge = Bridge(bus, self)
        self.host.install_kwin(default_shortcut())
        self._install_launchers()

    def do_activate(self) -> None:
        if self.window is not None:
            self._show()
            return
        self._build()

    def _build(self) -> None:
        win = Gtk.ApplicationWindow(application=self, title="Laden Ops")
        width, height = stored_window_size()
        win.set_default_size(width, height)
        win.set_resizable(True)
        win.set_size_request(680, 460)
        win.set_decorated(False)
        win.connect("close-request", self._on_close)

        web = WebKit.WebView()
        web.set_hexpand(True)
        web.set_vexpand(True)
        settings = web.get_settings()
        settings.set_enable_javascript(True)
        settings.set_enable_write_console_messages_to_stdout(True)
        rgba = Gdk.RGBA()
        rgba.parse("rgba(0,0,0,0)")
        web.set_background_color(rgba)

        manager = web.get_user_content_manager()
        manager.register_script_message_handler("host", None)
        manager.connect("script-message-received::host", self._on_message)
        script = WebKit.UserScript(
            BRIDGE_JS,
            WebKit.UserContentInjectedFrames.TOP_FRAME,
            WebKit.UserScriptInjectionTime.START,
            None,
            None,
        )
        manager.add_script(script)
        web.connect("load-changed", self._on_load)
        web.load_uri(INDEX.as_uri())

        overlay = Gtk.Overlay()
        overlay.set_child(web)
        handle = Gtk.WindowHandle()
        handle.add_css_class("drag-handle")
        handle.set_valign(Gtk.Align.START)
        handle.set_halign(Gtk.Align.FILL)
        handle.set_margin_end(56)
        strip = Gtk.Box()
        strip.add_css_class("drag-strip")
        strip.set_hexpand(True)
        strip.set_size_request(1, 18)
        handle.set_child(strip)
        overlay.add_overlay(handle)
        add_resize_edges(overlay, self._begin_resize)

        win.set_child(overlay)
        self._apply_css()
        self.window = win
        self.webview = web
        self._show()
        GLib.idle_add(self._apply_window_snap)
        GLib.timeout_add(800, self._track_size)
        GLib.timeout_add(1500, self._watch_display)
        try:
            display = Gdk.Display.get_default()
            display.connect("monitor-added", lambda *_: GLib.idle_add(self._apply_window_snap))
            display.connect("monitor-removed", lambda *_: GLib.idle_add(self._apply_window_snap))
        except Exception:
            pass

    def _apply_css(self) -> None:
        provider = Gtk.CssProvider()
        provider.load_from_string(
            """
            window {
              background-color: transparent;
              border-radius: 18px;
            }
            windowhandle.drag-handle,
            windowhandle.drag-handle box.drag-strip {
              background: transparent;
              box-shadow: none;
              min-height: 18px;
            }
            box.resize-edge { background: transparent; }
            """
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def _begin_resize(self, gesture, _n_press, _x, _y, place: str, edge) -> None:
        win = self.window
        if win is None:
            return
        surface = win.get_surface()
        event = gesture.get_current_event()
        if surface is None or event is None:
            return
        try:
            _ok, x, y = event.get_position()
            button = int(gesture.get_current_button() or 1)
            surface.begin_resize(edge, event.get_device(), button, float(x), float(y), int(event.get_time()))
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            print(f"laden-ops resize {place}", flush=True)
        except Exception as exc:
            print(f"laden-ops resize failed {place} {exc}", flush=True)

    def _apply_window_snap(self, mode: str | None = None) -> bool:
        win = self.window
        if win is None:
            return False
        snap = (mode or self._snap or stored_window_snap()).strip().lower()
        if snap not in ("left", "right", "off"):
            snap = "right"
        self._snap = snap
        if snap == "off":
            return False
        x, y, w, h = monitor_workarea(win)
        half = max(680, w // 2)
        nh = max(460, h)
        nx = x if snap == "left" else x + max(0, w - half)
        ny = y
        self._snap_sig = (x, y, w, h, snap)
        win.set_default_size(half, nh)
        try:
            win.set_size_request(680, 460)
        except Exception:
            pass
        # Prefer native moves; fall back to KWin/wmctrl on compositors that ignore GTK position.
        moved = _x11_move_resize(win, nx, ny, half, nh)
        if not moved:
            moved = _wmctrl_move_resize(nx, ny, half, nh)
        if not moved:
            _kwin_move_resize(nx, ny, half, nh)
        self._geom = (half, nh)
        save_geom(half, nh, snap)
        return False

    def _watch_display(self) -> bool:
        win = self.window
        if win is None:
            return False
        if self._snap == "off":
            return True
        sig = monitor_workarea(win) + (self._snap,)
        if sig != self._snap_sig:
            self._apply_window_snap()
        return True

    def _track_size(self) -> bool:
        win = self.window
        if win is None:
            return False
        if not win.get_mapped():
            return True
        width, height = win.get_width(), win.get_height()
        if width < 200 or height < 200 or (width, height) == self._geom:
            return True
        self._geom = (width, height)
        # While snapped, keep the half-screen layout rather than free-resize persistence alone.
        if self._snap in ("left", "right"):
            save_geom(width, height, self._snap)
            return True
        save_geom(width, height, "off")
        return True

    def _on_load(self, _web, event) -> None:
        if event != WebKit.LoadEvent.FINISHED or self.webview is None:
            return
        self.webview.evaluate_javascript(
            "String(document.querySelectorAll('.widget').length)",
            -1,
            None,
            None,
            None,
            self._on_js,
            None,
        )

    def _on_js(self, web, result, _data) -> None:
        try:
            value = web.evaluate_javascript_finish(result)
            text = value.to_string() if value is not None and hasattr(value, "to_string") else value
            print(f"laden-ops widgets={text}", flush=True)
        except Exception as exc:
            print(f"laden-ops ui-check {exc}", flush=True)

    def _on_message(self, _manager, message) -> None:
        raw = js_text(message)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            print("laden-ops bad message", raw[:200], flush=True)
            return
        msg_id = data.get("id")
        method = str(data.get("method") or "")
        params = data.get("params") or {}

        def work() -> None:
            try:
                result = self._dispatch(method, params)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)}
            GLib.idle_add(self._reply, msg_id, result)

        threading.Thread(target=work, daemon=True).start()

    def _reply(self, msg_id, result) -> bool:
        if self.webview is None or msg_id is None:
            return False
        script = f"window.__ladenReply({int(msg_id)}, {json.dumps(result)})"
        self.webview.evaluate_javascript(script, -1, None, None, None, None, None)
        return False

    def _dispatch(self, method: str, params: dict) -> dict:
        if method == "system":
            return self.host.sample_system()
        if method == "processes":
            return self.host.process_groups()
        if method == "watchApp":
            return self.host.watch_app(str(params.get("query") or ""))
        if method == "windows":
            return self.host.get_windows()
        if method == "launch":
            return self.host.launch(str(params.get("command") or ""))
        if method == "detectDefaults":
            return self.host.detect_defaults()
        if method == "listInstalledApps":
            return self.host.list_installed_apps()
        if method == "fetch":
            return self.host.fetch(str(params.get("url") or ""))
        if method == "localUser":
            return self.host.local_user()
        if method == "verifyPassword":
            return self.host.verify_password(str(params.get("password") or ""))
        if method == "vaultEnsureDir":
            return self.host.vault_ensure_dir(str(params.get("rel") or ""))
        if method == "vaultWriteFile":
            return self.host.vault_write_file(
                str(params.get("rel") or ""),
                str(params.get("filename") or "file"),
                str(params.get("data") or ""),
            )
        if method == "vaultReadOpen":
            return self.host.vault_read_open(str(params.get("rel") or ""), str(params.get("filename") or "file"))
        if method == "vaultReadChunk":
            return self.host.vault_read_chunk(str(params.get("token") or ""), int(params.get("offset") or 0))
        if method == "vaultWriteOpen":
            return self.host.vault_write_open(str(params.get("rel") or ""), str(params.get("filename") or "file"))
        if method == "vaultWriteChunk":
            return self.host.vault_write_chunk(str(params.get("token") or ""), str(params.get("data") or ""))
        if method == "vaultWriteFinish":
            return self.host.vault_write_finish(str(params.get("token") or ""))
        if method == "saveTextOpen":
            return self.host.save_text_open(str(params.get("filename") or "laden.vault.conf"))
        if method == "saveTextChunk":
            return self.host.save_text_chunk(str(params.get("token") or ""), str(params.get("data") or ""))
        if method == "saveTextFinish":
            return self.host.save_text_finish(str(params.get("token") or ""))
        if method == "vaultWriteNote":
            return self.host.vault_write_note(
                str(params.get("rel") or ""),
                str(params.get("filename") or "note.md"),
                str(params.get("body") or ""),
            )
        if method == "exportBudgetPdf":
            sections = params.get("sections")
            return self.host.export_budget_pdf(
                str(params.get("rel") or "economy"),
                str(params.get("title") or "month"),
                sections if isinstance(sections, list) else [],
            )
        if method == "exportCasePdf":
            dumps = params.get("dumps")
            return self.host.export_case_pdf(
                str(params.get("rel") or ""),
                str(params.get("title") or "case"),
                str(params.get("scope") or ""),
                str(params.get("summary") or ""),
                str(params.get("caseBody") or ""),
                dumps if isinstance(dumps, list) else [],
            )
        if method == "openTerminal":
            return self.host.open_terminal(params.get("cwd"), params.get("command"))
        if method == "openPath":
            return self.host.open_path(str(params.get("path") or ""))
        if method == "aiChat":
            return self.host.ai_chat(
                str(params.get("provider") or "xai"),
                str(params.get("apiKey") or ""),
                str(params.get("system") or ""),
                str(params.get("user") or ""),
                params.get("model"),
            )
        if method == "setShortcut":
            return self.host.install_kwin(str(params.get("shortcut") or "Ctrl+`"))
        if method == "setPauseMode":
            return self.host.set_pause_mode(bool(params.get("enabled")))
        if method == "windowSnap":
            return {"ok": True, "mode": self._snap if hasattr(self, "_snap") else stored_window_snap(), "supported": True}
        if method == "setWindowSnap":
            mode = str(params.get("mode") or "right").strip().lower()
            if mode not in ("left", "right", "off"):
                mode = "right"
            self._snap = mode
            save_geom(*(self._geom if self._geom != (0, 0) else stored_window_size()), mode)
            GLib.idle_add(self._apply_window_snap, mode)
            return {"ok": True, "mode": mode, "supported": True}
        if method == "pauseMode":
            return self.host.pause_mode()
        if method == "prepareUserFolder":
            return self.host.prepare_user_folder(str(params.get("email") or ""), str(params.get("username") or ""))
        if method == "saveKit":
            return self.host.save_kit(str(params.get("json") or ""))
        if method == "saveVaultConf":
            return self.host.save_vault_conf(str(params.get("text") or ""))
        if method == "loadVaultConf":
            return self.host.load_vault_conf()
        if method == "accountFetch":
            return self.host.account_fetch(
                str(params.get("url") or ""),
                str(params.get("method") or "GET"),
                str(params.get("body") or ""),
                str(params.get("token") or ""),
                params.get("timeout"),
            )
        if method == "dragWindow":
            return {"ok": True}
        if method == "shortcut":
            return {"ok": True, "shortcut": default_shortcut(), "backend": "kwin", "platform": "linux"}
        if method == "hide":
            GLib.idle_add(self._hide)
            return {"ok": True}
        if method == "quit":
            GLib.idle_add(self.quit)
            return {"ok": True}
        return {"ok": False, "error": f"unknown method {method}"}

    def _show(self) -> None:
        if self.window is None:
            return
        self._apply_window_snap()
        self.window.present()
        self.window.set_visible(True)
        self._visible = True

    def _hide(self) -> bool:
        if self.window is not None:
            self.window.set_visible(False)
        self._visible = False
        return False

    def _on_close(self, _win) -> bool:
        self._hide()
        return True

    def toggle(self) -> bool:
        now = GLib.get_monotonic_time() / 1000
        if now - self._last_toggle < 350:
            return False
        self._last_toggle = now
        if self.window is None:
            self._build()
            return False
        if self._visible and self.window.is_visible():
            self._hide()
        else:
            self._show()
        return False

    def _install_launchers(self) -> None:
        desktop = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Laden Ops\n"
            "Comment=Ldash personal dashboard by Laden AS\n"
            f"Exec=/usr/bin/python3 {ROOT / 'main.py'}\n"
            "Icon=utilities-system-monitor\n"
            "Terminal=false\n"
            "Categories=Utility;\n"
            "StartupWMClass=laden-ops\n"
        )
        app_dir = Path.home() / ".local" / "share" / "applications"
        auto_dir = Path.home() / ".config" / "autostart"
        app_dir.mkdir(parents=True, exist_ok=True)
        auto_dir.mkdir(parents=True, exist_ok=True)
        (app_dir / "laden-ops.desktop").write_text(desktop, encoding="utf-8")
        (auto_dir / "laden-ops.desktop").write_text(
            desktop + "X-GNOME-Autostart-enabled=true\n",
            encoding="utf-8",
        )


def main() -> None:
    DBusGMainLoop(set_as_default=True)
    app = OpsApp()
    sys.exit(app.run(sys.argv))


if __name__ == "__main__":
    main()
