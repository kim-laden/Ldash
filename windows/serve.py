"""Local dashboard server for the Windows and macOS shells. Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import mimetypes
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

from host import Host, default_shortcut  # noqa: E402

WEB = HERE.parent / "web"
if not (WEB / "index.html").exists():
    alt = HERE.parent.parent / "web"
    if (alt / "index.html").exists():
        WEB = alt

TOKEN = secrets.token_hex(16)
BOOT_ID = secrets.token_hex(4)
HOST = Host()


class ControlEvents:
    """Events for the shell's /ctl long poll (hide, quit, drag).

    A shell that passes ?after=<id> (macOS) gets the first event newer than id
    and the event is not consumed, so a poll the shell already gave up on (it
    timed out while the backend was busy or napped) can never swallow it.
    A shell without ?after= (Windows) keeps the old take-once behaviour.
    """

    def __init__(self, keep: int = 64) -> None:
        self._cond = threading.Condition()
        self._events: list[tuple[int, str]] = []
        self._next = 1
        self._legacy = 0
        self._keep = keep

    def put(self, name: str) -> int:
        with self._cond:
            event_id = self._next
            self._next += 1
            self._events.append((event_id, name))
            del self._events[:-self._keep]
            self._cond.notify_all()
            return event_id

    def wait(self, after: int | None, timeout: float) -> tuple[int, str]:
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                cursor = self._legacy if after is None else after
                for event_id, name in self._events:
                    if event_id > cursor:
                        if after is None:
                            self._legacy = event_id
                        return event_id, name
                left = deadline - time.monotonic()
                if left <= 0:
                    return cursor, "timeout"
                self._cond.wait(left)


CONTROL = ControlEvents()
MAX_BODY = 14_000_000

BRIDGE = """<script>
window.laden = {
  call(method, params) {
    return fetch("/call", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-Laden-Token": "%s"},
      body: JSON.stringify({ method: method, params: params || {} })
    }).then(function (res) { return res.json(); });
  }
};
</script>
""" % TOKEN

DARWIN_NAV = """<script>
document.addEventListener("click", function (ev) {
  var node = ev.target;
  while (node && node.tagName !== "A") node = node.parentElement;
  if (!node) return;
  var href = node.href || "";
  if (!/^https?:/i.test(href) || href.indexOf("http://127.0.0.1:") === 0) return;
  ev.preventDefault();
  window.laden.call("launch", { command: href });
}, true);
</script>
"""


def dispatch(method: str, params: dict) -> dict:
    try:
        if method == "system":
            return HOST.sample_system()
        if method == "processes":
            return HOST.process_groups()
        if method == "watchApp":
            return HOST.watch_app(str(params.get("query") or ""))
        if method == "windows":
            return HOST.get_windows()
        if method == "launch":
            return HOST.launch(str(params.get("command") or ""))
        if method == "fetch":
            return HOST.fetch(str(params.get("url") or ""))
        if method == "localUser":
            return HOST.local_user()
        if method == "verifyPassword":
            return HOST.verify_password(str(params.get("password") or ""))
        if method == "vaultEnsureDir":
            return HOST.vault_ensure_dir(str(params.get("rel") or ""))
        if method == "vaultWriteFile":
            return HOST.vault_write_file(
                str(params.get("rel") or ""),
                str(params.get("filename") or "file"),
                str(params.get("data") or ""),
            )
        if method == "vaultReadOpen":
            return HOST.vault_read_open(str(params.get("rel") or ""), str(params.get("filename") or "file"))
        if method == "vaultReadChunk":
            return HOST.vault_read_chunk(str(params.get("token") or ""), int(params.get("offset") or 0))
        if method == "vaultWriteOpen":
            return HOST.vault_write_open(str(params.get("rel") or ""), str(params.get("filename") or "file"))
        if method == "vaultWriteChunk":
            return HOST.vault_write_chunk(str(params.get("token") or ""), str(params.get("data") or ""))
        if method == "vaultWriteFinish":
            return HOST.vault_write_finish(str(params.get("token") or ""))
        if method == "saveTextOpen":
            return HOST.save_text_open(str(params.get("filename") or "laden.vault.conf"))
        if method == "saveTextChunk":
            return HOST.save_text_chunk(str(params.get("token") or ""), str(params.get("data") or ""))
        if method == "saveTextFinish":
            return HOST.save_text_finish(str(params.get("token") or ""))
        if method == "vaultWriteNote":
            return HOST.vault_write_note(
                str(params.get("rel") or ""),
                str(params.get("filename") or "note.md"),
                str(params.get("body") or ""),
            )
        if method == "exportBudgetPdf":
            sections = params.get("sections")
            return HOST.export_budget_pdf(
                str(params.get("rel") or "economy"),
                str(params.get("title") or "month"),
                sections if isinstance(sections, list) else [],
            )
        if method == "exportCasePdf":
            dumps = params.get("dumps")
            return HOST.export_case_pdf(
                str(params.get("rel") or ""),
                str(params.get("title") or "case"),
                str(params.get("scope") or ""),
                str(params.get("summary") or ""),
                str(params.get("caseBody") or ""),
                dumps if isinstance(dumps, list) else [],
            )
        if method == "openTerminal":
            return HOST.open_terminal(params.get("cwd"), params.get("command"))
        if method == "openPath":
            return HOST.open_path(str(params.get("path") or ""))
        if method == "aiChat":
            return HOST.ai_chat(
                str(params.get("provider") or "xai"),
                str(params.get("apiKey") or ""),
                str(params.get("system") or ""),
                str(params.get("user") or ""),
                params.get("model"),
            )
        if method == "prepareUserFolder":
            return HOST.prepare_user_folder(str(params.get("email") or ""), str(params.get("username") or ""))
        if method == "saveKit":
            return HOST.save_kit(str(params.get("json") or ""))
        if method == "saveVaultConf":
            return HOST.save_vault_conf(str(params.get("text") or ""))
        if method == "loadVaultConf":
            return HOST.load_vault_conf()
        if method == "accountFetch":
            return HOST.account_fetch(
                str(params.get("url") or ""),
                str(params.get("method") or "GET"),
                str(params.get("body") or ""),
                str(params.get("token") or ""),
                params.get("timeout"),
            )
        if method == "windowSnap":
            return HOST.window_snap()
        if method == "setWindowSnap":
            result = HOST.set_window_snap(str(params.get("mode") or "right"))
            CONTROL.put("snap")
            return result
        if method == "dragWindow":
            CONTROL.put("drag")
            return {"ok": True}
        if method == "setShortcut":
            return HOST.install_kwin(str(params.get("shortcut") or "Ctrl+`"))
        if method == "shortcut":
            backend = "hotkey" if sys.platform in ("win32", "darwin") else "kwin"
            return {"ok": True, "shortcut": default_shortcut(), "backend": backend, "platform": sys.platform}
        if method == "setPauseMode":
            return HOST.set_pause_mode(bool(params.get("enabled")))
        if method == "pauseMode":
            return HOST.pause_mode()
        if method == "hide":
            CONTROL.put("hide")
            return {"ok": True}
        if method == "quit":
            CONTROL.put("quit")
            return {"ok": True}
        return {"ok": False, "error": f"unknown method {method}"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def safe_file(url_path: str) -> Path | None:
    path = unquote(urlparse(url_path).path)
    if path in ("", "/"):
        path = "/index.html"
    rel = path.lstrip("/")
    if ".." in rel.split("/"):
        return None
    root = WEB.resolve()
    dest = (root / rel).resolve()
    if dest != root and root not in dest.parents:
        return None
    if not dest.is_file():
        return None
    return dest


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        return

    def _authorized(self) -> bool:
        return self.headers.get("X-Laden-Token") == TOKEN

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/ctl":
            if not self._authorized():
                self._send(403, b"{}", "application/json")
                return
            raw_after = parse_qs(urlparse(self.path).query).get("after", [""])[0]
            after = int(raw_after) if raw_after.isdigit() else None
            event_id, event = CONTROL.wait(after, 20)
            payload = json.dumps({"event": event, "id": event_id, "boot": BOOT_ID}).encode("utf-8")
            try:
                self._send(200, payload, "application/json")
            except OSError:
                return  # the shell gave up on this poll; the event stays for its next one
            if event == "quit":
                # Give the shell's live poll a moment to get it too; the shell
                # also stops this process when it quits.
                threading.Timer(1.0, self.server.shutdown).start()
            return
        dest = safe_file(self.path)
        if dest is None:
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        data = dest.read_bytes()
        if dest.name == "index.html":
            text = data.decode("utf-8")
            needle = "<head>"
            if needle in text:
                extra = BRIDGE + (DARWIN_NAV if sys.platform == "darwin" else "")
                text = text.replace(needle, "<head>" + extra, 1)
            data = text.encode("utf-8")
        mime = mimetypes.guess_type(dest.name)[0] or "application/octet-stream"
        if dest.suffix == ".js":
            mime = "text/javascript"
        self._send(200, data, mime)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/call":
            self._send(404, b"{}", "application/json")
            return
        if not self._authorized():
            self._send(403, b"{}", "application/json")
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > MAX_BODY:
            self._send(413, b'{"ok":false,"error":"body too large"}', "application/json")
            return
        raw = self.rfile.read(length) if length else b"{}"
        try:
            msg = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send(400, b'{"ok":false,"error":"bad json"}', "application/json")
            return
        if not isinstance(msg, dict):
            self._send(400, b'{"ok":false,"error":"bad json"}', "application/json")
            return
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        result = dispatch(str(msg.get("method") or ""), params)
        self._send(200, json.dumps(result).encode("utf-8"), "application/json")


def parent_watchdog(parent_pid: int, interval: float = 1.0, getppid=None, alive=None, stop=None,
                    misses: int = 2, log=None) -> None:
    """Exit when the shell that started us is gone (crash, SIGKILL, force quit).

    POSIX only. Checks every `interval` seconds whether our parent pid changed
    (we were reparented to launchd/init, pid 1) or no longer exists. Lenient:
    it takes `misses` bad checks in a row (about 2 s) before exiting, and a
    check that errors or a long sleep (App Nap, a suspended laptop) never counts
    as "the shell is gone" by itself. A hidden window is not a reason to exit:
    the watchdog only looks at the process, never at the UI.
    """
    import os
    import time

    getppid = getppid or os.getppid

    def _alive(pid: int) -> bool:
        try:
            os.kill(pid, 0)  # signal 0: existence check only
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _log(msg: str) -> None:
        try:
            print(f"watchdog: {msg}", file=sys.stderr, flush=True)
        except Exception:
            pass

    alive = alive or _alive
    log = log or _log
    stop = stop or (lambda: os._exit(0))
    bad = 0
    while True:
        time.sleep(interval)
        try:
            ppid = getppid()
            gone = ppid == 1 or ppid != parent_pid or not alive(parent_pid)
        except Exception:  # noqa: BLE001 - an odd check result is not proof the shell is gone
            continue
        if not gone:
            bad = 0
            continue
        bad += 1
        if bad >= max(1, misses):
            log(f"shell pid {parent_pid} is gone (ppid now {ppid}); backend exiting")
            stop()
            return


def start_parent_watchdog() -> bool:
    """Start the watchdog when the shell passed LADEN_PARENT_PID (macOS shell)."""
    import os

    raw = os.environ.get("LADEN_PARENT_PID", "")
    # Never on Windows: os.kill() there terminates the target process.
    if os.name != "posix" or not raw.isdigit() or int(raw) <= 1:
        return False
    threading.Thread(target=parent_watchdog, args=(int(raw),), name="ldash-parent-watchdog", daemon=True).start()
    return True


def warm_host_sensors() -> None:
    """Probe the temperature sensors in the background before the UI asks."""
    if sys.platform != "darwin":
        return
    try:
        from machost import warm_sensors

        warm_sensors()
        HOST._sensors_warmed = True
    except Exception:
        pass


def main() -> None:
    mimetypes.add_type("text/css", ".css")
    start_parent_watchdog()
    warm_host_sensors()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    print(f"PORT {port}", flush=True)
    print(f"TOKEN {TOKEN}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
