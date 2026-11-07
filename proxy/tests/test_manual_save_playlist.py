"""Fully offline tests for the ordered playlist save engine.

Layer 1 exercises ``proxy.manual_save_playlist.save_playlist`` with an injected
fake executor only. Layer 2 wires the real ``manual_save_route.save_track`` with
every ``SaveDeps`` seam replaced by a synthetic fake: no network, no real store,
no ffmpeg, no NAS paths.
"""
from __future__ import annotations

import hashlib
import json
import re
import stat
import threading
from types import SimpleNamespace

import pytest

from proxy import manual_save_core
from proxy import manual_save_playlist as playlist
from proxy import manual_save_route as route

SECRET_TOKEN = "TOPSECRET-COOKIE-123"
MEDIA_SECRET = "TOPSECRET-MEDIA-456"


# ---------------------------------------------------------------------------
# Synthetic executor envelopes (mirror the route envelope shape exactly)
# ---------------------------------------------------------------------------

def _published(song_id=1):
    return {
        "ok": True,
        "code": "ok",
        "message": "",
        "data": {
            "status": "published",
            "song_id": song_id,
            "title": f"曲目{song_id}",
            "artist": f"歌手{song_id}",
            "album": "专辑",
            "quality_requested": "jymaster",
            "quality_actual": "jymaster",
            "quality_actual_status": "reported",
            "quality_fallback": False,
            "source_type": "flac",
            "format": "flac",
            "size": 1234,
            "md5": "a" * 32,
            "basename": f"歌手{song_id} - 曲目{song_id}.flac",
            "target_dir": "/target",
            "cover_error": "",
            "tagged": True,
            "cover_embedded": False,
            "published_size": 1300,
            "published_md5": "b" * 32,
            "published_sha256": "c" * 64,
            "title_status": "tagged",
            "artist_status": "tagged",
            "album_status": "tagged",
            "lyricist": "",
            "lyricist_status": "unavailable",
            "composer": "",
            "composer_status": "unavailable",
            "cover_status": "unavailable",
            "lyric_status": "unavailable",
        },
    }


def _conflict(song_id=1):
    data = dict(_published(song_id)["data"])
    data["status"] = "conflict"
    data["cover_status"] = "not_published"
    data["cover_error"] = ""
    data["lyric_status"] = "not_attempted"
    return {"ok": False, "code": "conflict", "message": "目标文件已存在", "data": data}


def _route_failure(code, message="固定失败消息"):
    return {"ok": False, "code": code, "message": message, "data": {}}


class _Executor:
    """Records every dispatch and delegates to a synthetic result function."""

    def __init__(self, function):
        self.function = function
        self.calls = []

    def __call__(self, song_id):
        self.calls.append(song_id)
        return self.function(song_id)


class _FakeClock:
    """Returns the supplied times in order, then repeats the last one forever."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if len(self.values) > 1:
            return self.values.pop(0)
        return self.values[0]


def _publish_executor(*ids):
    published = set(ids)

    def result(song_id):
        if song_id in published:
            return _published(song_id)
        raise AssertionError(f"unexpected dispatch: {song_id}")

    return _Executor(result)


def _assert_lock_free():
    assert playlist._PLAYLIST_LOCK.locked() is False


# ---------------------------------------------------------------------------
# Layer 1: input validation (no executor dispatch is allowed)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "song_ids", [pytest.param([], id="list"), pytest.param((), id="tuple")]
)
def test_empty_playlist_is_invalid_request(song_ids):
    executor = _Executor(lambda song_id: pytest.fail("empty playlist dispatched"))
    result = playlist.save_playlist(song_ids, save_one=executor)
    assert result == {
        "ok": False,
        "code": "invalid_request",
        "message": "请求参数无效",
        "data": {},
    }
    assert executor.calls == []
    _assert_lock_free()


@pytest.mark.parametrize(
    "song_ids",
    [
        pytest.param("1", id="string"),
        pytest.param(1, id="int"),
        pytest.param(None, id="none"),
        pytest.param({"a": 1}, id="dict"),
        pytest.param(range(3), id="range"),
        pytest.param(iter([1]), id="iterator"),
        pytest.param(object(), id="object"),
    ],
)
def test_non_sequence_input_is_invalid_request(song_ids):
    executor = _Executor(lambda song_id: pytest.fail("non-sequence dispatched"))
    result = playlist.save_playlist(song_ids, save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "invalid_request"
    assert result["data"] == {}
    assert executor.calls == []


@pytest.mark.parametrize(
    "song_ids",
    [
        pytest.param([1, 0], id="zero"),
        pytest.param([1, -2], id="negative"),
        pytest.param([1, "2"], id="string_id"),
        pytest.param([1, True], id="bool"),
        pytest.param([1, 1.5], id="float"),
        pytest.param([1, []], id="list_entry"),
        pytest.param([1, {}], id="dict_entry"),
        pytest.param([None, False], id="bool_after_none"),
        pytest.param([5, 6, 7, -1], id="invalid_last"),
    ],
)
def test_invalid_song_id_entries_reject_whole_call(song_ids):
    executor = _Executor(lambda song_id: pytest.fail("invalid id list dispatched"))
    result = playlist.save_playlist(song_ids, save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "invalid_song_id"
    assert result["message"] == "song_id 必须是正整数"
    assert result["data"] == {}
    assert executor.calls == []
    _assert_lock_free()


def test_over_max_tracks_rejected():
    executor = _Executor(lambda song_id: pytest.fail("over-limit dispatched"))
    result = playlist.save_playlist(
        list(range(1, playlist.PLAYLIST_MAX_TRACKS + 2)), save_one=executor
    )
    assert result["ok"] is False
    assert result["code"] == "too_many_positions"
    assert result["data"] == {}
    assert executor.calls == []
    _assert_lock_free()


def test_exactly_max_tracks_accepted():
    song_ids = tuple(range(1, playlist.PLAYLIST_MAX_TRACKS + 1))
    executor = _publish_executor(*song_ids)
    result = playlist.save_playlist(song_ids, save_one=executor)
    assert result["ok"] is True
    assert result["code"] == "ok"
    assert result["data"]["positions_total"] == playlist.PLAYLIST_MAX_TRACKS
    assert result["data"]["counts"]["published"] == playlist.PLAYLIST_MAX_TRACKS
    assert executor.calls == list(song_ids)


@pytest.mark.parametrize(
    "start_position",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param("1", id="string"),
        pytest.param(1.5, id="float"),
        pytest.param(True, id="bool"),
        pytest.param(None, id="none"),
        pytest.param(2.0, id="float_integral"),
    ],
)
def test_invalid_start_position_rejected(start_position):
    executor = _Executor(lambda song_id: pytest.fail("bad start dispatched"))
    result = playlist.save_playlist(
        [1, 2], save_one=executor, start_position=start_position
    )
    assert result["ok"] is False
    assert result["code"] == "invalid_request"
    assert result["data"] == {}
    assert executor.calls == []


@pytest.mark.parametrize(
    "budget_seconds",
    [
        pytest.param(0, id="zero"),
        pytest.param(-1, id="negative"),
        pytest.param(0.0, id="zero_float"),
        pytest.param(3600.5, id="above_max"),
        pytest.param(3601, id="above_max_int"),
        pytest.param(float("inf"), id="inf"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(True, id="bool"),
        pytest.param("900", id="string"),
        pytest.param(None, id="none"),
    ],
)
def test_invalid_budget_rejected(budget_seconds):
    executor = _Executor(lambda song_id: pytest.fail("bad budget dispatched"))
    result = playlist.save_playlist(
        [1, 2], save_one=executor, budget_seconds=budget_seconds
    )
    assert result["ok"] is False
    assert result["code"] == "invalid_request"
    assert result["message"] == "请求参数无效"
    assert result["data"] == {}
    assert executor.calls == []


@pytest.mark.parametrize(
    "kwargs, expect_code",
    [
        pytest.param({"now": 1}, "invalid_request", id="now_int"),
        pytest.param({"now": "clock"}, "invalid_request", id="now_string"),
        pytest.param({"cancelled": 1}, "invalid_request", id="cancelled_int"),
        pytest.param({"cancelled": "flag"}, "invalid_request", id="cancelled_string"),
        pytest.param({"save_one": None}, "invalid_request", id="save_one_none"),
        pytest.param({"save_one": "save"}, "invalid_request", id="save_one_string"),
    ],
)
def test_invalid_now_or_cancelled_rejected(kwargs, expect_code):
    executor = _Executor(lambda song_id: pytest.fail("bad seam dispatched"))
    call_kwargs = dict(kwargs)
    call_kwargs.setdefault("save_one", executor)
    result = playlist.save_playlist([1, 2], **call_kwargs)
    assert result["ok"] is False
    assert result["code"] == expect_code
    assert result["data"] == {}
    assert executor.calls == []
    _assert_lock_free()


# ---------------------------------------------------------------------------
# Layer 1: envelope and item shape
# ---------------------------------------------------------------------------

def test_all_published_completes_ok():
    executor = _publish_executor(11, 12, 13)
    result = playlist.save_playlist([11, 12, 13], save_one=executor)
    assert result["ok"] is True
    assert result["code"] == "ok"
    assert result["message"] == ""
    data = result["data"]
    assert tuple(data) == (
        "status",
        "stop_reason",
        "start_position",
        "next_position",
        "positions_total",
        "counts",
        "items",
        "budget_seconds",
        "elapsed_seconds",
    )
    assert data["status"] == "completed"
    assert data["stop_reason"] == "input_exhausted"
    assert data["start_position"] == 1
    assert data["next_position"] is None
    assert data["positions_total"] == 3
    assert data["counts"] == {
        "total": 3,
        "published": 3,
        "conflict": 0,
        "failed": 0,
        "skipped": 0,
        "not_executed": 0,
    }
    assert data["budget_seconds"] == playlist.PLAYLIST_TOTAL_BUDGET_SECONDS
    assert isinstance(data["elapsed_seconds"], float)
    assert data["elapsed_seconds"] >= 0.0
    assert [item["position"] for item in data["items"]] == [1, 2, 3]
    assert [item["song_id"] for item in data["items"]] == [11, 12, 13]
    for item in data["items"]:
        assert item["status"] == "published"
        assert item["code"] == "ok"
        assert item["message"] == ""
        assert item["duplicate_of_position"] is None
        assert item["published_size"] > 0
        assert len(item["published_md5"]) == 32
        assert len(item["published_sha256"]) == 64
    assert executor.calls == [11, 12, 13]


def test_item_field_set_is_exact():
    assert len(playlist.PLAYLIST_ITEM_FIELDS) == 22
    assert playlist.PLAYLIST_ITEM_FIELDS == (
        "position",
        "song_id",
        "status",
        "code",
        "message",
        "title",
        "artist",
        "basename",
        "format",
        "quality_requested",
        "quality_actual",
        "quality_actual_status",
        "quality_fallback",
        "size",
        "md5",
        "published_size",
        "published_md5",
        "published_sha256",
        "cover_status",
        "cover_error",
        "lyric_status",
        "duplicate_of_position",
    )

    def result(song_id):
        if song_id == 1:
            return _published(1)
        if song_id == 2:
            return _conflict(2)
        if song_id == 3:
            return _route_failure("download_failed")
        return _route_failure("disabled")

    executor = _Executor(result)
    result_envelope = playlist.save_playlist(
        [1, None, 2, 3, 4], save_one=executor
    )
    items = result_envelope["data"]["items"]
    assert [item["status"] for item in items] == [
        "published",
        "skipped",
        "conflict",
        "failed",
        "failed",
    ]
    for item in items:
        assert tuple(item) == playlist.PLAYLIST_ITEM_FIELDS
        assert len(item) == 22
    assert executor.calls == [1, 2, 3, 4]


def test_executor_data_is_whitelisted_and_normalized():
    def result(song_id):
        if song_id == 11:
            envelope = _published(11)
            envelope["data"].update({
                "title": "T" * 600,
                "artist": 123,
                "basename": "B" * 300,
                "format": "F" * 300,
                "quality_requested": "Q" * 300,
                "quality_actual": "hires",
                "quality_actual_status": "reported",
                "quality_fallback": 1,
                "size": -5,
                "md5": "C" * 32,
                "published_size": 0,
                "published_md5": "D" * 32,
                "published_sha256": "A" * 64,
                "cover_status": "embedded",
                "cover_error": "url_invalid",
                "lyric_status": "published",
                "signed_url": "https://signed.example.com/x?token=" + SECRET_TOKEN,
                "cookie": "SESSION=" + SECRET_TOKEN,
                "target_dir": "/target",
                "message": "M" * 200,
            })
            return envelope
        if song_id == 12:
            envelope = _published(12)
            envelope["data"].update({
                "title": "标题",
                "artist": "歌手",
                "basename": "",
                "format": "",
                "quality_requested": "",
                "quality_actual": None,
                "quality_actual_status": ["reported"],
                "quality_fallback": True,
                "size": True,
                "md5": 123,
                "published_size": 5,
                "published_md5": 123,
                "published_sha256": 123,
                "cover_status": ["error"],
                "cover_error": 456,
                "lyric_status": 123,
                "signed_url": "https://signed.example.com/y",
            })
            return envelope
        if song_id == 13:
            envelope = _published(13)
            envelope["data"].update({
                "cover_status": "error",
                "cover_error": "embed_failed",
                "lyric_status": "error",
                "published_size": 10,
                "published_md5": "E" * 32,
                "published_sha256": "E" * 64,
            })
            return envelope
        return _route_failure("download_failed", message="M" * 200)

    executor = _Executor(result)
    envelope = playlist.save_playlist([11, 12, 13, 14], save_one=executor)
    items = envelope["data"]["items"]

    first = items[0]
    assert first["status"] == "published"
    assert first["title"] == "T" * 512
    assert first["artist"] == ""
    assert first["basename"] == "B" * 255
    assert first["format"] == "F" * 255
    assert first["quality_requested"] == "Q" * 120
    assert first["quality_actual"] == "hires"
    assert first["quality_actual_status"] == "reported"
    assert first["quality_fallback"] is False  # only literal True counts
    assert first["size"] == 0
    assert first["md5"] == "c" * 32
    assert first["published_size"] == 0
    assert first["published_md5"] == "d" * 32
    assert first["published_sha256"] == ""
    assert first["cover_status"] == "embedded"
    assert first["cover_error"] == ""
    assert first["lyric_status"] == "published"
    assert first["message"] == ""

    second = items[1]
    assert second["status"] == "published"
    assert second["quality_actual"] == ""
    assert second["quality_actual_status"] == "unknown"
    assert second["quality_fallback"] is True
    assert second["size"] == 0
    assert second["md5"] == ""
    assert second["published_size"] == 5
    assert second["published_md5"] == ""
    assert second["published_sha256"] == ""
    assert second["cover_status"] == "not_attempted"
    assert second["cover_error"] == ""
    assert second["lyric_status"] == "not_attempted"

    third = items[2]
    assert third["status"] == "published"
    assert third["cover_status"] == "error"
    assert third["cover_error"] == "embed_failed"
    assert third["lyric_status"] == "error"
    assert third["published_size"] == 10
    assert third["published_md5"] == "e" * 32
    assert third["published_sha256"] == "e" * 64

    fourth = items[3]
    assert fourth["status"] == "failed"
    assert fourth["code"] == "download_failed"
    assert fourth["message"] == playlist._MESSAGES["download_failed"]
    assert fourth["title"] == ""
    assert fourth["published_size"] == 0

    rendered = json.dumps(envelope, ensure_ascii=False)
    assert "https://" not in rendered
    assert SECRET_TOKEN not in rendered
    assert "signed_url" not in rendered
    assert "cookie" not in rendered


# ---------------------------------------------------------------------------
# Layer 1: failure classification
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "malformed",
    [
        pytest.param(None, id="none"),
        pytest.param([], id="list"),
        pytest.param("ok", id="string"),
        pytest.param({"ok": "true", "code": "ok", "data": {}}, id="ok_not_bool"),
        pytest.param({"ok": 1, "code": "ok", "data": {}}, id="ok_int"),
        pytest.param(
            {"ok": True, "code": "partial", "data": {}},
            id="ok_true_non_ok_code",
        ),
        pytest.param(
            {"ok": False, "code": "made_up_code", "data": {}},
            id="unknown_code",
        ),
        pytest.param({"ok": False, "code": 500, "data": {}}, id="code_not_str"),
        pytest.param(
            {
                "ok": True,
                "code": "ok",
                "data": [],
                "message": "https://signed.example.com/x?token=" + SECRET_TOKEN,
            },
            id="success_data_not_dict",
        ),
        pytest.param(
            {"ok": False, "code": "download_failed", "data": []},
            id="failure_data_not_dict",
        ),
    ],
)
def test_executor_malformed_results_abort_as_internal(malformed):
    executor = _Executor(lambda song_id: malformed)
    result = playlist.save_playlist([1, 2], save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "internal"
    assert result["message"] == "内部错误"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "internal"
    assert [item["status"] for item in data["items"]] == [
        "failed",
        "not_executed",
    ]
    assert data["items"][0]["code"] == "internal"
    assert data["items"][0]["message"] == "内部错误"
    assert data["next_position"] == 2
    assert executor.calls == [1]
    rendered = json.dumps(result, ensure_ascii=False)
    assert "https://" not in rendered
    assert SECRET_TOKEN not in rendered


def test_executor_exception_aborts_as_internal():
    def explode(song_id):
        raise RuntimeError(
            "boom https://signed.example.com/x?token=" + SECRET_TOKEN
        )

    executor = _Executor(explode)
    result = playlist.save_playlist([1, 2, 3], save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "internal"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "internal"
    assert [item["status"] for item in data["items"]] == [
        "failed",
        "not_executed",
        "not_executed",
    ]
    assert data["items"][0]["code"] == "internal"
    assert data["items"][0]["message"] == "内部错误"
    assert executor.calls == [1]
    rendered = json.dumps(result, ensure_ascii=False)
    assert "boom" not in rendered
    assert "https://" not in rendered
    assert SECRET_TOKEN not in rendered
    _assert_lock_free()


def test_conflict_continues_and_is_reported():
    def result(song_id):
        if song_id == 1:
            return _conflict(1)
        return _published(2)

    executor = _Executor(result)
    envelope = playlist.save_playlist([1, 2], save_one=executor)
    assert envelope["ok"] is False
    assert envelope["code"] == "partial"
    data = envelope["data"]
    assert data["status"] == "completed_with_issues"
    assert data["stop_reason"] == "input_exhausted"
    assert data["next_position"] is None
    first, second = data["items"]
    assert first["status"] == "conflict"
    assert first["code"] == "conflict"
    assert first["message"] == "目标文件已存在"
    assert first["title"] == "曲目1"
    assert first["basename"] == "歌手1 - 曲目1.flac"
    assert first["size"] > 0
    assert len(first["md5"]) == 32
    assert first["published_size"] == 0
    assert first["published_md5"] == ""
    assert first["cover_status"] == "not_published"
    assert first["lyric_status"] == "not_attempted"
    assert second["status"] == "published"
    assert data["counts"] == {
        "total": 2,
        "published": 1,
        "conflict": 1,
        "failed": 0,
        "skipped": 0,
        "not_executed": 0,
    }
    assert executor.calls == [1, 2]


@pytest.mark.parametrize("code", sorted(playlist.PLAYLIST_CONTINUE_CODES))
def test_continue_codes_do_not_stop_later_positions(code):
    def result(song_id):
        if song_id == 1:
            return _route_failure(code)
        return _published(song_id)

    executor = _Executor(result)
    envelope = playlist.save_playlist([1, 2, 3], save_one=executor)
    assert envelope["ok"] is False
    assert envelope["code"] == "partial"
    data = envelope["data"]
    assert data["status"] == "completed_with_issues"
    assert data["stop_reason"] == "input_exhausted"
    first_status = "conflict" if code == "conflict" else "failed"
    assert [item["status"] for item in data["items"]] == [
        first_status,
        "published",
        "published",
    ]
    assert data["items"][0]["code"] == code
    assert executor.calls == [1, 2, 3]


@pytest.mark.parametrize(
    "code", sorted(playlist.PLAYLIST_CONTINUE_CODES | playlist.PLAYLIST_ABORT_CODES)
)
def test_executor_failure_messages_are_fixed_for_all_failure_codes(code):
    sensitive_values = (
        "https://signed.example.com/audio?token=secret-token",
        "Cookie: session=secret-cookie",
        "/vol1/private/music",
        "RuntimeError: secret-exception",
    )
    injected_message = " | ".join(sensitive_values)

    def result(song_id):
        if song_id == 1:
            if code == "conflict":
                envelope = _conflict(1)
                envelope["message"] = injected_message
                return envelope
            return _route_failure(code, message=injected_message)
        return _published(song_id)

    executor = _Executor(result)
    envelope = playlist.save_playlist([1, 2, 3], save_one=executor)
    first = envelope["data"]["items"][0]

    assert first["code"] == code
    assert first["message"] == playlist._MESSAGES.get(
        code, playlist._ITEM_FALLBACK_MESSAGE
    )
    if code in playlist.PLAYLIST_CONTINUE_CODES:
        assert executor.calls == [1, 2, 3]
        assert first["status"] == ("conflict" if code == "conflict" else "failed")
        assert [item["status"] for item in envelope["data"]["items"]] == [
            first["status"], "published", "published"
        ]
    else:
        assert executor.calls == [1]
        assert first["status"] == "failed"
        assert [item["status"] for item in envelope["data"]["items"]] == [
            "failed", "not_executed", "not_executed"
        ]

    rendered = json.dumps(envelope, ensure_ascii=False)
    for sensitive_value in sensitive_values:
        assert sensitive_value not in rendered


@pytest.mark.parametrize("code", ["download_failed", "disabled"])
@pytest.mark.parametrize("message", [None, 123, {"detail": "private"}])
def test_executor_failure_missing_or_non_string_message_uses_fixed_text(code, message):
    def result(song_id):
        if song_id > 1 and code in playlist.PLAYLIST_CONTINUE_CODES:
            return _published(song_id)
        failure = _route_failure(code)
        if message is None:
            failure.pop("message")
        else:
            failure["message"] = message
        return failure

    executor = _Executor(result)
    envelope = playlist.save_playlist([1, 2], save_one=executor)
    first = envelope["data"]["items"][0]

    assert first["status"] == "failed"
    assert first["code"] == code
    assert first["message"] == playlist._MESSAGES[code]
    if code in playlist.PLAYLIST_ABORT_CODES:
        assert executor.calls == [1]
        assert envelope["data"]["items"][1]["status"] == "not_executed"
    else:
        assert executor.calls == [1, 2]
        assert envelope["data"]["items"][1]["status"] == "published"
    assert "private" not in json.dumps(envelope, ensure_ascii=False)


@pytest.mark.parametrize("code", sorted(playlist.PLAYLIST_ABORT_CODES))
def test_abort_codes_stop_dispatch(code):
    def result(song_id):
        return _route_failure(code)

    executor = _Executor(result)
    envelope = playlist.save_playlist([1, 2, 3], save_one=executor)
    assert envelope["ok"] is False
    assert envelope["code"] == code
    if code == "internal":
        assert envelope["message"] == "内部错误"
    data = envelope["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == code
    assert [item["status"] for item in data["items"]] == [
        "failed",
        "not_executed",
        "not_executed",
    ]
    assert data["items"][0]["code"] == code
    assert data["next_position"] == 2
    assert executor.calls == [1]


def test_duplicate_positions_are_all_executed_and_linked():
    executor = _publish_executor(7, 7, 7)
    result = playlist.save_playlist([7, 7, 7], save_one=executor)
    assert result["ok"] is True
    items = result["data"]["items"]
    assert [item["duplicate_of_position"] for item in items] == [None, 1, 1]
    assert [item["status"] for item in items] == ["published"] * 3
    assert executor.calls == [7, 7, 7]


def test_none_positions_are_skipped_without_executor_calls():
    executor = _publish_executor(5)
    result = playlist.save_playlist([None, 5, None], save_one=executor)
    assert executor.calls == [5]
    assert result["ok"] is False
    assert result["code"] == "partial"
    data = result["data"]
    assert data["status"] == "completed_with_issues"
    assert [item["status"] for item in data["items"]] == [
        "skipped",
        "published",
        "skipped",
    ]
    assert [item["code"] for item in data["items"]] == [
        "invalid_song_id",
        "ok",
        "invalid_song_id",
    ]
    assert [item["song_id"] for item in data["items"]] == [None, 5, None]
    assert all(
        item["message"] != ""
        for item in data["items"]
        if item["status"] == "skipped"
    )
    assert data["counts"]["skipped"] == 2
    assert data["next_position"] is None


# ---------------------------------------------------------------------------
# Layer 1: cancellation and budget
# ---------------------------------------------------------------------------

def test_cancelled_before_first_position_executes_nothing():
    executor = _publish_executor(1, 2, 3)
    result = playlist.save_playlist(
        [1, 2, 3], save_one=executor, cancelled=lambda: True
    )
    assert executor.calls == []
    assert result["ok"] is False
    assert result["code"] == "cancelled"
    assert result["message"] == "批量保存已取消"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "cancelled"
    assert data["next_position"] == 1
    assert [item["status"] for item in data["items"]] == ["not_executed"] * 3
    assert data["counts"]["not_executed"] == 3
    assert data["elapsed_seconds"] >= 0.0
    _assert_lock_free()


def test_cancelled_between_positions_marks_remainder_not_executed():
    answers = iter([False, True])
    executor = _publish_executor(1)
    result = playlist.save_playlist(
        [1, 2, 3], save_one=executor, cancelled=lambda: next(answers)
    )
    assert executor.calls == [1]
    assert result["code"] == "cancelled"
    data = result["data"]
    assert [item["status"] for item in data["items"]] == [
        "published",
        "not_executed",
        "not_executed",
    ]
    assert data["next_position"] == 2
    assert data["stop_reason"] == "cancelled"


def test_cancelled_check_error_counts_as_cancelled():
    def broken_check():
        raise RuntimeError("cancel probe failed")

    executor = _publish_executor(1)
    result = playlist.save_playlist(
        [1, 2], save_one=executor, cancelled=broken_check
    )
    assert executor.calls == []
    assert result["code"] == "cancelled"
    assert result["data"]["stop_reason"] == "cancelled"
    assert [item["status"] for item in result["data"]["items"]] == [
        "not_executed",
        "not_executed",
    ]


def test_budget_exhaustion_marks_remainder_not_executed():
    clock = _FakeClock([0.0, 0.0, 1000.0, 1000.0])
    executor = _publish_executor(1)
    result = playlist.save_playlist(
        [1, 2, 3],
        save_one=executor,
        budget_seconds=playlist.PLAYLIST_TOTAL_BUDGET_SECONDS,
        now=clock,
    )
    assert executor.calls == [1]
    assert result["ok"] is False
    assert result["code"] == "budget_exhausted"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "budget_exhausted"
    assert [item["status"] for item in data["items"]] == [
        "published",
        "not_executed",
        "not_executed",
    ]
    assert data["next_position"] == 2
    # Documented: elapsed may exceed the budget when the last dispatch is slow.
    assert data["elapsed_seconds"] == 1000.0
    assert data["elapsed_seconds"] > data["budget_seconds"]
    assert data["budget_seconds"] == 900.0


def test_budget_boundary_allows_position_when_remaining_equals_floor():
    clock = _FakeClock([0.0, 0.0, 60.0, 60.0])
    executor = _publish_executor(1, 2)
    result = playlist.save_playlist(
        [1, 2],
        save_one=executor,
        budget_seconds=120,
        now=clock,
    )
    assert executor.calls == [1, 2]
    assert result["ok"] is True
    assert result["data"]["elapsed_seconds"] == 60.0


def test_budget_bounds_accept_positive_values():
    executor = _publish_executor(1)
    accepted = playlist.save_playlist([1], save_one=executor, budget_seconds=3600)
    assert accepted["ok"] is True
    assert accepted["data"]["budget_seconds"] == 3600.0
    # A tiny positive budget is valid input but fails the fixed floor before I/O.
    stopped = playlist.save_playlist([1], save_one=executor, budget_seconds=1.0)
    assert stopped["ok"] is False
    assert stopped["code"] == "budget_exhausted"
    assert executor.calls == [1]


def test_budget_sufficient_completes():
    executor = _publish_executor(1, 2, 3)
    result = playlist.save_playlist([1, 2, 3], save_one=executor)
    assert result["ok"] is True
    assert result["data"]["status"] == "completed"
    assert result["data"]["elapsed_seconds"] < playlist.PLAYLIST_TOTAL_BUDGET_SECONDS


def test_next_position_and_start_position_resume_semantics():
    executor = _publish_executor(1, 2, 3)
    result = playlist.save_playlist(
        [1, 2, 3], save_one=executor, start_position=10
    )
    data = result["data"]
    assert data["start_position"] == 10
    assert [item["position"] for item in data["items"]] == [10, 11, 12]
    assert data["next_position"] is None
    assert result["ok"] is True

    def abort_after_first(song_id):
        if song_id == 1:
            return _route_failure("disabled")
        return _published(song_id)

    aborted = playlist.save_playlist(
        [1, 2, 3],
        save_one=_Executor(abort_after_first),
        start_position=10,
    )
    aborted_data = aborted["data"]
    assert aborted_data["next_position"] == 11
    assert [item["position"] for item in aborted_data["items"]] == [10, 11, 12]

    resumed = playlist.save_playlist(
        [2, 3],
        save_one=_Executor(lambda song_id: _published(song_id)),
        start_position=aborted_data["next_position"],
    )
    assert resumed["ok"] is True
    assert [item["position"] for item in resumed["data"]["items"]] == [11, 12]


# ---------------------------------------------------------------------------
# Layer 1: locking, serialization, invariants
# ---------------------------------------------------------------------------

def test_locked_playlist_returns_busy_without_execution():
    executor = _Executor(lambda song_id: pytest.fail("busy batch dispatched"))
    assert playlist._PLAYLIST_LOCK.acquire(blocking=False)
    try:
        result = playlist.save_playlist([4, 5], save_one=executor)
    finally:
        playlist._PLAYLIST_LOCK.release()
    assert executor.calls == []
    assert result["ok"] is False
    assert result["code"] == "busy"
    assert result["message"] == "已有批量保存任务进行中"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "busy"
    assert data["next_position"] == 1
    assert [item["status"] for item in data["items"]] == [
        "not_executed",
        "not_executed",
    ]
    assert data["counts"]["not_executed"] == 2
    assert data["elapsed_seconds"] == 0.0
    _assert_lock_free()


def test_concurrent_second_batch_returns_busy():
    entered = threading.Event()
    release = threading.Event()
    first_result = {}

    def slow_save(song_id):
        entered.set()
        assert release.wait(5.0)
        return _published(song_id)

    def run_first():
        first_result["value"] = playlist.save_playlist([41], save_one=slow_save)

    thread = threading.Thread(target=run_first, daemon=True)
    thread.start()
    try:
        assert entered.wait(5.0)
        second_calls = []
        second = playlist.save_playlist(
            [42], save_one=lambda song_id: second_calls.append(song_id) or _published(song_id)
        )
        assert second["code"] == "busy"
        assert second_calls == []
    finally:
        release.set()
        thread.join(5.0)
    assert not thread.is_alive()
    assert first_result["value"]["ok"] is True
    _assert_lock_free()


def test_playlist_lock_released_after_every_outcome():
    published = _Executor(lambda song_id: _published(song_id))

    assert playlist.save_playlist([1], save_one=published)["ok"] is True
    _assert_lock_free()
    assert playlist.save_playlist(
        [1], save_one=lambda song_id: _conflict(song_id)
    )["code"] == "partial"
    _assert_lock_free()
    assert playlist.save_playlist(
        [1], save_one=lambda song_id: _route_failure("download_failed")
    )["code"] == "partial"
    _assert_lock_free()
    assert playlist.save_playlist(
        [1], save_one=lambda song_id: _route_failure("disabled")
    )["code"] == "disabled"
    _assert_lock_free()
    assert playlist.save_playlist(
        [1], save_one=lambda song_id: 42
    )["code"] == "internal"
    _assert_lock_free()

    def explode(song_id):
        raise RuntimeError("boom")

    assert playlist.save_playlist(
        [1], save_one=explode
    )["code"] == "internal"
    _assert_lock_free()
    assert playlist.save_playlist(
        [1], save_one=published, cancelled=lambda: True
    )["code"] == "cancelled"
    _assert_lock_free()
    assert playlist.save_playlist(
        [1], save_one=published, budget_seconds=120,
        now=_FakeClock([0.0, 500.0]),
    )["code"] == "budget_exhausted"
    _assert_lock_free()
    assert playlist.save_playlist([], save_one=published)["code"] == "invalid_request"
    _assert_lock_free()

    assert playlist._PLAYLIST_LOCK.acquire(blocking=False)
    try:
        assert playlist.save_playlist([1], save_one=published)["code"] == "busy"
    finally:
        playlist._PLAYLIST_LOCK.release()
    _assert_lock_free()


def test_result_is_json_serializable_and_bounded():
    def result(song_id):
        envelope = _published(song_id)
        envelope["data"].update({
            "title": "T" * 600,
            "artist": "A" * 600,
            "basename": "B" * 300,
            "format": "F" * 300,
            "quality_requested": "Q" * 300,
            "quality_actual": "jymaster",
            "quality_actual_status": "reported",
            "md5": "C" * 32,
            "published_md5": "D" * 32,
            "published_size": 987654,
        })
        return envelope

    ids = list(range(1, playlist.PLAYLIST_MAX_TRACKS + 1))
    executor = _Executor(result)
    envelope = playlist.save_playlist(ids, save_one=executor)
    rendered = json.dumps(envelope, ensure_ascii=False)
    assert envelope["ok"] is True
    assert envelope["data"]["positions_total"] == playlist.PLAYLIST_MAX_TRACKS
    assert len(rendered) < 512 * 1024
    assert "https://" not in rendered
    assert SECRET_TOKEN not in rendered
    assert "signed_url" not in rendered
    for item in envelope["data"]["items"]:
        assert tuple(item) == playlist.PLAYLIST_ITEM_FIELDS


def test_counts_and_positions_invariants():
    def result(song_id):
        if song_id == 2:
            return _route_failure("download_failed")
        return _published(song_id)

    executor = _Executor(result)
    envelope = playlist.save_playlist([1, None, 2, 3], save_one=executor)
    data = envelope["data"]
    counts = data["counts"]
    assert envelope["code"] == "partial"
    assert data["status"] == "completed_with_issues"
    assert data["stop_reason"] == "input_exhausted"
    assert data["positions_total"] == data["counts"]["total"] == len(data["items"])
    assert data["positions_total"] == 4
    assert [item["position"] for item in data["items"]] == [1, 2, 3, 4]
    assert sum(
        counts[key]
        for key in ("published", "conflict", "failed", "skipped", "not_executed")
    ) == counts["total"]
    assert counts["published"] == 2
    assert counts["failed"] == 1
    assert counts["skipped"] == 1
    assert data["next_position"] is None
    _assert_lock_free()


# ---------------------------------------------------------------------------
# Layer 2: real route.save_track with every SaveDeps seam injected
# ---------------------------------------------------------------------------

def _dir_stat(*, ino, dev=1, uid=1000, mode=0o700):
    return SimpleNamespace(
        st_mode=stat.S_IFDIR | mode, st_dev=dev, st_ino=ino, st_uid=uid
    )


class _FakeStore:
    """In-memory no-replace store: an existing name always yields CONFLICT."""

    def __init__(self):
        self.files = {}
        self.published_names = []
        self.overwrite_attempts = 0
        self._buffer = b""
        self._sealed = b""

    def begin_owned_stage(self, target_id):
        self._buffer = b""
        return "stage"

    def append(self, stage, chunk):
        self._buffer += bytes(chunk)

    def seal(self, stage):
        self._sealed = self._buffer

    def publish_noreplace(self, stage, basename):
        if basename in self.files:
            self.overwrite_attempts += 1
            return manual_save_core.StoreResult.CONFLICT
        self.files[basename] = self._sealed
        self.published_names.append(basename)
        return manual_save_core.StoreResult.PUBLISHED

    def abort_owned_stage(self, stage):
        return None

    def release_owned_stage(self, stage):
        return None

    def close(self):
        return None


class _Pipeline:
    """All SaveDeps seams; every byte/name comes from this in-memory fake."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.store = _FakeStore()
        self.http_calls = 0
        self.http_urls = []
        self.downloads = []
        self.open_store_calls = 0
        self.fail_download_ids = set()
        self.fail_info_ids = set()
        self.info_titles = {}
        self._temp_counter = 0

    # -- deterministic payloads -------------------------------------------
    @staticmethod
    def payload(song_id):
        return f"PAYLOAD-{song_id}-".encode() + b"x" * 17

    @staticmethod
    def media_url(song_id):
        return (
            f"https://signed.example.com/media/{song_id}.flac"
            f"?token={MEDIA_SECRET}"
        )

    # -- injected seams ----------------------------------------------------
    def http_json(self, url):
        self.http_calls += 1
        self.http_urls.append(url)
        match = re.search(r"/api/v1/song/(\d+)/(info|url)", url)
        assert match is not None
        song_id = int(match.group(1))
        kind = match.group(2)
        if kind == "info":
            if song_id in self.fail_info_ids:
                raise OSError("synthetic info failure")
            variants = self.info_titles.get(song_id)
            title = f"曲目{song_id}"
            if variants:
                title = variants.pop(0) if len(variants) > 1 else variants[0]
            return {
                "ok": True,
                "data": {
                    "name": title,
                    "ar": [{"name": f"歌手{song_id}"}],
                    "al": {"name": "专辑"},
                    "no": 1,
                    "cd": 1,
                },
            }
        payload = self.payload(song_id)
        return {
            "ok": True,
            "data": {
                "url": self.media_url(song_id),
                "type": "flac",
                "level": "jymaster",
                "size": len(payload),
                "md5": hashlib.md5(payload).hexdigest(),
                "id": song_id,
                "code": 200,
                "freeTrialInfo": None,
            },
        }

    def lyric_json(self, url):
        assert url.endswith("/lyric")
        return {"ok": True, "data": {"lyric": ""}}

    def download(self, media_url, destination):
        self.downloads.append(media_url)
        match = re.search(r"/media/(\d+)\.flac", media_url)
        assert match is not None
        song_id = int(match.group(1))
        if song_id in self.fail_download_ids:
            raise OSError("synthetic download failure")
        payload = self.payload(song_id)
        destination.write_bytes(payload)
        return {"size": len(payload), "md5": hashlib.md5(payload).hexdigest()}

    def probe(self, path):
        return {
            "streams": [{"index": 0, "codec_type": "audio", "codec_name": "flac"}],
            "format": {"format_name": "flac"},
        }

    def decode_check(self, path):
        return None

    def tag(self, source, tagged, metadata):
        tagged.write_bytes(b"TAGGED:" + source.read_bytes())

    def make_tempdir(self):
        self._temp_counter += 1
        workdir = self.tmp_path / f"work-{self._temp_counter}"
        workdir.mkdir()
        return workdir

    def validate_tagged(self, path, expected_format, metadata, expected_cover):
        return {
            "streams": [
                {"index": 0, "codec_type": "audio", "codec_name": expected_format}
            ],
            "format": {"format_name": expected_format, "tags": dict(metadata)},
        }

    def open_store(self, target_dir, staging_dir):
        self.open_store_calls += 1
        return self.store

    def deps(self, *, enabled=True):
        values = {
            "FNMUSIC_MANUAL_SAVE_ENABLED": "true" if enabled else "false",
            "FNMUSIC_MANUAL_SAVE_TARGET": "/target",
            "FNMUSIC_MANUAL_SAVE_STAGING": "/staging",
            "FNMUSIC_MUSICBOX_URL": "http://127.0.0.1:8770",
        }

        def stat_fn(path):
            if path == "/target":
                return _dir_stat(ino=1)
            if path == "/staging":
                return _dir_stat(ino=2)
            raise OSError("missing")

        return route.SaveDeps(
            config=values,
            stat=stat_fn,
            geteuid=lambda: 1000,
            http_json=self.http_json,
            lyric_json=self.lyric_json,
            download=self.download,
            probe=self.probe,
            decode_check=self.decode_check,
            tag=self.tag,
            open_store=self.open_store,
            make_tempdir=self.make_tempdir,
            verify_cover=lambda path: None,
            validate_tagged=self.validate_tagged,
        )


def _route_executor(deps):
    def save_one(song_id):
        return route.save_track(song_id, deps=deps)

    return _Executor(save_one)


def test_pipeline_three_ordered_tracks_publish_in_order(tmp_path):
    pipeline = _Pipeline(tmp_path)
    executor = _route_executor(pipeline.deps())
    result = playlist.save_playlist([101, 102, 103], save_one=executor)
    assert result["ok"] is True
    assert result["code"] == "ok"
    data = result["data"]
    assert data["status"] == "completed"
    assert data["stop_reason"] == "input_exhausted"
    assert data["next_position"] is None
    items = data["items"]
    assert [item["status"] for item in items] == ["published"] * 3
    assert [item["position"] for item in items] == [1, 2, 3]
    assert [item["song_id"] for item in items] == [101, 102, 103]
    assert [item["basename"] for item in items] == pipeline.store.published_names
    assert len(pipeline.store.files) == 3
    assert executor.calls == [101, 102, 103]
    for item in items:
        assert item["code"] == "ok"
        assert item["message"] == ""
        assert item["published_size"] > 0
        assert len(item["published_md5"]) == 32
        assert item["quality_actual"] == "jymaster"
        assert item["quality_actual_status"] == "reported"
        assert item["quality_fallback"] is False
        assert item["format"] == "flac"
        assert item["cover_status"] == "unavailable"
        assert item["lyric_status"] == "unavailable"
    rendered = json.dumps(result, ensure_ascii=False)
    assert "https://" not in rendered
    assert MEDIA_SECRET not in rendered
    assert SECRET_TOKEN not in rendered


def test_pipeline_duplicate_position_conflicts_without_overwriting(tmp_path):
    pipeline = _Pipeline(tmp_path)
    executor = _route_executor(pipeline.deps())
    result = playlist.save_playlist([201, 201], save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "partial"
    data = result["data"]
    assert data["status"] == "completed_with_issues"
    first, second = data["items"]
    assert first["status"] == "published"
    assert second["status"] == "conflict"
    assert second["code"] == "conflict"
    assert second["message"] == "目标文件已存在"
    assert second["basename"] == first["basename"]
    assert second["title"] == first["title"]
    assert second["size"] == first["size"]
    assert second["md5"] == first["md5"]
    assert second["published_size"] == 0
    assert second["published_md5"] == ""
    assert second["cover_status"] == "not_published"
    assert second["lyric_status"] == "not_attempted"
    assert pipeline.store.published_names == [first["basename"]]
    assert len(pipeline.store.files) == 1
    assert pipeline.store.overwrite_attempts == 1
    assert executor.calls == [201, 201]


def test_pipeline_track_failure_does_not_stop_later_tracks(tmp_path):
    pipeline = _Pipeline(tmp_path)
    pipeline.fail_download_ids = {301}
    executor = _route_executor(pipeline.deps())
    result = playlist.save_playlist([301, 302], save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "partial"
    data = result["data"]
    assert data["status"] == "completed_with_issues"
    first, second = data["items"]
    assert first["status"] == "failed"
    assert first["code"] == "download_failed"
    assert first["message"] == "下载失败"
    assert first["title"] == ""
    assert second["status"] == "published"
    assert second["song_id"] == 302
    assert len(pipeline.store.files) == 1
    assert executor.calls == [301, 302]


def test_pipeline_duplicate_same_id_different_metadata_publishes_both(tmp_path):
    pipeline = _Pipeline(tmp_path)
    pipeline.info_titles[401] = ["第一版标题", "第二版标题"]
    executor = _route_executor(pipeline.deps())
    result = playlist.save_playlist([401, 401], save_one=executor)
    assert result["ok"] is True
    items = result["data"]["items"]
    assert [item["status"] for item in items] == ["published", "published"]
    assert [item["duplicate_of_position"] for item in items] == [None, 1]
    assert items[0]["title"] == "第一版标题"
    assert items[1]["title"] == "第二版标题"
    assert items[0]["basename"] != items[1]["basename"]
    assert len(pipeline.store.files) == 2
    assert pipeline.store.overwrite_attempts == 0
    assert executor.calls == [401, 401]


def test_pipeline_budget_stops_before_later_tracks(tmp_path):
    pipeline = _Pipeline(tmp_path)
    executor = _route_executor(pipeline.deps())
    clock = _FakeClock([0.0, 0.0, 700.0, 700.0])
    result = playlist.save_playlist(
        [501, 502, 503],
        save_one=executor,
        budget_seconds=700,
        now=clock,
    )
    assert result["ok"] is False
    assert result["code"] == "budget_exhausted"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "budget_exhausted"
    assert data["next_position"] == 2
    assert data["elapsed_seconds"] == 700.0
    assert [item["status"] for item in data["items"]] == [
        "published",
        "not_executed",
        "not_executed",
    ]
    assert len(pipeline.store.files) == 1
    assert executor.calls == [501]


def test_pipeline_disabled_config_aborts_without_touching_store_or_http(tmp_path):
    pipeline = _Pipeline(tmp_path)
    executor = _route_executor(pipeline.deps(enabled=False))
    result = playlist.save_playlist([601, 602], save_one=executor)
    assert result["ok"] is False
    assert result["code"] == "disabled"
    assert result["message"] == "手动保存未启用"
    data = result["data"]
    assert data["status"] == "incomplete"
    assert data["stop_reason"] == "disabled"
    assert data["next_position"] == 2
    first, second = data["items"]
    assert first["status"] == "failed"
    assert first["code"] == "disabled"
    assert first["message"] == "手动保存未启用"
    assert second["status"] == "not_executed"
    assert executor.calls == [601]
    assert pipeline.http_calls == 0
    assert pipeline.downloads == []
    assert pipeline.open_store_calls == 0
    assert pipeline.store.files == {}


def test_pipeline_default_seams_are_not_used(tmp_path, monkeypatch):
    seam_names = (
        "_default_geteuid",
        "_default_cover_fetch",
        "_default_verify_cover",
        "_default_make_tempdir",
        "_default_http_json",
        "_default_lyric_http_json",
        "_default_info_http_json",
        "_default_download",
        "_default_ffprobe",
        "_default_decode_check",
        "_default_validate_tagged",
        "_default_verify_tagged_cover",
        "_default_tag",
        "_default_open_store",
    )

    def make_fail(name):
        def fail(*args, **kwargs):
            raise AssertionError(f"route default seam used: {name}")

        return fail

    for name in seam_names:
        assert hasattr(route, name), name
        monkeypatch.setattr(route, name, make_fail(name))

    pipeline = _Pipeline(tmp_path)
    executor = _route_executor(pipeline.deps())
    result = playlist.save_playlist([701, 702], save_one=executor)
    assert result["ok"] is True
    assert [item["status"] for item in result["data"]["items"]] == [
        "published",
        "published",
    ]
    assert len(pipeline.store.files) == 2
