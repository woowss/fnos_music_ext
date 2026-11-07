"""Synthetic tests for bounded, read-only playlist previews."""
from __future__ import annotations

import copy
import importlib.util
import json
import logging
import sys
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

SERVICE_DIR = Path(__file__).resolve().parents[2] / "musicbox-service"
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))

import netease_ext
import playback
import runner

_PRIVATE_ERROR_MARKER = "SYNTHETIC_PRIVATE_ERROR_MARKER"


class ForbiddenAttemptRecorder:
    def __init__(self):
        self.attempts: list[str] = []

    def trip(self, target: str):
        self.attempts.append(target)
        raise AssertionError(f"forbidden preview operation attempted: {target}")


def _next(script: list[Any]) -> Any:
    value = script[0]
    if len(script) > 1:
        del script[0]
    return value


def _playlist(
    track_ids: list[Any],
    *,
    track_count: int | None = None,
    version: int | None = 7,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    data: dict[str, Any] = {
        "code": 200,
        "playlist": {
            "trackCount": len(track_ids) if track_count is None else track_count,
            "trackIds": copy.deepcopy(track_ids),
        },
    }
    if version is not None:
        data["playlist"]["updateTime"] = version
    if extra:
        data["playlist"].update(copy.deepcopy(extra))
    return data


def _assert_private_marker_redacted(result: dict[str, Any], caplog):
    assert _PRIVATE_ERROR_MARKER not in json.dumps(result)
    assert _PRIVATE_ERROR_MARKER not in caplog.text


class FakeNetEaseAPI:
    """Narrow fake: preview may only read account, playlist, and song details."""

    def __init__(
        self,
        *,
        accounts: list[Any] | None = None,
        playlists: list[Any] | None = None,
        missing_details: set[int] | None = None,
        failed_detail_ids: set[int] | None = None,
        mapping_failed_ids: set[int] | None = None,
        detail_responses: list[Any] | None = None,
    ):
        self.accounts = accounts or [{"code": 200, "account": {"id": 9001}, "profile": None}]
        self.playlists = playlists or [_playlist([11])]
        self.missing_details = missing_details or set()
        self.failed_detail_ids = failed_detail_ids or set()
        self.mapping_failed_ids = mapping_failed_ids or set()
        self.detail_responses = detail_responses or []
        self.recorder: ForbiddenAttemptRecorder | None = None
        self.account_calls = 0
        self.playlist_calls: list[tuple[str, dict[str, Any]]] = []
        self.detail_calls: list[list[int]] = []

    def get_account_info(self):
        self.account_calls += 1
        value = _next(self.accounts)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    def eapi_request(self, path: str, params: dict[str, Any]):
        assert path == "/api/v6/playlist/detail"
        assert params == {"id": 42, "n": 1000, "s": 0}
        self.playlist_calls.append((path, copy.deepcopy(params)))
        value = _next(self.playlists)
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)

    def request(self, method: str, path: str, params: dict[str, Any]):
        assert method == "POST"
        assert path == "/weapi/v3/song/detail"
        ids = json.loads(params["ids"])
        assert ids == list(dict.fromkeys(ids))
        assert len(ids) <= 100
        assert json.loads(params["c"]) == [{"id": song_id} for song_id in ids]
        self.detail_calls.append(ids)
        if self.failed_detail_ids.intersection(ids):
            raise RuntimeError(_PRIVATE_ERROR_MARKER)
        if self.detail_responses:
            response = _next(self.detail_responses)
            if isinstance(response, BaseException):
                raise response
            return copy.deepcopy(response)
        songs = []
        for song_id in reversed(ids):
            if song_id in self.missing_details:
                continue
            row: dict[str, Any] = {
                "id": song_id,
                "name": f"Synthetic {song_id}",
                "ar": [{"name": f"Artist {song_id}"}],
                "al": {"name": f"Album {song_id}"},
                "dt": 123000,
            }
            if song_id in self.mapping_failed_ids:
                row["dt"] = object()
            songs.append(row)
        return {"code": 200, "songs": songs}

    def songs_url(self, *_args, **_kwargs):
        self._trip("fake_api.songs_url")

    def songs_detail(self, *_args, **_kwargs):
        self._trip("fake_api.songs_detail")

    def song_lyric(self, *_args, **_kwargs):
        self._trip("fake_api.song_lyric")

    def _trip(self, target: str):
        assert self.recorder is not None, "forbidden API sentinel recorder was not installed"
        self.recorder.trip(target)


@pytest.fixture(autouse=True)
def disallowed_attempt_guard(monkeypatch):
    """Record prohibited access attempts and assert none were swallowed by production."""
    recorder = ForbiddenAttemptRecorder()

    def blocked(target: str):
        def record(*_args, **_kwargs):
            recorder.trip(target)
        return record

    try:
        import httpx

        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked("httpx.sync_transport"))
        monkeypatch.setattr(
            httpx.AsyncHTTPTransport, "handle_async_request", blocked("httpx.async_transport"),
        )
    except ImportError:
        pass
    try:
        import requests

        monkeypatch.setattr(requests.Session, "request", blocked("requests.transport"))
    except ImportError:
        pass
    monkeypatch.setattr(urllib.request, "urlopen", blocked("urllib.urlopen"))
    monkeypatch.setattr(runner, "run_musicbox", blocked("runner.run_musicbox"))
    monkeypatch.setattr(playback, "get_song_url_async", blocked("playback.get_song_url_async"))
    monkeypatch.setattr(netease_ext, "get_song_url", blocked("netease_ext.get_song_url"))
    monkeypatch.setattr(netease_ext, "_url_cache", _PlaybackCacheTripwire(recorder))
    app_module = sys.modules.get("musicbox_preview_test_app")
    if app_module is not None:
        monkeypatch.setattr(
            app_module, "get_song_url_async", blocked("app.get_song_url_async"),
        )
    yield recorder
    assert recorder.attempts == [], f"forbidden preview operations attempted: {recorder.attempts}"


class _PlaybackCacheTripwire(dict):
    def __init__(self, recorder: ForbiddenAttemptRecorder):
        super().__init__()
        self.recorder = recorder

    def get(self, *_args, **_kwargs):
        self.recorder.trip("playback_cache.read")

    def __setitem__(self, _key, _value):
        self.recorder.trip("playback_cache.write")


@pytest.fixture
def install_fake_api(monkeypatch, disallowed_attempt_guard):
    def install(api: FakeNetEaseAPI):
        api.recorder = disallowed_attempt_guard
        monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: api)
        return api

    return install


@pytest.fixture(scope="module")
def musicbox_app():
    """Load the service app only for route tests and remove handlers it adds."""
    logger_snapshots = {
        name: list(logger.handlers)
        for name, logger in logging.Logger.manager.loggerDict.items()
        if isinstance(logger, logging.Logger)
    }
    root_handlers = list(logging.root.handlers)
    module_name = "musicbox_preview_test_app"
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_DIR / "app.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        for name, logger in logging.Logger.manager.loggerDict.items():
            if not isinstance(logger, logging.Logger):
                continue
            previous = logger_snapshots.get(name, [])
            for handler in list(logger.handlers):
                if handler not in previous:
                    logger.removeHandler(handler)
                    handler.close()
        for handler in list(logging.root.handlers):
            if handler not in root_handlers:
                logging.root.removeHandler(handler)
                handler.close()
        sys.modules.pop(module_name, None)


def test_stable_empty_playlist_is_complete_and_never_import_ready(install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist([])]))

    result = netease_ext.user_playlist_preview(42)

    assert result["ok"] is True
    assert result["status"] == "complete"
    assert result["complete"] is True
    assert result["metadata_complete"] is True
    assert result["items"] == []
    assert result["import_ready"] is False
    assert api.detail_calls == []


def test_duplicate_ids_keep_order_positions_and_map_out_of_order_details(install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist([{"id": 11}, 12, 11])]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "complete"
    assert [row["position"] for row in result["items"]] == [1, 2, 3]
    assert [row["song_id"] for row in result["items"]] == [11, 12, 11]
    assert [row["song"]["name"] for row in result["items"]] == [
        "Synthetic 11", "Synthetic 12", "Synthetic 11",
    ]
    assert api.detail_calls == [[11, 12]]
    assert result["import_ready"] is False


@pytest.mark.parametrize(("unique_count", "expected_batches"), [(100, [100]), (101, [100, 1])])
def test_details_are_batched_at_most_100(unique_count, expected_batches, install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist(list(range(1, unique_count + 1)))]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "complete"
    assert [len(batch) for batch in api.detail_calls] == expected_batches
    assert all(len(batch) <= 100 for batch in api.detail_calls)


def test_exact_1000_position_boundary_is_supported(install_fake_api):
    ids = list(range(1, 1001))
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist(ids)]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "complete"
    assert result["track_count"] == result["position_count"] == 1000
    assert [len(batch) for batch in api.detail_calls] == [100] * 10
    assert result["import_ready"] is False


def test_more_than_1000_positions_is_unsupported_without_detail_fetch(install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist(list(range(1, 1002)))]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "unsupported"
    assert result["error_code"] == "POSITION_LIMIT_EXCEEDED"
    assert api.detail_calls == []


@pytest.mark.parametrize(
    ("raw", "expected_status", "expected_error"),
    [
        ({"code": 200, "playlist": {"trackCount": -1, "trackIds": [], "updateTime": 1}}, "unsupported", "UNSUPPORTED_SOURCE"),
        ({"code": 200, "playlist": {"trackCount": True, "trackIds": [], "updateTime": 1}}, "unsupported", "UNSUPPORTED_SOURCE"),
        ({"code": 200, "playlist": {"trackCount": 0, "trackIds": []}}, "unsupported", "UNSUPPORTED_SOURCE"),
        ({"code": 200, "playlist": {"trackCount": 0, "updateTime": 1}}, "unsupported", "UNSUPPORTED_SOURCE"),
        ({"code": 301, "playlist": {"trackCount": 0, "trackIds": [], "updateTime": 1}}, "upstream_error", "SOURCE_READ_FAILED"),
    ],
)
def test_invalid_count_missing_version_and_unsupported_structure_have_exact_results(
    raw, expected_status, expected_error, install_fake_api,
):
    install_fake_api(FakeNetEaseAPI(playlists=[raw]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == expected_status
    assert result["error_code"] == expected_error
    assert result["import_ready"] is False


@pytest.mark.parametrize(
    ("track_ids", "declared_count"),
    [([11, 12, 13], 2), ([11], 2)],
)
def test_valid_ids_with_position_count_mismatch_report_count_mismatch(
    track_ids, declared_count, install_fake_api,
):
    install_fake_api(FakeNetEaseAPI(
        playlists=[_playlist(track_ids, track_count=declared_count)],
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "incomplete"
    assert result["error_code"] == "TRACK_COUNT_MISMATCH"
    assert result["track_count"] == declared_count
    assert result["position_count"] == len(track_ids)
    assert [row["song_id"] for row in result["items"]] == track_ids


def test_invalid_id_keeps_a_position_and_is_reported_only_when_counts_align(install_fake_api):
    install_fake_api(FakeNetEaseAPI(playlists=[_playlist([11, "bad", 12])]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "incomplete"
    assert result["error_code"] == "INVALID_TRACK_ID"
    assert [(row["position"], row["status"]) for row in result["items"]] == [
        (1, "ok"), (2, "invalid_id"), (3, "ok"),
    ]


@pytest.mark.parametrize(
    ("account", "expected_status", "expected_error"),
    [
        ({"code": 301, "account": None, "profile": None}, "not_logged_in", None),
        ({"code": -1, "account": None, "profile": None}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": None, "profile": None}, "not_logged_in", None),
        ({"code": 200, "account": [], "profile": None}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": "", "profile": None}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": 0, "profile": None}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": False, "profile": None}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": None, "profile": []}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": None, "profile": ""}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": None, "profile": 0}, "upstream_error", "SOURCE_READ_FAILED"),
        ({"code": 200, "account": None, "profile": False}, "upstream_error", "SOURCE_READ_FAILED"),
    ],
)
def test_account_auth_result_classification(account, expected_status, expected_error, install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(accounts=[account]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == expected_status
    assert result.get("error_code") == expected_error
    assert result["import_ready"] is False
    if expected_status == "not_logged_in":
        assert result["logged_in"] is False
        assert api.playlist_calls == []
    elif expected_status == "upstream_error":
        assert api.playlist_calls == []


def test_account_api_exception_is_upstream_error_without_private_text(install_fake_api, caplog):
    install_fake_api(FakeNetEaseAPI(accounts=[RuntimeError(_PRIVATE_ERROR_MARKER)]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "upstream_error"
    assert result["error_code"] == "SOURCE_READ_FAILED"
    _assert_private_marker_redacted(result, caplog)
    assert "RuntimeError" in caplog.text


def test_initial_playlist_exception_is_redacted_in_response_and_logs(install_fake_api, caplog):
    install_fake_api(FakeNetEaseAPI(
        playlists=[RuntimeError(_PRIVATE_ERROR_MARKER)],
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "upstream_error"
    assert result["error_code"] == "SOURCE_READ_FAILED"
    _assert_private_marker_redacted(result, caplog)
    assert "RuntimeError" in caplog.text


def test_missing_and_mapping_failed_details_are_distinct(install_fake_api, caplog):
    api = install_fake_api(FakeNetEaseAPI(
        playlists=[_playlist([11, 12, 13])],
        missing_details={12},
        mapping_failed_ids={13},
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "complete"
    assert result["metadata_complete"] is False
    assert [row["error_code"] for row in result["items"]] == [
        None, "DETAIL_NOT_FOUND", "DETAIL_MAPPING_FAILED",
    ]
    assert api.detail_calls == [[11, 12, 13]]
    _assert_private_marker_redacted(result, caplog)


def test_failed_detail_batch_is_distinct_and_private_marker_is_not_returned(install_fake_api, caplog):
    api = install_fake_api(FakeNetEaseAPI(
        playlists=[_playlist([11])],
        failed_detail_ids={11},
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "complete"
    assert result["metadata_complete"] is False
    assert result["items"][0]["error_code"] == "DETAILS_FETCH_FAILED"
    _assert_private_marker_redacted(result, caplog)
    assert "RuntimeError" in caplog.text
    assert api.detail_calls == [[11]]


@pytest.mark.parametrize(
    ("detail_response", "expected_error"),
    [
        ({"code": -1, "songs": [], "message": _PRIVATE_ERROR_MARKER}, "DETAILS_FETCH_FAILED"),
        ({"code": 200, "songs": None, "message": _PRIVATE_ERROR_MARKER}, "DETAILS_FETCH_FAILED"),
        ({"code": 200, "songs": {"bad": "structure"}, "message": _PRIVATE_ERROR_MARKER}, "DETAILS_FETCH_FAILED"),
        ({"code": 200, "songs": []}, "DETAIL_NOT_FOUND"),
    ],
)
def test_detail_error_responses_and_empty_success_are_distinct(
    detail_response, expected_error, install_fake_api, caplog,
):
    install_fake_api(FakeNetEaseAPI(
        playlists=[_playlist([11])],
        detail_responses=[detail_response],
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "complete"
    assert result["metadata_complete"] is False
    assert result["items"][0]["error_code"] == expected_error
    _assert_private_marker_redacted(result, caplog)
    if expected_error == "DETAILS_FETCH_FAILED":
        assert "RuntimeError" in caplog.text


@pytest.mark.parametrize(
    "after",
    [
        _playlist([11], version=8),
        _playlist([11], track_count=2, version=7),
        _playlist([12], version=7),
    ],
)
def test_source_version_or_sequence_change_prevents_complete_result(after, install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist([11]), after]))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "incomplete"
    assert result["error_code"] == "SOURCE_CHANGED_OR_RECHECK_FAILED"
    assert result["import_ready"] is False


def test_failed_source_recheck_prevents_complete_result(install_fake_api, caplog):
    api = install_fake_api(FakeNetEaseAPI(
        playlists=[_playlist([11]), RuntimeError(_PRIVATE_ERROR_MARKER)],
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "incomplete"
    assert result["error_code"] == "SOURCE_CHANGED_OR_RECHECK_FAILED"
    _assert_private_marker_redacted(result, caplog)
    assert "RuntimeError" in caplog.text
    assert api.playlist_calls and len(api.playlist_calls) == 2


def test_account_change_during_preview_prevents_complete_result(install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(
        accounts=[
            {"code": 200, "account": {"id": 9001}, "profile": None},
            {"code": 200, "account": {"id": 9002}, "profile": None},
        ],
        playlists=[_playlist([11])],
    ))

    result = netease_ext.user_playlist_preview(42)

    assert result["status"] == "incomplete"
    assert result["error_code"] == "SOURCE_CHANGED_OR_RECHECK_FAILED"


def test_http_preview_route_and_invalid_id_validation(musicbox_app, install_fake_api):
    api = install_fake_api(FakeNetEaseAPI(playlists=[_playlist([11])]))

    with TestClient(musicbox_app.app) as client:
        valid = client.get("/api/v1/user/playlists/42/preview")
        invalid = client.get("/api/v1/user/playlists/0/preview")

    assert valid.status_code == 200
    assert valid.json()["status"] == "complete"
    assert valid.json()["import_ready"] is False
    assert invalid.status_code == 400
    assert len(api.playlist_calls) == 2  # before/after for the valid request only


def test_existing_user_playlist_routes_keep_their_response_shapes(musicbox_app, monkeypatch):
    monkeypatch.setattr(
        musicbox_app, "fetch_user_playlists", lambda _limit: {"playlists": [], "uid": 9001},
    )
    monkeypatch.setattr(musicbox_app, "playlist_track_ids", lambda _playlist_id: [11])
    monkeypatch.setattr(musicbox_app, "batch_song_details", lambda _ids: [{"song_id": 11, "fee": 1}])
    monkeypatch.setattr(musicbox_app, "vip_entitled", lambda: False)

    with TestClient(musicbox_app.app) as client:
        playlists = client.get("/api/v1/user/playlists")
        tracks = client.get("/api/v1/user/playlists/42/tracks")

    assert playlists.status_code == 200
    assert playlists.json() == {
        "ok": True, "data": [], "account_uid": 9001, "logged_in": True,
    }
    assert tracks.status_code == 200
    assert tracks.json() == {
        "ok": True, "data": [{"song_id": 11, "fee": 1, "needs_vip": True}],
    }
