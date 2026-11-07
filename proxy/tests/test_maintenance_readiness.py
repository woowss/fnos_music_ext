"""Local-only tests for maintenance readiness and its explicit CLI mode."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
import types

import pytest

_ISOLATION_ROOT = Path(os.environ.get("FNMUSIC_TEST_ISOLATION_ROOT", ""))
assert str(_ISOLATION_ROOT) not in ("", "."), "run through the isolated TEMP runner"
assert Path(os.environ["FNMUSIC_HOME"]).resolve() == _ISOLATION_ROOT.resolve()
for _key in ("FNMUSIC_MUSIC_DB", "FNMUSIC_UPSTREAM_SOCK", "FNMUSIC_CACHE_DIR",
             "FNMUSIC_TEE_SAVE_DIR", "FNMUSIC_RECOMMEND_DIR", "XDG_CACHE_HOME",
             "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
    assert Path(os.environ[_key]).resolve().is_relative_to(_ISOLATION_ROOT.resolve()), _key

try:
    import fcntl  # noqa: F401
except ImportError:
    _fcntl_stub = types.ModuleType("fcntl")
    _fcntl_stub.LOCK_EX = 2
    _fcntl_stub.LOCK_NB = 4
    _fcntl_stub.flock = lambda *_args: None
    sys.modules["fcntl"] = _fcntl_stub
    try:
        from proxy import takeover
    finally:
        sys.modules.pop("fcntl", None)
else:
    from proxy import takeover

from proxy import app


_PROCESS = {"pid": 43210, "start": "123456", "exe": "/opt/fnmusic/proxy-python"}
_IDENTITY = {"kind": "proxy", "inode": [1, 98765], "process": _PROCESS}


class _Clock:
    def __init__(self, step=0.005):
        self.value = 100.0
        self.step = step

    def __call__(self):
        current = self.value
        self.value += self.step
        return current


class _Socket:
    def close(self):
        pass


class _ReadyState:
    target = Path("/tmp/synthetic-fnmusic-listen.sock")

    def __init__(self):
        self.recorded = dict(_IDENTITY)
        self.loads = 0
        self.change_after_first_load = False

    def load(self):
        self.loads += 1
        if self.change_after_first_load and self.loads > 1:
            return {"proxy": {**_IDENTITY, "inode": [1, 98766]}}
        return {"proxy": dict(self.recorded)}


def _install_ready_stubs(monkeypatch, state, *, body=None, peer=None, request_error=None,
                          inode_value=None, process_value=None, connected_peer=None):
    requests = []

    def fake_snapshot(_path, _timeout=0.9):
        return dict(_IDENTITY)

    def fake_request(_path, route, _timeout=4):
        requests.append(route)
        if request_error is not None:
            raise request_error
        return body, dict(peer if peer is not None else _PROCESS)

    def fake_process(_pid):
        return dict(process_value if process_value is not None else _PROCESS)

    def fake_connect(_path, _timeout=0.4):
        return _Socket(), dict(connected_peer if connected_peer is not None else _PROCESS)

    monkeypatch.setattr(takeover, "snapshot", fake_snapshot)
    monkeypatch.setattr(takeover, "request", fake_request)
    monkeypatch.setattr(takeover, "inode", lambda _path: list(inode_value if inode_value is not None else _IDENTITY["inode"]))
    monkeypatch.setattr(takeover, "process", fake_process)
    monkeypatch.setattr(takeover, "connect", fake_connect)
    monkeypatch.setattr(takeover.time, "monotonic", _Clock())
    monkeypatch.setattr(takeover.time, "sleep", lambda _seconds: None)
    return requests


def _valid_livez():
    return {"ok": True, "service": "fnmusic-ext", "pid": _PROCESS["pid"],
            "maintenance_startup": True}


async def _asgi_get(path):
    messages = []
    received = False

    async def receive():
        nonlocal received
        if received:
            return {"type": "http.disconnect"}
        received = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode("ascii"), "query_string": b"",
        "root_path": "", "headers": [(b"host", b"local-test")],
        "client": ("testclient", 123), "server": ("testserver", 80),
    }
    await app.app(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return status, body


def test_livez_keeps_ordinary_shape_and_adds_only_maintenance_marker(monkeypatch):
    monkeypatch.setattr(app, "_MAINTENANCE_STARTUP", False)
    ordinary = asyncio.run(app.ext_livez())
    assert ordinary == {"ok": True, "service": "fnmusic-ext", "pid": os.getpid()}
    assert set(ordinary) == {"ok", "service", "pid"}

    monkeypatch.setattr(app, "_MAINTENANCE_STARTUP", True)
    maintenance = asyncio.run(app.ext_livez())
    assert maintenance == {"ok": True, "service": "fnmusic-ext", "pid": os.getpid(),
                           "maintenance_startup": True}


def test_maintenance_liveness_is_http_200_while_healthz_is_http_503(monkeypatch):
    monkeypatch.setattr(app, "_MAINTENANCE_STARTUP", True)
    live_status, live_body = asyncio.run(_asgi_get("/_ext/livez"))
    health_status, health_body = asyncio.run(_asgi_get("/_ext/healthz"))

    assert live_status == 200
    assert json.loads(live_body) == {
        "ok": True, "service": "fnmusic-ext", "pid": os.getpid(), "maintenance_startup": True,
    }
    assert health_status == 503
    assert json.loads(health_body) == {
        "ok": False, "error": "maintenance-startup-readiness-unavailable",
    }


def test_maintenance_healthz_returns_fixed_503_without_clients_or_dependency_getters(monkeypatch):
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append("called")
        raise AssertionError("maintenance healthz must not create or use a client")

    monkeypatch.setattr(app, "_MAINTENANCE_STARTUP", True)
    monkeypatch.setattr(app.httpx, "AsyncClient", forbidden)
    for name in ("get_upstream_client", "get_musicdl_client", "get_musicbox_client", "get_lx_client"):
        monkeypatch.setattr(app, name, forbidden)

    result = asyncio.run(app.ext_healthz(types.SimpleNamespace(app=app.app)))

    assert result.status_code == 503
    assert json.loads(result.body) == {
        "ok": False,
        "error": "maintenance-startup-readiness-unavailable",
    }
    assert calls == []


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class _Client:
    def __init__(self, name, status, calls):
        self.name = name
        self.status = status
        self.calls = calls

    async def get(self, path, *, timeout):
        self.calls.append((self.name, path, timeout))
        return _Response(self.status, {"ok": True})


def _normal_healthz(monkeypatch, source_statuses):
    monkeypatch.setattr(app, "_MAINTENANCE_STARTUP", False)
    for key, value in (("musicdl_enabled", True), ("lx_enabled", True), ("netease_enabled", True)):
        monkeypatch.setitem(app.CONF, key, value)
    monkeypatch.setattr(app, "_netease_runtime_usable", lambda: True)
    monkeypatch.setattr(app, "get_version", lambda: "synthetic-version")
    monkeypatch.setattr(app.dailyrec, "llm_enabled", lambda: False)
    monkeypatch.setattr(app.dailyrec, "source_slot_claimed", lambda: False)
    monkeypatch.setattr(app.dailyrec, "last_recommend_summary", lambda: {})
    calls = []
    clients = {
        "upstream": _Client("upstream", source_statuses["upstream"], calls),
        "musicdl": _Client("musicdl", source_statuses["musicdl"], calls),
        "musicbox": _Client("musicbox", source_statuses["musicbox"], calls),
        "lxmusic": _Client("lxmusic", source_statuses["lxmusic"], calls),
    }
    for name, client in clients.items():
        monkeypatch.setattr(app, {
            "upstream": "get_upstream_client",
            "musicdl": "get_musicdl_client",
            "musicbox": "get_musicbox_client",
            "lxmusic": "get_lx_client",
        }[name], lambda _app, client=client: client)
    return calls


def test_ordinary_healthz_keeps_dependency_success_semantics(monkeypatch):
    calls = _normal_healthz(monkeypatch, {
        "upstream": 200, "musicdl": 200, "musicbox": 200, "lxmusic": 200,
    })
    payload = asyncio.run(app.ext_healthz(types.SimpleNamespace(app=app.app)))

    assert payload["ok"] is True
    assert payload["upstream"] == payload["musicdl"] == payload["musicbox"] == payload["lxmusic"] == "ok"
    assert payload["version"] == "synthetic-version"
    assert [path for _name, path, _timeout in calls] == [
        "/music/api/v1/search/track?keyword=healthz_probe", "/healthz", "/healthz", "/healthz",
    ]


def test_ordinary_healthz_keeps_dependency_failure_semantics(monkeypatch):
    calls = _normal_healthz(monkeypatch, {
        "upstream": 503, "musicdl": 503, "musicbox": 503, "lxmusic": 503,
    })
    payload = asyncio.run(app.ext_healthz(types.SimpleNamespace(app=app.app)))

    assert payload["ok"] is False
    assert payload["upstream"] == payload["musicdl"] == payload["musicbox"] == payload["lxmusic"] == "fail"
    assert payload["failures"] == ["upstream", "musicdl", "musicbox", "lxmusic"]
    assert len(calls) == 4


def test_ordinary_healthz_keeps_dependency_timeout_semantics(monkeypatch):
    _normal_healthz(monkeypatch, {
        "upstream": 200, "musicdl": 200, "musicbox": 200, "lxmusic": 200,
    })

    async def timeout(_awaitable, **_kwargs):
        close = getattr(_awaitable, "close", None)
        if close:
            close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(app.asyncio, "wait_for", timeout)
    payload = asyncio.run(app.ext_healthz(types.SimpleNamespace(app=app.app)))

    assert payload["ok"] is False
    assert payload["upstream"] == payload["musicdl"] == payload["musicbox"] == payload["lxmusic"] == "fail"
    assert all(payload["details"][name]["error"] == "TimeoutError"
               for name in ("upstream", "musicdl", "musicbox", "lxmusic"))


def test_maintenance_readiness_uses_only_livez_and_rechecks_owned_process(monkeypatch):
    state = _ReadyState()
    routes = _install_ready_stubs(monkeypatch, state, body=_valid_livez())

    takeover.wait_ready(state, 1, maintenance_startup=True)

    assert routes == ["/_ext/livez"]
    assert state.loads >= 3  # initial ownership plus before/after stability checks


@pytest.mark.parametrize(
    "body,peer,expected_reason",
    [
        ([], _PROCESS, "maintenance-liveness-invalid-response"),
        ({"ok": False, "service": "fnmusic-ext", "pid": 43210,
          "maintenance_startup": True}, _PROCESS, "maintenance-liveness-invalid-response"),
        ({"ok": True, "service": "other", "pid": 43210,
          "maintenance_startup": True}, _PROCESS, "maintenance-liveness-invalid-response"),
        ({"ok": True, "service": "fnmusic-ext", "pid": 43210}, _PROCESS,
         "maintenance-liveness-invalid-response"),
        ({"ok": True, "service": "fnmusic-ext", "pid": 43210,
          "maintenance_startup": False}, _PROCESS, "maintenance-liveness-invalid-response"),
        ({"ok": True, "service": "fnmusic-ext", "pid": True,
          "maintenance_startup": True}, _PROCESS, "maintenance-liveness-invalid-response"),
        ({"ok": True, "service": "fnmusic-ext", "pid": 43211,
          "maintenance_startup": True}, _PROCESS, "maintenance-liveness-peer-mismatch"),
        (_valid_livez(), {"pid": 43211, "start": "other", "exe": "/other"},
         "maintenance-liveness-peer-mismatch"),
    ],
)
def test_maintenance_readiness_rejects_invalid_livez_without_health_fallback(
    monkeypatch, body, peer, expected_reason,
):
    state = _ReadyState()
    routes = _install_ready_stubs(monkeypatch, state, body=body, peer=peer)

    with pytest.raises(takeover.Unsafe, match=expected_reason) as exc:
        takeover.wait_ready(state, 0.02, maintenance_startup=True)

    assert routes and set(routes) == {"/_ext/livez"}
    assert "healthz" not in str(exc.value)
    assert "music" not in str(exc.value)


@pytest.mark.parametrize(
    "request_error,expected_reason",
    [
        (takeover.Unsafe("HTTP 503 private response"), "maintenance-liveness-rejected"),
        (ValueError("invalid json private payload"), "maintenance-liveness-invalid-json"),
        (TimeoutError("private timeout detail"), "maintenance-liveness-timeout"),
        (OSError("private socket detail"), "maintenance-liveness-transport-error"),
        (IndexError("private malformed response"), "maintenance-liveness-probe-error"),
    ],
)
def test_maintenance_readiness_classifies_probe_failures_and_never_falls_back(
    monkeypatch, request_error, expected_reason,
):
    state = _ReadyState()
    routes = _install_ready_stubs(monkeypatch, state, request_error=request_error)

    with pytest.raises(takeover.Unsafe, match=expected_reason) as exc:
        takeover.wait_ready(state, 0.02, maintenance_startup=True)

    assert routes and set(routes) == {"/_ext/livez"}
    assert "private" not in str(exc.value)
    assert "healthz" not in str(exc.value)


@pytest.mark.parametrize("identity_failure", ["inode", "start", "exe", "connected-peer", "ownership"])
def test_maintenance_readiness_rejects_identity_changes_after_probe(monkeypatch, identity_failure):
    state = _ReadyState()
    kwargs = {}
    if identity_failure == "inode":
        kwargs["inode_value"] = [1, 99999]
    elif identity_failure == "start":
        kwargs["process_value"] = {**_PROCESS, "start": "different"}
    elif identity_failure == "exe":
        kwargs["process_value"] = {**_PROCESS, "exe": "/other/executable"}
    elif identity_failure == "connected-peer":
        kwargs["connected_peer"] = {**_PROCESS, "pid": 43211}
    elif identity_failure == "ownership":
        state.change_after_first_load = True
    routes = _install_ready_stubs(monkeypatch, state, body=_valid_livez(), **kwargs)

    with pytest.raises(takeover.Unsafe, match="maintenance-liveness-identity-changed|maintenance-liveness-ownership-unavailable"):
        takeover.wait_ready(state, 0.02, maintenance_startup=True)

    assert routes and set(routes) == {"/_ext/livez"}


def test_ordinary_readiness_still_uses_healthz_without_environment_inference(monkeypatch):
    monkeypatch.setenv("FNMUSIC_MAINTENANCE_STARTUP", "1")
    state = _ReadyState()
    routes = _install_ready_stubs(
        monkeypatch,
        state,
        body={"ok": True, "upstream": "ok", "musicdl": "ok"},
    )

    takeover.wait_ready(state, 1)

    assert routes == ["/_ext/healthz"]


@pytest.mark.parametrize("flag,expected", [(False, False), (True, True)])
def test_ready_cli_passes_explicit_mode_not_process_environment(monkeypatch, flag, expected):
    calls = []

    class FakeState:
        def __init__(self, *_args, **_kwargs):
            pass

    def fake_wait_ready(_state, timeout, *, maintenance_startup=False):
        calls.append((timeout, maintenance_startup))

    monkeypatch.setattr(sys, "argv", ["takeover.py", "ready"] + (["--maintenance-startup"] if flag else []))
    monkeypatch.setenv("FNMUSIC_MAINTENANCE_STARTUP", "1" if not flag else "0")
    monkeypatch.setattr(takeover, "State", FakeState)
    monkeypatch.setattr(takeover, "wait_ready", fake_wait_ready)

    assert takeover.main() == 0
    assert calls == [(30.0, expected)]


@pytest.mark.parametrize("command", ["preflight", "status", "restore"])
def test_mode_flag_is_rejected_before_state_for_non_run_or_ready_commands(monkeypatch, command):
    constructed = []

    def forbidden_state(*_args, **_kwargs):
        constructed.append(True)
        raise AssertionError("invalid mode must be rejected before State construction")

    monkeypatch.setattr(sys, "argv", ["takeover.py", command, "--maintenance-startup"])
    monkeypatch.setattr(takeover, "State", forbidden_state)
    with pytest.raises(SystemExit) as exc:
        takeover.main()
    assert exc.value.code == 2
    assert constructed == []
