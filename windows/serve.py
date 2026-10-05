"""Local dashboard server for the Windows and macOS shells. Binds to 127.0.0.1 only."""

from __future__ import annotations

import json
import mimetypes
import queue
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

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
HOST = Host()
CONTROL: queue.Queue[str] = queue.Queue()
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
            )
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
            try:
                event = CONTROL.get(timeout=20)
            except queue.Empty:
                event = "timeout"
            payload = json.dumps({"event": event}).encode("utf-8")
            self._send(200, payload, "application/json")
            if event == "quit":
                threading.Thread(target=self.server.shutdown, daemon=True).start()
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


def main() -> None:
    mimetypes.add_type("text/css", ".css")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    port = server.server_address[1]
    print(f"PORT {port}", flush=True)
    print(f"TOKEN {TOKEN}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
