"""Synthetic tests for the manual-save gateway helpers; no real sockets or network."""
from __future__ import annotations

import json
import threading
from urllib.parse import quote

import pytest

from proxy import webui_gateway as gw


CRLF = chr(13) + chr(10)
HEAD_END = (CRLF + CRLF).encode("latin-1")
SONG_ID = 489970550
SIGNED_URL = "https://signed.example.com/secret?token=TOPSECRETTAIL"


def _enabled_status():
    return {
        "ok": True,
        "code": "ok",
        "message": "",
        "data": {
            "enabled": True,
            "target_dir": "/vol1/music",
            "staging_dir": "/vol1/staging",
            "quality_order": ["jymaster", "hires"],
            "max_bytes": 1234,
            "signed_url": SIGNED_URL,
            "cookie": "SECRETCOOKIE",
        },
    }


def _disabled_status():
    return {"ok": False, "code": "disabled", "message": "手动保存未启用", "data": {}}


def _published_result():
    return {
        "ok": True,
        "code": "ok",
        "message": "",
        "data": {
            "status": "published",
            "song_id": SONG_ID,
            "title": "测试歌曲",
            "artist": "歌手A",
            "album": "专辑",
            "quality_requested": "jymaster",
            "quality_actual": "jymaster",
            "quality_actual_status": "reported",
            "quality_fallback": False,
            "source_type": "flac",
            "format": "flac",
            "size": 30,
            "md5": "a" * 32,
            "basename": "歌手A - 测试歌曲.flac",
            "target_dir": "/vol1/music",
            "cover_error": "",
            "tagged": True,
            "cover_embedded": False,
            "title_status": "tagged",
            "artist_status": "tagged",
            "album_status": "tagged",
            "lyricist": "",
            "lyricist_status": "unavailable",
            "composer": "",
            "composer_status": "unavailable",
            "cover_status": "unavailable",
            "lyric_status": "unavailable",
            "published_size": 34,
            "published_md5": "b" * 32,
            "published_sha256": "c" * 64,
            "signed_url": SIGNED_URL,
            "cookie": "SECRETCOOKIE",
        },
    }


class FakeEngine:
    def __init__(self, *, status_result=None, save_result=None):
        self.status_result = _enabled_status() if status_result is None else status_result
        self.save_result = _published_result() if save_result is None else save_result
        self.status_calls = 0
        self.save_calls = []

    def status(self):
        self.status_calls += 1
        return self.status_result

    def save_track(self, song_id):
        self.save_calls.append(song_id)
        return self.save_result


class BlockingEngine(FakeEngine):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.release = threading.Event()

    def save_track(self, song_id):
        self.save_calls.append(song_id)
        self.release.wait(5)
        return self._result()

    def _result(self):
        return _published_result()


class RaisingEngine(FakeEngine):
    def save_track(self, song_id):
        self.save_calls.append(song_id)
        raise RuntimeError("secret-worker-detail")


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
    status_line = head.split(CRLF.encode("latin-1"))[0].decode("latin-1")
    return int(status_line.split(" ")[1]), json.loads(body.decode("utf-8"))


def _assert_shape(payload):
    assert set(payload) == {"ok", "code", "message", "data"}
    assert type(payload["ok"]) is bool
    assert type(payload["code"]) is str and payload["code"]
    assert type(payload["message"]) is str
    assert isinstance(payload["data"], dict)


def _track_raw(song_id=SONG_ID, **kwargs):
    return _raw(
        "POST", gw.MANUAL_SAVE_TRACK_PATHS[0],
        body=json.dumps({"song_id": song_id}).encode("utf-8"), **kwargs,
    )


def test_path_matching_covers_both_prefixes_and_methods():
    for target in gw.MANUAL_SAVE_STATUS_PATHS:
        assert gw._is_manual_save_status_request(_raw("GET", target)) is True
        assert gw._is_manual_save_track_request(_raw("GET", target)) is False
    for target in gw.MANUAL_SAVE_TRACK_PATHS:
        assert gw._is_manual_save_track_request(_raw("POST", target)) is True
        assert gw._is_manual_save_status_request(_raw("POST", target)) is False
    assert gw._is_manual_save_status_request(
        _raw("GET", "/app/fnmusic-ext/api/host-file")) is False
    assert gw._is_manual_save_track_request(
        _raw("POST", "/api/manual-save/unknown")) is False
    assert gw._is_manual_save_status_request(
        _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0] + "?x=1")) is False
    assert gw._is_manual_save_status_request(b"garbage") is False


@pytest.mark.parametrize(("admin", "csrf"), [(False, True), (True, False)])
def test_status_requires_admin_claim_and_csrf_header(admin, csrf):
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], admin=admin, csrf=csrf)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_status(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert payload["data"] == {}
    assert engine.status_calls == 0
    assert client.closed is True


@pytest.mark.parametrize("csrf_value", ["0", "true", "yes", ""])
def test_status_rejects_wrong_csrf_header_value(csrf_value):
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], csrf_value=csrf_value)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_status(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert engine.status_calls == 0


def test_status_returns_only_whitelisted_enabled_data():
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[1])
    client = FakeClient(raw)
    gw.handle_manual_save_status(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 200
    assert payload["ok"] is True
    assert payload["code"] == "ok"
    _assert_shape(payload)
    assert set(payload["data"]) == {
        "enabled", "target_dir", "staging_dir", "quality_order", "max_bytes",
    }
    assert payload["data"]["target_dir"] == "/vol1/music"
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert "SECRETCOOKIE" not in rendered


def test_status_disabled_engine_returns_fixed_403():
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0])
    client = FakeClient(raw)
    gw.handle_manual_save_status(
        client, raw, engine=FakeEngine(status_result=_disabled_status()))
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "disabled"
    assert payload["data"] == {"enabled": False}


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param(_raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], body=b"{}"),
                     id="get_with_body"),
        pytest.param(_raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0],
                          extra_headers=(("x-fnmusic-save", "1"),)),
                     id="duplicate_csrf_header"),
        pytest.param(_raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0],
                          extra_headers=(("Transfer-Encoding", "chunked"),)),
                     id="transfer_encoding"),
        pytest.param(_raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], version="HTTP/2"),
                     id="bad_version"),
        pytest.param(_raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0],
                          extra_headers=(("X-Big", "x" * (17 * 1024)),)),
                     id="oversized_header"),
    ],
)
def test_status_rejects_non_strict_requests(raw):
    client = FakeClient(raw)
    gw.handle_manual_save_status(client, raw, engine=FakeEngine())
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"
    assert client.closed is True


def test_status_engine_failure_is_fixed_500_without_exception_text():
    class BrokenEngine:
        def status(self):
            raise RuntimeError("secret-status-detail")

    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0])
    client = FakeClient(raw)
    gw.handle_manual_save_status(client, raw, engine=BrokenEngine())
    status, payload = _response(client)
    assert status == 500
    assert payload["code"] == "internal"
    assert "secret-status-detail" not in client.data.decode("utf-8", "replace")


@pytest.mark.parametrize(
    ("body", "expected_code"),
    [
        (b"", "invalid_request"),
        (b"not-json", "invalid_request"),
        (b"[]", "invalid_request"),
        (b'{"song_id":0}', "invalid_song_id"),
        (b'{"song_id":-3}', "invalid_song_id"),
        (b'{"song_id":"1"}', "invalid_song_id"),
        (b'{"song_id":true}', "invalid_song_id"),
        (b'{"song_id":1.5}', "invalid_song_id"),
        (b"{}", "invalid_song_id"),
    ],
)
def test_track_rejects_invalid_body_shapes(body, expected_code):
    raw = _raw("POST", gw.MANUAL_SAVE_TRACK_PATHS[0], body=body)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == expected_code
    assert payload["data"] == {}
    assert engine.save_calls == []


def test_track_rejects_missing_content_length_and_oversized_body():
    raw = _raw("POST", gw.MANUAL_SAVE_TRACK_PATHS[0],
               body=json.dumps({"song_id": SONG_ID}).encode("utf-8"),
               omit_content_length=True)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"

    raw = _raw("POST", gw.MANUAL_SAVE_TRACK_PATHS[0],
               content_length=gw.MANUAL_SAVE_MAX_BODY + 1, body=b"")
    client = FakeClient(raw)
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"
    assert engine.save_calls == []


def test_track_success_returns_sanitized_engine_result():
    raw = _track_raw()
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 200
    _assert_shape(payload)
    assert payload["ok"] is True
    assert payload["code"] == "ok"
    expected_fields = {
        "status", "song_id", "title", "artist", "album", "quality_requested",
        "quality_actual", "quality_actual_status", "quality_fallback",
        "source_type", "format", "size", "md5", "basename",
        "target_dir", "tagged", "cover_embedded", "published_size",
        "published_md5", "published_sha256", "title_status", "artist_status", "album_status",
        "lyricist", "lyricist_status", "composer", "composer_status",
        "cover_status", "cover_error", "lyric_status",
        "ownership", "lyric_ownership", "credits_source",
    }
    assert set(payload["data"]) == expected_fields
    assert payload["data"]["status"] == "published"
    assert payload["data"]["song_id"] == SONG_ID
    assert payload["data"]["quality_requested"] == "jymaster"
    assert payload["data"]["quality_actual"] == "jymaster"
    assert payload["data"]["quality_actual_status"] == "reported"
    assert payload["data"]["quality_fallback"] is False
    assert payload["data"]["cover_error"] == ""
    assert payload["data"]["basename"] == "歌手A - 测试歌曲.flac"
    assert payload["data"]["published_size"] == 34
    assert payload["data"]["published_md5"] == "b" * 32
    assert payload["data"]["published_sha256"] == "c" * 64
    assert engine.save_calls == [SONG_ID]
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert "SECRETCOOKIE" not in rendered
    assert client.closed is True


def test_published_file_sha256_is_whitelisted_only_for_valid_success():
    published = gw._sanitize_manual_save_data({
        "status": "published", "published_size": 12,
        "published_sha256": "A" * 64, "secret": "must be dropped",
    })
    assert published["published_size"] == 12
    assert published["published_sha256"] == "a" * 64
    assert "secret" not in published

    malformed = gw._sanitize_manual_save_data({
        "status": "published", "published_size": True,
        "published_sha256": "z" * 64,
    })
    assert malformed["published_size"] == 0
    assert malformed["published_sha256"] == ""

    conflict = gw._sanitize_manual_save_data({
        "status": "conflict", "published_size": 12,
        "published_sha256": "a" * 64,
    })
    assert conflict["published_size"] == 0
    assert conflict["published_sha256"] == ""


def test_track_accepts_extra_json_fields():
    body = json.dumps({"song_id": SONG_ID, "note": "ignored"}).encode("utf-8")
    raw = _raw("POST", gw.MANUAL_SAVE_TRACK_PATHS[1], body=body)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 200
    assert payload["ok"] is True
    assert engine.save_calls == [SONG_ID]


@pytest.mark.parametrize(
    ("engine_code", "expected_status"),
    [("busy", 409), ("conflict", 409), ("service_unavailable", 503),
     ("publish_failed", 500), ("invalid_audio", 502),
     ("quality_unverified", 502), ("quality_identity_mismatch", 502),
     ("trial_only", 502)],
)
def test_track_maps_engine_codes_to_fixed_http_status(engine_code, expected_status):
    save_result = {
        "ok": False, "code": engine_code, "message": "固定错误", "data": {},
    }
    raw = _track_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_track(
        client, raw, engine=FakeEngine(save_result=save_result))
    status, payload = _response(client)
    assert status == expected_status
    _assert_shape(payload)
    assert payload["ok"] is False
    assert payload["code"] == engine_code


def test_new_quality_failure_codes_use_fixed_messages():
    assert gw._manual_save_message("quality_unverified") == "音质响应无法核验"
    assert gw._manual_save_message("quality_identity_mismatch") == "无法确认音质响应所属曲目"
    assert gw._manual_save_message("trial_only") == "仅取得试听片段，未保存"
    for code in ("quality_unverified", "quality_identity_mismatch", "trial_only"):
        message = gw._manual_save_message(code)
        assert message == gw._MANUAL_SAVE_GATEWAY_MESSAGES[code]
        assert "http" not in message and "/" not in message and "token" not in message


@pytest.mark.parametrize(
    ("raw_status", "raw_actual", "raw_fallback",
     "expected_status", "expected_actual", "expected_fallback"),
    [
        pytest.param("bogus", "jymaster", True, "unknown", "", True, id="illegal-status"),
        pytest.param(None, "jymaster", "yes", "unknown", "", False, id="missing-status"),
        pytest.param("reported", "", True, "unknown", "", True, id="reported-empty-actual"),
        pytest.param("reported", "Not A Token", True, "unknown", "", True,
                     id="reported-illegal-token"),
        pytest.param("reported", "lossless", True, "reported", "lossless", True,
                     id="reported-legal-token"),
        pytest.param("unknown", "lossless", False, "unknown", "", False,
                     id="unknown-drops-actual"),
    ],
)
def test_sanitize_quality_status_consistency(
    raw_status, raw_actual, raw_fallback,
    expected_status, expected_actual, expected_fallback,
):
    sanitized = gw._sanitize_manual_save_data({
        "quality_requested": "jymaster",
        "quality_actual": raw_actual,
        "quality_actual_status": raw_status,
        "quality_fallback": raw_fallback,
    })
    assert sanitized["quality_actual_status"] == expected_status
    assert sanitized["quality_actual"] == expected_actual
    assert sanitized["quality_fallback"] is expected_fallback


@pytest.mark.parametrize(
    ("raw_status", "raw_error", "expected"),
    [
        pytest.param("error", "host_untrusted", "host_untrusted", id="error-valid"),
        pytest.param("error", "payload_invalid", "payload_invalid", id="error-payload"),
        pytest.param("error", None, "unknown", id="error-missing"),
        pytest.param("error", "", "unknown", id="error-empty"),
        pytest.param("error", "bogus", "unknown", id="error-illegal"),
        pytest.param("error", 5, "unknown", id="error-non-str"),
        pytest.param("published", "http_error", "", id="published-drops-error"),
        pytest.param("unavailable", None, "", id="unavailable-empty"),
        pytest.param("not_published", "embed_failed", "", id="not-published-empty"),
        pytest.param("bogus", "host_untrusted", "", id="illegal-status-empty"),
    ],
)
def test_sanitize_cover_error_consistency(raw_status, raw_error, expected):
    data = {"cover_status": raw_status}
    if raw_error is not None:
        data["cover_error"] = raw_error
    sanitized = gw._sanitize_manual_save_data(data)
    assert sanitized["cover_error"] == expected
    if sanitized["cover_status"] != "error":
        assert sanitized["cover_error"] == ""


def test_track_disabled_engine_short_circuits_before_save():
    raw = _track_raw()
    client = FakeClient(raw)
    engine = FakeEngine(status_result=_disabled_status())
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "disabled"
    assert engine.save_calls == []


def test_track_reads_body_from_later_socket_chunks():
    body = json.dumps({"song_id": 123}).encode("utf-8")
    head = _raw("POST", gw.MANUAL_SAVE_TRACK_PATHS[0], body=b"", content_length=len(body))
    client = FakeClient(extra_chunks=(body[:6], body[6:]))
    engine = FakeEngine()
    gw.handle_manual_save_track(client, head, engine=engine)
    status, payload = _response(client)
    assert status == 200
    assert engine.save_calls == [123]


def test_track_timeout_returns_504_and_gateway_stays_responsive():
    raw = _track_raw()
    client = FakeClient(raw)
    engine = BlockingEngine()
    gw.handle_manual_save_track(client, raw, engine=engine, timeout=0.05)
    status, payload = _response(client)
    assert status == 504
    assert payload["code"] == "timeout"
    assert payload["data"] == {}
    assert engine.save_calls == [SONG_ID]
    engine.release.set()


def test_manual_save_timeout_does_not_preempt_engine_budget():
    # The engine's own worst-case budget (download 580 s + probe/decode/tag) can
    # exceed 600 s; the gateway must only 504 after that budget would be spent.
    assert gw.MANUAL_SAVE_TIMEOUT == 1200
    assert gw.MANUAL_SAVE_TIMEOUT > gw.manual_save_route.DOWNLOAD_SUBPROCESS_TIMEOUT + 300


def test_track_worker_exception_is_fixed_internal_error():
    raw = _track_raw()
    client = FakeClient(raw)
    gw.handle_manual_save_track(client, raw, engine=RaisingEngine())
    status, payload = _response(client)
    assert status == 500
    assert payload["code"] == "internal"
    assert "secret-worker-detail" not in client.data.decode("utf-8", "replace")


def test_handle_dispatches_manual_save_without_touching_upstream(monkeypatch):
    engine = FakeEngine()
    monkeypatch.setattr(gw, "manual_save_route", engine)
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0])
    client = FakeClient(raw)
    gw._handle(client, ("127.0.0.1", 1))
    status, payload = _response(client)
    assert status == 200
    assert payload["ok"] is True
    assert engine.status_calls == 1
    assert client.closed is True


# --------------------------------------------------------------- search ---


def _search_result(items=None, *, keyword="周杰伦 晴天", limit=20, **overrides):
    result = {
        "ok": True,
        "code": "ok",
        "message": "",
        "data": {"items": [] if items is None else items, "keyword": keyword, "limit": limit},
    }
    result.update(overrides)
    return result


SEARCH_RAW_ITEMS = [
    {
        "song_id": 111,
        "title": "歌曲一",
        "artist": "歌手A / 歌手B",
        "album": "专辑一",
        "quality": "lossless",
        "duration": 215000,
        "url": SIGNED_URL,
        "album_pic_url": SIGNED_URL,
        "unknown": "secret-unknown",
    },
    {"song_id": 0, "title": "无效"},
    {"title": "没有 id"},
]


class FakeSearchEngine:
    def __init__(self, *, status_result=None, search_result=None):
        self.status_result = _enabled_status() if status_result is None else status_result
        self.search_result = (
            _search_result(SEARCH_RAW_ITEMS) if search_result is None else search_result
        )
        self.status_calls = 0
        self.search_calls = []

    def status(self):
        self.status_calls += 1
        return self.status_result

    def search_tracks(self, keyword):
        self.search_calls.append(keyword)
        return self.search_result


class BlockingSearchEngine(FakeSearchEngine):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def search_tracks(self, keyword):
        self.search_calls.append(keyword)
        self.release.wait(5)
        return self.search_result


class RaisingSearchEngine(FakeSearchEngine):
    def search_tracks(self, keyword):
        self.search_calls.append(keyword)
        raise RuntimeError("secret-search-detail")


def _search_raw(query: str | None = None, *, target=None, **kwargs):
    path = gw.MANUAL_SAVE_SEARCH_PATHS[0] if target is None else target
    if query is not None:
        path = path + "?" + query
    return _raw("GET", path, **kwargs)


def test_search_path_matching_covers_both_prefixes_and_methods():
    for target in gw.MANUAL_SAVE_SEARCH_PATHS:
        assert gw._is_manual_save_search_request(_raw("GET", target)) is True
        assert gw._is_manual_save_search_request(_raw("GET", target + "?keyword=abc")) is True
        assert gw._is_manual_save_search_request(_raw("POST", target + "?keyword=abc")) is False
        assert gw._is_manual_save_search_request(_raw("GET", target + "x")) is False
    assert gw._is_manual_save_search_request(
        _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0])) is False
    assert gw._is_manual_save_search_request(
        _raw("GET", "/api/manual-save/search/extra")) is False


@pytest.mark.parametrize(("admin", "csrf"), [(False, True), (True, False)])
def test_search_requires_admin_claim_and_csrf_header(admin, csrf):
    raw = _search_raw("keyword=abc", admin=admin, csrf=csrf)
    client = FakeClient(raw)
    engine = FakeSearchEngine()
    gw.handle_manual_save_search(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert engine.search_calls == []


@pytest.mark.parametrize(
    ("query", "expected_code"),
    [
        (None, "invalid_keyword"),
        ("", "invalid_keyword"),
        ("keyword=", "invalid_keyword"),
        ("keyword=%20%20", "invalid_keyword"),
        ("keyword=" + quote("长" * 121, safe=""), "invalid_keyword"),
        ("keyword=" + ("x" * (gw.MANUAL_SAVE_MAX_QUERY + 1)), "invalid_request"),
        ("keyword=a&keyword=b", "invalid_request"),
        ("keyword=a&foo=1", "invalid_request"),
        ("keyword", "invalid_request"),
        ("keyword=a&&foo=1", "invalid_request"),
        ("keyword=%E4%B8", "invalid_request"),
    ],
)
def test_search_rejects_bad_query_before_engine(query, expected_code):
    raw = _search_raw(query)
    client = FakeClient(raw)
    engine = FakeSearchEngine()
    gw.handle_manual_save_search(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == expected_code
    assert engine.search_calls == []


def test_search_percent_decodes_keyword_and_sanitizes_output():
    keyword = "周杰伦 晴天"
    raw = _search_raw("keyword=" + quote(keyword, safe=""))
    client = FakeClient(raw)
    engine = FakeSearchEngine()
    gw.handle_manual_save_search(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 200
    _assert_shape(payload)
    assert payload["ok"] is True
    assert payload["code"] == "ok"
    assert engine.search_calls == [keyword]
    assert set(payload["data"]) == {"items", "keyword", "limit"}
    assert len(payload["data"]["items"]) == 1
    item = payload["data"]["items"][0]
    assert set(item) == {"song_id", "title", "artist", "album", "quality", "duration"}
    assert item["song_id"] == 111
    assert item["title"] == "歌曲一"
    rendered = client.data.decode("utf-8", "replace")
    assert SIGNED_URL not in rendered
    assert "secret-unknown" not in rendered


@pytest.mark.parametrize(
    ("engine_code", "expected_status"),
    [("service_unavailable", 503), ("invalid_keyword", 400), ("internal", 500)],
)
def test_search_maps_engine_error_codes(engine_code, expected_status):
    search_result = {"ok": False, "code": engine_code, "message": "固定错误", "data": {}}
    raw = _search_raw("keyword=abc")
    client = FakeClient(raw)
    engine = FakeSearchEngine(search_result=search_result)
    gw.handle_manual_save_search(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == expected_status
    _assert_shape(payload)
    assert payload["ok"] is False
    assert payload["code"] == engine_code
    assert payload["data"] == {}


def test_search_disabled_engine_short_circuits_before_engine_call():
    raw = _search_raw("keyword=abc")
    client = FakeClient(raw)
    engine = FakeSearchEngine(status_result=_disabled_status())
    gw.handle_manual_save_search(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "disabled"
    assert engine.search_calls == []


def test_search_timeout_returns_504_and_gateway_stays_responsive():
    raw = _search_raw("keyword=abc")
    client = FakeClient(raw)
    engine = BlockingSearchEngine()
    gw.handle_manual_save_search(client, raw, engine=engine, timeout=0.05)
    status, payload = _response(client)
    assert status == 504
    assert payload["code"] == "timeout"
    assert engine.search_calls == ["abc"]
    engine.release.set()


def test_search_worker_exception_is_fixed_internal_error():
    raw = _search_raw("keyword=abc")
    client = FakeClient(raw)
    gw.handle_manual_save_search(client, raw, engine=RaisingSearchEngine())
    status, payload = _response(client)
    assert status == 500
    assert payload["code"] == "internal"
    assert "secret-search-detail" not in client.data.decode("utf-8", "replace")


def test_handle_dispatches_manual_save_search_without_touching_upstream(monkeypatch):
    engine = FakeSearchEngine()
    monkeypatch.setattr(gw, "manual_save_route", engine)
    raw = _search_raw("keyword=abc")
    client = FakeClient(raw)
    gw._handle(client, ("127.0.0.1", 1))
    status, payload = _response(client)
    assert status == 200
    assert payload["ok"] is True
    assert engine.search_calls == ["abc"]
    assert client.closed is True


# --------------------------------------------------- track authorization ---


@pytest.mark.parametrize("target", gw.MANUAL_SAVE_TRACK_PATHS)
@pytest.mark.parametrize(
    ("admin", "csrf", "csrf_value"),
    [
        (False, True, "1"),
        (True, False, "1"),
        (True, True, "0"),
        (True, True, "true"),
    ],
    ids=["missing_admin", "missing_csrf", "wrong_csrf_value_0", "wrong_csrf_value_true"],
)
def test_track_requires_admin_and_csrf_before_engine(target, admin, csrf, csrf_value):
    body = json.dumps({"song_id": SONG_ID}).encode("utf-8")
    raw = _raw(
        "POST", target, admin=admin, csrf=csrf, csrf_value=csrf_value, body=body,
    )
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert payload["data"] == {}
    assert engine.save_calls == []
    assert client.closed is True


# ------------------------------------------------ L4 user-id claim (G0) ---


def test_l4_user_id_requirement_is_an_explicit_constant():
    assert gw.MANUAL_SAVE_REQUIRE_USER_ID is True


def test_status_requires_user_id_claim_before_engine():
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], userid=None)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_status(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert payload["data"] == {}
    assert engine.status_calls == 0
    assert client.closed is True


def test_track_requires_user_id_claim_before_engine():
    raw = _track_raw(userid=None)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert engine.save_calls == []
    assert client.closed is True


def test_search_requires_user_id_claim_before_engine():
    raw = _search_raw("keyword=abc", userid=None)
    client = FakeClient(raw)
    engine = FakeSearchEngine()
    gw.handle_manual_save_search(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert engine.search_calls == []


def test_missing_user_id_passes_only_when_the_switch_is_off(monkeypatch):
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], userid=None)
    client = FakeClient(raw)
    gw.handle_manual_save_status(client, raw, engine=FakeEngine())
    assert _response(client)[0] == 403

    monkeypatch.setattr(gw, "MANUAL_SAVE_REQUIRE_USER_ID", False)
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0], userid=None)
    client = FakeClient(raw)
    gw.handle_manual_save_status(client, raw, engine=FakeEngine())
    assert _response(client)[0] == 200


@pytest.mark.parametrize("target", gw.MANUAL_SAVE_TRACK_PATHS)
def test_duplicate_user_id_header_is_rejected_before_engine(target):
    body = json.dumps({"song_id": SONG_ID}).encode("utf-8")
    raw = _raw(
        "POST", target, body=body,
        extra_headers=(("X-Trim-Userid", "1001"),),
    )
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_track(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 400
    assert payload["code"] == "invalid_request"
    assert engine.save_calls == []
    assert client.closed is True


def test_admin_claim_name_is_case_insensitive_and_ows_is_trimmed():
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0]).replace(
        b"X-Trim-Isadmin: true", b"X-TRIM-ISADMIN:  true ")
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_status(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 200
    assert payload["ok"] is True
    assert engine.status_calls == 1


@pytest.mark.parametrize("value", [b"True", b"1"])
def test_admin_claim_still_requires_the_canonical_lower_case_value(value):
    raw = _raw("GET", gw.MANUAL_SAVE_STATUS_PATHS[0]).replace(
        b"X-Trim-Isadmin: true", b"X-Trim-Isadmin: " + value)
    client = FakeClient(raw)
    engine = FakeEngine()
    gw.handle_manual_save_status(client, raw, engine=engine)
    status, payload = _response(client)
    assert status == 403
    assert payload["code"] == "forbidden"
    assert engine.status_calls == 0


def test_sanitize_manual_save_data_allows_not_attempted_cover_and_lyric():
    sample = _published_result()["data"]
    sample["cover_status"] = "not_attempted"
    sample["lyric_status"] = "not_attempted"
    sanitized = gw._sanitize_manual_save_data(sample)
    assert sanitized["cover_status"] == "not_attempted"
    assert sanitized["lyric_status"] == "not_attempted"
