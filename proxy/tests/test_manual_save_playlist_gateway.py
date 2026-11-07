"""Gateway contract tests for the whole-playlist manual-save endpoints (P5).

Contract ``FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01`` §3/§4.3.1/§7.1/§9.2.

Every case is fully offline: the snapshot "producer" is a stub object, the
ledger is a real ``ManualSaveJobsLedger`` in ``tmp_path`` (real temporary
SQLite file), the engine/executor is a fake, and no socket, NAS, ffmpeg, probe
script or real ``.env`` value is touched. The HTTP layer is exercised through
the same ``FakeClient`` style as ``test_manual_save_gateway.py``.
"""
from __future__ import annotations

import json
import sqlite3
import threading

import pytest

from proxy import manual_save_jobs as ledger_mod
from proxy import manual_save_playlist_jobs as adapter
from proxy import webui_gateway as gw

CRLF = chr(13) + chr(10)
HEAD_END = (CRLF + CRLF).encode("latin-1")
PLAYLIST_ID = 987654321
OTHER_PLAYLIST_ID = 111222333
FINGERPRINT = "a" * 64
OTHER_FINGERPRINT = "b" * 64
JOB_ID = "0" * 48
UNKNOWN_JOB_ID = "f" * 48
WHEEL_SHA256 = (
    "7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12"
)
SIGNED_URL = "https://signed.example.com/secret?token=TOPSECRETTAIL"
SECRET_COOKIE = "SECRETCOOKIE"
SCOPE_USER = adapter.derive_actor_scope("1000")
OTHER_SCOPE_USER = adapter.derive_actor_scope("2000")


# ---------------------------------------------------------------------------
# Envelope / payload helpers
# ---------------------------------------------------------------------------

def _enabled_status():
    return {
        "ok": True,
        "code": "ok",
        "message": "",
        "data": {
            "enabled": True,
            "target_dir": "/vol1/music",
            "staging_dir": "/vol1/staging",
            "quality_order": ["jymaster"],
            "max_bytes": 1234,
        },
    }


def _disabled_status():
    return {"ok": False, "code": "disabled", "message": "手动保存未启用", "data": {}}


def _published():
    return {"ok": True, "code": "ok", "message": "", "data": {"status": "published"}}


def _conflict():
    return {"ok": False, "code": "conflict", "message": "", "data": {"status": "conflict"}}


def _failure(code):
    return {"ok": False, "code": code, "message": "", "data": {}}


def _artifact(**overrides):
    artifact = {
        "provider": "netease",
        "package": "NetEase-MusicBox",
        "version": "0.5.3",
        "wheel_sha256": WHEEL_SHA256,
        "dist_info_present": True,
    }
    artifact.update(overrides)
    return artifact


DEFAULT_TRACK_IDS = (1901371647, 1901371648, 1901371647)


def _snapshot_payload(track_ids=DEFAULT_TRACK_IDS, **overrides):
    """Complete producer payload, exact §4.2 shape (plus §4.2 source_artifact)."""
    positions = [
        {"position": index, "track_id": track_id, "error_code": None}
        for index, track_id in enumerate(track_ids, start=1)
    ]
    positions = overrides.pop("positions", positions)
    track_count = overrides.pop("track_count", len(positions))
    position_count = overrides.pop("position_count", len(positions))
    payload = {
        "ok": True,
        "status": "complete",
        "error_code": None,
        "schema_version": 1,
        "provider": "netease",
        "account_uid": 123456789,
        "playlist_id": PLAYLIST_ID,
        "track_count": track_count,
        "position_count": position_count,
        "enumeration_complete": True,
        "source_fingerprint": FINGERPRINT,
        "source_versions": [["updateTime", 1730000000]],
        "positions": positions,
        "metadata_status": "not_requested",
        "metadata_complete": False,
        "import_ready": False,
        "production_ready": False,
        "provider_verified": False,
        "source_artifact": _artifact(),
        "artifact_error_code": None,
    }
    payload.update(overrides)
    return payload


class FakeProducer:
    """Stub producer: returns queued payloads (or raises queued exceptions)."""

    def __init__(self, payloads=()):
        self.payloads = list(payloads)
        self.calls = []

    def fetch_snapshot(self, playlist_id):
        self.calls.append(playlist_id)
        if not self.payloads:
            raise AssertionError("producer called without a queued payload")
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return payload


class FakeEngine:
    """Route engine double: only ``status`` is used by the gateway gate."""

    def __init__(self, *, status_result=None):
        self.status_result = _enabled_status() if status_result is None else status_result
        self.status_calls = 0
        self.save_calls = []

    def status(self):
        self.status_calls += 1
        return self.status_result

    def save_track(self, song_id):
        self.save_calls.append(song_id)
        return _published()


class SaveEngine:
    """Single-track executor double feeding the real P4 adapter/ledger."""

    def __init__(self, results=()):
        self.results = list(results)
        self.calls = []

    def save_track(self, song_id):
        self.calls.append(song_id)
        if self.results:
            return self.results.pop(0)
        return _published()


def _make_deps(tmp_path, producer, save_engine, *, enabled=True):
    """Real ledger + real P4 service + stub producer/fake executor, no network."""
    db_path = str(tmp_path / "jobs.db")
    config = adapter.PlaylistJobServiceConfig(db_path=db_path, enabled=enabled)
    ledger = ledger_mod.ManualSaveJobsLedger(db_path)
    service = adapter.PlaylistJobService(
        config, producer, engine=save_engine, ledger=ledger)
    return ledger, service, gw._ManualSavePlaylistDeps(
        service=service, producer=producer, enabled=enabled)


def _job_count(db_path):
    connection = sqlite3.connect(str(db_path))
    try:
        return connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    finally:
        connection.close()


# ---------------------------------------------------------------------------
# Raw HTTP request / response helpers (same style as the existing gateway test)
# ---------------------------------------------------------------------------

class FakeClient:
    def __init__(self, request=b"", extra_chunks=()):
        self._chunks = ([request] if request else []) + list(extra_chunks)
        self.data = b""
        self.closed = False
        self.timeouts = []

    def sendall(self, data):
        self.data += data

    def settimeout(self, value):
        self.timeouts.append(value)

    def recv(self, size):
        if self._chunks:
            return self._chunks.pop(0)
        return b""

    def close(self):
        self.closed = True


def _raw(method, target, *, admin=True, csrf=True, csrf_value="1", userid="1000",
         extra_headers=(), body=b"", version="HTTP/1.1", content_length=None,
         omit_content_length=False):
    lines = [f"{method} {target} {version}"]
    if admin:
        lines.append("X-Trim-Isadmin: true")
    if userid is not None:
        lines.append(f"X-Trim-Userid: {userid}")
    if csrf:
        lines.append(f"x-fnmusic-save: {csrf_value}")
    lines.append("Host: localhost")
    if not omit_content_length:
        value = len(body) if content_length is None else content_length
        lines.append(f"Content-Length: {value}")
    for name, value in extra_headers:
        lines.append(f"{name}: {value}")
    lines.append("Connection: close")
    return (CRLF.join(lines) + CRLF + CRLF).encode("latin-1") + body


def _response(client):
    head, _, body = client.data.partition(HEAD_END)
    if not head:
        return None, None
    status_line = head.split(CRLF.encode("latin-1"))[0].decode("latin-1")
    return int(status_line.split(" ")[1]), json.loads(body.decode("utf-8"))


def _assert_shape(payload):
    assert set(payload) == {"ok", "code", "message", "data"}
    assert type(payload["ok"]) is bool
    assert type(payload["code"]) is str and payload["code"]
    assert type(payload["message"]) is str
    assert isinstance(payload["data"], dict)


def _get(target, *, query=None, **kwargs):
    url = target if query is None else target + "?" + query
    return _raw("GET", url, **kwargs)


def _post(target, body, **kwargs):
    if isinstance(body, bytes):
        raw_body = body
    else:
        raw_body = json.dumps(body).encode("utf-8")
    return _raw("POST", target, body=raw_body, **kwargs)


# ---------------------------------------------------------------------------
# Path / method matching
# ---------------------------------------------------------------------------

PLAYLIST_ROUTES = (
    ("playlists", "GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS),
    ("snapshot", "GET", gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS),
    ("create", "POST", gw.MANUAL_SAVE_PLAYLIST_JOBS_PATHS),
    ("job", "GET", gw.MANUAL_SAVE_PLAYLIST_JOB_PATHS),
    ("advance", "POST", gw.MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS),
    ("cancel", "POST", gw.MANUAL_SAVE_PLAYLIST_CANCEL_PATHS),
)


def test_request_kind_covers_both_prefixes_and_exact_methods():
    for kind, method, paths in PLAYLIST_ROUTES:
        for target in paths:
            suffix = "?playlist_id=1" if kind == "snapshot" else (
                "?job_id=" + JOB_ID if kind == "job" else "")
            assert gw._manual_save_playlist_request_kind(
                _raw(method, target + suffix)) == kind
        # Opposite method must never enter the new handlers (§3.0).
        wrong_method = "POST" if method == "GET" else "GET"
        for target in paths:
            assert gw._manual_save_playlist_request_kind(
                _raw(wrong_method, target)) is None


def test_request_kind_ignores_unrelated_and_undefined_paths():
    assert gw._manual_save_playlist_request_kind(
        _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0])) is None
    # §3.8: reconcile/recover paths are deliberately not in the target list.
    for path in ("/api/manual-save/playlist/reconcile",
                 "/api/manual-save/playlist/recover",
                 "/app/fnmusic-ext/api/manual-save/playlist/jobs/" + JOB_ID):
        assert gw._manual_save_playlist_request_kind(_raw("POST", path)) is None
        assert gw._manual_save_playlist_request_kind(_raw("GET", path)) is None
    assert gw._manual_save_playlist_request_kind(b"garbage") is None


def test_dispatch_route_inserted_before_head_validation():
    # A duplicate identity header is ambiguous: the local handler rejects it with
    # its documented fixed 400 before any upstream connection is attempted.
    raw = _get(gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
               extra_headers=(("X-Trim-Userid", "2000"),))
    client = FakeClient(raw)
    gw._handle(client, ("127.0.0.1", 1))
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"
    assert client.closed is True


# ---------------------------------------------------------------------------
# Strict head parsing / authorization (L3 + L4)
# ---------------------------------------------------------------------------

def _snapshot_raw(**kwargs):
    return _get(
        gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS[0],
        query=f"playlist_id={PLAYLIST_ID}", **kwargs)


@pytest.mark.parametrize("raw", [
    pytest.param(_raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
                      extra_headers=(("x-fnmusic-save", "1"),)),
                 id="duplicate-csrf-header"),
    pytest.param(_raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
                      extra_headers=(("Transfer-Encoding", "chunked"),)),
                 id="transfer-encoding"),
    pytest.param(_raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
                      version="HTTP/2"), id="bad-http-version"),
    pytest.param(_raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
                      extra_headers=(("X-Big", "x" * (17 * 1024)),)),
                 id="oversized-head"),
    pytest.param(_raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
                      body=b"{}"), id="get-with-body"),
    pytest.param(_raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0],
                      content_length="+1"), id="bad-content-length"),
    pytest.param(b"GET /api/manual-save/playlist/playlists HTTP/1.1",
                 id="missing-head-terminator"),
])
def test_head_parser_rejects_non_strict_requests(raw):
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_playlist_playlists(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"
    assert payload["message"] == "请求无效"
    assert engine.status_calls == 0
    assert client.closed is True


@pytest.mark.parametrize(("admin", "csrf", "userid"), [
    (False, True, "1000"),   # non-admin
    (True, False, "1000"),   # missing x-fnmusic-save
    (True, True, None),      # missing user-id claim (L4 prerequisite)
    (True, True, ""),        # syntactically invalid user-id claim
])
def test_authorization_fails_closed_before_switch_or_work(admin, csrf, userid):
    tmp_path_payload = _snapshot_payload()
    producer = FakeProducer([tmp_path_payload])
    engine = FakeEngine()
    raw = _snapshot_raw(admin=admin, csrf=csrf, userid=userid)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=engine,
                                            deps=gw._ManualSavePlaylistDeps(
                                                service=object(), producer=producer))
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert payload["message"] == "仅管理员可执行手动保存"
    assert engine.status_calls == 0
    assert producer.calls == []
    assert client.closed is True


def test_wrong_csrf_value_is_forbidden():
    raw = _snapshot_raw(csrf_value="0")
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"


def test_playlist_keeps_user_id_requirement_when_track_switch_relaxed(monkeypatch):
    # Even if the single-track L4 switch were relaxed, the playlist endpoints
    # still need the canonical user-id claim to derive actor_scope (§2.4/§8.3).
    monkeypatch.setattr(gw, "MANUAL_SAVE_REQUIRE_USER_ID", False)
    raw = _snapshot_raw(userid=None)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"


# ---------------------------------------------------------------------------
# Switches: master vs playlist, ledger path
# ---------------------------------------------------------------------------

def test_master_switch_off_returns_disabled_without_work():
    producer = FakeProducer([_snapshot_payload()])
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw,
        engine=FakeEngine(status_result=_disabled_status()),
        deps=gw._ManualSavePlaylistDeps(service=object(), producer=producer))
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "disabled"
    assert payload["message"] == "手动保存未启用"
    assert payload["data"] == {"enabled": False}
    assert producer.calls == []


def test_playlist_switch_off_returns_playlist_disabled_without_work():
    producer = FakeProducer([_snapshot_payload()])
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw, engine=FakeEngine(),
        deps=gw._ManualSavePlaylistDeps(service=object(), producer=producer,
                                        enabled=False))
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "playlist_disabled"
    assert payload["message"] == "整歌单保存未启用"
    assert producer.calls == []


def test_master_switch_checked_before_playlist_switch():
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw,
        engine=FakeEngine(status_result=_disabled_status()),
        deps=gw._ManualSavePlaylistDeps(
            service=object(), producer=FakeProducer(), enabled=False))
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "disabled"


def test_playlist_disabled_resolver_maps_config_switch(monkeypatch):
    def disabled_config(environ):
        return adapter.PlaylistJobServiceConfig(db_path="/tmp/unused.db", enabled=False)

    monkeypatch.setattr(adapter, "load_service_config", disabled_config)
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "playlist_disabled"


def test_missing_ledger_path_resolves_to_config_invalid(monkeypatch):
    def broken_config(environ):
        return adapter.PlaylistJobServiceConfig(db_path=None, enabled=True)

    monkeypatch.setattr(adapter, "load_service_config", broken_config)
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 500
    assert payload["code"] == "config_invalid"
    assert payload["message"] == "手动保存配置无效"


# ---------------------------------------------------------------------------
# Query parsing (strict, fixed codes)
# ---------------------------------------------------------------------------

def _deps(tmp_path, payloads=(), save_engine=None):
    producer = FakeProducer(payloads)
    _ledger, _service, deps = _make_deps(
        tmp_path, producer, save_engine if save_engine is not None else SaveEngine())
    return producer, deps


@pytest.mark.parametrize(("target", "method", "handler", "expected_code"), [
    (gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS[0], "GET",
     "handle_manual_save_playlist_snapshot", "invalid_request"),
    (gw.MANUAL_SAVE_PLAYLIST_JOB_PATHS[0], "GET",
     "handle_manual_save_playlist_job", "invalid_request"),
])
def test_missing_query_is_invalid_request(tmp_path, target, method, handler,
                                          expected_code):
    _producer, deps = _deps(tmp_path)
    raw = _raw(method, target)
    client = FakeClient(raw)
    getattr(gw, handler)(client, raw, engine=FakeEngine(), deps=deps)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == expected_code


@pytest.mark.parametrize("query", [
    "playlist_id=1&extra=2",
    "playlist_id=1&playlist_id=2",
    "playlist_id",
    "playlist_id=1&",
    "=1",
])
def test_snapshot_query_rejects_structural_errors(tmp_path, query):
    _producer, deps = _deps(tmp_path)
    raw = _get(gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS[0], query=query)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine(),
                                            deps=deps)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"


@pytest.mark.parametrize("value", ["0", "-1", "+1", "1.5", "abc", "4294967296",
                                   "99999999999", ""])
def test_snapshot_invalid_playlist_id_uses_fixed_400(tmp_path, value):
    _producer, deps = _deps(tmp_path)
    raw = _get(gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS[0],
               query="playlist_id=" + value)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine(),
                                            deps=deps)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_playlist_id"
    assert payload["message"] == "歌单 ID 必须是正整数"


def test_snapshot_identity_query_field_is_invalid_request(tmp_path):
    _producer, deps = _deps(tmp_path)
    raw = _get(gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS[0],
               query="actor_scope=" + SCOPE_USER)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(client, raw, engine=FakeEngine(),
                                            deps=deps)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# Snapshot: success shape, §4.3.1 seven re-checks, artifact re-check
# ---------------------------------------------------------------------------

def _snapshot_call(tmp_path, payload, **handler_kwargs):
    producer, deps = _deps(tmp_path, [payload])
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw, engine=FakeEngine(), deps=deps, **handler_kwargs)
    status, body = _response(client)
    return client, status, body, producer


def test_snapshot_success_shape_and_values(tmp_path):
    _client, status, body, producer = _snapshot_call(tmp_path, _snapshot_payload())
    assert status == 200
    _assert_shape(body)
    assert body["ok"] is True and body["code"] == "ok" and body["message"] == ""
    data = body["data"]
    assert set(data) == {
        "playlist_id", "track_count", "position_count",
        "duplicate_position_count", "enumeration_complete",
        "source_fingerprint", "source_artifact", "manifest_schema_version",
    }
    assert data["playlist_id"] == PLAYLIST_ID
    assert data["track_count"] == 3
    assert data["position_count"] == 3
    assert data["duplicate_position_count"] == 1
    assert data["enumeration_complete"] is True
    assert data["source_fingerprint"] == FINGERPRINT
    assert data["manifest_schema_version"] == 1
    assert data["source_artifact"] == {
        "provider": "netease",
        "package": "NetEase-MusicBox",
        "version": "0.5.3",
        "wheel_sha256": WHEEL_SHA256,
    }
    assert producer.calls == [PLAYLIST_ID]


def test_snapshot_does_not_echo_producer_secrets_or_paths(tmp_path):
    payload = _snapshot_payload(
        signed_url=SIGNED_URL,
        cookie=SECRET_COOKIE,
        cover_url="https://p1.music.126.net/cover.jpg",
        target_dir="/vol1/music",
        staging_dir="/vol1/staging",
    )
    payload["positions"][0]["signed_url"] = SIGNED_URL
    client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 200
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert SECRET_COOKIE not in rendered
    assert "cover.jpg" not in rendered
    assert "/vol1/music" not in rendered
    assert "/vol1/staging" not in rendered


@pytest.mark.parametrize(("overrides", "expected_status", "expected_code"), [
    # 1. positions list length must equal position_count/track_count (§4.3.1).
    ({"positions": []}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 1, "track_id": 1, "error_code": None}],
      "track_count": 2, "position_count": 2}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 1, "track_id": 1, "error_code": None}],
      "track_count": 1, "position_count": 2}, 409, "manifest_incomplete"),
    # 2. positions must be a contiguous 1..N sequence.
    ({"positions": [{"position": 1, "track_id": 1, "error_code": None},
                    {"position": 3, "track_id": 2, "error_code": None}],
      "track_count": 2, "position_count": 2}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 2, "track_id": 1, "error_code": None},
                    {"position": 1, "track_id": 2, "error_code": None}],
      "track_count": 2, "position_count": 2}, 409, "manifest_incomplete"),
    # 3. track ids are positive, non-bool, and no position error_code is set.
    ({"positions": [{"position": 1, "track_id": 0, "error_code": None}],
      "track_count": 1, "position_count": 1}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 1, "track_id": -5, "error_code": None}],
      "track_count": 1, "position_count": 1}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 1, "track_id": "5", "error_code": None}],
      "track_count": 1, "position_count": 1}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 1, "track_id": True, "error_code": None}],
      "track_count": 1, "position_count": 1}, 409, "manifest_incomplete"),
    ({"positions": [{"position": 1, "track_id": 5,
                     "error_code": "INVALID_TRACK_ID"}],
      "track_count": 1, "position_count": 1}, 409, "manifest_incomplete"),
    # 4. track_count bounds.
    ({"positions": [], "track_count": 0, "position_count": 0},
     409, "manifest_incomplete"),
    # 5. fingerprint format.
    ({"source_fingerprint": "z" * 64}, 409, "manifest_incomplete"),
    ({"source_fingerprint": "a" * 63}, 409, "manifest_incomplete"),
    # 6. provider / schema_version are fixed (§4.1).
    ({"provider": "musickit"}, 500, "source_artifact_mismatch"),
    ({"schema_version": 2}, 500, "source_artifact_mismatch"),
    # 7. enumeration_complete=false must never be accepted.
    ({"enumeration_complete": False}, 409, "manifest_incomplete"),
])
def test_snapshot_gateway_rechecks_producer_structure(
        tmp_path, overrides, expected_status, expected_code):
    payload = _snapshot_payload(**overrides)
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == expected_status
    assert body["ok"] is False
    assert body["code"] == expected_code
    _assert_shape(body)


def test_snapshot_track_count_over_limit_is_playlist_too_large(tmp_path):
    track_ids = tuple(range(1, 1002))
    payload = _snapshot_payload(track_ids=track_ids)
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 409
    assert body["code"] == "playlist_too_large"
    assert body["message"] == "歌单成员超过 1000 个"
    # Counts are reported, never silently truncated to the limit.
    assert body["data"]["track_count"] == 1001


def test_snapshot_exactly_1000_tracks_is_accepted(tmp_path):
    payload = _snapshot_payload(track_ids=tuple(range(1, 1001)))
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 200
    assert body["data"]["track_count"] == 1000


@pytest.mark.parametrize("error_code", [
    "SOURCE_READ_FAILED", "PLAYLIST_ID_MISMATCH", "POSITION_LIMIT_EXCEEDED",
    "SOURCE_LIMIT_REACHED", "TRACK_COUNT_MISMATCH", "INVALID_TRACK_ID",
    "SOURCE_SCHEMA_CHANGED", "ACCOUNT_CHANGED", "PLAYLIST_CHANGED",
    "SOURCE_VERSION_CHANGED", "TRACK_SEQUENCE_CHANGED", "UNSUPPORTED_SOURCE",
    "SOURCE_CHANGED_OR_RECHECK_FAILED",
])
def test_snapshot_source_error_code_whitelist_is_echoed(tmp_path, error_code):
    payload = _snapshot_payload(status="incomplete", ok=True,
                                error_code=error_code)
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 409
    assert body["code"] == "manifest_incomplete"
    assert body["data"]["source_error_code"] == error_code
    assert body["data"]["enumeration_complete"] is False
    assert set(body["data"]) == {
        "playlist_id", "track_count", "position_count",
        "enumeration_complete", "source_error_code",
    }
    assert body["data"]["playlist_id"] == PLAYLIST_ID


@pytest.mark.parametrize("error_code", [
    "MYSTERY_REASON", "TRACK_COUNT_MISMATCH_SECRET", "", None, "source_read_failed",
])
def test_snapshot_unknown_source_error_code_is_dropped(tmp_path, error_code):
    payload = _snapshot_payload(status="incomplete", ok=True,
                                error_code=error_code)
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 409
    assert body["data"]["source_error_code"] == ""


@pytest.mark.parametrize(("status_value", "artifact_error", "expected_status",
                          "expected_code"), [
    ("source_changed", None, 409, "source_changed"),
    ("unsupported", None, 409, "playlist_unsupported"),
    ("unsupported", "source_artifact_mismatch", 500, "source_artifact_mismatch"),
    ("upstream_error", None, 502, "playlist_unavailable"),
    ("not_logged_in", None, 403, "not_logged_in"),
])
def test_snapshot_non_complete_status_mapping(tmp_path, status_value,
                                              artifact_error, expected_status,
                                              expected_code):
    payload = _snapshot_payload(status=status_value, ok=True,
                                artifact_error_code=artifact_error)
    if status_value == "unsupported":
        payload["error_code"] = "UNSUPPORTED_SOURCE"
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == expected_status
    assert body["code"] == expected_code


def test_snapshot_position_limit_error_maps_to_playlist_too_large(tmp_path):
    payload = _snapshot_payload(status="unsupported", ok=True,
                                error_code="SOURCE_LIMIT_REACHED")
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 409
    assert body["code"] == "playlist_too_large"


@pytest.mark.parametrize("overrides", [
    {"version": "0.5.4"},
    {"dist_info_present": False},
    {"package": "OtherBox"},
    {"provider": "musickit"},
])
def test_snapshot_complete_branch_artifact_recheck(tmp_path, overrides):
    payload = _snapshot_payload(source_artifact=_artifact(**overrides))
    client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 500
    assert body["code"] == "source_artifact_mismatch"
    assert body["message"] == "音乐服务版本与固定证据不一致"
    assert SIGNED_URL not in client.data.decode("utf-8", "replace")


def test_snapshot_complete_branch_artifact_error_code_is_rejected(tmp_path):
    payload = _snapshot_payload(artifact_error_code="source_artifact_mismatch")
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 500
    assert body["code"] == "source_artifact_mismatch"


def test_snapshot_missing_artifact_is_fail_closed(tmp_path):
    payload = _snapshot_payload()
    del payload["source_artifact"]
    _client, status, body, _producer = _snapshot_call(tmp_path, payload)
    assert status == 500
    assert body["code"] == "source_artifact_mismatch"


def test_snapshot_producer_transport_error_maps_to_fixed_code(tmp_path):
    producer = FakeProducer([
        adapter.ProducerError(adapter.PRODUCER_TIMEOUT),
    ])
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw, engine=FakeEngine(),
        deps=gw._ManualSavePlaylistDeps(service=object(), producer=producer))
    status, body = _response(client)
    assert status == 504
    assert body["code"] == "timeout"


def test_snapshot_malformed_producer_payload_maps_to_playlist_unavailable(tmp_path):
    producer = FakeProducer([adapter.ProducerError(
        adapter.PRODUCER_MALFORMED_RESPONSE)])
    raw = _snapshot_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw, engine=FakeEngine(),
        deps=gw._ManualSavePlaylistDeps(service=object(), producer=producer))
    status, body = _response(client)
    assert status == 502
    assert body["code"] == "playlist_unavailable"


# ---------------------------------------------------------------------------
# Create: fingerprint binding, derived idempotency key, body allow-list
# ---------------------------------------------------------------------------

def _create_raw(playlist_id=PLAYLIST_ID, fingerprint=FINGERPRINT, **kwargs):
    body = {"playlist_id": playlist_id, "source_fingerprint": fingerprint}
    extra = kwargs.pop("body", None)
    if extra is not None:
        body = extra
    return _post(gw.MANUAL_SAVE_PLAYLIST_JOBS_PATHS[0], body, **kwargs)


def _create_call(tmp_path, payloads, *, body=None, save_engine=None, **kwargs):
    producer = FakeProducer(list(payloads))
    _ledger, _service, deps = _make_deps(
        tmp_path, producer, save_engine if save_engine is not None else SaveEngine())
    raw = _create_raw(**(body or {}))
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        client, raw, engine=FakeEngine(), deps=deps, **kwargs)
    status, response = _response(client)
    return client, status, response, producer, deps, str(tmp_path / "jobs.db")


def test_create_success_shape_manifest_and_job_summary(tmp_path):
    payload = _snapshot_payload()
    _client, status, body, producer, _deps_obj, db_path = _create_call(
        tmp_path, [payload])
    assert status == 200
    _assert_shape(body)
    assert body["ok"] is True and body["code"] == "ok"
    data = body["data"]
    assert set(data) == {"job", "manifest_sha256", "manifest"}
    assert "entries" not in data["manifest"]
    manifest = data["manifest"]
    assert manifest == {
        "kind": "playlist",
        "target_id": "manual-save",
        "provider_id": "netease-0.5.3-7f695b35",
        "collection_id": "netease-playlist-" + str(PLAYLIST_ID),
        "collection_version": "sha256-" + FINGERPRINT,
        "enumeration_complete": True,
        "positions_total": 3,
    }
    assert len(data["manifest_sha256"]) == 64
    job = data["job"]
    assert set(job) == {
        "job_id", "state", "cancel_requested", "counts", "positions_total",
        "manifest_sha256", "audio_verified", "production_ready", "import_ready",
    }
    assert job["state"] == "queued"
    assert job["cancel_requested"] is False
    assert job["positions_total"] == 3
    assert job["counts"]["queued"] == 3
    assert job["audio_verified"] is False
    assert job["production_ready"] is False
    assert job["import_ready"] is False
    assert job["manifest_sha256"] == data["manifest_sha256"]
    assert producer.calls == [PLAYLIST_ID]
    assert _job_count(db_path) == 1


def test_create_derives_idempotency_key_and_repeat_returns_same_job(tmp_path):
    payload = _snapshot_payload()
    producer = FakeProducer([payload, payload])
    _ledger, _service, deps = _make_deps(tmp_path, producer, SaveEngine())
    raw = _create_raw()
    first_client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        first_client, raw, engine=FakeEngine(), deps=deps)
    first_status, first = _response(first_client)
    second_client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        second_client, raw, engine=FakeEngine(), deps=deps)
    second_status, second = _response(second_client)
    assert first_status == second_status == 200
    assert first["data"]["job"]["job_id"] == second["data"]["job"]["job_id"]
    assert first["data"]["job"]["manifest_sha256"] == \
        second["data"]["job"]["manifest_sha256"]
    assert _job_count(str(tmp_path / "jobs.db")) == 1
    # §3.4 step 7 exact derivation: length 76, legal ledger identifier.
    key = gw._manual_save_playlist_derived_key(PLAYLIST_ID, FINGERPRINT)
    assert key == "p" + str(PLAYLIST_ID) + "-" + FINGERPRINT
    # 1 ("p") + digits of playlist_id + 1 ("-") + 64 hex fingerprint.
    assert len(key) == 1 + len(str(PLAYLIST_ID)) + 1 + 64
    assert len(key) <= 128
    assert ledger_mod._IDENTIFIER_RE.fullmatch(key) is not None


def test_create_fingerprint_mismatch_is_source_changed_without_ledger_write(tmp_path):
    changed = _snapshot_payload(source_fingerprint=OTHER_FINGERPRINT)
    _client, status, body, producer, _deps_obj, db_path = _create_call(
        tmp_path, [changed])
    assert status == 409
    assert body["code"] == "source_changed"
    assert body["message"] == "歌单在确认后发生变化，请重新读取"
    assert producer.calls == [PLAYLIST_ID]
    assert _job_count(db_path) == 0


def test_create_non_complete_snapshot_is_rejected_without_ledger_write(tmp_path):
    payload = _snapshot_payload(status="incomplete", ok=True,
                                error_code="TRACK_COUNT_MISMATCH")
    _client, status, body, _producer, _deps_obj, db_path = _create_call(
        tmp_path, [payload])
    assert status == 409
    assert body["code"] == "manifest_incomplete"
    assert _job_count(db_path) == 0


def test_create_artifact_mismatch_on_complete_branch(tmp_path):
    payload = _snapshot_payload(source_artifact=_artifact(version="0.5.4"))
    _client, status, body, _producer, _deps_obj, db_path = _create_call(
        tmp_path, [payload])
    assert status == 500
    assert body["code"] == "source_artifact_mismatch"
    assert _job_count(db_path) == 0


@pytest.mark.parametrize(("body", "expected_status", "expected_code"), [
    ({"playlist_id": 0, "source_fingerprint": FINGERPRINT},
     400, "invalid_playlist_id"),
    ({"playlist_id": -1, "source_fingerprint": FINGERPRINT},
     400, "invalid_playlist_id"),
    ({"playlist_id": "1", "source_fingerprint": FINGERPRINT},
     400, "invalid_playlist_id"),
    ({"playlist_id": True, "source_fingerprint": FINGERPRINT},
     400, "invalid_playlist_id"),
    ({"playlist_id": PLAYLIST_ID, "source_fingerprint": "z" * 64},
     400, "invalid_fingerprint"),
    ({"playlist_id": PLAYLIST_ID, "source_fingerprint": "a" * 63},
     400, "invalid_fingerprint"),
    ({"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
      "idempotency_key": ""}, 400, "invalid_idempotency_key"),
    ({"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
      "idempotency_key": "bad key"}, 400, "invalid_idempotency_key"),
    ({"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
      "idempotency_key": 5}, 400, "invalid_idempotency_key"),
])
def test_create_body_validation_codes(tmp_path, body, expected_status,
                                      expected_code):
    producer = FakeProducer([_snapshot_payload()])
    _ledger, _service, deps = _make_deps(tmp_path, producer, SaveEngine())
    raw = _create_raw(body=body)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        client, raw, engine=FakeEngine(), deps=deps)
    status, response = _response(client)
    assert status == expected_status
    assert response["code"] == expected_code
    assert producer.calls == []


@pytest.mark.parametrize("body", [
    {"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
     "actor_scope": SCOPE_USER},
    {"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
     "userid": "2000"},
    {"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
     "x-trim-userid": "2000"},
    {"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
     "entries": []},
    {"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
     "idempotency_key": "k1", "extra": 1},
])
def test_create_rejects_unknown_or_identity_fields(tmp_path, body):
    producer = FakeProducer([_snapshot_payload()])
    _ledger, _service, deps = _make_deps(tmp_path, producer, SaveEngine())
    raw = _create_raw(body=body)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        client, raw, engine=FakeEngine(), deps=deps)
    status, response = _response(client)
    assert status == 400
    assert response["code"] == "invalid_request"
    assert producer.calls == []


@pytest.mark.parametrize("raw_body", [b"not-json", b"[]", b"\xff\xfe", b""])
def test_create_rejects_malformed_body(tmp_path, raw_body):
    producer = FakeProducer([_snapshot_payload()])
    _ledger, _service, deps = _make_deps(tmp_path, producer, SaveEngine())
    raw = _raw("POST", gw.MANUAL_SAVE_PLAYLIST_JOBS_PATHS[0], body=raw_body)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        client, raw, engine=FakeEngine(), deps=deps)
    status, response = _response(client)
    assert status == 400
    assert response["code"] == "invalid_request"
    assert producer.calls == []


def test_create_requires_content_length(tmp_path):
    producer = FakeProducer([_snapshot_payload()])
    _ledger, _service, deps = _make_deps(tmp_path, producer, SaveEngine())
    body = json.dumps({"playlist_id": PLAYLIST_ID,
                       "source_fingerprint": FINGERPRINT}).encode("utf-8")
    raw = _raw("POST", gw.MANUAL_SAVE_PLAYLIST_JOBS_PATHS[0], body=body,
               content_length=len(body), omit_content_length=True)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        client, raw, engine=FakeEngine(), deps=deps)
    status, response = _response(client)
    assert status == 400
    assert response["code"] == "invalid_request"
    assert producer.calls == []


def test_create_explicit_idempotency_key_conflict_on_different_manifest(tmp_path):
    producer = FakeProducer([
        _snapshot_payload(),
        _snapshot_payload(source_fingerprint=OTHER_FINGERPRINT),
    ])
    _ledger, _service, deps = _make_deps(tmp_path, producer, SaveEngine())
    first_raw = _create_raw(body={
        "playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT,
        "idempotency_key": "shared-key-1"})
    first_client = FakeClient(first_raw)
    gw.handle_manual_save_playlist_create(
        first_client, first_raw, engine=FakeEngine(), deps=deps)
    assert _response(first_client)[0] == 200
    second_raw = _create_raw(body={
        "playlist_id": PLAYLIST_ID, "source_fingerprint": OTHER_FINGERPRINT,
        "idempotency_key": "shared-key-1"})
    second_client = FakeClient(second_raw)
    gw.handle_manual_save_playlist_create(
        second_client, second_raw, engine=FakeEngine(), deps=deps)
    status, response = _response(second_client)
    assert status == 409
    assert response["code"] == "idempotency_conflict"
    assert _job_count(str(tmp_path / "jobs.db")) == 1


# ---------------------------------------------------------------------------
# Query job: shape, sanitization, cross-scope 404
# ---------------------------------------------------------------------------

def _created_job(tmp_path, payload=None, save_engine=None):
    producer = FakeProducer([_snapshot_payload() if payload is None else payload])
    ledger, service, deps = _make_deps(
        tmp_path, producer, save_engine if save_engine is not None else SaveEngine())
    job = service.create_job(SCOPE_USER, PLAYLIST_ID, _snapshot_payload()
                             if payload is None else payload)
    return ledger, service, deps, job


def _job_raw(job_id=JOB_ID, **kwargs):
    return _get(gw.MANUAL_SAVE_PLAYLIST_JOB_PATHS[0],
                query="job_id=" + job_id, **kwargs)


def test_job_query_returns_fixed_positions_and_summary(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    raw = _job_raw(job.job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 200
    _assert_shape(body)
    data = body["data"]
    assert set(data) == {"job", "positions"}
    assert data["job"]["job_id"] == job.job_id
    assert data["job"]["positions_total"] == 3
    assert len(data["positions"]) == 3
    assert [row["position"] for row in data["positions"]] == [1, 2, 3]
    assert set(data["positions"][0]) == {
        "position", "source_id", "state", "reason", "reason_message",
        "cleanup_pending",
    }
    assert data["positions"][0]["source_id"] == "1901371647"
    assert data["positions"][0]["state"] == "queued"
    assert data["positions"][0]["reason"] is None
    assert data["positions"][0]["reason_message"] == ""
    assert data["positions"][0]["cleanup_pending"] is False
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert SECRET_COOKIE not in rendered
    assert "/vol1/music" not in rendered


def test_job_query_cross_scope_is_not_found(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    raw = _job_raw(job.job_id, userid="2000")
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 404
    assert body["code"] == "not_found"
    assert body["message"] == "任务不存在"


@pytest.mark.parametrize("job_id", [UNKNOWN_JOB_ID, "abc", "0" * 47, "0" * 49,
                                    "A" * 48, "", "0" * 47 + "g"])
def test_job_query_invalid_or_unknown_id_is_not_found(tmp_path, job_id):
    _ledger, _service, deps, job = _created_job(tmp_path)
    raw = _job_raw(job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 404
    assert body["code"] == "not_found"


# ---------------------------------------------------------------------------
# Advance: five run statuses, lock, busy-does-not-consume, timeout
# ---------------------------------------------------------------------------

class StubAdvanceService:
    """Wraps the real service but returns a controlled AdvanceReport."""

    def __init__(self, inner, status, *, disposition=None, position=None,
                 result=None, gate=None):
        self.inner = inner
        self.status = status
        self.disposition = disposition
        self.position = position
        self.result = result
        self.gate = gate
        self.calls = 0

    def advance_once(self, actor_scope, job_id):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        return adapter.AdvanceReport(
            self.status, position=self.position, result=self.result,
            disposition=self.disposition)

    def get_job(self, actor_scope, job_id):
        return self.inner.get_job(actor_scope, job_id)


def _stub_deps(service, producer):
    return gw._ManualSavePlaylistDeps(service=service, producer=producer)


def _advance_raw(job_id=JOB_ID, **kwargs):
    body = {"job_id": job_id}
    extra = kwargs.pop("body", None)
    if extra is not None:
        body = extra
    return _post(gw.MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS[0], body, **kwargs)


def _advance(tmp_path, deps, save_engine=None, job_id=None, **handler_kwargs):
    raw = _advance_raw(job_id if job_id is not None else JOB_ID)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_advance(
        client, raw, engine=FakeEngine(), deps=deps, **handler_kwargs)
    status, body = _response(client)
    return client, status, body


def test_advance_completed_published_returns_continue(tmp_path):
    save_engine = SaveEngine([_published()])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    _client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    assert status == 200
    _assert_shape(body)
    assert body["ok"] is True and body["code"] == "ok"
    run = body["data"]["run"]
    assert set(run) == {
        "status", "disposition", "position", "outcome", "reason", "detail",
    }
    assert run == {
        "status": "completed",
        "disposition": "continue",
        "position": 1,
        "outcome": "published",
        "reason": "bytes_published",
        "detail": None,
    }
    assert body["data"]["job"]["counts"]["published"] == 1
    assert body["data"]["job"]["state"] == "queued"
    assert save_engine.calls == [1901371647]


def test_advance_completed_continue_code_keeps_going(tmp_path):
    save_engine = SaveEngine([_failure("download_failed")])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    _client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    assert status == 200
    assert body["data"]["run"]["status"] == "completed"
    assert body["data"]["run"]["disposition"] == "continue"
    assert body["data"]["run"]["outcome"] == "rejected"
    assert body["data"]["run"]["reason"] == "download_failed"
    assert body["data"]["job"]["counts"]["rejected"] == 1


def test_advance_completed_abort_code_stops_dispatch(tmp_path):
    save_engine = SaveEngine([_failure("disabled")])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    _client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    assert status == 200
    assert body["data"]["run"]["disposition"] == "abort"
    assert body["data"]["run"]["outcome"] == "failed"
    assert body["data"]["run"]["reason"] == "disabled"


def test_advance_commit_unknown_freezes_and_blocks_further_dispatch(tmp_path):
    save_engine = SaveEngine([_failure("publish_failed")])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    _client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    assert status == 200
    assert body["ok"] is True
    run = body["data"]["run"]
    assert run["status"] == "commit_unknown"
    assert run["disposition"] == "freeze"
    assert run["outcome"] == "commit_unknown"
    assert run["reason"] == "commit_unknown"
    assert body["data"]["job"]["state"] == "reconciliation_required"
    # Frozen ledger: the next advance is no_work + continue and never calls the
    # executor again (§5.4/§6.1).
    _client2, status2, body2 = _advance(tmp_path, deps, job_id=job.job_id)
    assert status2 == 200
    assert body2["data"]["run"]["status"] == "no_work"
    assert body2["data"]["run"]["disposition"] == "continue"
    assert len(save_engine.calls) == 1


def test_advance_completion_unknown_is_500_freeze(tmp_path):
    save_engine = SaveEngine()
    _ledger, service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    stub = StubAdvanceService(service, "completion_unknown",
                              disposition=adapter.DISPOSITION_FREEZE)
    _client, status, body = _advance(tmp_path, _stub_deps(stub, deps.producer),
                                     job_id=job.job_id)
    assert status == 500
    assert body["ok"] is False
    assert body["code"] == "completion_unknown"
    assert body["message"] == "任务结果写入失败，需人工核对"
    assert body["data"]["run"]["status"] == "completion_unknown"
    assert body["data"]["run"]["disposition"] == "freeze"
    assert save_engine.calls == []


def test_advance_stale_completion_is_409_freeze(tmp_path):
    _ledger, service, deps, job = _created_job(tmp_path)
    position = ledger_mod.PositionSnapshot(
        position=1, source_id="1901371647", source_status="ok",
        state="published", outcome="published", reason="bytes_published",
        cleanup_pending=False)
    stub = StubAdvanceService(service, "stale_completion", position=position,
                              disposition=None)
    _client, status, body = _advance(tmp_path, _stub_deps(stub, deps.producer),
                                     job_id=job.job_id)
    assert status == 409
    assert body["code"] == "stale_completion"
    assert body["message"] == "迟到的执行结果未覆盖记录"
    assert body["data"]["run"]["disposition"] == "freeze"
    assert body["data"]["run"]["position"] == 1


def test_advance_no_work_is_200_continue(tmp_path):
    _ledger, service, deps, job = _created_job(tmp_path)
    stub = StubAdvanceService(service, "no_work", disposition=None)
    _client, status, body = _advance(tmp_path, _stub_deps(stub, deps.producer),
                                     job_id=job.job_id)
    assert status == 200
    assert body["ok"] is True
    assert body["data"]["run"]["status"] == "no_work"
    assert body["data"]["run"]["disposition"] == "continue"
    assert body["data"]["run"]["position"] is None


def test_advance_busy_lock_does_not_consume_a_position(tmp_path):
    save_engine = SaveEngine([_published()])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    assert gw.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    try:
        _client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    finally:
        gw.MANUAL_SAVE_PLAYLIST_LOCK.release()
    assert status == 409
    assert body["code"] == "busy"
    assert body["message"] == "已有保存任务进行中"
    assert body["data"] == {}
    assert save_engine.calls == []
    # The position is still queued: busy never claimed it (§7.1).
    current = deps.service.get_job(SCOPE_USER, job.job_id)
    assert current.counts["queued"] == 3
    assert current.counts["running"] == 0


def test_advance_timeout_returns_504_while_worker_finishes(tmp_path):
    _ledger, service, deps, job = _created_job(tmp_path)
    gate = threading.Event()
    stub = StubAdvanceService(service, "no_work", disposition=None, gate=gate)
    raw = _advance_raw(job.job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_advance(
        client, raw, engine=FakeEngine(), deps=_stub_deps(stub, deps.producer),
        timeout=0.05)
    status, body = _response(client)
    assert status == 504
    assert body["code"] == "timeout"
    assert body["message"] == "保存超时"
    gate.set()
    for _ in range(100):
        if not gw.MANUAL_SAVE_PLAYLIST_LOCK.locked():
            break
        threading.Event().wait(0.01)
    assert gw.MANUAL_SAVE_PLAYLIST_LOCK.locked() is False


@pytest.mark.parametrize("body", [
    {"job_id": "abc"},
    {"job_id": "0" * 47},
    {"job_id": ""},
])
def test_advance_bad_job_id_is_not_found(tmp_path, body):
    _ledger, _service, deps, job = _created_job(tmp_path)
    raw = _advance_raw(body=body)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_advance(
        client, raw, engine=FakeEngine(), deps=deps)
    status, response = _response(client)
    assert status == 404
    assert response["code"] == "not_found"


def test_advance_unknown_job_is_not_found(tmp_path):
    _ledger, _service, deps, _job = _created_job(tmp_path)
    _client, status, body = _advance(tmp_path, deps, job_id=UNKNOWN_JOB_ID)
    assert status == 404
    assert body["code"] == "not_found"


def test_advance_identity_body_field_is_invalid_request(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    body = {"job_id": job.job_id, "actor_scope": SCOPE_USER}
    raw = _advance_raw(body=body)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_advance(
        client, raw, engine=FakeEngine(), deps=deps)
    status, response = _response(client)
    assert status == 400
    assert response["code"] == "invalid_request"


def test_advance_ledger_unavailable_is_503(tmp_path):
    _ledger, service, deps, job = _created_job(tmp_path)

    class BrokenService(StubAdvanceService):
        def advance_once(self, actor_scope, job_id):
            raise ledger_mod.LedgerUnavailable()

    stub = BrokenService(service, "no_work")
    _client, status, body = _advance(
        tmp_path, _stub_deps(stub, deps.producer), job_id=job.job_id)
    assert status == 503
    assert body["code"] == "ledger_unavailable"


# ---------------------------------------------------------------------------
# Gateway-level execution lock serializes track and advance (§7.1)
# ---------------------------------------------------------------------------

def test_track_endpoint_takes_the_same_gateway_lock(tmp_path):
    engine = FakeEngine()
    raw = _raw("POST", gw.MANUAL_SAVE_TRACK_PATHS[0],
               body=json.dumps({"song_id": 123}).encode("utf-8"))
    assert gw.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    try:
        client = FakeClient(raw)
        gw.handle_manual_save_track(client, raw, engine=engine)
        status, body = _response(client)
    finally:
        gw.MANUAL_SAVE_PLAYLIST_LOCK.release()
    assert status == 409
    assert body["code"] == "busy"
    assert body["message"] == "已有保存任务进行中"
    assert engine.save_calls == []
    # Lock released: the same request now executes normally.
    client2 = FakeClient(raw)
    gw.handle_manual_save_track(client2, raw, engine=engine)
    status2, body2 = _response(client2)
    assert status2 == 200
    assert engine.save_calls == [123]


def test_advance_blocked_while_track_holds_the_lock(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    assert gw.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    try:
        _client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    finally:
        gw.MANUAL_SAVE_PLAYLIST_LOCK.release()
    assert status == 409
    assert body["code"] == "busy"
    assert deps.service.get_job(SCOPE_USER, job.job_id).counts["queued"] == 3


# ---------------------------------------------------------------------------
# Cancel (§3.7)
# ---------------------------------------------------------------------------

def _cancel_raw(job_id=JOB_ID, **kwargs):
    body = {"job_id": job_id}
    extra = kwargs.pop("body", None)
    if extra is not None:
        body = extra
    return _post(gw.MANUAL_SAVE_PLAYLIST_CANCEL_PATHS[0], body, **kwargs)


def _cancel(deps, job_id, **handler_kwargs):
    raw = _cancel_raw(job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_cancel(
        client, raw, engine=FakeEngine(), deps=deps, **handler_kwargs)
    status, body = _response(client)
    return client, status, body


def test_cancel_marks_queued_positions_and_is_idempotent(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    _client, status, body = _cancel(deps, job.job_id)
    assert status == 200
    _assert_shape(body)
    job_data = body["data"]["job"]
    assert job_data["cancel_requested"] is True
    assert job_data["state"] == "cancelled"
    assert job_data["counts"]["cancelled"] == 3
    _client2, status2, body2 = _cancel(deps, job.job_id)
    assert status2 == 200
    assert body2["data"]["job"] == job_data


def test_cancel_does_not_wait_for_the_execution_lock(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    assert gw.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    try:
        _client, status, body = _cancel(deps, job.job_id)
    finally:
        gw.MANUAL_SAVE_PLAYLIST_LOCK.release()
    assert status == 200
    assert body["data"]["job"]["cancel_requested"] is True


@pytest.mark.parametrize("job_id", ["abc", "A" * 48, ""])
def test_cancel_bad_job_id_is_not_found(tmp_path, job_id):
    _ledger, _service, deps, _job = _created_job(tmp_path)
    _client, status, body = _cancel(deps, job_id)
    assert status == 404
    assert body["code"] == "not_found"


def test_cancel_unknown_job_is_not_found(tmp_path):
    _ledger, _service, deps, _job = _created_job(tmp_path)
    _client, status, body = _cancel(deps, UNKNOWN_JOB_ID)
    assert status == 404
    assert body["code"] == "not_found"


def test_cancel_identity_body_field_is_invalid_request(tmp_path):
    _ledger, _service, deps, job = _created_job(tmp_path)
    raw = _cancel_raw(body={"job_id": job.job_id, "actor_scope": SCOPE_USER})
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_cancel(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 400
    assert body["code"] == "invalid_request"


# ---------------------------------------------------------------------------
# Playlist list (§3.2)
# ---------------------------------------------------------------------------

def _list_payload():
    return {
        "ok": True,
        "logged_in": True,
        "account_uid": 123456789,
        "data": [
            {"playlist_id": 111, "name": "歌单一", "track_count": 3,
             "updated_at": 1730000000, "user_id": 123456789,
             "cover_url": SIGNED_URL, "special_type": 0},
            {"playlist_id": 222, "name": "收藏歌单", "track_count": 0,
             "updated_at": 0, "user_id": 999, "cover_url": "https://secret"},
            {"playlist_id": "bad", "name": "invalid id", "track_count": 1,
             "updated_at": 0, "user_id": 1},
            {"playlist_id": 333, "name": "invalid count", "track_count": -1,
             "updated_at": 0, "user_id": 1},
            "not-a-mapping",
        ],
    }


def _list_call(tmp_path, payload=None, *, fetcher=None):
    producer, deps = _deps(tmp_path)
    raw = _raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0])
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_playlists(
        client, raw, engine=FakeEngine(), deps=deps,
        list_fetcher=fetcher if fetcher is not None else (
            lambda timeout: _list_payload() if payload is None else payload),
        timeout=1.0)
    status, body = _response(client)
    return client, status, body


def test_playlists_sanitizes_rows_to_the_whitelist(tmp_path):
    client, status, body = _list_call(tmp_path)
    assert status == 200
    _assert_shape(body)
    data = body["data"]
    assert set(data) == {"account_uid", "playlists"}
    assert data["account_uid"] == 123456789
    assert data["playlists"] == [
        {"playlist_id": 111, "name": "歌单一", "track_count": 3,
         "updated_at": 1730000000, "owned": True},
        {"playlist_id": 222, "name": "收藏歌单", "track_count": 0,
         "updated_at": 0, "owned": False},
    ]
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert "cover_url" not in rendered
    assert "special_type" not in rendered
    assert "https://secret" not in rendered


def test_playlists_truncates_long_names_to_256_chars(tmp_path):
    payload = _list_payload()
    payload["data"] = [{"playlist_id": 1, "name": "长" * 400, "track_count": 1,
                        "updated_at": 0, "user_id": 123456789}]
    _client, status, body = _list_call(tmp_path, payload)
    assert status == 200
    assert len(body["data"]["playlists"][0]["name"]) == 256


def test_playlists_caps_rows_at_100(tmp_path):
    payload = _list_payload()
    payload["data"] = [
        {"playlist_id": index + 1, "name": "p", "track_count": 0,
         "updated_at": 0, "user_id": 123456789}
        for index in range(150)
    ]
    _client, status, body = _list_call(tmp_path, payload)
    assert status == 200
    assert len(body["data"]["playlists"]) == 100


def test_playlists_not_logged_in_is_403(tmp_path):
    payload = {"ok": False, "error": "not_logged_in", "logged_in": False}
    _client, status, body = _list_call(tmp_path, payload)
    assert status == 403
    assert body["code"] == "not_logged_in"
    assert body["message"] == "网易云账号未登录"


def test_playlists_unavailable_is_502(tmp_path):
    payload = {"ok": False, "error": "boom"}
    _client, status, body = _list_call(tmp_path, payload)
    assert status == 502
    assert body["code"] == "playlist_unavailable"


def test_playlists_timeout_is_504(tmp_path):
    producer, deps = _deps(tmp_path)
    raw = _raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0])
    client = FakeClient(raw)

    def blocking_fetcher(timeout):
        threading.Event().wait(0.2)
        return _list_payload()

    gw.handle_manual_save_playlist_playlists(
        client, raw, engine=FakeEngine(), deps=deps,
        list_fetcher=blocking_fetcher, timeout=0.02)
    status, body = _response(client)
    assert status == 504
    assert body["code"] == "timeout"


def test_playlists_producer_transport_error_is_502(tmp_path):
    producer, deps = _deps(tmp_path)
    raw = _raw("GET", gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0])
    client = FakeClient(raw)

    def failing_fetcher(timeout):
        raise adapter.ProducerError(adapter.PRODUCER_HTTP_STATUS)

    gw.handle_manual_save_playlist_playlists(
        client, raw, engine=FakeEngine(), deps=deps,
        list_fetcher=failing_fetcher, timeout=1.0)
    status, body = _response(client)
    assert status == 502
    assert body["code"] == "playlist_unavailable"


@pytest.mark.parametrize(("target", "expected_code"), [
    (gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS[0] + "?limit=10", "invalid_request"),
])
def test_playlists_rejects_query_and_body(tmp_path, target, expected_code):
    producer, deps = _deps(tmp_path)
    raw = _get(target)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_playlists(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 400
    assert body["code"] == expected_code


# ---------------------------------------------------------------------------
# Loopback validation for the gateway's own outbound URL (§8.3)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(("url", "expected_code"), [
    ("https://127.0.0.1:8770", "producer_not_loopback"),
    ("http://10.0.0.5:8770", "producer_not_loopback"),
    ("http://example.com:8770", "producer_not_loopback"),
    ("http://user:pass@127.0.0.1:8770", "producer_not_loopback"),
    ("http://127.0.0.1:8770/api", "producer_not_loopback"),
    ("http://127.0.0.1:8770?x=1", "producer_not_loopback"),
    ("", "producer_config_invalid"),
    (None, "producer_config_invalid"),
])
def test_loopback_target_validation(url, expected_code):
    with pytest.raises(adapter.PlaylistJobError) as error:
        gw._manual_save_playlist_loopback_target(url)
    assert error.value.code == expected_code


def test_loopback_target_accepts_local_forms():
    assert gw._manual_save_playlist_loopback_target("http://127.0.0.1:8770") == (
        "127.0.0.1", 8770)
    assert gw._manual_save_playlist_loopback_target("http://localhost") == (
        "localhost", 80)
    assert gw._manual_save_playlist_loopback_target("http://[::1]:8770") == (
        "::1", 8770)


# ---------------------------------------------------------------------------
# Fixed code tables / message discipline / reason rendering
# ---------------------------------------------------------------------------

def test_every_registered_code_has_http_status_and_fixed_message():
    assert set(gw._MANUAL_SAVE_PLAYLIST_MESSAGES) == set(
        gw._MANUAL_SAVE_PLAYLIST_HTTP_STATUS)
    for code, status in gw._MANUAL_SAVE_PLAYLIST_HTTP_STATUS.items():
        assert type(status) is int and 200 <= status <= 599
        message = gw._manual_save_playlist_message(code)
        assert isinstance(message, str)
        if code == "ok":
            assert message == ""
        else:
            assert message and "http" not in message and "/" not in message
        assert message == gw._MANUAL_SAVE_PLAYLIST_MESSAGES[code]


def test_unregistered_code_collapses_to_internal():
    assert gw._manual_save_playlist_message("mystery_code") == "内部错误"
    assert gw._manual_save_playlist_http_status("mystery_code", False) == 500
    assert gw._manual_save_playlist_code_for_exception(
        RuntimeError("secret text")) == "internal"
    client = FakeClient()
    gw._manual_save_playlist_send_result(
        client, {"ok": False, "code": "mystery_code", "data": {}})
    status, body = _response(client)
    assert status == 500
    assert body["code"] == "internal"
    assert "mystery_code" not in body["message"]
    assert "secret" not in client.data.decode("utf-8", "replace")


def test_ok_result_with_unknown_code_collapses_to_internal():
    client = FakeClient()
    gw._manual_save_playlist_send_result(
        client, {"ok": True, "code": "not_ok", "data": {}})
    status, body = _response(client)
    assert status == 500
    assert body["ok"] is False
    assert body["code"] == "internal"


@pytest.mark.parametrize(("exception_code", "expected_code", "expected_status"), [
    ("producer_timeout", "timeout", 504),
    ("producer_unreachable", "playlist_unavailable", 502),
    ("producer_not_loopback", "config_invalid", 500),
    ("producer_config_invalid", "config_invalid", 500),
    ("manifest_disabled", "config_invalid", 500),
    ("ledger_unavailable", "ledger_unavailable", 503),
    ("capacity_exceeded", "capacity_exceeded", 409),
    ("idempotency_conflict", "idempotency_conflict", 409),
    ("not_found", "not_found", 404),
    ("invalid_schema", "internal", 500),
])
def test_exception_codes_map_to_reviewed_http_codes(exception_code,
                                                    expected_code,
                                                    expected_status):
    class FixedCodeError(Exception):
        def __init__(self, code_value):
            self.code = code_value
            super().__init__(code_value)

    mapped = gw._manual_save_playlist_code_for_exception(
        FixedCodeError(exception_code))
    assert mapped == expected_code
    assert gw._manual_save_playlist_http_status(mapped, False) == expected_status


def test_reason_message_table_matches_contract_and_fallback():
    assert gw._manual_save_playlist_reason_message("bytes_published") == "已发布"
    assert gw._manual_save_playlist_reason_message("destination_conflict") == \
        "目标已存在同名文件，未覆盖"
    assert gw._manual_save_playlist_reason_message("stage_unavailable") == "暂存不可用"
    assert gw._manual_save_playlist_reason_message("staging_failed") == "暂存不可用"
    assert gw._manual_save_playlist_reason_message("download_failed") == "下载失败"
    assert gw._manual_save_playlist_reason_message(None) == ""
    assert gw._manual_save_playlist_reason_message("unknown_reason_xyz") == \
        "未保存（原因未登记）"
    assert gw._manual_save_playlist_reason_message("原样文本") == \
        "未保存（原因未登记）"


def test_reason_message_rendered_in_job_query(tmp_path):
    save_engine = SaveEngine([
        _failure("download_failed"),
        _published(),
        _failure("staging_unavailable"),
    ])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    for _ in range(3):
        _client, status, _body = _advance(tmp_path, deps, job_id=job.job_id)
        assert status == 200
    raw = _job_raw(job.job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 200
    rows = body["data"]["positions"]
    assert rows[0]["reason"] == "download_failed"
    assert rows[0]["reason_message"] == "下载失败"
    assert rows[1]["reason"] == "bytes_published"
    assert rows[1]["reason_message"] == "已发布"
    assert rows[2]["reason"] == "stage_unavailable"
    assert rows[2]["reason_message"] == "暂存不可用"


def test_duplicate_positions_are_kept_never_merged(tmp_path):
    payload = _snapshot_payload()
    _ledger, _service, deps, job = _created_job(tmp_path, payload=payload)
    raw = _job_raw(job.job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(client, raw, engine=FakeEngine(), deps=deps)
    _status, body = _response(client)
    rows = body["data"]["positions"]
    assert [row["position"] for row in rows] == [1, 2, 3]
    assert rows[0]["source_id"] == rows[2]["source_id"] == "1901371647"


# ---------------------------------------------------------------------------
# Remaining contract edges: targets, bounded inputs, ledger errors, detail
# ---------------------------------------------------------------------------

ENDPOINT_HANDLERS = (
    ("handle_manual_save_playlist_playlists", "GET",
     gw.MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS, None),
    ("handle_manual_save_playlist_snapshot", "GET",
     gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS, None),
    ("handle_manual_save_playlist_create", "POST",
     gw.MANUAL_SAVE_PLAYLIST_JOBS_PATHS,
     {"playlist_id": PLAYLIST_ID, "source_fingerprint": FINGERPRINT}),
    ("handle_manual_save_playlist_job", "GET",
     gw.MANUAL_SAVE_PLAYLIST_JOB_PATHS, None),
    ("handle_manual_save_playlist_advance", "POST",
     gw.MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS, {"job_id": JOB_ID}),
    ("handle_manual_save_playlist_cancel", "POST",
     gw.MANUAL_SAVE_PLAYLIST_CANCEL_PATHS, {"job_id": JOB_ID}),
)


@pytest.mark.parametrize(("handler_name", "method", "paths", "body"),
                         ENDPOINT_HANDLERS)
def test_each_endpoint_has_a_strict_head_parse(tmp_path, handler_name, method,
                                               paths, body):
    handler = getattr(gw, handler_name)
    # Duplicate CSRF header: ambiguous framing, fixed 400 before any work.
    raw = _raw(method, paths[0], body=json.dumps(body).encode("utf-8")
               if body is not None else b"",
               extra_headers=(("x-fnmusic-save", "1"),))
    client = FakeClient(raw)
    handler(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"
    assert client.closed is True
    # A target outside the fixed path list is rejected, never partially matched.
    illegal = paths[0] + "/extra"
    raw2 = _raw(method, illegal, body=json.dumps(body).encode("utf-8")
                if body is not None else b"")
    client2 = FakeClient(raw2)
    handler(client2, raw2, engine=FakeEngine())
    status2, payload2 = _response(client2)
    assert status2 == 400
    assert payload2["code"] == "invalid_request"


def test_post_body_over_limit_is_invalid_request(tmp_path):
    _producer, deps = _deps(tmp_path)
    raw = _raw("POST", gw.MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS[0],
               content_length=gw.MANUAL_SAVE_MAX_BODY + 1)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_advance(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 400
    assert body["code"] == "invalid_request"


def test_snapshot_query_over_limit_is_invalid_request(tmp_path):
    _producer, deps = _deps(tmp_path)
    long_query = "playlist_id=1&" + "a" * (gw.MANUAL_SAVE_MAX_QUERY + 1)
    raw = _get(gw.MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS[0], query=long_query)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_snapshot(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 400
    assert body["code"] == "invalid_request"


def test_create_capacity_exceeded_is_409(tmp_path):
    class CapacityService:
        def create_job(self, actor_scope, playlist_id, payload, *, idempotency_key=None):
            raise ledger_mod.CapacityExceeded()

    deps = gw._ManualSavePlaylistDeps(
        service=CapacityService(), producer=FakeProducer([_snapshot_payload()]))
    raw = _create_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_create(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 409
    assert body["code"] == "capacity_exceeded"
    assert body["message"] == "任务账本容量已满"


def test_query_ledger_unavailable_is_503(tmp_path):
    class UnavailableService:
        def get_job(self, actor_scope, job_id):
            raise ledger_mod.LedgerUnavailable()

    deps = gw._ManualSavePlaylistDeps(service=UnavailableService(), producer=object())
    raw = _job_raw(JOB_ID)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 503
    assert body["code"] == "ledger_unavailable"


def test_query_corrupt_ledger_is_internal_without_detail(tmp_path):
    class CorruptService:
        def get_job(self, actor_scope, job_id):
            raise ledger_mod.CorruptLedgerError("secret-corrupt-detail")

    deps = gw._ManualSavePlaylistDeps(service=CorruptService(), producer=object())
    raw = _job_raw(JOB_ID)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_job(
        client, raw, engine=FakeEngine(), deps=deps)
    status, body = _response(client)
    assert status == 500
    assert body["code"] == "internal"
    assert "secret-corrupt-detail" not in client.data.decode("utf-8", "replace")


def test_advance_never_echoes_raw_executor_data(tmp_path):
    leaked = {
        "ok": True, "code": "ok", "message": "", "data": {
            "status": "published", "song_id": 1901371647,
            "signed_url": SIGNED_URL, "cookie": SECRET_COOKIE,
            "target_dir": "/vol1/music", "staging_dir": "/vol1/staging",
        },
    }
    save_engine = SaveEngine([leaked])
    _ledger, _service, deps, job = _created_job(tmp_path, save_engine=save_engine)
    client, status, body = _advance(tmp_path, deps, job_id=job.job_id)
    assert status == 200
    assert body["data"]["run"]["detail"] is None
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert SECRET_COOKIE not in rendered
    assert "/vol1/music" not in rendered
    assert "/vol1/staging" not in rendered


def test_gateway_remains_responsive_after_advance_timeout(tmp_path):
    _ledger, service, deps, job = _created_job(tmp_path)
    gate = threading.Event()
    stub = StubAdvanceService(service, "no_work", disposition=None, gate=gate)
    raw = _advance_raw(job.job_id)
    client = FakeClient(raw)
    gw.handle_manual_save_playlist_advance(
        client, raw, engine=FakeEngine(),
        deps=_stub_deps(stub, deps.producer), timeout=0.05)
    assert _response(client)[0] == 504
    gate.set()
    for _ in range(200):
        if not gw.MANUAL_SAVE_PLAYLIST_LOCK.locked():
            break
        threading.Event().wait(0.01)
    raw2 = _job_raw(job.job_id)
    client2 = FakeClient(raw2)
    gw.handle_manual_save_playlist_job(
        client2, raw2, engine=FakeEngine(), deps=deps)
    status2, body2 = _response(client2)
    assert status2 == 200
    assert body2["data"]["job"]["job_id"] == job.job_id


def test_reason_messages_match_route_messages_for_overlapping_codes():
    from proxy import manual_save_route as route

    overlap = set(gw._MANUAL_SAVE_PLAYLIST_REASON_MESSAGES) & set(route._MESSAGES)
    assert len(overlap) >= 10
    for code in overlap:
        assert gw._MANUAL_SAVE_PLAYLIST_REASON_MESSAGES[code] == \
            route._MESSAGES[code]


def test_resolve_deps_builds_a_loopback_producer(tmp_path, monkeypatch):
    monkeypatch.setattr(
        adapter, "load_service_config",
        lambda environ: adapter.PlaylistJobServiceConfig(
            db_path=str(tmp_path / "jobs.db"), enabled=True))
    monkeypatch.setattr(
        gw.manual_save_playlist_jobs, "PlaylistSnapshotClient",
        adapter.PlaylistSnapshotClient)
    monkeypatch.setattr(gw, "_manual_save_playlist_environ", lambda: {})
    monkeypatch.setattr(
        gw, "_manual_save_playlist_musicbox_url", lambda: "http://127.0.0.1:8770")
    deps = gw._manual_save_playlist_resolve_deps()
    assert deps.enabled is True
    assert isinstance(deps.producer, adapter.PlaylistSnapshotClient)
    assert deps.service is not None


def test_resolve_deps_rejects_non_loopback_musicbox_url(monkeypatch):
    monkeypatch.setattr(
        adapter, "load_service_config",
        lambda environ: adapter.PlaylistJobServiceConfig(
            db_path="/tmp/jobs.db", enabled=True))
    monkeypatch.setattr(gw, "_manual_save_playlist_environ", lambda: {})
    monkeypatch.setattr(
        gw, "_manual_save_playlist_musicbox_url", lambda: "http://10.0.0.5:8770")
    with pytest.raises(adapter.PlaylistJobError) as error:
        gw._manual_save_playlist_resolve_deps()
    assert error.value.code == "config_invalid"


