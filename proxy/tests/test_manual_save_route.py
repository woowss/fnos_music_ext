"""Synthetic tests for the manual-save engine: no network, ffmpeg, or real publish."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import stat
import threading
from types import SimpleNamespace
from unittest import mock

import pytest

from proxy import manual_save_core
from proxy import manual_save_route as route


SIGNED_URL = "https://signed.example.com/media/track.flac?token=TOPSECRETTAIL"
PAYLOAD = b"synthetic flac bytes for manual save"
PAYLOAD_MD5 = hashlib.md5(PAYLOAD).hexdigest()
BACKSLASH = chr(92)
TAB = chr(9)


def _dir_stat(*, ino, dev=1, uid=1000, mode=0o700):
    return SimpleNamespace(st_mode=stat.S_IFDIR | mode, st_dev=dev, st_ino=ino, st_uid=uid)


def _config(**overrides):
    values = {
        "FNMUSIC_MANUAL_SAVE_ENABLED": "true",
        "FNMUSIC_MANUAL_SAVE_TARGET": "/target",
        "FNMUSIC_MANUAL_SAVE_STAGING": "/staging",
        "FNMUSIC_MUSICBOX_URL": "http://127.0.0.1:8770",
    }
    values.update(overrides)
    return values


def _base_deps(*, stat_fn=None, geteuid=None, **overrides):
    def default_stat(path):
        if path == "/target":
            return _dir_stat(ino=1)
        if path == "/staging":
            return _dir_stat(ino=2)
        raise OSError("missing")

    deps = route.SaveDeps(
        config=_config(),
        stat=stat_fn or default_stat,
        geteuid=geteuid or (lambda: 1000),
    )
    return replace(deps, **overrides) if overrides else deps


def _info_payload(name="测试歌曲", artists=("歌手A", "歌手B"), album="测试专辑",
                  number=3, disc=1):
    return {
        "ok": True,
        "data": {
            "name": name,
            "ar": [{"name": artist} for artist in artists],
            "al": {"name": album},
            "no": number,
            "cd": disc,
        },
    }


def _url_payload(*, url=SIGNED_URL, media_type="flac", level="jymaster",
                 size=len(PAYLOAD), md5=PAYLOAD_MD5, ok=True):
    return {
        "ok": ok,
        "data": {"url": url, "type": media_type, "level": level,
                 "size": size, "md5": md5},
    }


class FakeStore:
    def __init__(self, publish_result="published"):
        self.publish_result = publish_result
        self.begin_target = None
        self.chunks = []
        self.sealed = False
        self.published_basename = None
        self.closed = False

    def begin_owned_stage(self, target_id):
        self.begin_target = target_id
        return "stage-token"

    def append(self, stage, chunk):
        self.chunks.append(bytes(chunk))

    def seal(self, stage):
        self.sealed = True

    def publish_noreplace(self, stage, basename):
        self.published_basename = basename
        if self.publish_result == "published":
            return manual_save_core.StoreResult.PUBLISHED
        return manual_save_core.StoreResult.CONFLICT

    def close(self):
        self.closed = True


def _pipeline_deps(tmp_path, *, info=None, url=None, probe=None, decode=None,
                   tag=None, store=None, download=None, make_tempdir=None):
    workdir = tmp_path / "work"
    info_payload = _info_payload() if info is None else info
    url_payload = _url_payload() if url is None else url
    fake_store = FakeStore() if store is None else store

    def fake_http(url_text):
        if url_text.endswith("/info"):
            return info_payload
        return url_payload

    def fake_download(media_url, destination):
        assert media_url == SIGNED_URL
        destination.write_bytes(PAYLOAD)
        return {"size": len(PAYLOAD), "md5": PAYLOAD_MD5}

    def fake_probe(_path):
        return {"streams": [{"codec_type": "audio", "codec_name": "flac"}],
                "format": {"format_name": "flac"}}

    def fake_decode(_path):
        return None

    def fake_tag(source, tagged, _metadata):
        tagged.write_bytes(b"TAGGED:" + source.read_bytes())

    def fake_make_tempdir():
        workdir.mkdir(exist_ok=True)
        return workdir

    def fake_open_store(_target_dir, _staging_dir):
        return fake_store

    deps = _base_deps(
        http_json=fake_http,
        download=download or fake_download,
        probe=probe or fake_probe,
        decode_check=decode or fake_decode,
        tag=tag or fake_tag,
        open_store=fake_open_store,
        make_tempdir=make_tempdir or fake_make_tempdir,
    )
    return deps, fake_store, workdir


def _assert_no_signed_url(result):
    rendered = json.dumps(result, ensure_ascii=False)
    assert SIGNED_URL not in rendered
    assert "TOPSECRETTAIL" not in rendered
    assert "https://" not in rendered


def test_status_disabled_by_default_and_enabled_data_shape():
    disabled = route.status(deps=route.SaveDeps(config={}))
    assert disabled["ok"] is False
    assert disabled["code"] == "disabled"
    assert disabled["data"]["enabled"] is False

    enabled = route.status(deps=route.SaveDeps(config=_config()))
    assert enabled["ok"] is True
    assert enabled["code"] == "ok"
    assert enabled["data"]["enabled"] is True
    assert enabled["data"]["target_dir"] == "/target"
    assert enabled["data"]["staging_dir"] == "/staging"
    assert enabled["data"]["quality_order"] == list(route.QUALITY_ORDER)
    assert enabled["data"]["max_bytes"] == route.MAX_BYTES


def test_disabled_save_fails_closed_without_touching_dependencies():
    deps = replace(_base_deps(), config=_config(FNMUSIC_MANUAL_SAVE_ENABLED="false"),
                   http_json=lambda _url: pytest.fail("http reached while disabled"))
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is False
    assert result["code"] == "disabled"
    assert result["data"] == {}


@pytest.mark.parametrize("song_id", [0, -1, "1", 1.5, True, None, [1]])
def test_invalid_song_id_is_rejected_fixedly(song_id):
    result = route.save_track(song_id, deps=route.SaveDeps(config={}))
    assert result["ok"] is False
    assert result["code"] == "invalid_song_id"
    assert result["message"] == route._MESSAGES["invalid_song_id"]
    assert result["data"] == {}


@pytest.mark.parametrize(
    "stat_fn",
    [
        pytest.param(lambda path: (_ for _ in ()).throw(OSError("missing")),
                     id="missing"),
        pytest.param(
            lambda path: _dir_stat(ino=1)
            if path == "/target" else _dir_stat(ino=2, dev=2),
            id="different_device",
        ),
        pytest.param(
            lambda path: _dir_stat(ino=1)
            if path == "/target" else _dir_stat(ino=2, mode=0o755),
            id="staging_mode",
        ),
        pytest.param(
            lambda path: _dir_stat(ino=1)
            if path == "/target" else _dir_stat(ino=2, uid=0),
            id="staging_owner",
        ),
        pytest.param(
            lambda path: _dir_stat(ino=1)
            if path == "/target" else _dir_stat(ino=1),
            id="same_inode",
        ),
    ],
)
def test_config_invalid_fails_closed_before_any_io(stat_fn):
    deps = replace(
        _base_deps(stat_fn=stat_fn),
        http_json=lambda _url: pytest.fail("http reached with invalid config"),
        download=lambda _url, _dest: pytest.fail("download reached"),
        open_store=lambda _target, _staging: pytest.fail("store reached"),
    )
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is False
    assert result["code"] == "config_invalid"
    assert result["data"] == {}


def test_missing_configured_directory_is_config_invalid():
    deps = replace(_base_deps(), config=_config(FNMUSIC_MANUAL_SAVE_TARGET=""))
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "config_invalid"


def test_success_publishes_tagged_bytes_and_hides_signed_url(tmp_path):
    deps, store, workdir = _pipeline_deps(tmp_path)
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is True
    assert result["code"] == "ok"
    data = result["data"]
    assert data["status"] == "published"
    assert data["song_id"] == 489970550
    assert data["title"] == "测试歌曲"
    assert data["artist"] == "歌手A/歌手B"
    assert data["album"] == "测试专辑"
    assert data["quality_requested"] == "jymaster"
    assert data["quality_actual"] == "jymaster"
    assert data["source_type"] == "flac"
    assert data["format"] == "flac"
    assert data["size"] == len(PAYLOAD)
    assert data["md5"] == PAYLOAD_MD5
    assert data["basename"] == "歌手A_歌手B - 测试歌曲.flac"
    assert data["target_dir"] == "/target"
    assert data["tagged"] is True
    assert data["cover_embedded"] is False
    assert data["published_size"] == len(b"TAGGED:" + PAYLOAD)
    assert data["published_md5"] == hashlib.md5(b"TAGGED:" + PAYLOAD).hexdigest()
    assert set(data) == {
        "status", "song_id", "title", "artist", "album", "quality_requested",
        "quality_actual", "source_type", "format", "size", "md5", "basename",
        "target_dir", "tagged", "cover_embedded", "published_size",
        "published_md5",
    }
    assert store.begin_target == "manual-save"
    assert b"".join(store.chunks) == b"TAGGED:" + PAYLOAD
    assert store.published_basename == data["basename"]
    assert store.sealed is True
    assert store.closed is True
    assert workdir.exists() is False
    _assert_no_signed_url(result)


def test_quality_ladder_learns_first_usable_answer(tmp_path):
    attempted = []

    def ladder_http(url_text):
        if url_text.endswith("/info"):
            return _info_payload()
        quality = url_text.rsplit("=", 1)[1]
        attempted.append(quality)
        if quality in {"jymaster", "hires"}:
            return {"ok": True, "data": {"url": "", "level": quality}}
        return _url_payload(level="lossless")

    deps, _store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=ladder_http)
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is True
    assert attempted == ["jymaster", "hires", "lossless"]
    assert result["data"]["quality_requested"] == "lossless"
    assert result["data"]["quality_actual"] == "lossless"


def test_basename_sanitizes_forbidden_characters_and_caps_length(tmp_path):
    hostile_title = (
        "bad/name" + BACKSLASH + "with:colons" + TAB + "and" + "长" * 400
    )
    deps, store, _workdir = _pipeline_deps(
        tmp_path, info=_info_payload(name=hostile_title),
    )
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is True
    basename = result["data"]["basename"]
    assert basename.endswith(".flac")
    for forbidden in ("/", BACKSLASH, ":"):
        assert forbidden not in basename
    assert not any(unicodedata_control(character) for character in basename)
    assert len(basename) <= manual_save_core.MAX_BASENAME_CHARS
    assert basename[-1] not in (" ", ".")
    assert store.published_basename == basename


def unicodedata_control(character):
    import unicodedata

    return unicodedata.category(character) in {"Cc", "Cs"}


def test_busy_single_flight_while_first_save_holds_the_engine(tmp_path):
    started = threading.Event()
    release = threading.Event()
    results = []

    def blocking_http(url_text):
        if url_text.endswith("/info"):
            started.set()
            release.wait(5)
            raise OSError("service down")
        raise AssertionError("unexpected url")

    deps, _store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=blocking_http)
    thread = threading.Thread(target=lambda: results.append(route.save_track(1, deps=deps)))
    thread.start()
    assert started.wait(5) is True
    second = route.save_track(2, deps=deps)
    release.set()
    thread.join(5)
    assert thread.is_alive() is False
    assert second["ok"] is False
    assert second["code"] == "busy"
    assert results and results[0]["code"] == "service_unavailable"


def test_service_unavailable_redacts_transport_errors(tmp_path):
    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=lambda _url: (_ for _ in ()).throw(OSError("secret-detail")))
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "service_unavailable"
    assert "secret-detail" not in json.dumps(result)
    assert store.closed is False


def test_no_quality_available_when_ladder_is_exhausted(tmp_path):
    calls = []

    def no_url(_url_text):
        calls.append(1)
        return {"ok": False, "message": "nope"}

    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, http_json=lambda url_text: (
        _info_payload() if url_text.endswith("/info") else no_url(url_text)
    ))
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "no_quality_available"
    assert len(calls) == len(route.QUALITY_ORDER)
    assert store.closed is False


@pytest.mark.parametrize("field", ["size", "md5"])
def test_integrity_mismatch_fails_before_publish(tmp_path, field):
    url = _url_payload()
    if field == "size":
        url["data"]["size"] = len(PAYLOAD) + 1
    else:
        url["data"]["md5"] = "0" * 32
    deps, store, _workdir = _pipeline_deps(tmp_path, url=url)
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "integrity_mismatch"
    assert store.begin_target is None
    assert store.closed is False


def test_missing_api_size_and_md5_is_tolerated(tmp_path):
    url = _url_payload()
    url["data"].pop("size")
    url["data"].pop("md5")
    deps, store, _workdir = _pipeline_deps(tmp_path, url=url)
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is True
    assert result["data"]["size"] == len(PAYLOAD)
    assert result["data"]["md5"] == PAYLOAD_MD5
    assert store.closed is True


def test_download_failure_is_fixed_and_cleans_workdir(tmp_path):
    def failing_download(_url, destination):
        destination.write_bytes(PAYLOAD[:4])
        raise OSError("signed url detail")

    deps, store, workdir = _pipeline_deps(tmp_path, download=failing_download)
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "download_failed"
    assert "signed url detail" not in json.dumps(result)
    assert store.closed is False
    assert workdir.exists() is False


@pytest.mark.parametrize(
    "probe",
    [
        {"streams": [], "format": {"format_name": "flac"}},
        {"streams": [{"codec_type": "audio"}], "format": {"format_name": "wav"}},
        {"streams": [{"codec_type": "audio"}], "format": {}},
        "not-a-probe",
    ],
)
def test_invalid_probe_is_invalid_audio(tmp_path, probe):
    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, probe=lambda _path: probe)
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "invalid_audio"
    assert store.closed is False


def test_decode_failure_is_invalid_audio(tmp_path):
    deps, store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, decode_check=lambda _path: (_ for _ in ()).throw(OSError("decode")))
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "invalid_audio"
    assert store.closed is False


def test_tag_failure_is_fixed_and_publish_never_attempted(tmp_path):
    def failing_tag(_source, _tagged, _metadata):
        raise OSError("tag detail")

    deps, store, workdir = _pipeline_deps(tmp_path, tag=failing_tag)
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "tag_failed"
    assert "tag detail" not in json.dumps(result)
    assert store.begin_target is None
    assert store.closed is False
    assert workdir.exists() is False


def test_staging_unavailable_when_store_cannot_open(tmp_path):
    deps, _store, _workdir = _pipeline_deps(tmp_path)
    deps = replace(deps, open_store=lambda _target, _staging: (_ for _ in ()).throw(OSError("open")))
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "staging_unavailable"


def test_publish_failure_is_fixed_and_store_is_closed(tmp_path):
    store = FakeStore()

    def explode(_stage, _basename):
        raise RuntimeError("publish detail")

    store.publish_noreplace = explode
    deps, _store, _workdir = _pipeline_deps(tmp_path, store=store)
    result = route.save_track(489970550, deps=deps)
    assert result["code"] == "publish_failed"
    assert "publish detail" not in json.dumps(result)
    assert store.sealed is True
    assert store.closed is True


def test_conflict_returns_fixed_conflict_without_overwrite(tmp_path):
    deps, store, _workdir = _pipeline_deps(tmp_path, store=FakeStore("conflict"))
    result = route.save_track(489970550, deps=deps)
    assert result["ok"] is False
    assert result["code"] == "conflict"
    assert result["data"]["status"] == "conflict"
    assert result["data"]["basename"] == store.published_basename
    assert store.published_basename == "歌手A_歌手B - 测试歌曲.flac"
    assert store.closed is True
    _assert_no_signed_url(result)


CURL_TEST_PAYLOAD = b"synthetic curl payload"
CURL_TEST_MD5 = hashlib.md5(CURL_TEST_PAYLOAD).hexdigest()


def _expected_curl_quote(value):
    escaped = value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))
    return chr(34) + escaped + chr(34)


def _curl_run_writing(destination, *, payload=CURL_TEST_PAYLOAD, returncode=0,
                      calls=None):
    def run(argv, **kwargs):
        if calls is not None:
            calls["argv"] = list(argv)
            calls["stdin"] = kwargs.get("input")
            calls["kwargs"] = kwargs
        if returncode == 0 and payload is not None:
            destination.write_bytes(payload)
        return SimpleNamespace(returncode=returncode, stdout=b"", stderr=b"")

    return run


def test_default_download_passes_url_via_curl_stdin_never_argv(tmp_path):
    destination = tmp_path / "source.bin"
    calls = {}
    with mock.patch.object(
        route.subprocess, "run",
        side_effect=_curl_run_writing(destination, calls=calls),
    ):
        result = route._default_download(SIGNED_URL, destination)
    assert result == {"size": len(CURL_TEST_PAYLOAD), "md5": CURL_TEST_MD5}
    assert calls["argv"] == [route.CURL_BIN, "-K", "-"]
    for argument in calls["argv"]:
        assert SIGNED_URL not in argument
        assert "TOPSECRETTAIL" not in argument
    stdin = calls["stdin"]
    assert 'url = "' + SIGNED_URL + '"' in stdin
    assert "output = " + _expected_curl_quote(str(destination)) in stdin
    assert "user-agent = " + _expected_curl_quote(route.CURL_USER_AGENT) in stdin
    assert "referer = " + _expected_curl_quote(route.CURL_REFERER) in stdin
    config_lines = stdin.splitlines()
    for flag in ("location", "fail", "silent", "show-error"):
        assert flag in config_lines
    assert route.DOWNLOAD_TIMEOUT == 180.0
    assert route.DOWNLOAD_SUBPROCESS_TIMEOUT == 580.0
    assert route.DOWNLOAD_MAX_ATTEMPTS == 4
    assert f"max-time = {int(route.DOWNLOAD_TIMEOUT)}" in config_lines
    assert f"max-filesize = {route.MAX_BYTES}" in config_lines
    assert 'continue-at = "-"' in config_lines
    assert not any(
        line.startswith(("retry =", "retry-delay", "retry-all-errors"))
        for line in config_lines
    )
    kwargs = calls["kwargs"]
    assert kwargs["text"] is True
    assert kwargs["capture_output"] is True
    assert kwargs["timeout"] == route.DOWNLOAD_TIMEOUT + 10.0
    assert stdin.endswith(chr(10))


def test_default_download_escapes_quotes_in_curl_config(tmp_path):
    destination = tmp_path / "source.bin"
    url = 'https://signed.example.com/a"b?token=TOPSECRETTAIL'
    calls = {}
    with mock.patch.object(
        route.subprocess, "run",
        side_effect=_curl_run_writing(destination, calls=calls),
    ):
        route._default_download(url, destination)
    escaped = (
        'url = "https://signed.example.com/a' + chr(92) + '"b?token=TOPSECRETTAIL"'
    )
    assert escaped in calls["stdin"]
    assert chr(10) not in calls["stdin"].split("url = ", 1)[1].splitlines()[0]


@pytest.mark.parametrize(
    "bad_url",
    [
        SIGNED_URL + chr(10),
        SIGNED_URL + chr(13),
        "https://signed.example.com/x" + chr(0),
        "ftp://signed.example.com/x",
        "not-a-url",
    ],
)
def test_default_download_rejects_invalid_url_or_framing_before_curl(tmp_path, bad_url):
    destination = tmp_path / "source.bin"
    with mock.patch.object(
        route.subprocess, "run", side_effect=AssertionError("curl must not run"),
    ):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(bad_url, destination)
    assert caught.value.code == "download_failed"
    assert "TOPSECRETTAIL" not in str(caught.value)


def test_default_download_requires_absolute_destination(tmp_path):
    with mock.patch.object(
        route.subprocess, "run", side_effect=AssertionError("curl must not run"),
    ):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, route.Path("relative-source.bin"))
    assert caught.value.code == "download_failed"


def test_default_download_nonzero_curl_exit_is_download_failed(tmp_path):
    destination = tmp_path / "source.bin"

    def failing_run(_argv, **_kwargs):
        destination.write_bytes(b"partial bytes")
        return SimpleNamespace(returncode=22, stdout=b"", stderr=b"curl: secret-detail")

    with mock.patch.object(route.subprocess, "run", side_effect=failing_run):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)
    assert caught.value.code == "download_failed"
    assert "secret-detail" not in str(caught.value)


def test_default_download_timeout_is_download_failed(tmp_path):
    destination = tmp_path / "source.bin"

    def timing_out_run(argv, **_kwargs):
        raise route.subprocess.TimeoutExpired(cmd=argv, timeout=310.0)

    with mock.patch.object(route.subprocess, "run", side_effect=timing_out_run):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)
    assert caught.value.code == "download_failed"


def test_default_download_missing_output_file_is_download_failed(tmp_path):
    destination = tmp_path / "source.bin"
    with mock.patch.object(
        route.subprocess, "run",
        side_effect=_curl_run_writing(destination, payload=None),
    ):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)
    assert caught.value.code == "download_failed"


def test_default_download_zero_byte_file_is_download_failed(tmp_path):
    destination = tmp_path / "source.bin"
    with mock.patch.object(
        route.subprocess, "run",
        side_effect=_curl_run_writing(destination, payload=b""),
    ):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)
    assert caught.value.code == "download_failed"


def test_default_download_oversize_file_is_download_failed(tmp_path, monkeypatch):
    destination = tmp_path / "source.bin"
    monkeypatch.setattr(route, "MAX_BYTES", 4)
    with mock.patch.object(
        route.subprocess, "run",
        side_effect=_curl_run_writing(destination, payload=b"12345"),
    ):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)
    assert caught.value.code == "download_failed"


SEARCH_ITEMS = [
    {
        "song_id": 111,
        "name": "歌曲一",
        "ar": [{"name": "歌手A"}, {"name": "歌手B"}],
        "al": {"name": "专辑一"},
        "dt": 215000,
        "quality": "lossless",
        "url": "https://signed.example.com/never-return",
        "album_pic_url": "https://signed.example.com/cover",
        "fee": 1,
        "unknown": {"nested": "value"},
    },
    {
        "id": 222,
        "title": "歌曲二",
        "artist": "歌手C",
        "album_name": "专辑二",
        "duration": 180.5,
        "quality": "lossless",
    },
    {"song_id": 444, "name": "歌曲四", "duration": 215000},
    {"song_id": 0, "name": "无效一"},
    {"song_id": "333", "name": "无效二"},
    {"song_id": True, "name": "无效三"},
    {"name": "无 id"},
    "not-an-item",
]


def test_search_tracks_sanitizes_items_and_hides_raw_payload():
    calls = []

    def fake_http(url_text):
        calls.append(url_text)
        return {"ok": True, "code": 200, "data": SEARCH_ITEMS}

    deps = route.SaveDeps(config=_config(), http_json=fake_http)
    result = route.search_tracks("  周杰伦 晴天  ", deps=deps)
    assert result["ok"] is True
    assert result["code"] == "ok"
    assert set(result["data"]) == {"items", "keyword", "limit"}
    assert result["data"]["keyword"] == "周杰伦 晴天"
    assert result["data"]["limit"] == route.SEARCH_LIMIT
    items = result["data"]["items"]
    assert [item["song_id"] for item in items] == [111, 222, 444]
    assert set(items[0]) == {"song_id", "title", "artist", "album", "quality", "duration"}
    assert items[0] == {
        "song_id": 111,
        "title": "歌曲一",
        "artist": "歌手A / 歌手B",
        "album": "专辑一",
        "quality": "lossless",
        "duration": 215000,
    }
    assert items[1]["artist"] == "歌手C"
    assert items[1]["album"] == "专辑二"
    assert items[1]["duration"] == 180500
    assert items[2]["duration"] == 215000
    assert calls == [
        "http://127.0.0.1:8770/api/v1/search?keyword="
        "%E5%91%A8%E6%9D%B0%E4%BC%A6%20%E6%99%B4%E5%A4%A9&type=song&limit=20"
    ]
    rendered = json.dumps(result, ensure_ascii=False)
    assert "https://" not in rendered
    assert "album_pic_url" not in rendered
    assert "nested" not in rendered


def test_search_tracks_caps_items_to_limit():
    raw_items = [
        {"song_id": index + 1, "name": f"歌曲{index}"}
        for index in range(route.SEARCH_LIMIT + 5)
    ]
    deps = route.SaveDeps(
        config=_config(), http_json=lambda _url: {"ok": True, "data": raw_items},
    )
    result = route.search_tracks("关键词", deps=deps)
    assert result["ok"] is True
    assert len(result["data"]["items"]) == route.SEARCH_LIMIT


@pytest.mark.parametrize("keyword", ["", "   ", "x" * 121, None, 5, b"x", "长" * 121])
def test_search_tracks_rejects_empty_or_oversized_keyword(keyword):
    deps = route.SaveDeps(
        config=_config(),
        http_json=lambda _url: pytest.fail("service must not be called"),
    )
    result = route.search_tracks(keyword, deps=deps)
    assert result["ok"] is False
    assert result["code"] == "invalid_keyword"
    assert result["data"] == {}


def test_search_tracks_disabled_without_service_call():
    deps = route.SaveDeps(
        config=_config(FNMUSIC_MANUAL_SAVE_ENABLED="false"),
        http_json=lambda _url: pytest.fail("service must not be called"),
    )
    result = route.search_tracks("关键词", deps=deps)
    assert result["code"] == "disabled"


@pytest.mark.parametrize(
    "payload",
    [
        {"ok": False, "message": "blocked"},
        {"ok": True, "data": {"songs": []}},
        {"ok": True, "data": "not-a-list"},
        "not-a-dict",
    ],
)
def test_search_tracks_service_malformed_is_service_unavailable(payload):
    deps = route.SaveDeps(config=_config(), http_json=lambda _url: payload)
    result = route.search_tracks("关键词", deps=deps)
    assert result["code"] == "service_unavailable"
    assert result["data"] == {}


def test_search_tracks_transport_error_is_service_unavailable_and_redacted():
    def failing_http(_url):
        raise OSError("secret-service-detail")

    deps = route.SaveDeps(config=_config(), http_json=failing_http)
    result = route.search_tracks("关键词", deps=deps)
    assert result["code"] == "service_unavailable"
    assert "secret-service-detail" not in json.dumps(result)


def test_search_tracks_all_invalid_items_yield_empty_ok_list():
    deps = route.SaveDeps(
        config=_config(),
        http_json=lambda _url: {"ok": True, "data": [{"song_id": 0}, {"id": -1}, {}]},
    )
    result = route.search_tracks("关键词", deps=deps)
    assert result["ok"] is True
    assert result["data"]["items"] == []


def test_default_download_resumes_partial_transfer_on_second_attempt(tmp_path):
    destination = tmp_path / "source.bin"
    attempts = []

    def run(_argv, **kwargs):
        attempts.append(kwargs["input"])
        if len(attempts) == 1:
            destination.write_bytes(CURL_TEST_PAYLOAD[:5])
            return SimpleNamespace(returncode=56, stdout=b"", stderr=b"reset")
        destination.write_bytes(CURL_TEST_PAYLOAD)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with mock.patch.object(route.subprocess, "run", side_effect=run):
        result = route._default_download(SIGNED_URL, destination)

    assert len(attempts) == 2
    assert result == {"size": len(CURL_TEST_PAYLOAD), "md5": CURL_TEST_MD5}
    assert 'continue-at = "-"' in attempts[0].splitlines()
    assert 'continue-at = "-"' in attempts[1].splitlines()
    assert "retry = 3" not in attempts[1]
    assert destination.read_bytes() == CURL_TEST_PAYLOAD


def test_default_download_stops_retrying_after_timeout_then_success(tmp_path):
    destination = tmp_path / "source.bin"
    attempts = []

    def run(argv, **kwargs):
        attempts.append(kwargs)
        if len(attempts) == 1:
            raise route.subprocess.TimeoutExpired(cmd=argv, timeout=kwargs["timeout"])
        destination.write_bytes(CURL_TEST_PAYLOAD)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    with mock.patch.object(route.subprocess, "run", side_effect=run):
        result = route._default_download(SIGNED_URL, destination)

    assert len(attempts) == 2
    assert result == {"size": len(CURL_TEST_PAYLOAD), "md5": CURL_TEST_MD5}
    assert attempts[1]["timeout"] == route.DOWNLOAD_TIMEOUT + 10.0


def test_default_download_bounds_attempts_when_no_progress(tmp_path):
    destination = tmp_path / "source.bin"
    attempts = []

    def run(_argv, **kwargs):
        attempts.append(kwargs["input"])
        destination.write_bytes(CURL_TEST_PAYLOAD[:5])
        return SimpleNamespace(returncode=56, stdout=b"", stderr=b"reset")

    with mock.patch.object(route.subprocess, "run", side_effect=run):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)

    assert caught.value.code == "download_failed"
    assert len(attempts) == route.DOWNLOAD_MAX_ATTEMPTS
    assert all('continue-at = "-"' in config for config in attempts)


def test_default_download_stops_when_total_budget_is_exhausted(tmp_path, monkeypatch):
    destination = tmp_path / "source.bin"
    attempts = []
    clock = {"now": 1000.0}
    monkeypatch.setattr(route.time, "monotonic", lambda: clock["now"])

    def run(_argv, **kwargs):
        attempts.append(kwargs["input"])
        clock["now"] += route.DOWNLOAD_SUBPROCESS_TIMEOUT
        return SimpleNamespace(returncode=56, stdout=b"", stderr=b"reset")

    with mock.patch.object(route.subprocess, "run", side_effect=run):
        with pytest.raises(route._SaveFailure) as caught:
            route._default_download(SIGNED_URL, destination)

    assert caught.value.code == "download_failed"
    assert len(attempts) == 1


def test_is_configured_path_rejects_real_nul_lf_cr():
    assert route._is_configured_path("/vol1/music") is True
    for control in (chr(0), chr(10), chr(13)):
        assert route._is_configured_path("/vol1/mu" + control + "sic") is False
    assert route._is_configured_path("relative/path") is False
    # A literal backslash-x sequence is a valid POSIX path character, not a control byte.
    assert route._is_configured_path("/vol1/literal" + chr(92) + "x00") is True
