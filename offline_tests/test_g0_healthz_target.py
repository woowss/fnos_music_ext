"""The marked G0 probe target is a fixed, non-privileged health response.

These tests run the real ASGI app and its HTTP middleware without starting its
lifespan or a server. They do not establish proxy, systemd, fnOS, or NAS
behavior; the gateway observer and normal HTTP transport still have their own
documented activity.
"""
from __future__ import annotations

import asyncio
import builtins
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
MARKER = b"0123456789abcdef0123456789abcdef"
USER_CANARY = b"user-canary-8d9e"
NAME_CANARY = b"name-canary-7c3a"


def _load_webui(monkeypatch):
    # Importing the service normally configures a file logger. Keep test import
    # isolated; no service lifespan or scheduler is started by this helper.
    from proxy import fnlog

    monkeypatch.setattr(fnlog, "setup_logging", lambda *_args, **_kwargs: None)
    name = f"g0_healthz_webui_{id(monkeypatch)}"
    path = REPO_ROOT / "webui-service" / "app.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _asgi_get(module, path: str, *, query: bytes, headers: list[tuple[bytes, bytes]], loop=None):
    messages = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    async def run():
        await module.app(
            {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": path,
                "raw_path": path.encode("ascii"),
                "query_string": query,
                "headers": headers,
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 12345),
                "root_path": "",
                "state": {},
            },
            receive,
            send,
        )

    if loop is None:
        asyncio.run(run())
    else:
        loop.run_until_complete(run())
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in messages
        if message["type"] == "http.response.body"
    )
    return start["status"], body


@pytest.mark.parametrize("path", ["/healthz", "/app/fnmusic-ext/healthz"])
def test_healthz_target_returns_fixed_ack_without_identity_or_marker_echo(monkeypatch, path):
    module = _load_webui(monkeypatch)
    status, body = _asgi_get(
        module,
        path,
        query=b"g0_observe=" + MARKER,
        headers=[
            (b"x-trim-userid", USER_CANARY),
            (b"x-trim-username", NAME_CANARY),
            (b"x-trim-isadmin", b"true"),
        ],
    )

    assert status == 200
    assert body == b'{"ok":true}'
    assert MARKER not in body
    assert USER_CANARY not in body and NAME_CANARY not in body
    assert b"x-trim" not in body.lower()


def test_healthz_handler_does_not_call_external_or_privileged_helpers(monkeypatch):
    module = _load_webui(monkeypatch)
    attempts: list[str] = []
    # Construct the Windows Proactor loop before socket sentinels are patched;
    # event-loop bootstrap itself creates an internal socket pair.
    loop = asyncio.new_event_loop()

    def forbidden(name: str):
        def fail(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"healthz unexpectedly called {name}")
        return fail

    try:
        with monkeypatch.context() as patch:
            for name in ("read_env", "supervisor_status", "_fetch_json", "get_http",
                         "_read_version", "_identity_claims_from_scope", "_has_admin_claim"):
                patch.setattr(module, name, forbidden(name))
            patch.setattr(module.httpx, "AsyncClient", forbidden("httpx.AsyncClient"))

            from proxy import env_merge
            patch.setattr(env_merge, "parse_env_file", forbidden("parse_env_file"))
            patch.setattr(Path, "read_text", forbidden("Path.read_text"))
            patch.setattr(Path, "write_text", forbidden("Path.write_text"))
            patch.setattr(Path, "open", forbidden("Path.open"))
            patch.setattr(builtins, "open", forbidden("open"))
            patch.setattr(os, "open", forbidden("os.open"))
            patch.setattr(subprocess, "run", forbidden("subprocess.run"))
            patch.setattr(subprocess, "Popen", forbidden("subprocess.Popen"))
            patch.setattr(socket, "socket", forbidden("socket.socket"))
            patch.setattr(socket, "create_connection", forbidden("socket.create_connection"))
            patch.setattr(threading.Thread, "start", forbidden("thread.start"))
            patch.setattr(asyncio, "create_task", forbidden("asyncio.create_task"))

            status, body = _asgi_get(
                module,
                "/app/fnmusic-ext/healthz",
                query=b"g0_observe=" + MARKER,
                # Duplicate/malformed identity claims remain irrelevant to this fixed
                # non-/api response; the observer, not this endpoint, inspects structure.
                headers=[(b"x-trim-isadmin", b"true"), (b"X-Trim-IsAdmin", b"false")],
                loop=loop,
            )
    finally:
        loop.close()

    assert status == 200
    assert body == b'{"ok":true}'
    assert attempts == []
