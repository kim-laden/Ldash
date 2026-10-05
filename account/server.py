#!/usr/bin/env python3
"""Ldash account service.

One sealed vault.conf per login. SQLite holds the account row; the sealed file also
lives under VAULTS/<id>/vault.conf. The password and the kit text are not logged.
The direct link returns the sealed file. The account password opens it in Ldash.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.environ.get("LDASH_ACCOUNT_DATA", os.path.join(ROOT, "data"))
DB_PATH = os.path.join(DATA, "ldash.sqlite")
VAULTS = os.environ.get("LDASH_ACCOUNT_VAULTS", os.path.join(DATA, "vaults"))
PUBLIC = os.environ.get("LDASH_ACCOUNT_PUBLIC", "http://127.0.0.1:8741").rstrip("/")
BIND = os.environ.get("LDASH_ACCOUNT_BIND", "127.0.0.1:8741")
READONLY = os.environ.get("LDASH_ACCOUNT_READONLY", "").strip().lower() in ("1", "true", "yes", "on")
MAX_BODY = 8_000_000


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def ensure_store() -> None:
    os.makedirs(DATA, mode=0o700, exist_ok=True)
    os.makedirs(VAULTS, mode=0o700, exist_ok=True)


def vault_path(account_id: str, create: bool = True) -> str:
    safe = "".join(ch for ch in account_id if ch.isalnum() or ch in "-_")[:64]
    folder = os.path.join(VAULTS, safe or "_")
    if create:
        os.makedirs(folder, mode=0o700, exist_ok=True)
    return os.path.join(folder, "vault.conf")


def write_vault_file(account_id: str, conf: str) -> None:
    """Write the sealed vault.conf beside the DB. Never log the text."""
    path = vault_path(account_id)
    tmp = path + ".tmp"
    raw = conf.encode("utf-8")
    with open(tmp, "wb") as handle:
        handle.write(raw)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def read_vault_file(account_id: str) -> str:
    path = vault_path(account_id, create=False)
    if not os.path.isfile(path):
        return ""
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def connect() -> sqlite3.Connection:
    ensure_store()
    db = sqlite3.connect(DB_PATH)
    os.chmod(DB_PATH, 0o600)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            salt BLOB NOT NULL,
            pass_hash BLOB NOT NULL,
            token_hash TEXT,
            conf TEXT,
            updated_at TEXT
        )
        """
    )
    return db


def pass_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def public_link(account_id: str) -> str:
    return f"{PUBLIC}/vault/{account_id}/vault.conf"


def read_json(raw: bytes) -> dict:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def valid_email(email: str) -> bool:
    return "@" in email and "." in email.split("@", 1)[-1] and len(email) <= 120 and " " not in email


class Handler(BaseHTTPRequestHandler):
    server_version = "LdashAccount"

    def log_message(self, fmt: str, *args) -> None:
        # Method and path only. Never the body, the password, or the token.
        try:
            self.log_request()
        except Exception:
            return

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload).encode("utf-8"), "application/json; charset=utf-8")

    def _read(self) -> bytes:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > MAX_BODY:
            return b""
        return self.rfile.read(length) if length else b""

    def _bearer(self) -> str:
        header = self.headers.get("Authorization") or ""
        if header.startswith("Bearer "):
            return header[7:].strip()
        return ""

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/v1/health":
            payload = {"ok": True}
            if READONLY:
                payload["readOnly"] = True
                payload["role"] = "standby"
            self._json(200, payload)
            return
        prefix = "/vault/"
        if path.startswith(prefix) and path.endswith("/vault.conf"):
            account_id = path[len(prefix) : -len("/vault.conf")].strip("/")
            if not account_id or "/" in account_id:
                self._json(404, {"ok": False, "error": "No vault.conf for that link"})
                return
            conf = read_vault_file(account_id)
            if not conf:
                with connect() as db:
                    row = db.execute("SELECT conf FROM accounts WHERE id = ?", (account_id,)).fetchone()
                conf = (row["conf"] if row and row["conf"] else "") or ""
            if not conf:
                self._json(404, {"ok": False, "error": "No vault.conf for that link"})
                return
            self._send(200, conf.encode("utf-8"), "text/plain; charset=utf-8")
            return
        self._json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path not in ("/v1/register", "/v1/login"):
            self._json(404, {"ok": False, "error": "Not found"})
            return
        if READONLY and path == "/v1/register":
            self._json(503, {"ok": False, "error": "Standby is read-only. Create the account when the main cloud is back."})
            return
        body = read_json(self._read())
        email = str(body.get("email") or "").strip().lower()
        password = str(body.get("password") or "")
        if not valid_email(email) or len(password) < 8 or len(password) > 200:
            self._json(400, {"ok": False, "error": "Email and a password of at least 8 characters are required."})
            return
        with connect() as db:
            row = db.execute("SELECT * FROM accounts WHERE email = ?", (email,)).fetchone()
            if path == "/v1/register":
                if row:
                    self._json(409, {"ok": False, "error": "That email already has an account. Log in."})
                    return
                account_id = secrets.token_hex(16)
                salt = secrets.token_bytes(16)
                digest = pass_hash(password, salt)
                token = secrets.token_urlsafe(32)
                db.execute(
                    "INSERT INTO accounts (id, email, salt, pass_hash, token_hash, conf, updated_at) VALUES (?, ?, ?, ?, ?, '', ?)",
                    (account_id, email, salt, digest, token_hash(token), now()),
                )
                db.commit()
            else:
                if not row or not hmac.compare_digest(pass_hash(password, bytes(row["salt"])), bytes(row["pass_hash"])):
                    self._json(401, {"ok": False, "error": "Email or password was not accepted."})
                    return
                account_id = row["id"]
                token = secrets.token_urlsafe(32)
                # Standby login verifies only. Do not write a token that could
                # later PUT into a stale copy and diverge from main.
                if not READONLY:
                    db.execute("UPDATE accounts SET token_hash = ? WHERE id = ?", (token_hash(token), account_id))
                    db.commit()
        payload = {"ok": True, "token": token, "confUrl": public_link(account_id)}
        if READONLY:
            payload["standby"] = True
            payload["readOnly"] = True
        self._json(200, payload)

    def do_PUT(self) -> None:
        if urlparse(self.path).path != "/v1/vault":
            self._json(404, {"ok": False, "error": "Not found"})
            return
        if READONLY:
            self._json(503, {"ok": False, "error": "Standby is read-only. Vault writes wait for the main cloud."})
            return
        token = self._bearer()
        if not token:
            self._json(401, {"ok": False, "error": "Log in again."})
            return
        text = self._read().decode("utf-8", "replace")
        start = text.find("{")
        try:
            parsed = json.loads(text[start:] if start >= 0 else text)
        except json.JSONDecodeError:
            self._json(400, {"ok": False, "error": "The kit is not valid"})
            return
        if not isinstance(parsed, dict) or parsed.get("kind") != "laden.vault.conf" or parsed.get("sealed") is not True:
            self._json(400, {"ok": False, "error": "The online copy must be a sealed vault.conf"})
            return
        digest = token_hash(token)
        with connect() as db:
            row = db.execute("SELECT id FROM accounts WHERE token_hash = ?", (digest,)).fetchone()
            if not row:
                self._json(401, {"ok": False, "error": "Log in again."})
                return
            db.execute("UPDATE accounts SET conf = ?, updated_at = ? WHERE id = ?", (text, now(), row["id"]))
            db.commit()
            account_id = row["id"]
        write_vault_file(account_id, text)
        self._json(200, {"ok": True, "confUrl": public_link(account_id)})


def main() -> None:
    host, _, port = BIND.partition(":")
    httpd = ThreadingHTTPServer((host or "127.0.0.1", int(port or "8741")), Handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
