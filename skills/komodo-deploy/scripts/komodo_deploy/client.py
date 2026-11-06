"""Minimal Komodo Core v2 HTTP API client.

v2 requests are `POST /{read|write|execute}/{RequestName}` with the params object as the body.
"""
from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from .config import Settings

REQUEST_TIMEOUT_S = 30
POLL_INTERVAL_S = 2.0
DEFAULT_WAIT_TIMEOUT_S = 900
MAX_ERROR_CHARS = 800
# v2.3+ paginates list requests (default 50); limit 0 disables pagination.
NO_PAGINATION = {"limit": 0}


class KomodoError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def build_ssl_context(settings: Settings) -> ssl.SSLContext:
    if settings.ca_bundle:
        return ssl.create_default_context(cafile=settings.ca_bundle)
    if settings.insecure:
        print("warning: TLS verification disabled (KOMODO_INSECURE)", file=sys.stderr)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _error_message(body: str) -> str:
    try:
        data = json.loads(body)
        if isinstance(data, dict) and "error" in data:
            trace = data.get("trace") or []
            return " | ".join([str(data["error"]), *map(str, trace)])[:MAX_ERROR_CHARS]
    except json.JSONDecodeError:
        pass
    return body[:MAX_ERROR_CHARS]


def request_url(host: str, path: str, rtype: str) -> str:
    return f"{host}/{path}/{rtype}"


class Client:
    def __init__(self, settings: Settings, urlopen: Callable = urllib.request.urlopen):
        self.settings = settings
        self._urlopen = urlopen
        self._ctx = build_ssl_context(settings)

    def call(self, path: str, rtype: str, params: dict | None = None) -> Any:
        req = urllib.request.Request(
            request_url(self.settings.host, path, rtype),
            data=json.dumps(params or {}).encode(),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Api-Key": self.settings.key,
                "X-Api-Secret": self.settings.secret,
            },
        )
        try:
            with self._urlopen(req, context=self._ctx, timeout=REQUEST_TIMEOUT_S) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            msg = _error_message(exc.read().decode(errors="replace"))
            raise KomodoError(f"{rtype} failed ({exc.code}): {msg}", exc.code) from exc
        except urllib.error.URLError as exc:
            raise KomodoError(f"{rtype}: cannot reach {self.settings.host}: {exc.reason}") from exc
        return json.loads(raw) if raw else None

    def read(self, rtype: str, params: dict | None = None) -> Any:
        return self.call("read", rtype, params)

    def list_all(self, rtype: str, params: dict | None = None) -> list:
        """List request with pagination disabled, so large instances aren't truncated."""
        return self.read(rtype, {**(params or {}), **NO_PAGINATION})

    def write(self, rtype: str, params: dict | None = None) -> Any:
        return self.call("write", rtype, params)

    def execute(self, rtype: str, params: dict | None = None) -> Any:
        return self.call("execute", rtype, params)


def update_id(update: dict) -> str:
    if "id" in update:
        return update["id"]
    oid = update.get("_id")
    return oid["$oid"] if isinstance(oid, dict) else str(oid)


def wait_update(client: Client, uid: str, timeout_s: float = DEFAULT_WAIT_TIMEOUT_S,
                interval_s: float = POLL_INTERVAL_S, sleep: Callable = time.sleep) -> dict:
    """Poll GetUpdate until Complete; print new stage names to stderr as progress."""
    deadline = time.monotonic() + timeout_s
    seen = 0
    while True:
        update = client.read("GetUpdate", {"id": uid})
        logs = update.get("logs") or []
        for log in logs[seen:]:
            mark = "ok" if log.get("success") else "FAILED"
            print(f"  [{mark}] {log.get('stage')}", file=sys.stderr)
        seen = len(logs)
        if update.get("status") == "Complete":
            return update
        if time.monotonic() >= deadline:
            raise KomodoError(f"Timed out after {timeout_s}s waiting for update {uid}")
        sleep(interval_s)
