"""Offline regressions for ambiguous management identity-header claims.

These tests exercise only synthetic ASGI scopes and fake sockets. They do not
authenticate a real fnOS gateway or establish that the headers are trustworthy.
"""
from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
import sys

import pytest

from proxy import webui_gateway


REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_webui(monkeypatch):
    # Importing the service normally configures a file logger. Keep this unit
    # test import side-effect free while exercising the actual middleware.
    from proxy import fnlog

    monkeypatch.setattr(fnlog, "setup_logging", lambda *_args, **_kwargs: None)
    name = f"management_claims_webui_{id(monkeypatch)}"
    path = REPO_ROOT / "webui-service" / "app.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _asgi_call(module, headers, path="/api/settings"):
    downstream_calls = []
    messages = []

    async def downstream(_scope, _receive, _send):
        downstream_calls.append("called")

    async def send(message):
        messages.append(message)

    scope = {"type": "http", "path": path, "headers": headers}
    middleware = module.AuthMiddleware(downstream)
    asyncio.run(middleware(scope, None, send))
    return downstream_calls, messages


@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-trim-isadmin", b"true"), (b"X-Trim-IsAdmin", b"false")],
        [(b"X-Trim-IsAdmin", b"false"), (b"x-trim-isadmin", b"true")],
        [(b"x-trim-isadmin", b"true"), (b"x-trim-isadmin", b"true")],
        [(b"x-trim-isadmin", b"true, false")],
        [(b"x-trim-isadmin", b"true\x01")],
        [(b"x-trim-isadmin", b"TRUE")],
        [(b"x-trim-isadmin", b"true"), (b"x-trim-userid", b"uid-a"),
         (b"X-Trim-UserID", b"uid-b")],
        [(b"x-trim-isadmin", b"true"), (b"x-trim-username", b"a"),
         (b"X-Trim-Username", b"b")],
        [(b"x-trim-isadmin", b"true"), (b"content-length", b"0"),
         (b"Content-Length", b"0")],
        [[b"x-trim-isadmin", b"true"], [b"X-Trim-IsAdmin", b"false"]],
        {b"x-trim-isadmin": b"true"},
        [(b"x-trim-isadmin", "true")],
        [(b"x-trim-isadmin", b"true"), (b"invalid header name", b"x")],
    ],
)
def test_asgi_management_claims_fail_closed_before_downstream(monkeypatch, headers):
    module = _load_webui(monkeypatch)
    downstream, messages = _asgi_call(module, headers)
    assert downstream == []
    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 403


@pytest.mark.parametrize("pair_container", [tuple, list])
def test_asgi_single_canonical_admin_claim_preserves_downstream_path(monkeypatch, pair_container):
    module = _load_webui(monkeypatch)
    headers = pair_container([b"x-trim-isadmin", b"true"])
    downstream, messages = _asgi_call(module, [headers])
    assert downstream == ["called"]
    assert messages == []


@pytest.mark.parametrize("headers", [[], [(b"x-trim-isadmin", b"false")]])
def test_asgi_missing_or_false_admin_claim_remains_denied(monkeypatch, headers):
    module = _load_webui(monkeypatch)
    downstream, messages = _asgi_call(module, headers)
    assert downstream == []
    assert messages[0]["status"] == 403


class FakeSocket:
    def __init__(self, incoming: bytes):
        self.incoming = incoming
        self.sent: list[bytes] = []
        self.closed = False

    def recv(self, size: int) -> bytes:
        chunk, self.incoming = self.incoming[:size], self.incoming[size:]
        return chunk

    def settimeout(self, _timeout: float | None) -> None:
        pass

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True


def _request(path: str, headers: list[tuple[bytes, bytes]]) -> bytes:
    lines = [f"POST {path} HTTP/1.1".encode("ascii")]
    lines.extend(name + b": " + value for name, value in headers)
    return b"\r\n".join(lines) + b"\r\n\r\n{}"


_SPECIAL_PATHS = [
    "/api/host-file",
    "/app/fnmusic-ext/api/host-file",
    "/api/fs-check",
    "/app/fnmusic-ext/api/fs-check",
]


def test_identity_parser_is_allowed_and_copied_to_webui_container():
    dockerfile = (REPO_ROOT / "container" / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    copy_lines = " ".join(line for line in dockerfile.splitlines() if line.startswith("COPY"))
    assert "proxy/gateway_identity.py" in copy_lines
    assert "!proxy/gateway_identity.py" in dockerignore


@pytest.mark.parametrize("path", _SPECIAL_PATHS)
@pytest.mark.parametrize(
    "headers",
    [
        [(b"x-trim-isadmin", b"true"), (b"X-Trim-IsAdmin", b"false")],
        [(b"X-Trim-IsAdmin", b"false"), (b"x-trim-isadmin", b"true")],
        [(b"x-trim-isadmin", b"true"), (b"x-trim-isadmin", b"true")],
        [(b"x-trim-isadmin", b"true, false")],
        [(b"x-trim-isadmin", b"true\x01")],
        [(b"x-trim-isadmin", b"TRUE")],
        [(b"x-trim-isadmin", b"true"), (b"x-trim-userid", b"uid-a"),
         (b"X-Trim-UserID", b"uid-b")],
        [(b"x-trim-isadmin", b"true"), (b"x-trim-username", b"a"),
         (b"X-Trim-Username", b"b")],
        [(b"x-trim-isadmin", b"true"), (b"content-length", b"0"),
         (b"Content-Length", b"0")],
        [(b"x-trim-isadmin", b"true"), (b"bad header", b"x")],
    ],
)
def test_gateway_special_routes_reject_ambiguous_claims_before_work(monkeypatch, path, headers):
    client = FakeSocket(_request(path, headers))

    def forbidden(*_args, **_kwargs):
        pytest.fail("request reached a body, filesystem, or directory action")

    monkeypatch.setattr(webui_gateway, "_read_full_body", forbidden)
    monkeypatch.setattr(webui_gateway, "_probe_dir_writable", forbidden)
    monkeypatch.setattr(webui_gateway.Path, "stat", forbidden)
    webui_gateway._handle(client, ("unused", 0))

    assert client.closed
    assert len(client.sent) == 1
    assert client.sent[0].startswith(b"HTTP/1.1 403 ")
    assert b"uid-a" not in client.sent[0] and b"uid-b" not in client.sent[0]


@pytest.mark.parametrize("path", _SPECIAL_PATHS)
def test_gateway_single_canonical_true_reaches_existing_handler_path(monkeypatch, path):
    client = FakeSocket(_request(path, [(b"x-trim-isadmin", b"true")]))
    calls = []

    def body_reader(*_args, **_kwargs):
        calls.append("body")
        if path.endswith("host-file"):
            return b'{"path":"/not-a-real-script.js"}'
        return b'{"path":"/not-a-real-directory"}'

    def missing_path(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(webui_gateway, "_read_full_body", body_reader)
    monkeypatch.setattr(webui_gateway.Path, "stat", missing_path)
    monkeypatch.setattr(webui_gateway.Path, "is_symlink", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(webui_gateway, "_probe_dir_writable", lambda *_args, **_kwargs: pytest.fail("filesystem probe"))
    webui_gateway._handle(client, ("unused", 0))
    assert calls == ["body"]
    assert client.sent and not client.sent[0].startswith(b"HTTP/1.1 403 ")


def test_gateway_claim_parser_rejects_pair_and_byte_limits():
    prefix = b"POST /api/fs-check HTTP/1.1\r\n"
    too_many = prefix + b"x: y\r\n" * 128 + b"x-trim-isadmin: true\r\n\r\n"
    too_large = prefix + b"x: " + b"a" * (64 * 1024) + b"\r\nx-trim-isadmin: true\r\n\r\n"
    assert webui_gateway._admin_claim_from_raw_head(too_many) is False
    assert webui_gateway._admin_claim_from_raw_head(too_large) is False
