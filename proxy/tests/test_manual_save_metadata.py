"""Offline tests for FNMEXT-METADATA-DELIVERY-20261101.

Covers (a) the exact p1/p2 cover-host allowlist and the redacted ``cover_error``
classes, and (b) the bounded lyric/info retry budgets. No test touches the real
network, a real resolver, ffmpeg, or real sleeps: ``subprocess.run``/fetch seams
are injected and ``route.time.sleep`` is replaced with a no-op in every test.
"""
from __future__ import annotations

from dataclasses import replace
import json
import urllib.error

import pytest

from proxy import manual_save_route as route
from proxy import webui_gateway as gateway
from proxy.tests.test_manual_save_route import (
    FakeStore,
    SIGNED_URL,
    _info_payload,
    _jpeg_header,
    _pipeline_deps,
    _png_header,
    _url_payload,
)


@pytest.fixture(autouse=True)
def _no_real_retry_sleep(monkeypatch):
    monkeypatch.setattr(route.time, "sleep", lambda _seconds: None)


def _save(deps):
    return route.save_track(489970550, deps=deps)


# ------------------------------------------------------------- cover hosts --

def test_cover_host_allowlist_is_exactly_p1_and_p2():
    assert type(route.NETEASE_COVER_HOSTS) is frozenset
    assert route.NETEASE_COVER_HOSTS == frozenset({
        "p1.music.126.net", "p2.music.126.net",
    })


def test_cover_url_accepts_p2_and_upgrades_to_https():
    assert route._normalize_cover_url(
        "http://p2.music.126.net/a/b.jpg?token=1"
    ) == "https://p2.music.126.net/a/b.jpg?token=1"
    assert route._normalize_cover_url(
        "HTTPS://P2.Music.126.Net/a.jpg"
    ) == "https://p2.music.126.net/a.jpg"
    assert route._normalize_cover_url(
        "http://p1.music.126.net/a.jpg"
    ) == "https://p1.music.126.net/a.jpg"


@pytest.mark.parametrize(
    "host",
    [
        "p3.music.126.net",
        "p4.music.126.net",
        "pass.music.126.net",
        "m801.music.126.net",
        "music.126.net",
        "img.music.126.net",
        "p2.music.126.net.evil.test",
        "p1.music.126.net.",
        "p2.music.126.net.cn",
    ],
)
def test_cover_url_rejects_unobserved_music_126_hosts(host):
    with pytest.raises(ValueError) as caught:
        route._normalize_cover_url(f"https://{host}/cover.jpg")
    assert str(caught.value) == "cover_url_untrusted"
    assert route._cover_error_code(caught.value) == "host_untrusted"


@pytest.mark.parametrize(
    "url",
    [
        "ftp://p2.music.126.net/cover.jpg",
        "https://user:pw@p2.music.126.net/cover.jpg",
        "https://p2.music.126.net:8443/cover.jpg",
        "https://p2.music.126.net:80/cover.jpg",
        "https://p2.music.126.net/cover.jpg#fragment",
        "https://p2.music.126.net/cover.jpg" + chr(10),
        "https://p2.music.126.net" + chr(1) + "/cover.jpg",
        "javascript:alert(1)",
        "//p2.music.126.net/cover.jpg",
        "",
        None,
    ],
)
def test_cover_url_rejects_userinfo_port_fragment_scheme(url):
    with pytest.raises(ValueError) as caught:
        route._normalize_cover_url(url)
    assert str(caught.value) == "cover_url_invalid"
    assert route._cover_error_code(caught.value) == "url_invalid"


def test_cover_dns_non_public_is_address_untrusted(monkeypatch):
    monkeypatch.setattr(
        route.socket, "getaddrinfo",
        lambda *_args, **_kwargs: [(None, None, None, None, ("127.0.0.1", 443))],
    )
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch("https://p2.music.126.net/cover.jpg")
    assert str(caught.value) == "cover_dns_not_public"
    assert route._cover_error_code(caught.value) == "address_untrusted"


def test_cover_fetch_preserves_fixed_reason_after_all_addresses_fail(monkeypatch):
    class BrokenConnection:
        def __init__(self, host, address):
            self.host = host
            self.address = address

        def request(self, method, target, **_kwargs):
            raise OSError(f"dial failed: {self.address} {self.host} {target}")

        def close(self):
            return None

    monkeypatch.setattr(
        route, "_public_cover_addresses",
        lambda _host: ["93.184.216.34", "151.101.1.69"],
    )
    monkeypatch.setattr(route, "_PinnedCoverConnection", BrokenConnection)
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch("https://p2.music.126.net/cover.jpg?token=signed")
    reason = str(caught.value)
    assert reason in {
        "cover_fetch_http_error",
        "cover_fetch_payload_invalid",
        "cover_fetch_unknown",
    }
    for secret in (
        "p2.music.126.net", "93.184.216.34", "151.101.1.69",
        "https://", "cover.jpg", "token", "signed",
    ):
        assert secret not in reason
    assert route._cover_error_code(caught.value) == "http_error"
    assert type(caught.value).__name__ == "ValueError"


# ------------------------------------------------------- cover error classes --

def test_cover_error_classification_table():
    cases = [
        (ValueError("cover_url_untrusted"), "fetch", "host_untrusted"),
        (ValueError("cover_url_invalid"), "fetch", "url_invalid"),
        (ValueError("cover_dns_not_public"), "fetch", "address_untrusted"),
        (ValueError("cover_dns_invalid"), "fetch", "address_untrusted"),
        (ValueError("cover_dns_empty"), "fetch", "address_untrusted"),
        (ValueError("cover_dns_too_many_addresses"), "fetch", "address_untrusted"),
        (ValueError("cover_dns_resolve_failed"), "fetch", "address_untrusted"),
        (ValueError("cover_fetch_http_error"), "fetch", "http_error"),
        (OSError("tls reset"), "fetch", "http_error"),
        (TimeoutError("slow"), "fetch", "http_error"),
        (ValueError("cover_fetch_payload_invalid"), "fetch", "payload_invalid"),
        (ValueError("cover_mime_invalid"), "fetch", "payload_invalid"),
        (ValueError("cover_too_large"), "fetch", "payload_invalid"),
        (ValueError("cover_dimensions_invalid"), "fetch", "payload_invalid"),
        (ValueError("cover_dimensions_unsafe"), "fetch", "payload_invalid"),
        (ValueError("cover_fetch_unknown"), "fetch", "unknown"),
        (RuntimeError("boom"), "fetch", "unknown"),
        (ValueError("cover_decode_failed"), "verify", "decode_failed"),
        (OSError("decode failed"), "verify", "decode_failed"),
        (RuntimeError("local boom"), "verify", "unknown"),
    ]
    for exc, stage, expected in cases:
        assert route._cover_error_code(exc, stage=stage) == expected
        assert expected in route._COVER_ERRORS
    assert "embed_failed" in route._COVER_ERRORS


def test_cover_error_empty_when_not_error(tmp_path):
    # No picUrl at all -> unavailable, no reason.
    deps, _store, _workdir = _pipeline_deps(tmp_path)
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["cover_status"] == "unavailable"
    assert result["data"]["cover_error"] == ""

    # Source-attached cover -> preserved, no reason.
    def probe_with_cover(_path):
        return {
            "streams": [
                {"index": 0, "codec_type": "audio", "codec_name": "flac"},
                {"index": 1, "codec_type": "video",
                 "disposition": {"attached_pic": 1}},
            ],
            "format": {"format_name": "flac"},
        }

    preserved_dir = tmp_path / "preserved"
    preserved_dir.mkdir()
    deps, _store, _workdir = _pipeline_deps(
        preserved_dir, probe=probe_with_cover,
    )
    result = _save(deps)
    assert result["data"]["cover_status"] == "preserved"
    assert result["data"]["cover_error"] == ""


def test_p2_cover_pipeline_embeds_and_reports_empty_cover_error(tmp_path):
    info = _info_payload()
    info["data"]["al"]["picUrl"] = "http://p2.music.126.net/cover.jpg"
    seen = {}

    def cover_fetch(url):
        seen["url"] = url
        return _jpeg_header(), "image/jpeg"

    deps, _store, _workdir = _pipeline_deps(
        tmp_path, info=info, cover=cover_fetch,
    )
    result = _save(deps)
    assert result["ok"] is True
    data = result["data"]
    assert data["cover_status"] == "embedded"
    assert data["cover_error"] == ""
    assert data["cover_embedded"] is True
    assert seen["url"] == "https://p2.music.126.net/cover.jpg"
    assert "p2.music.126.net" not in str(result)


def test_unknown_cover_host_fails_closed_before_fetch(tmp_path):
    info = _info_payload()
    info["data"]["al"]["picUrl"] = "https://p3.music.126.net/cover.jpg"
    fetched = []
    deps, _store, _workdir = _pipeline_deps(
        tmp_path, info=info,
        cover=lambda url: fetched.append(url) or (_jpeg_header(), "image/jpeg"),
    )
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["cover_status"] == "error"
    assert result["data"]["cover_error"] == "host_untrusted"
    assert fetched == []
    assert "p3.music.126.net" not in str(result)


def test_unknown_cover_host_keeps_audio_published(tmp_path):
    info = _info_payload()
    info["data"]["al"]["picUrl"] = "https://pass.music.126.net/cover.jpg"
    deps, store, _workdir = _pipeline_deps(tmp_path, info=info)
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["status"] == "published"
    assert result["data"]["cover_status"] == "error"
    assert result["data"]["cover_error"] == "host_untrusted"
    assert result["data"]["cover_embedded"] is False
    assert store.published_names == ["歌手A_歌手B - 测试歌曲.flac"]
    assert store.closed is True


def test_success_data_contains_cover_error(tmp_path):
    deps, _store, _workdir = _pipeline_deps(tmp_path)
    result = _save(deps)
    assert "cover_error" in result["data"]
    assert result["data"]["cover_error"] == ""

    conflict_dir = tmp_path / "conflict"
    conflict_dir.mkdir()
    conflict_store = FakeStore(outcomes=["conflict"])
    deps, _store, _workdir = _pipeline_deps(conflict_dir, store=conflict_store)
    result = _save(deps)
    assert result["ok"] is False
    assert result["code"] == "conflict"
    assert "cover_error" in result["data"]
    assert result["data"]["cover_error"] == ""
    assert result["data"]["cover_status"] == "not_published"


# ------------------------------------------------------ bounded http/retry --

def test_default_http_json_accepts_timeout_override(monkeypatch):
    recorded = []

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _limit):
            return b'{"ok": true}'

    def fake_urlopen(_request, timeout=None):
        recorded.append(timeout)
        return FakeResponse()

    monkeypatch.setattr(route.urllib.request, "urlopen", fake_urlopen)
    assert route._default_http_json("http://127.0.0.1:8770/api") == {"ok": True}
    assert route._default_http_json("http://127.0.0.1:8770/api", timeout=3.0) == {"ok": True}
    assert route._default_lyric_http_json("http://127.0.0.1:8770/api") == {"ok": True}
    assert recorded == [route.API_TIMEOUT, 3.0, route.LYRIC_TIMEOUT]


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(TimeoutError("slow"), id="timeout"),
        pytest.param(ConnectionError("reset"), id="connection"),
        pytest.param(OSError("transport"), id="oserror"),
        pytest.param(
            urllib.error.HTTPError(
                "https://signed.example.com/x", 500, "server", {}, None),
            id="http-500",
        ),
        pytest.param(
            urllib.error.HTTPError(
                "https://signed.example.com/x", 408, "timeout", {}, None),
            id="http-408",
        ),
        pytest.param(
            urllib.error.HTTPError(
                "https://signed.example.com/x", 429, "rate", {}, None),
            id="http-429",
        ),
    ],
)
def test_retry_helper_retries_transient_then_succeeds(error):
    calls = []
    sleeps = []

    def operation():
        calls.append(1)
        if len(calls) == 1:
            raise error
        return "ok"

    result = route._retry_transient(
        operation,
        max_attempts=2,
        retry_delay=0.5,
        total_budget=31.0,
        sleep=sleeps.append,
    )
    assert result == "ok"
    assert len(calls) == 2
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 0.5


def test_retry_helper_stops_after_two_attempts():
    calls = []
    sleeps = []

    def operation():
        calls.append(1)
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        route._retry_transient(
            operation,
            max_attempts=2,
            retry_delay=1.0,
            total_budget=62.0,
            sleep=sleeps.append,
        )
    assert len(calls) == route.LYRIC_MAX_ATTEMPTS
    assert len(sleeps) == route.LYRIC_MAX_ATTEMPTS - 1


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(ValueError("json"), id="value"),
        pytest.param(UnicodeError("bad"), id="unicode"),
        pytest.param(route._SaveFailure("internal"), id="save-failure"),
        pytest.param(KeyError("missing"), id="key"),
        pytest.param(
            urllib.error.HTTPError(
                "https://signed.example.com/x", 404, "not found", {}, None),
            id="http-404",
        ),
        pytest.param(
            urllib.error.HTTPError(
                "https://signed.example.com/x", 403, "forbidden", {}, None),
            id="http-403",
        ),
    ],
)
def test_retry_helper_does_not_retry_non_transient(error):
    calls = []
    sleeps = []

    def operation():
        calls.append(1)
        raise error

    with pytest.raises(type(error)):
        route._retry_transient(
            operation,
            max_attempts=2,
            retry_delay=0.5,
            total_budget=31.0,
            sleep=sleeps.append,
        )
    assert len(calls) == 1
    assert sleeps == []


def test_retry_helper_respects_total_budget():
    clock = {"now": 1000.0}
    calls = []

    def operation():
        calls.append(1)
        clock["now"] += 100.0
        raise TimeoutError("slow")

    with pytest.raises(TimeoutError):
        route._retry_transient(
            operation,
            max_attempts=5,
            retry_delay=0.5,
            total_budget=50.0,
            sleep=lambda _seconds: None,
            monotonic=lambda: clock["now"],
        )
    assert len(calls) == 1


# -------------------------------------------------- lyric / info integration --

def test_lyric_retry_success_publishes_lrc(tmp_path):
    attempts = []

    def lyric_http(url_text):
        assert url_text.endswith("/lyric")
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("slow lyric")
        return {"ok": True, "data": {"lyric": "[00:00.00]line one"}}

    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, lyric_json=lyric_http)
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["lyric_status"] == "published"
    assert attempts == [1, 1]
    assert store.published_names[-1].endswith(".lrc")


def test_lyric_two_timeouts_is_error_not_unavailable(tmp_path):
    attempts = []

    def lyric_http(_url):
        attempts.append(1)
        raise TimeoutError("slow lyric")

    deps, _store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, lyric_json=lyric_http)
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["status"] == "published"
    assert result["data"]["lyric_status"] == "error"
    assert result["data"]["lyric_status"] != "unavailable"
    assert len(attempts) == route.LYRIC_MAX_ATTEMPTS


def test_lyric_absent_lyric_is_unavailable(tmp_path):
    deps, _store, _workdir = _pipeline_deps(
        tmp_path, lyric={"ok": True, "data": {"lyric": "", "tlyric": ""}},
    )
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["lyric_status"] == "unavailable"


# --------------------------------------------------- credit lines in lyrics --

@pytest.mark.parametrize(
    ("line", "expected"),
    [
        pytest.param("[00:00.000] 作词 : 周杰伦", ("周杰伦", ""), id="timed-space"),
        pytest.param("[00:00.000] 作词：周杰伦", ("周杰伦", ""), id="fullwidth-colon"),
        pytest.param("作词：周杰伦", ("周杰伦", ""), id="no-timestamp"),
        pytest.param("  [00:01.000]  作曲 : 林夕  ", ("", "林夕"), id="ows"),
        pytest.param("[00:01.00] lyricist: Alex", ("Alex", ""), id="english"),
        pytest.param("[00:01.00] Composer: Bob", ("", "Bob"), id="english-upper"),
    ],
)
def test_credit_line_parser_accepts_common_forms(line, expected):
    assert route._credits_from_lyric((line + "\n").encode("utf-8")) == expected


def test_credit_line_parser_reads_first_credit_per_role():
    lyric = (
        "[00:00.000] 作词 : 甲\n"
        "[00:01.000] 作曲 : 乙\n"
        "[00:02.000] 作词 : 丙\n"
        "[00:10.000] 歌词正文"
    ).encode("utf-8")
    assert route._credits_from_lyric(lyric) == ("甲", "乙")


def test_credit_line_parser_ignores_plain_lyrics():
    lyric = (
        "[00:00.000] 我们作词作曲都不容易\n"
        "[00:01.000] 词不达意\n"
        "[00:02.000] 曲终人散\n"
    ).encode("utf-8")
    assert route._credits_from_lyric(lyric) == ("", "")


def test_credit_line_parser_is_bounded_to_leading_lines():
    filler = "\n".join(f"[00:{index:02d}.000] 第{index}行歌词" for index in range(80))
    lyric = (filler + "\n[00:00.000] 作词 : 太晚\n").encode("utf-8")
    assert route._credits_from_lyric(lyric) == ("", "")


def test_credit_line_parser_truncates_long_values():
    value = "词" * 600
    lyricist, composer = route._credits_from_lyric(
        ("[00:00.000] 作词 : " + value + "\n").encode("utf-8"))
    assert lyricist == value[:route._CREDIT_MAX_CHARS]
    assert composer == ""


@pytest.mark.parametrize(
    "lyric",
    [None, b"", "作词 : 甲", 5, ["作词 : 甲".encode("utf-8")]],
)
def test_credit_line_parser_rejects_non_bytes_or_missing(lyric):
    assert route._credits_from_lyric(lyric) == ("", "")


def test_credit_line_parser_rejects_undecodable_bytes():
    assert route._credits_from_lyric(b"\xff\xfe\x00\x00") == ("", "")


def test_credit_line_parser_accepts_bytearray():
    assert route._credits_from_lyric(bytearray("作词：甲".encode("utf-8"))) == ("甲", "")


def test_info_credits_are_preferred_over_lyric_credit_lines(tmp_path):
    def info_http(_url):
        info = _info_payload()
        info["data"]["lyricist"] = "信息词"
        info["data"]["composer"] = ""
        return info

    deps, _store, _workdir = _pipeline_deps(
        tmp_path,
        info=info_http(None),
        lyric={"ok": True, "data": {
            "lyric": "[00:00.000] 作词 : 歌词词\n[00:01.000] 作曲 : 歌词曲",
        }},
    )
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["lyricist"] == "信息词"
    assert result["data"]["composer"] == "歌词曲"
    assert result["data"]["credits_source"] == "mixed"


def test_info_retry_success_completes_save(tmp_path):
    attempts = []

    def http_json(url_text):
        if url_text.endswith("/info"):
            attempts.append(1)
            if len(attempts) == 1:
                raise TimeoutError("slow info")
            return _info_payload()
        return _url_payload()

    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=http_json)
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["status"] == "published"
    assert len(attempts) == route.INFO_MAX_ATTEMPTS
    assert store.closed is True


def test_info_two_timeouts_is_service_unavailable(tmp_path):
    attempts = []

    def http_json(url_text):
        if url_text.endswith("/info"):
            attempts.append(1)
            raise TimeoutError("slow info")
        raise AssertionError("ladder must not run without info")

    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=http_json)
    result = _save(deps)
    assert result["ok"] is False
    assert result["code"] == "service_unavailable"
    assert len(attempts) == route.INFO_MAX_ATTEMPTS
    assert store.closed is False


def test_info_non_retryable_is_not_retried(tmp_path):
    attempts = []

    def http_json(url_text):
        if url_text.endswith("/info"):
            attempts.append(1)
            raise ValueError("payload")
        raise AssertionError("ladder must not run without info")

    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=http_json)
    result = _save(deps)
    assert result["code"] == "service_unavailable"
    assert len(attempts) == 1
    assert store.closed is False


def test_metadata_retry_constants_below_gateway_timeout():
    assert route.INFO_TIMEOUT == route.API_TIMEOUT == 15.0
    assert route.INFO_MAX_ATTEMPTS == 2
    assert route.INFO_RETRY_DELAY == 0.5
    assert route.INFO_TOTAL_BUDGET == 31.0
    assert route.LYRIC_TIMEOUT == 30.0
    assert route.LYRIC_MAX_ATTEMPTS == 2
    assert route.LYRIC_RETRY_DELAY == 1.0
    assert route.LYRIC_TOTAL_BUDGET == 62.0

    info_worst = (
        route.INFO_MAX_ATTEMPTS * route.INFO_TIMEOUT
        + (route.INFO_MAX_ATTEMPTS - 1) * route.INFO_RETRY_DELAY
    )
    lyric_worst = (
        route.LYRIC_MAX_ATTEMPTS * route.LYRIC_TIMEOUT
        + (route.LYRIC_MAX_ATTEMPTS - 1) * route.LYRIC_RETRY_DELAY
    )
    assert info_worst == 30.5
    assert lyric_worst == 61.0
    assert info_worst <= route.INFO_TOTAL_BUDGET
    assert lyric_worst <= route.LYRIC_TOTAL_BUDGET
    assert info_worst + lyric_worst < gateway.MANUAL_SAVE_TIMEOUT
    assert (
        route.DOWNLOAD_SUBPROCESS_TIMEOUT
        + info_worst
        + lyric_worst
        < gateway.MANUAL_SAVE_TIMEOUT
    )


# ------------------------------------------------- F2: embed_failed branch --

def test_tagged_cover_missing_attached_pic_is_embed_failed(tmp_path):
    """Expected cover but the tagged probe has no attached_pic -> error+reason."""

    def audio_only_tagged(_path, expected_format, _metadata, _expected_cover):
        return {
            "streams": [
                {"index": 0, "codec_type": "audio",
                 "codec_name": "flac" if expected_format == "flac" else "mp3"},
            ],
            "format": {"format_name": expected_format},
        }

    info = _info_payload()
    info["data"]["al"]["picUrl"] = "https://p2.music.126.net/cover.jpg"
    deps, store, _workdir = _pipeline_deps(
        tmp_path,
        info=info,
        cover=lambda _url: (_jpeg_header(), "image/jpeg"),
        lyric={"ok": True, "data": {"lyric": "[00:00.00]line"}},
        validate_tagged=audio_only_tagged,
    )
    result = _save(deps)
    assert result["ok"] is True
    data = result["data"]
    assert data["status"] == "published"
    assert data["cover_status"] == "error"
    assert data["cover_error"] == "embed_failed"
    assert data["cover_embedded"] is False
    assert data["published_size"] > 0
    assert data["lyric_status"] == "published"
    assert store.published_names[0].endswith(".flac")
    assert store.closed is True


# ------------------------------------- F3: fetch failure class after all fail --

def _two_address_cover_failures(monkeypatch, errors):
    class BrokenConnection:
        def __init__(self, _host, _address):
            pass

        def request(self, *_args, **_kwargs):
            raise errors.pop(0)

        def close(self):
            return None

    monkeypatch.setattr(
        route, "_public_cover_addresses",
        lambda _host: ["93.184.216.34", "151.101.1.69"],
    )
    monkeypatch.setattr(route, "_PinnedCoverConnection", BrokenConnection)


def test_cover_fetch_payload_invalid_reason_after_all_addresses_fail(monkeypatch):
    _two_address_cover_failures(
        monkeypatch, [OSError("reset"), ValueError("cover_mime_invalid")])
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch(
            "https://p2.music.126.net/cover.jpg?token=signed")
    reason = str(caught.value)
    assert reason == "cover_fetch_payload_invalid"
    assert route._cover_error_code(caught.value) == "payload_invalid"
    for secret in (
        "p2.music.126.net", "93.184.216.34", "151.101.1.69",
        "https://", "token", "signed",
    ):
        assert secret not in reason


def test_cover_fetch_unknown_reason_after_all_addresses_fail(monkeypatch):
    _two_address_cover_failures(monkeypatch, [OSError("reset"), RuntimeError("boom")])
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch("https://p2.music.126.net/cover.jpg")
    reason = str(caught.value)
    assert reason == "cover_fetch_unknown"
    assert route._cover_error_code(caught.value) == "unknown"
    for secret in (
        "p2.music.126.net", "93.184.216.34", "151.101.1.69", "https://", "cover.jpg",
    ):
        assert secret not in reason


# ------------------------------------ F4: info explicitly uses INFO_TIMEOUT --

def test_info_uses_explicit_info_timeout(monkeypatch):
    recorded = []

    class FakeResponse:
        status = 200

        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _limit):
            return self._body

    body = json.dumps(_info_payload()).encode("utf-8")

    def fake_urlopen(_request, timeout=None):
        recorded.append(timeout)
        return FakeResponse(body)

    monkeypatch.setattr(route.urllib.request, "urlopen", fake_urlopen)
    metadata = route._fetch_song_info(
        route._default_info_http_json, "http://127.0.0.1:8770", 489970550,
    )
    assert metadata.title == "测试歌曲"
    assert recorded == [route.INFO_TIMEOUT]
    assert route.INFO_TIMEOUT == 15.0


def test_save_default_http_uses_info_timeout_for_info_and_ladder(tmp_path, monkeypatch):
    recorded = []
    info_bytes = json.dumps(_info_payload()).encode("utf-8")
    url_bytes = json.dumps(_url_payload()).encode("utf-8")

    class FakeResponse:
        status = 200

        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def read(self, _limit):
            return self._body

    def fake_urlopen(request, timeout=None):
        recorded.append(timeout)
        body = info_bytes if request.full_url.endswith("/info") else url_bytes
        return FakeResponse(body)

    monkeypatch.setattr(route.urllib.request, "urlopen", fake_urlopen)
    deps, _store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=None)
    result = _save(deps)
    assert result["ok"] is True
    assert result["data"]["status"] == "published"
    assert recorded == [route.INFO_TIMEOUT, route.INFO_TIMEOUT]


# ===========================================================================
# Addendum A1: the source CDN declares JPEG as `image/jpg`
# ===========================================================================

class _FakeCoverResponse:
    def __init__(self, data, *, content_type=None, status=200,
                 content_length=None, content_encoding="identity"):
        self.status = status
        self._data = data
        self._content_type = content_type
        self._content_length = content_length
        self._content_encoding = content_encoding

    def getheader(self, name, default=""):
        lowered = name.casefold()
        if lowered == "content-type":
            return default if self._content_type is None else self._content_type
        if lowered == "content-length":
            return self._content_length
        if lowered == "content-encoding":
            return self._content_encoding
        return default

    def read(self, _limit):
        return self._data


def _install_fake_cover_connection(monkeypatch, response, requests=None):
    class FakeConnection:
        def __init__(self, host, address):
            self.host = host
            self.address = address

        def request(self, method, target, **_kwargs):
            if requests is not None:
                requests.append((method, target))

        def getresponse(self):
            return response

        def close(self):
            return None

    monkeypatch.setattr(
        route, "_public_cover_addresses", lambda _host: ["93.184.216.34"])
    monkeypatch.setattr(route, "_PinnedCoverConnection", FakeConnection)


def test_cover_declared_mime_alias_table_is_closed():
    assert route._COVER_DECLARED_MIME_ALIASES == {
        "image/jpeg": "image/jpeg",
        "image/jpg": "image/jpeg",
        "image/png": "image/png",
        "image/webp": "image/webp",
    }
    for value in (
        "image/pjpeg", "image/x-png", "image/*", "text/html",
        "application/octet-stream", "image/gif", "",
    ):
        assert route._normalize_cover_declared_mime(value) == ""


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("image/jpg", "image/jpeg", id="jpg"),
        pytest.param("IMAGE/JPG", "image/jpeg", id="jpg-upper"),
        pytest.param(" image/jpg ", "image/jpeg", id="jpg-ows"),
        pytest.param("\timage/jpeg\t", "image/jpeg", id="jpeg-tabs"),
        pytest.param(" image/png ", "image/png", id="png-ows"),
        pytest.param("IMAGE/WEBP", "image/webp", id="webp-upper"),
    ],
)
def test_cover_declared_mime_normalizes_case_and_ows(raw, expected):
    assert route._normalize_cover_declared_mime(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("image/jpg; charset=binary", "image/jpeg", id="jpg-value"),
        pytest.param("image/jpg;charset=binary", "image/jpeg", id="no-space"),
        pytest.param("image/jpg ; charset = binary", "image/jpeg", id="spaces"),
        pytest.param("image/jpg; CHARSET=UTF-8", "image/jpeg", id="name-case"),
        pytest.param("image/png;charset=x", "image/png", id="png-charset"),
        pytest.param(
            "image/webp; charset=0123456789._-", "image/webp", id="webp-token"),
    ],
)
def test_cover_declared_mime_accepts_single_charset_token(raw, expected):
    assert route._normalize_cover_declared_mime(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("image/jpg, image/png", id="comma-list"),
        pytest.param("image/jpg; foo=bar", id="other-param"),
        pytest.param('image/jpg; charset="binary"', id="quoted"),
        pytest.param("image/jpg;", id="trailing-semicolon"),
        pytest.param("image/jpg; charset=", id="empty-token"),
        pytest.param("image/jpg; charset=binary; x=y", id="two-params"),
        pytest.param("image/jpg; charset", id="no-equals"),
        pytest.param("image/jpg; charset=binary=x", id="token-with-equals"),
        pytest.param("image/jpg; charset=binary extra", id="token-with-space"),
        pytest.param("image/jpg; charset=binary/foo", id="token-with-slash"),
        pytest.param("image/jpg; charset=binary*", id="token-with-star"),
        pytest.param("image/jpg; ;", id="double-empty"),
        pytest.param("image/pjpeg", id="pjpeg"),
        pytest.param("image/x-png", id="x-png"),
        pytest.param("image/*", id="wildcard"),
        pytest.param("image/jpg/extra", id="extra-slash"),
        pytest.param("text/html", id="html"),
        pytest.param("", id="empty"),
        pytest.param(" ", id="blank"),
    ],
)
def test_cover_declared_mime_rejects_other_or_malformed_parameters(raw):
    assert route._normalize_cover_declared_mime(raw) == ""


@pytest.mark.parametrize("value", [None, 5, 1.5, b"image/jpg", ["image/jpg"]])
def test_cover_declared_mime_rejects_non_strings(value):
    assert route._normalize_cover_declared_mime(value) == ""


def test_default_cover_fetch_normalizes_image_jpg_to_jpeg(monkeypatch):
    data = _jpeg_header(80, 40)
    requests = []
    _install_fake_cover_connection(
        monkeypatch,
        _FakeCoverResponse(data, content_type="image/jpg"),
        requests=requests,
    )
    fetched = route._default_cover_fetch(
        "http://p1.music.126.net/cover.jpg")
    assert fetched == {"data": data, "mime": "image/jpeg"}
    assert requests == [("GET", "/cover.jpg")]


def test_default_cover_fetch_rejects_image_jpg_with_png_magic(monkeypatch):
    _install_fake_cover_connection(
        monkeypatch,
        _FakeCoverResponse(_png_header(80, 40), content_type="image/jpg"),
    )
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch("http://p1.music.126.net/cover.jpg")
    assert str(caught.value) == "cover_fetch_payload_invalid"
    assert route._cover_error_code(caught.value) == "payload_invalid"


@pytest.mark.parametrize(
    "content_type",
    [pytest.param("text/html", id="html"),
     pytest.param("", id="empty"),
     pytest.param(None, id="missing")],
)
def test_default_cover_fetch_rejects_non_image_or_missing_content_type(
    monkeypatch, content_type,
):
    _install_fake_cover_connection(
        monkeypatch,
        _FakeCoverResponse(_jpeg_header(80, 40), content_type=content_type),
    )
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch("https://p1.music.126.net/cover.jpg")
    assert str(caught.value) == "cover_fetch_payload_invalid"
    assert route._cover_error_code(caught.value) == "payload_invalid"


def test_default_cover_fetch_mime_invalid_is_payload_invalid(monkeypatch):
    _install_fake_cover_connection(
        monkeypatch,
        _FakeCoverResponse(_jpeg_header(80, 40), content_type="image/*"),
    )
    with pytest.raises(ValueError) as caught:
        route._default_cover_fetch("https://p1.music.126.net/cover.jpg")
    assert str(caught.value) == "cover_fetch_payload_invalid"
    assert route._cover_error_code(caught.value) == "payload_invalid"
    assert route._cover_error_code(
        ValueError("cover_mime_invalid")) == "payload_invalid"


def test_fetch_cover_asset_injected_mime_must_be_canonical():
    data = _jpeg_header(80, 40)
    url = "https://p1.music.126.net/cover.jpg"

    with pytest.raises(ValueError, match="cover_mime_invalid"):
        route._fetch_cover_asset(
            url, route.SaveDeps(cover_fetch=lambda _u: (data, "image/jpg")))

    canonical = route._fetch_cover_asset(
        url, route.SaveDeps(cover_fetch=lambda _u: (data, "image/jpeg")))
    assert canonical.mime == "image/jpeg"
    padded = route._fetch_cover_asset(
        url, route.SaveDeps(cover_fetch=lambda _u: {"data": data, "mime": " image/jpeg "}))
    assert padded.mime == "image/jpeg"


def test_image_jpg_alias_pipeline_embeds_cover_with_jpg_extension(
    tmp_path, monkeypatch,
):
    data = _jpeg_header(80, 40)
    response = _FakeCoverResponse(data, content_type="image/jpg")
    _install_fake_cover_connection(monkeypatch, response)
    info = _info_payload()
    info["data"]["al"]["picUrl"] = "http://p1.music.126.net/cover.jpg"
    seen = {}

    def tag(source, tagged, metadata):
        seen["cover_path"] = metadata.cover_path
        tagged.write_bytes(b"TAGGED:" + source.read_bytes())

    deps, store, _workdir = _pipeline_deps(tmp_path, info=info, tag=tag)
    result = _save(deps)
    assert result["ok"] is True
    save_data = result["data"]
    assert save_data["status"] == "published"
    assert save_data["cover_status"] == "embedded"
    assert save_data["cover_embedded"] is True
    assert save_data["cover_error"] == ""
    assert seen["cover_path"] is not None
    assert seen["cover_path"].name.endswith(".jpg")
    assert store.published_names[0].endswith(".flac")
