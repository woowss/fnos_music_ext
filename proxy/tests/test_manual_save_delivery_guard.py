"""Offline tests for FNMEXT-DELIVERY-GUARD.

Part 1 covers quality verification semantics: every case drives the real engine
through the ``SaveDeps`` seams (``http_json``, ``download``, ...), so no network,
ffmpeg, or real publish is involved. The ladder may only downgrade on
*definitive* evidence: a 404 without a URL, or an explicit non-empty
``freeTrialInfo`` trial marker. A reported ``level`` is never a failure, and it
is never translated into a marketing name.

Part 2 covers download-target hardening: URL normalization, DNS/address policy,
per-hop probing, and the pinned final transfer. ``subprocess.run`` is always
replaced by a two-phase fake (probe configs versus transfer configs) and DNS is
always injected through ``SaveDeps.media_resolve`` / the ``resolve`` argument,
so no real curl, resolver, or network access happens in this file.
"""
from __future__ import annotations

from dataclasses import replace
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from proxy import manual_save_route as route
from proxy.tests.test_manual_save_route import (
    PAYLOAD,
    PAYLOAD_MD5,
    PUBLIC_MEDIA_IPV4,
    PUBLIC_MEDIA_IPV4_ALT,
    PUBLIC_MEDIA_IPV6,
    SIGNED_URL,
    _CurlStub,
    _curl_rc,
    _expected_curl_quote,
    _info_payload,
    _media_addrinfo,
    _media_resolver,
    _pipeline_deps,
    _probe_ok,
    _transfer_ok,
    _transfer_rc,
    _url_payload,
)


SONG_ID = 489970550
MUSICBOX = "http://127.0.0.1:8770"
TRIAL_MARKER = {"start": 0, "end": 30}


class _UnexpectedTier(BaseException):
    """Not an Exception: the engine must never mask an unexpected ladder tier."""


def _run_ladder(answers, *, song_id=SONG_ID):
    """Drive ``_select_quality`` over ``{tier: payload | exception}``."""
    attempted = []

    def fake_http(url_text):
        tier = url_text.rsplit("=", 1)[1]
        attempted.append(tier)
        if tier not in answers:
            raise _UnexpectedTier(tier)
        answer = answers[tier]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    try:
        selection = route._select_quality(fake_http, MUSICBOX, song_id)
    except route._SaveFailure as exc:
        return None, exc.code, attempted
    return selection, "", attempted


def _assert_ladder_failure(answers, expected_code, expected_attempted, **kwargs):
    selection, code, attempted = _run_ladder(answers, **kwargs)
    assert selection is None
    assert code == expected_code
    assert attempted == expected_attempted
    return code


# ---------------------------------------------------------------- transport --

@pytest.mark.parametrize(
    "error",
    [pytest.param(OSError("connection refused"), id="oserror"),
     pytest.param(TimeoutError("timed out"), id="timeout")],
)
def test_ladder_transport_error_aborts_without_downgrade(error):
    answers = {"jymaster": error}
    code = _assert_ladder_failure(answers, "service_unavailable", ["jymaster"])
    assert route._MESSAGES[code] == "音乐服务不可用"


def test_ladder_non_mapping_aborts():
    _assert_ladder_failure({"jymaster": ["not-a-mapping"]},
                           "quality_unverified", ["jymaster"])


# -------------------------------------------------------------- payload shape --

def test_ladder_ok_false_aborts():
    _assert_ladder_failure({"jymaster": _url_payload(ok=False)},
                           "quality_unverified", ["jymaster"])


def test_ladder_missing_code_aborts():
    payload = _url_payload()
    del payload["data"]["code"]
    _assert_ladder_failure({"jymaster": payload},
                           "quality_unverified", ["jymaster"])


def test_ladder_string_code_aborts():
    payload = _url_payload()
    payload["data"]["code"] = "200"
    _assert_ladder_failure({"jymaster": payload},
                           "quality_unverified", ["jymaster"])


@pytest.mark.parametrize("code", [500, 0, -1, 201, True, 200.0])
def test_ladder_unknown_code_aborts(code):
    payload = _url_payload()
    payload["data"]["code"] = code
    _assert_ladder_failure({"jymaster": payload},
                           "quality_unverified", ["jymaster"])


# ---------------------------------------------------------------- 200 / 404 --

def test_200_without_url_is_unverified_not_downgrade():
    # A 200 without a usable URL is contradictory evidence; it must not silently
    # fall through to the next (lower) tier.
    _assert_ladder_failure({"jymaster": _url_payload(url=None)},
                           "quality_unverified", ["jymaster"])


@pytest.mark.parametrize(
    "url",
    [pytest.param("ftp://signed.example.com/track.flac", id="ftp"),
     pytest.param("/relative/track.flac", id="relative"),
     pytest.param("signed.example.com/track.flac", id="no-scheme")],
)
def test_200_non_http_url_is_unverified(url):
    _assert_ladder_failure({"jymaster": _url_payload(url=url)},
                           "quality_unverified", ["jymaster"])


def test_404_without_url_is_definitive_unavailable():
    answers = {
        "jymaster": _url_payload(url=None, code=404, level="jymaster"),
        "hires": _url_payload(level="hires"),
    }
    selection, code, attempted = _run_ladder(answers)
    assert code == ""
    assert attempted == ["jymaster", "hires"]
    assert selection.requested == "hires"
    assert selection.actual == "hires"
    assert selection.actual_status == "reported"
    assert selection.fallback is True
    assert selection.media_url == SIGNED_URL


def test_404_with_url_is_unverified():
    _assert_ladder_failure(
        {"jymaster": _url_payload(url=SIGNED_URL, code=404)},
        "quality_unverified", ["jymaster"],
    )


def test_all_404_is_no_quality_available():
    answers = {
        tier: _url_payload(url=None, code=404, level=tier)
        for tier in route.QUALITY_ORDER
    }
    _assert_ladder_failure(answers, "no_quality_available",
                           list(route.QUALITY_ORDER))


# ------------------------------------------------------------------ trials --

def test_all_trial_is_trial_only():
    answers = {
        tier: _url_payload(free_trial=TRIAL_MARKER, level=tier)
        for tier in route.QUALITY_ORDER
    }
    code = _assert_ladder_failure(answers, "trial_only", list(route.QUALITY_ORDER))
    assert route._MESSAGES[code] == "仅取得试听片段，未保存"


def test_trial_high_tier_falls_back_to_full_lower_tier():
    answers = {
        "jymaster": _url_payload(free_trial=TRIAL_MARKER),
        "hires": _url_payload(level="hires"),
    }
    selection, code, attempted = _run_ladder(answers)
    assert code == ""
    assert attempted == ["jymaster", "hires"]
    assert selection.requested == "hires"
    assert selection.actual == "hires"
    assert selection.actual_status == "reported"
    assert selection.fallback is True


def test_trial_high_tier_falls_back_when_lower_reports_lower_level():
    answers = {
        "jymaster": _url_payload(free_trial=TRIAL_MARKER),
        "hires": _url_payload(level="lossless"),
    }
    selection, code, _attempted = _run_ladder(answers)
    assert code == ""
    assert selection.requested == "hires"
    assert selection.actual == "lossless"
    assert selection.actual_status == "reported"
    assert selection.fallback is True


# ------------------------------------------------------- reported level tokens --

def test_server_reported_lower_level_still_succeeds():
    # 2026-11-01 device evidence: requesting jymaster yields a verified lossless
    # answer. It is a success, not a mismatch and not an unknown.
    selection, code, attempted = _run_ladder({"jymaster": _url_payload(level="lossless")})
    assert code == ""
    assert attempted == ["jymaster"]
    assert selection.requested == "jymaster"
    assert selection.actual == "lossless"
    assert selection.actual_status == "reported"
    assert selection.fallback is True


@pytest.mark.parametrize(
    ("level", "expected"),
    [("  Studio.Master ", "studio.master"),
     ("LOSSESS-PLUS_1", "lossess-plus_1"),
     ("hires", "hires")],
)
def test_server_reported_other_token_is_reported_verbatim(level, expected):
    selection, code, _attempted = _run_ladder({"jymaster": _url_payload(level=level)})
    assert code == ""
    assert selection.actual == expected
    assert selection.actual_status == "reported"


def test_missing_level_reports_unknown():
    payload = _url_payload()
    del payload["data"]["level"]
    selection, code, _attempted = _run_ladder({"jymaster": payload})
    assert code == ""
    assert selection.requested == "jymaster"
    assert selection.actual == ""
    assert selection.actual_status == "unknown"
    assert selection.fallback is False


@pytest.mark.parametrize(
    "level",
    [None, True, 5, 1.5, ["jymaster"], "   ", "超清母带", "-leading-dash",
     "x" * 65, "with space"],
)
def test_blank_or_invalid_level_is_unknown_not_failure(level):
    selection, code, _attempted = _run_ladder({"jymaster": _url_payload(level=level)})
    assert code == ""
    assert selection.actual == ""
    assert selection.actual_status == "unknown"


def test_present_level_matching_is_reported():
    selection, code, _attempted = _run_ladder({"jymaster": _url_payload(level="jymaster")})
    assert code == ""
    assert selection.requested == "jymaster"
    assert selection.actual == "jymaster"
    assert selection.actual_status == "reported"
    assert selection.fallback is False


def test_ladder_skip_sets_fallback_even_when_label_matches():
    answers = {
        "jymaster": _url_payload(url=None, code=404, level="jymaster"),
        "hires": _url_payload(level="hires"),
    }
    selection, code, attempted = _run_ladder(answers)
    assert code == ""
    assert attempted == ["jymaster", "hires"]
    assert selection.actual == selection.requested == "hires"
    assert selection.fallback is True


# ----------------------------------------------------------------- identity --

@pytest.mark.parametrize(
    "song_id_value",
    [1, SONG_ID + 1, "not-a-number", None, True, 0, -1, 200.0],
)
def test_id_mismatch_fails_closed(song_id_value):
    payload = _url_payload()
    payload["data"]["id"] = song_id_value
    _assert_ladder_failure({"jymaster": payload},
                           "quality_identity_mismatch", ["jymaster"])


def test_missing_id_fails_closed():
    payload = _url_payload()
    del payload["data"]["id"]
    _assert_ladder_failure({"jymaster": payload},
                           "quality_identity_mismatch", ["jymaster"])


def test_song_id_key_absent_is_not_identity_failure():
    # The real device payload has no `songId` key at all: that is normal.
    payload = _url_payload()
    assert "songId" not in payload["data"]
    selection, code, _attempted = _run_ladder({"jymaster": payload})
    assert code == ""
    assert selection.actual == "jymaster"


@pytest.mark.parametrize(
    "song_id_value",
    [SONG_ID + 1, "not-a-number", None, True, 0, -1, 1.5],
)
def test_song_id_present_mismatch_fails(song_id_value):
    payload = _url_payload()
    payload["data"]["songId"] = song_id_value
    _assert_ladder_failure({"jymaster": payload},
                           "quality_identity_mismatch", ["jymaster"])


@pytest.mark.parametrize(
    "song_id_value",
    [SONG_ID + 1, 1, "not-a-number", None, True, 0, -1],
)
def test_song_id_present_invalid_fails(song_id_value):
    # Both keys exist and must agree with the requested song id.
    payload = _url_payload(include_song_id=True)
    payload["data"]["songId"] = song_id_value
    _assert_ladder_failure({"jymaster": payload},
                           "quality_identity_mismatch", ["jymaster"])


def test_both_ids_present_must_agree():
    payload = _url_payload(include_song_id=True)
    payload["data"]["songId"] = SONG_ID + 1
    _assert_ladder_failure({"jymaster": payload},
                           "quality_identity_mismatch", ["jymaster"])


def test_identity_is_required_for_404_rows_too():
    payload = _url_payload(url=None, code=404)
    payload["data"]["id"] = SONG_ID + 1
    _assert_ladder_failure({"jymaster": payload},
                           "quality_identity_mismatch", ["jymaster"])


# --------------------------------------------------------------- freeTrialInfo --

def test_free_trial_key_missing_fails_closed():
    payload = _url_payload()
    del payload["data"]["freeTrialInfo"]
    _assert_ladder_failure({"jymaster": payload},
                           "quality_unverified", ["jymaster"])


def test_free_trial_null_accepts():
    selection, code, _attempted = _run_ladder({"jymaster": _url_payload(free_trial=None)})
    assert code == ""
    assert selection.actual_status == "reported"
    assert selection.media_url == SIGNED_URL


@pytest.mark.parametrize(
    "marker",
    [pytest.param(TRIAL_MARKER, id="start-end"),
     pytest.param({"start": 0}, id="start-only")],
)
def test_free_trial_mapping_is_trial_evidence(marker):
    answers = {
        "jymaster": _url_payload(free_trial=marker),
        "hires": _url_payload(level="hires"),
    }
    selection, code, attempted = _run_ladder(answers)
    assert code == ""
    assert attempted == ["jymaster", "hires"]
    assert selection.requested == "hires"


@pytest.mark.parametrize(
    "marker",
    [{}, [], "trial", 0, False],
)
def test_free_trial_empty_mapping_or_bad_type_fails_closed(marker):
    _assert_ladder_failure({"jymaster": _url_payload(free_trial=marker)},
                           "quality_unverified", ["jymaster"])


# ------------------------------------------------- ignored entitlement fields --

@pytest.mark.parametrize(
    "extra",
    [
        pytest.param({"fee": 8, "payed": 1, "rightSource": 0}, id="device-values"),
        pytest.param({"fee": 0, "payed": 0, "rightSource": 8}, id="other-values"),
        pytest.param({"fee": "8", "payed": None, "rightSource": [0]}, id="hostile"),
    ],
)
def test_fee_payed_rightsource_do_not_change_decisions(extra):
    payload = _url_payload(level="lossless")
    payload["data"].update(extra)
    payload["data"].update({
        "levelConfuse": None,
        "message": None,
        "br": 769876,
        "sr": 44100,
        "type": "flac",
    })
    selection, code, _attempted = _run_ladder({"jymaster": payload})
    assert code == ""
    assert selection.actual == "lossless"
    assert selection.actual_status == "reported"


# ------------------------------------------------------------ engine results --

def _pipeline_ladder(tmp_path, answers):
    deps, store, workdir = _pipeline_deps(tmp_path)

    def fake_http(url_text):
        if url_text.endswith("/info"):
            return _info_payload()
        tier = url_text.rsplit("=", 1)[1]
        if tier not in answers:
            raise _UnexpectedTier(tier)
        return answers[tier]

    return replace(deps, http_json=fake_http), store, workdir


def test_success_carries_quality_status_and_fallback(tmp_path):
    answers = {
        "jymaster": _url_payload(free_trial=TRIAL_MARKER),
        "hires": _url_payload(level="lossless"),
    }
    deps, _store, _workdir = _pipeline_ladder(tmp_path, answers)
    result = route.save_track(SONG_ID, deps=deps)
    assert result["ok"] is True
    data = result["data"]
    assert data["status"] == "published"
    assert data["quality_requested"] == "hires"
    assert data["quality_actual"] == "lossless"
    assert data["quality_actual_status"] == "reported"
    assert data["quality_fallback"] is True
    assert data["source_type"] == "flac"
    assert data["format"] == "flac"
    assert data["size"] == len(PAYLOAD)
    assert data["md5"] == PAYLOAD_MD5
    assert set(data) >= {
        "quality_requested", "quality_actual",
        "quality_actual_status", "quality_fallback",
    }


def test_success_never_contains_signed_url(tmp_path):
    deps, store, _workdir = _pipeline_deps(tmp_path)
    result = route.save_track(SONG_ID, deps=deps)
    assert result["ok"] is True
    assert store.published_basename == result["data"]["basename"]
    rendered = json.dumps(result, ensure_ascii=False)
    assert SIGNED_URL not in rendered
    assert "TOPSECRETTAIL" not in rendered
    assert "https://" not in rendered


# ===========================================================================
# Part 2: download-target hardening (URL policy, DNS policy, probe, transfer)
# ===========================================================================

OBSERVED_CDN_URL = (
    "http://media.cdn.example.com/20261101/abcdef0123456789/jdymusic/obj/"
    "w5rDlsOJsrLeon944_bA/12345/12345.mp3?vuutid=example"
)
SECOND_HOST = "other-cdn.example.com"


def _probe_deadline():
    return route.time.monotonic() + 600.0


def _resolver(*addresses):
    return lambda _host, port: list(_media_addrinfo(*addresses, port=port))


def _resolve_lines(stub, *, phase="probe"):
    calls = stub.probe_calls if phase == "probe" else stub.transfer_calls
    values = []
    for call in calls:
        for line in call["config"].splitlines():
            if line.startswith("resolve = "):
                values.append(line[len("resolve = "):])
    return values


def _hop(addresses=("1.1.1.1",), *, probe):
    stub = _CurlStub(probe=probe)
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        hit = route._probe_media_hop(
            SIGNED_URL, "signed.example.com", 443, list(addresses),
            deadline=_probe_deadline(),
        )
    return hit, stub


def _hop_failure(addresses=("1.1.1.1",), *, probe):
    stub = _CurlStub(probe=probe)
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        with pytest.raises(route._SaveFailure) as caught:
            route._probe_media_hop(
                SIGNED_URL, "signed.example.com", 443, list(addresses),
                deadline=_probe_deadline(),
            )
    return caught.value.code, stub


def _follow(url, *, addresses=("1.1.1.1",), probe=()):
    stub = _CurlStub(probe=probe)
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        target = route._follow_media_redirects(url, _resolver(*addresses))
    return target, stub


def _follow_failure(url, *, addresses=("1.1.1.1",), probe=()):
    stub = _CurlStub(probe=probe)
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        with pytest.raises(route._SaveFailure) as caught:
            route._follow_media_redirects(url, _resolver(*addresses))
    return caught.value.code, stub


def _download(destination, *, url=SIGNED_URL, addresses=(PUBLIC_MEDIA_IPV4,),
              probe=(), transfer=()):
    stub = _CurlStub(probe=probe, transfer=transfer)
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        result = route._default_download(
            url, destination, resolve=_resolver(*addresses),
        )
    return result, stub


def _download_failure(destination, *, url=SIGNED_URL,
                      addresses=(PUBLIC_MEDIA_IPV4,), probe=(), transfer=()):
    stub = _CurlStub(probe=probe, transfer=transfer)
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(
                url, destination, resolve=_resolver(*addresses),
            )
    return caught.value.code, stub


# ----------------------------------------------------------------- URL policy --

def test_url_policy_accepts_observed_http_cdn_and_keeps_scheme():
    assert route._normalize_media_url(OBSERVED_CDN_URL) == OBSERVED_CDN_URL
    assert route._normalize_media_url(OBSERVED_CDN_URL).startswith("http://")
    upper = route._normalize_media_url(
        OBSERVED_CDN_URL.replace("http://", "HTTP://", 1))
    assert upper.startswith("http://")
    # Explicit default ports are legal and normalized away.
    assert route._normalize_media_url(
        "https://cdn.example.com:443/a.mp3?token=1"
    ) == "https://cdn.example.com/a.mp3?token=1"
    assert route._normalize_media_url(
        "http://cdn.example.com:80/a.mp3"
    ) == "http://cdn.example.com/a.mp3"
    # The query string is preserved verbatim, never decoded.
    assert route._normalize_media_url(
        "https://cdn.example.com/a.mp3?x=%2F&y=1+2"
    ).endswith("?x=%2F&y=1+2")


@pytest.mark.parametrize(
    "url",
    [pytest.param("https://user@cdn.example.com/a.mp3", id="user"),
     pytest.param("https://user:pass@cdn.example.com/a.mp3", id="user-pass")],
)
def test_url_policy_rejects_userinfo(url):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == "download_target_untrusted"


@pytest.mark.parametrize(
    "url",
    [pytest.param("https://cdn.example.com:8443/a.mp3", id="https-8443"),
     pytest.param("https://cdn.example.com:80/a.mp3", id="https-on-http-port"),
     pytest.param("http://cdn.example.com:443/a.mp3", id="http-on-https-port")],
)
def test_url_policy_rejects_non_default_port(url):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == "download_target_untrusted"


@pytest.mark.parametrize(
    "url",
    [pytest.param("https://cdn.example.com/a.mp3#frag", id="fragment"),
     pytest.param("https://cdn.example.com/a.mp3#", id="empty-fragment"),
     pytest.param("https://cdn.example.com", id="no-path"),
     pytest.param("https://cdn.example.com?token=1", id="query-no-path")],
)
def test_url_policy_rejects_fragment_and_empty_path(url):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == "download_target_untrusted"


@pytest.mark.parametrize(
    "url",
    [pytest.param("http://127.0.0.1/a.mp3", id="ipv4-loopback"),
     pytest.param("http://10.0.0.5/a.mp3", id="ipv4-private"),
     pytest.param("http://[::1]/a.mp3", id="ipv6-loopback"),
     pytest.param("https://[2606:2800:220:1:248:1893:25c8:1946]/a.mp3",
                  id="ipv6-literal")],
)
def test_url_policy_rejects_ip_literal_host(url):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == "download_target_untrusted"


@pytest.mark.parametrize(
    "url",
    [pytest.param("https://cdn.example.com/a" + chr(92) + "b.mp3", id="backslash"),
     pytest.param("https://cdn.example.com/a b.mp3", id="space"),
     pytest.param("https://例子.example/a.mp3", id="non-ascii-host"),
     pytest.param("https://cdn.example.com/音乐.mp3", id="non-ascii-path"),
     pytest.param("https://cdn.example.com/a" + chr(1) + "b.mp3", id="control")],
)
def test_url_policy_rejects_backslash_and_non_ascii(url):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == "download_target_untrusted"


@pytest.mark.parametrize(
    "control",
    [pytest.param(chr(0), id="nul"),
     pytest.param(chr(13), id="cr"),
     pytest.param(chr(10), id="lf")],
)
def test_framing_nul_cr_lf_is_download_failed(control):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url("https://cdn.example.com/a.mp3" + control)
    assert caught.value.code == "download_failed"


@pytest.mark.parametrize(
    "url",
    [pytest.param("https://cdn.example.com./a.mp3", id="trailing-dot"),
     pytest.param("https://a..b.example.com/x.mp3", id="empty-label"),
     pytest.param("https://-bad.example.com/x.mp3", id="leading-hyphen"),
     pytest.param("https://bad_label.example.com/x.mp3", id="underscore"),
     pytest.param("https://" + "a" * 64 + ".example.com/x.mp3", id="label-too-long"),
     pytest.param("https://" + ("a" * 63 + ".") * 4 + "com/x.mp3", id="host-too-long")],
)
def test_url_policy_rejects_malformed_hosts(url):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == "download_target_untrusted"


@pytest.mark.parametrize(
    ("url", "expected_code"),
    [pytest.param("ftp://cdn.example.com/a.mp3", "download_failed", id="ftp"),
     pytest.param("file:///tmp/a.mp3", "download_failed", id="file"),
     pytest.param("not-a-url", "download_failed", id="no-scheme"),
     pytest.param("", "download_failed", id="empty"),
     pytest.param(None, "download_failed", id="non-str"),
     pytest.param(
         "https://cdn.example.com/" + "x" * route.DOWNLOAD_MAX_MEDIA_URL_CHARS,
         "download_failed", id="too-long")],
)
def test_url_policy_rejects_unsupported_scheme_and_empty(url, expected_code):
    with pytest.raises(route._SaveFailure) as caught:
        route._normalize_media_url(url)
    assert caught.value.code == expected_code


# ---------------------------------------------------------------- DNS policy --

def test_media_dns_accepts_twenty_addresses():
    addresses = [f"8.8.8.{index}" for index in range(1, 21)]
    resolved = route._resolve_media_addresses(
        "cdn.example.com", 443, _media_resolver(*addresses))
    assert resolved == addresses


def test_media_dns_accepts_thirty_two_addresses():
    addresses = [f"8.8.8.{index}" for index in range(1, 33)]
    resolved = route._resolve_media_addresses(
        "cdn.example.com", 443, _media_resolver(*addresses))
    assert resolved == addresses
    assert len(resolved) == route.DOWNLOAD_MAX_MEDIA_ADDRESSES


def test_media_dns_too_many_or_empty_rejected():
    too_many = [f"8.8.8.{index}" for index in range(1, 34)]
    with pytest.raises(route._SaveFailure) as caught:
        route._resolve_media_addresses(
            "cdn.example.com", 443, _media_resolver(*too_many))
    assert caught.value.code == "download_target_untrusted"

    with pytest.raises(route._SaveFailure) as caught:
        route._resolve_media_addresses(
            "cdn.example.com", 443, lambda _host, _port: [])
    assert caught.value.code == "download_target_untrusted"

    deduped = route._resolve_media_addresses(
        "cdn.example.com", 443,
        _media_resolver("1.1.1.1", "1.1.1.1", "8.8.8.8"),
    )
    assert deduped == ["1.1.1.1", "8.8.8.8"]

    # 33 raw entries that dedupe to 32 distinct addresses are accepted.
    raw = [f"8.8.8.{index}" for index in range(1, 33)] + ["8.8.8.1"]
    assert len(route._resolve_media_addresses(
        "cdn.example.com", 443, _media_resolver(*raw))) == 32


@pytest.mark.parametrize(
    "address",
    [pytest.param("127.0.0.1", id="loopback"),
     pytest.param("10.0.0.1", id="private-10"),
     pytest.param("172.16.0.1", id="private-172"),
     pytest.param("192.168.1.1", id="private-192"),
     pytest.param("169.254.1.1", id="link-local"),
     pytest.param("100.64.0.1", id="cgnat"),
     pytest.param("198.18.0.1", id="benchmark"),
     pytest.param("224.0.0.1", id="multicast"),
     pytest.param("0.0.0.0", id="unspecified"),
     pytest.param("::1", id="ipv6-loopback"),
     pytest.param("fc00::1", id="ipv6-ula"),
     pytest.param("fe80::1", id="ipv6-link-local"),
     pytest.param("::ffff:127.0.0.1", id="ipv4-mapped"),
     pytest.param("64:ff9b::7f00:1", id="nat64")],
)
def test_media_dns_rejects_non_global_addresses(address):
    with pytest.raises(route._SaveFailure) as caught:
        route._resolve_media_addresses(
            "cdn.example.com", 443, _media_resolver(address))
    assert caught.value.code == "download_target_untrusted"


def test_media_dns_requires_all_addresses_public():
    with pytest.raises(route._SaveFailure) as caught:
        route._resolve_media_addresses(
            "cdn.example.com", 443, _media_resolver("1.1.1.1", "10.0.0.1"))
    assert caught.value.code == "download_target_untrusted"


def test_media_dns_transport_error_is_download_failed():
    def broken(_host, _port):
        raise OSError("resolver down")

    with pytest.raises(route._SaveFailure) as caught:
        route._resolve_media_addresses("cdn.example.com", 443, broken)
    assert caught.value.code == "download_failed"


def test_ipv6_resolve_value_is_bracketed():
    assert route._curl_resolve_value(
        "cdn.example.com", 443, PUBLIC_MEDIA_IPV6
    ) == f"cdn.example.com:443:[{PUBLIC_MEDIA_IPV6}]"
    assert route._curl_resolve_value(
        "cdn.example.com", 80, PUBLIC_MEDIA_IPV4
    ) == "cdn.example.com:80:" + PUBLIC_MEDIA_IPV4


# ------------------------------------------------------------------- probing --

def test_probe_tries_addresses_in_resolution_order():
    addresses = [PUBLIC_MEDIA_IPV4, "8.8.8.8", "8.8.4.4"]
    hit, stub = _hop(addresses, probe=[_curl_rc(56), _curl_rc(56), _probe_ok(200)])
    assert hit.address == "8.8.4.4"
    assert hit.redirect_url == ""
    assert _resolve_lines(stub) == [
        _expected_curl_quote("signed.example.com:443:" + address)
        for address in addresses
    ]


def test_probe_selects_first_successful_address():
    addresses = [PUBLIC_MEDIA_IPV4, "8.8.8.8"]
    hit, stub = _hop(addresses, probe=[_probe_ok(200)])
    assert hit.address == addresses[0]
    assert hit.redirect_url == ""
    assert len(stub.probe_calls) == 1


def test_probe_attempts_bounded_four_per_hop():
    addresses = [f"8.8.8.{index}" for index in range(1, 6)]
    code, stub = _hop_failure(
        addresses, probe=[_curl_rc(56)] * route.DOWNLOAD_MAX_MEDIA_PROBE_ATTEMPTS)
    assert code == "download_failed"
    assert len(stub.probe_calls) == route.DOWNLOAD_MAX_MEDIA_PROBE_ATTEMPTS


def test_probe_all_addresses_failed_is_download_failed():
    code, stub = _hop_failure(
        [PUBLIC_MEDIA_IPV4, "8.8.8.8"], probe=[_curl_rc(56), _curl_rc(56)])
    assert code == "download_failed"
    assert len(stub.probe_calls) == 2


def test_probe_http_error_falls_back_to_range_get_same_address():
    hit, stub = _hop(probe=[_probe_ok(403), _probe_ok(206)])
    assert hit.address == "1.1.1.1"
    assert len(stub.probe_calls) == 2
    head_lines = _CurlStub.config_lines(stub.probe_calls[0])
    range_lines = _CurlStub.config_lines(stub.probe_calls[1])
    assert "head" in head_lines
    assert 'range = "0-0"' not in head_lines
    assert "head" not in range_lines
    assert 'range = "0-0"' in range_lines
    assert _expected_curl_quote(
        "signed.example.com:443:1.1.1.1") in stub.probe_calls[1]["config"]


def test_probe_head_200_used_without_range_fallback():
    hit, stub = _hop(probe=[_probe_ok(200)])
    assert hit.address == "1.1.1.1"
    assert len(stub.probe_calls) == 1
    lines = _CurlStub.config_lines(stub.probe_calls[0])
    assert "head" in lines
    assert 'range = "0-0"' not in lines


def test_probe_zero_redirect_empty_redirect_url_is_normal():
    hit, stub = _hop(probe=[_probe_ok(200)])
    assert hit.redirect_url == ""
    assert len(stub.probe_calls) == 1


def test_3xx_empty_redirect_url_is_probe_failure():
    code, stub = _hop_failure(probe=[_probe_ok(302)])
    assert code == "download_failed"
    # No range fallback is attempted for a 3xx; the address just failed.
    assert len(stub.probe_calls) == 1


def test_probe_config_uses_single_address_resolve():
    addresses = [PUBLIC_MEDIA_IPV4, "8.8.8.8"]
    hit, stub = _hop(addresses, probe=[_probe_ok(200)])
    assert hit.address == addresses[0]
    assert len(stub.probe_calls) == 1
    lines = _CurlStub.config_lines(stub.probe_calls[0])
    resolve_lines = [line for line in lines if line.startswith("resolve = ")]
    assert resolve_lines == [
        "resolve = " + _expected_curl_quote(
            "signed.example.com:443:" + PUBLIC_MEDIA_IPV4)
    ]
    assert "8.8.8.8" not in stub.probe_calls[0]["config"]


def test_probe_config_has_no_location_and_pins_resolve():
    _hit, stub = _hop(probe=[_probe_ok(200)])
    lines = _CurlStub.config_lines(stub.probe_calls[0])
    for forbidden in ("location", "fail", 'continue-at = "-"'):
        assert forbidden not in lines
    assert not any(line.startswith("max-filesize =") for line in lines)
    assert "max-redirs = 0" in lines
    assert any(line.startswith("resolve = ") for line in lines)
    assert 'proto = "=http,https"' in lines
    assert 'write-out = "%{http_code} %{redirect_url}"' in lines
    assert 'output = "/dev/null"' in lines


# ----------------------------------------------------------------- redirects --

def test_redirect_to_untrusted_target_is_terminal_untrusted():
    code, stub = _follow_failure(
        SIGNED_URL,
        addresses=(PUBLIC_MEDIA_IPV4, "8.8.8.8"),
        probe=[_probe_ok(302, "http://10.0.0.5/media/track.flac")],
    )
    assert code == "download_target_untrusted"
    # Terminal: the second resolved address is never probed.
    assert len(stub.probe_calls) == 1


def test_redirect_loop_is_download_failed():
    code, stub = _follow_failure(SIGNED_URL, probe=[_probe_ok(302, SIGNED_URL)])
    assert code == "download_failed"
    assert len(stub.probe_calls) == 1


def test_redirect_hop_limit_three_then_reject():
    urls = [f"https://cdn.example.com/{index}.flac" for index in range(6)]
    probe = [_probe_ok(302, urls[index + 1]) for index in range(4)]
    code, stub = _follow_failure(urls[0], probe=probe)
    assert code == "download_failed"
    assert len(stub.probe_calls) == route.DOWNLOAD_MAX_REDIRECTS + 1


def test_redirect_https_to_http_blocked_but_http_to_https_allowed():
    blocked, _stub = _follow_failure(
        "https://cdn.example.com/start.flac",
        probe=[_probe_ok(302, "http://cdn.example.com/next.flac")],
    )
    assert blocked == "download_target_untrusted"

    target, stub = _follow(
        "http://cdn.example.com/start.flac",
        probe=[_probe_ok(302, "https://cdn.example.com/next.flac"), _probe_ok(200)],
    )
    assert target.url == "https://cdn.example.com/next.flac"
    assert target.host == "cdn.example.com"
    assert len(stub.probe_calls) == 2


def test_redirect_to_other_public_host_allowed_no_allowlist():
    target, stub = _follow(
        "https://signed.example.com/start.flac",
        probe=[_probe_ok(302, f"https://{SECOND_HOST}/final.flac"), _probe_ok(200)],
    )
    assert target.url == f"https://{SECOND_HOST}/final.flac"
    assert target.host == SECOND_HOST
    assert target.address == "1.1.1.1"
    assert len(stub.probe_calls) == 2


# ------------------------------------------------------- pinned final transfer --

def test_transfer_pins_probe_selected_address(tmp_path):
    destination = tmp_path / "source.bin"
    addresses = [PUBLIC_MEDIA_IPV4, PUBLIC_MEDIA_IPV4_ALT]
    result, stub = _download(
        destination, addresses=addresses,
        probe=[_curl_rc(56), _probe_ok(200)],
        transfer=[_transfer_ok(destination, payload=PAYLOAD)],
    )
    assert result == {"size": len(PAYLOAD), "md5": PAYLOAD_MD5}
    assert len(stub.transfer_calls) == 1
    assert _resolve_lines(stub, phase="transfer") == [
        _expected_curl_quote("signed.example.com:443:" + PUBLIC_MEDIA_IPV4_ALT)
    ]
    assert PUBLIC_MEDIA_IPV4 not in stub.transfer_calls[0]["config"]


def test_transfer_does_not_switch_address_on_failure(tmp_path):
    destination = tmp_path / "source.bin"
    addresses = [PUBLIC_MEDIA_IPV4, PUBLIC_MEDIA_IPV4_ALT]
    result, stub = _download(
        destination, addresses=addresses,
        probe=[_curl_rc(56), _probe_ok(200)],
        transfer=[
            _transfer_rc(56, destination=destination, partial=PAYLOAD[:5]),
            _transfer_ok(destination, payload=PAYLOAD),
        ],
    )
    assert result == {"size": len(PAYLOAD), "md5": PAYLOAD_MD5}
    assert len(stub.probe_calls) == 2  # probed once, never re-probed
    assert len(stub.transfer_calls) == 2
    for call in stub.transfer_calls:
        assert "signed.example.com:443:" + PUBLIC_MEDIA_IPV4_ALT in call["config"]
        assert PUBLIC_MEDIA_IPV4 not in call["config"]


def test_transfer_retries_same_address_four_attempts(tmp_path):
    destination = tmp_path / "source.bin"
    code, stub = _download_failure(
        destination,
        probe=[_probe_ok(200)],
        transfer=[
            _transfer_rc(56, destination=destination, partial=PAYLOAD[:5])
            for _ in range(route.DOWNLOAD_MAX_ATTEMPTS)
        ],
    )
    assert code == "download_failed"
    assert len(stub.probe_calls) == 1
    assert len(stub.transfer_calls) == route.DOWNLOAD_MAX_ATTEMPTS
    for call in stub.transfer_calls:
        assert PUBLIC_MEDIA_IPV4 in call["config"]


def test_transfer_budget_exhaustion_is_download_failed(tmp_path, monkeypatch):
    destination = tmp_path / "source.bin"
    clock = {"now": 1000.0}
    monkeypatch.setattr(route.time, "monotonic", lambda: clock["now"])

    def exhaust_transfer():
        clock["now"] += route.DOWNLOAD_SUBPROCESS_TIMEOUT
        return SimpleNamespace(returncode=56, stdout="", stderr="reset")

    code, stub = _download_failure(
        destination,
        probe=[_probe_ok(200)],
        transfer=[exhaust_transfer],
    )
    assert code == "download_failed"
    assert len(stub.transfer_calls) == 1


def test_slow_transfer_does_not_extend_budget(tmp_path, monkeypatch):
    destination = tmp_path / "source.bin"
    clock = {"now": 1000.0}
    monkeypatch.setattr(route.time, "monotonic", lambda: clock["now"])

    def slow_transfer():
        clock["now"] += 250.0
        return SimpleNamespace(returncode=56, stdout="", stderr="reset")

    code, stub = _download_failure(
        destination,
        probe=[_probe_ok(200)],
        transfer=[slow_transfer, slow_transfer, slow_transfer],
    )
    assert code == "download_failed"
    assert len(stub.transfer_calls) == 3  # budget, not DOWNLOAD_MAX_ATTEMPTS, stops it
    budget_lines = [
        line
        for call in stub.transfer_calls
        for line in _CurlStub.config_lines(call)
        if line.startswith("max-time = ")
    ]
    assert budget_lines == ["max-time = 180", "max-time = 180", "max-time = 80"]


def test_resume_preserved_across_attempts(tmp_path):
    destination = tmp_path / "source.bin"
    result, stub = _download(
        destination,
        probe=[_probe_ok(200)],
        transfer=[
            _transfer_rc(56, destination=destination, partial=PAYLOAD[:5]),
            _transfer_ok(destination, payload=PAYLOAD),
        ],
    )
    assert result == {"size": len(PAYLOAD), "md5": PAYLOAD_MD5}
    assert len(stub.transfer_calls) == 2
    for call in stub.transfer_calls:
        assert 'continue-at = "-"' in _CurlStub.config_lines(call)
    assert destination.read_bytes() == PAYLOAD


def test_transfer_config_forbids_redirect_following(tmp_path):
    destination = tmp_path / "source.bin"
    _result, stub = _download(
        destination,
        probe=[_probe_ok(200)],
        transfer=[_transfer_ok(destination, payload=PAYLOAD)],
    )
    lines = _CurlStub.config_lines(stub.transfer_calls[0])
    assert "location" not in lines
    assert "max-redirs = 0" in lines
    assert "fail" in lines
    assert f"max-filesize = {route.MAX_BYTES}" in lines
    assert 'continue-at = "-"' in lines
    assert "connect-timeout = 15" in lines
    assert 'proto = "=http,https"' in lines
    assert 'write-out = "%{http_code}"' in lines


def test_transfer_3xx_after_probe_is_untrusted_and_deletes_output(tmp_path):
    destination = tmp_path / "source.bin"

    def redirect():
        destination.write_bytes(b"redirect-body")
        return SimpleNamespace(returncode=0, stdout="302", stderr="")

    code, _stub = _download_failure(
        destination,
        probe=[_probe_ok(200)],
        transfer=[redirect],
    )
    assert code == "download_target_untrusted"
    assert destination.exists() is False


@pytest.mark.parametrize(
    "status",
    [pytest.param("204", id="204"),
     pytest.param("403", id="403"),
     pytest.param("500", id="500"),
     pytest.param("garbage", id="unparseable")],
)
def test_transfer_status_outside_200_206_is_download_failed(tmp_path, status):
    destination = tmp_path / "source.bin"

    def unexpected_status():
        destination.write_bytes(b"error-body")
        return SimpleNamespace(returncode=0, stdout=status, stderr="")

    code, _stub = _download_failure(
        destination,
        probe=[_probe_ok(200)],
        transfer=[unexpected_status],
    )
    assert code == "download_failed"
    assert destination.exists() is False


# ------------------------------------------------------------- leak / wiring --

def test_download_errors_never_leak_url_host_or_token(tmp_path):
    secrets = (SIGNED_URL, "TOPSECRETTAIL", "signed.example.com")
    destination = tmp_path / "source.bin"

    private_resolver = _media_resolver("10.0.0.5")
    with mock.patch.object(
        route.subprocess, "run", side_effect=AssertionError("curl must not run"),
    ):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination, resolve=private_resolver)
    assert caught.value.code == "download_target_untrusted"
    for secret in secrets + ("10.0.0.5",):
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)

    code, _stub = _download_failure(
        destination,
        addresses=("1.1.1.1", "8.8.8.8", "8.8.4.4", "9.9.9.9"),
        probe=[_curl_rc(56)] * route.DOWNLOAD_MAX_MEDIA_PROBE_ATTEMPTS,
    )
    assert code == "download_failed"

    def redirect():
        destination.write_bytes(b"x")
        return SimpleNamespace(returncode=0, stdout="302", stderr="")

    stub = _CurlStub(probe=[_probe_ok(200)], transfer=[redirect])
    with mock.patch.object(route.subprocess, "run", side_effect=stub):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(
                SIGNED_URL, destination, resolve=_media_resolver(PUBLIC_MEDIA_IPV4),
            )
    assert caught.value.code == "download_target_untrusted"
    for secret in secrets:
        assert secret not in str(caught.value)
        assert secret not in repr(caught.value)

    success_destination = tmp_path / "ok.bin"
    result, _stub = _download(
        success_destination,
        probe=[_probe_ok(200)],
        transfer=[_transfer_ok(success_destination, payload=PAYLOAD)],
    )
    rendered = json.dumps(result, ensure_ascii=False)
    assert SIGNED_URL not in rendered
    assert "TOPSECRETTAIL" not in rendered
    assert "https://" not in rendered


def test_private_dns_media_resolve_is_untrusted(tmp_path):
    deps, store, workdir = _pipeline_deps(tmp_path)
    deps = replace(
        deps,
        download=None,
        media_resolve=_resolver("10.0.0.5"),
    )
    with mock.patch.object(
        route.subprocess, "run", side_effect=AssertionError("curl must not run"),
    ):
        result = route.save_track(SONG_ID, deps=deps)
    assert result["ok"] is False
    assert result["code"] == "download_target_untrusted"
    assert result["message"] == "下载目标未通过安全校验"
    assert result["data"] == {}
    assert store.begin_target is None
    assert workdir.exists() is False


def test_download_target_untrusted_gateway_mapping():
    from proxy import webui_gateway as gateway

    message = "下载目标未通过安全校验"
    assert route._MESSAGES["download_target_untrusted"] == message
    assert gateway._MANUAL_SAVE_GATEWAY_MESSAGES["download_target_untrusted"] == message
    assert gateway._MANUAL_SAVE_HTTP_STATUS["download_target_untrusted"] == 502
    assert gateway._manual_save_message("download_target_untrusted") == message

