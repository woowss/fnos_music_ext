"""Ordered, offline playlist save engine.

This module is a pure orchestration primitive for saving several input positions
in order. It performs no I/O of its own: the caller must inject ``save_one``
(the single-track executor, e.g. ``manual_save_route.save_track``), and may
optionally inject a clock and a cancellation predicate. There is deliberately
no default executor and no dependency bag, so importing this module can never
reach the network, the filesystem, ffmpeg, or the WebUI gateway.

Fixed contract (FNMEXT-PLAYLIST-IMPORT-20261101)
------------------------------------------------
* Every input position is validated before the first execution; an invalid
  list rejects the whole call without executing anything.
* Executions are strictly serial; each executable position calls
  ``save_one(song_id)`` exactly once and positions are independent.
* The batch holds only ``_PLAYLIST_LOCK`` for its whole lifetime. It never
  acquires, inspects, or waits on ``manual_save_route._SAVE_LOCK``; the route
  takes its own per-track lock inside ``save_one``.
* One aborted position stops dispatch (already executed positions are kept);
  a continue-code failure does not stop later positions.
* Results carry the same ``{ok, code, message, data}`` envelope as the route,
  with fixed non-interpolated messages: URLs, cookies, paths, and raw
  exception text are never echoed.
* ``ok`` is true if and only if ``code == "ok"``; ``completed`` is the only
  status that yields that code.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
import math
import re
import threading
import time

PLAYLIST_MAX_TRACKS = 100
PLAYLIST_TOTAL_BUDGET_SECONDS = 900.0
PLAYLIST_MAX_BUDGET_SECONDS = 3600.0
PLAYLIST_MIN_REMAINING_SECONDS = 60.0

# Failure codes that only fail their own position; later positions still run.
PLAYLIST_CONTINUE_CODES = frozenset({
    "conflict",
    "service_unavailable",
    "no_quality_available",
    "quality_unverified",
    "quality_identity_mismatch",
    "trial_only",
    "download_target_untrusted",
    "download_failed",
    "integrity_mismatch",
    "invalid_audio",
    "tag_failed",
})

# Environment-level failure codes that stop the whole batch.
PLAYLIST_ABORT_CODES = frozenset({
    "disabled",
    "config_invalid",
    "busy",
    "staging_unavailable",
    "publish_failed",
    "internal",
})

# Every item has exactly these keys, in this order.
PLAYLIST_ITEM_FIELDS = (
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
    "cover_status",
    "cover_error",
    "lyric_status",
    "duplicate_of_position",
)

# One batch at a time per process. This lock is never the route save lock.
_PLAYLIST_LOCK = threading.Lock()

_MESSAGES = {
    "ok": "",
    "partial": "部分位置未保存成功",
    "invalid_request": "请求参数无效",
    "too_many_positions": "位置数量超过上限",
    "invalid_song_id": "song_id 必须是正整数",
    "cancelled": "批量保存已取消",
    "budget_exhausted": "批量保存预算耗尽",
    "busy": "已有批量保存任务进行中",
    "not_executed": "未执行",
    "disabled": "手动保存未启用",
    "config_invalid": "手动保存配置无效",
    "service_unavailable": "音乐服务不可用",
    "no_quality_available": "没有可用的音质",
    "quality_unverified": "音质响应无法核验",
    "quality_identity_mismatch": "无法确认音质响应所属曲目",
    "trial_only": "仅取得试听片段，未保存",
    "download_target_untrusted": "下载目标未通过安全校验",
    "download_failed": "下载失败",
    "integrity_mismatch": "文件校验不一致",
    "invalid_audio": "音频文件无效",
    "tag_failed": "写入标签失败",
    "staging_unavailable": "暂存不可用",
    "publish_failed": "发布失败",
    "conflict": "目标文件已存在",
    "internal": "内部错误",
}
_ITEM_FALLBACK_MESSAGE = "保存失败"

_COVER_STATUSES = frozenset({
    "preserved",
    "embedded",
    "unavailable",
    "unsupported",
    "error",
    "not_published",
    "not_attempted",
})
_COVER_ERRORS = frozenset({
    "host_untrusted",
    "url_invalid",
    "address_untrusted",
    "http_error",
    "payload_invalid",
    "decode_failed",
    "embed_failed",
    "unknown",
})
_LYRIC_STATUSES = frozenset({
    "published",
    "unavailable",
    "error",
    "conflict",
    "not_attempted",
    "unknown",
})

# A delivered quality is reported only when the route handed back a legal
# lowercase server token; anything else is normalized to unknown with no value.
_QUALITY_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_MD5_RE = re.compile(r"[0-9a-fA-F]{32}")
_TEXT_LIMITS = {
    "title": 512,
    "artist": 512,
    "basename": 255,
    "format": 255,
    "quality_requested": 120,
    "quality_actual": 120,
    "message": 120,
}

__all__ = [
    "PLAYLIST_MAX_TRACKS",
    "PLAYLIST_TOTAL_BUDGET_SECONDS",
    "PLAYLIST_MAX_BUDGET_SECONDS",
    "PLAYLIST_MIN_REMAINING_SECONDS",
    "PLAYLIST_ITEM_FIELDS",
    "PLAYLIST_CONTINUE_CODES",
    "PLAYLIST_ABORT_CODES",
    "save_playlist",
]


def _failure(code: str) -> dict:
    """Fixed input-error envelope; ``data`` is always empty here."""
    return {
        "ok": False,
        "code": code,
        "message": _MESSAGES.get(code, _MESSAGES["internal"]),
        "data": {},
    }


def _valid_budget(value: object) -> bool:
    if type(value) is int:
        return 0 < value <= PLAYLIST_MAX_BUDGET_SECONDS
    if type(value) is float:
        return math.isfinite(value) and 0.0 < value <= PLAYLIST_MAX_BUDGET_SECONDS
    return False


def _validate_inputs(
    song_ids: object,
    save_one: object,
    start_position: object,
    budget_seconds: object,
    cancelled: object,
    now: object,
) -> tuple[list | None, str]:
    """Validate the complete call before any lock or execution.

    Returns ``(cleaned_positions, code)``; a non-empty code rejects the call.
    """
    if not isinstance(song_ids, (list, tuple)):
        return None, "invalid_request"
    if len(song_ids) == 0:
        return None, "invalid_request"
    if len(song_ids) > PLAYLIST_MAX_TRACKS:
        return None, "too_many_positions"
    cleaned: list = []
    for value in song_ids:
        if value is None:
            cleaned.append(None)
        elif type(value) is int and not isinstance(value, bool) and value > 0:
            cleaned.append(value)
        else:
            return None, "invalid_song_id"
    if type(start_position) is not int or isinstance(start_position, bool):
        return None, "invalid_request"
    if start_position <= 0:
        return None, "invalid_request"
    if not _valid_budget(budget_seconds):
        return None, "invalid_request"
    if not callable(save_one):
        return None, "invalid_request"
    if now is not None and not callable(now):
        return None, "invalid_request"
    if cancelled is not None and not callable(cancelled):
        return None, "invalid_request"
    return cleaned, ""


def _duplicate_link(
    first_seen: dict[int, int], song_id: int | None, position: int
) -> int | None:
    """Earliest earlier position holding the same song_id, else ``None``.

    ``None`` placeholders are not song ids and never link.
    """
    if song_id is None:
        return None
    first = first_seen.get(song_id)
    if first is None:
        first_seen[song_id] = position
        return None
    return first


def _clean_text(value: object, field: str) -> str:
    limit = _TEXT_LIMITS[field]
    if type(value) is not str:
        return ""
    return value[:limit]


def _clean_size(value: object, *, minimum: int) -> int:
    if type(value) is int and not isinstance(value, bool) and value >= minimum:
        return value
    return 0


def _clean_md5(value: object) -> str:
    if type(value) is str and _MD5_RE.fullmatch(value):
        return value.casefold()
    return ""


def _quality_pair(data: Mapping) -> tuple[str, str]:
    actual_status = data.get("quality_actual_status")
    actual = _clean_text(data.get("quality_actual"), "quality_actual")
    if actual_status == "reported" and _QUALITY_TOKEN_RE.fullmatch(actual):
        return "reported", actual
    return "unknown", ""


def _copy_success_fields(item: dict, data: Mapping, *, published: bool) -> None:
    """Copy only the delivery whitelist from a published/conflict envelope."""
    item["title"] = _clean_text(data.get("title"), "title")
    item["artist"] = _clean_text(data.get("artist"), "artist")
    item["basename"] = _clean_text(data.get("basename"), "basename")
    item["format"] = _clean_text(data.get("format"), "format")
    item["quality_requested"] = _clean_text(data.get("quality_requested"), "quality_requested")
    status, actual = _quality_pair(data)
    item["quality_actual_status"] = status
    item["quality_actual"] = actual
    item["quality_fallback"] = data.get("quality_fallback") is True
    item["size"] = _clean_size(data.get("size"), minimum=0)
    item["md5"] = _clean_md5(data.get("md5"))
    if published:
        item["published_size"] = _clean_size(data.get("published_size"), minimum=1)
        item["published_md5"] = _clean_md5(data.get("published_md5"))
    cover_status = data.get("cover_status")
    if type(cover_status) is str and cover_status in _COVER_STATUSES:
        item["cover_status"] = cover_status
    if item["cover_status"] == "error":
        cover_error = data.get("cover_error")
        if type(cover_error) is str and cover_error in _COVER_ERRORS:
            item["cover_error"] = cover_error
    lyric_status = data.get("lyric_status")
    if type(lyric_status) is str and lyric_status in _LYRIC_STATUSES:
        item["lyric_status"] = lyric_status


def _item_skeleton(
    position: int, song_id: int | None, duplicate_of_position: int | None
) -> dict:
    """Default item: not executed, empty delivery fields, exact key order."""
    return {
        "position": position,
        "song_id": song_id,
        "status": "not_executed",
        "code": "not_executed",
        "message": _MESSAGES["not_executed"],
        "title": "",
        "artist": "",
        "basename": "",
        "format": "",
        "quality_requested": "",
        "quality_actual": "",
        "quality_actual_status": "unknown",
        "quality_fallback": False,
        "size": 0,
        "md5": "",
        "published_size": 0,
        "published_md5": "",
        "cover_status": "not_attempted",
        "cover_error": "",
        "lyric_status": "not_attempted",
        "duplicate_of_position": duplicate_of_position,
    }


def _not_executed_item(
    position: int, song_id: int | None, duplicate_of_position: int | None
) -> dict:
    return _item_skeleton(position, song_id, duplicate_of_position)


def _skipped_item(position: int, duplicate_of_position: int | None) -> dict:
    item = _item_skeleton(position, None, duplicate_of_position)
    item["status"] = "skipped"
    item["code"] = "invalid_song_id"
    item["message"] = _MESSAGES["invalid_song_id"]
    return item


def _failed_item(
    position: int,
    song_id: int,
    duplicate_of_position: int | None,
    code: str,
    message: str,
) -> dict:
    item = _item_skeleton(position, song_id, duplicate_of_position)
    item["status"] = "failed"
    item["code"] = code
    item["message"] = message
    return item


def _internal_item(
    position: int, song_id: int, duplicate_of_position: int | None
) -> dict:
    return _failed_item(
        position, song_id, duplicate_of_position, "internal", _MESSAGES["internal"]
    )


def _normalize_result(
    result: object,
    position: int,
    song_id: int,
    duplicate_of_position: int | None,
) -> dict:
    """Normalize one injected executor result into a single item.

    Malformed executor results are fail-closed to ``failed/internal``; they are
    never interpreted optimistically and their text is not echoed.
    """
    if not isinstance(result, Mapping):
        return _internal_item(position, song_id, duplicate_of_position)
    ok = result.get("ok")
    if type(ok) is not bool:
        return _internal_item(position, song_id, duplicate_of_position)
    code = result.get("code")
    data = result.get("data")
    if type(code) is not str or not isinstance(data, dict):
        return _internal_item(position, song_id, duplicate_of_position)
    if ok:
        if code != "ok":
            return _internal_item(position, song_id, duplicate_of_position)
        item = _item_skeleton(position, song_id, duplicate_of_position)
        item["status"] = "published"
        item["code"] = "ok"
        item["message"] = ""
        _copy_success_fields(item, data, published=True)
        return item
    if code not in PLAYLIST_CONTINUE_CODES and code not in PLAYLIST_ABORT_CODES:
        return _internal_item(position, song_id, duplicate_of_position)
    if code == "conflict":
        item = _item_skeleton(position, song_id, duplicate_of_position)
        item["status"] = "conflict"
        item["code"] = "conflict"
        item["message"] = _MESSAGES["conflict"]
        _copy_success_fields(item, data, published=False)
        return item
    raw_message = result.get("message")
    if type(raw_message) is str:
        message = raw_message[: _TEXT_LIMITS["message"]]
    else:
        message = _MESSAGES.get(code, _ITEM_FALLBACK_MESSAGE)
    return _failed_item(
        position, song_id, duplicate_of_position, code, message
    )


def _finalize(
    items: list[dict],
    stop_reason: str,
    start_position: int,
    budget_seconds: float,
    elapsed_seconds: float,
) -> dict:
    counts = {
        "total": len(items),
        "published": 0,
        "conflict": 0,
        "failed": 0,
        "skipped": 0,
        "not_executed": 0,
    }
    for item in items:
        counts[item["status"]] += 1
    if stop_reason:
        status = "incomplete"
        code = stop_reason
        next_position = next(
            (
                item["position"]
                for item in items
                if item["status"] == "not_executed"
            ),
            None,
        )
    elif counts["published"] == counts["total"]:
        status = "completed"
        code = "ok"
        next_position = None
    else:
        status = "completed_with_issues"
        code = "partial"
        next_position = None
    data = {
        "status": status,
        "stop_reason": stop_reason or "input_exhausted",
        "start_position": start_position,
        "next_position": next_position,
        "positions_total": len(items),
        "counts": counts,
        "items": items,
        "budget_seconds": float(budget_seconds),
        "elapsed_seconds": float(elapsed_seconds),
    }
    return {
        "ok": code == "ok",
        "code": code,
        "message": _MESSAGES.get(code, _MESSAGES["internal"]),
        "data": data,
    }


def _busy_envelope(
    song_ids: list, start_position: int, budget_seconds: object
) -> dict:
    """Another batch owns the playlist lock: report busy with zero execution."""
    first_seen: dict[int, int] = {}
    items = []
    for index, value in enumerate(song_ids):
        position = start_position + index
        duplicate = _duplicate_link(first_seen, value, position)
        items.append(_not_executed_item(position, value, duplicate))
    return _finalize(
        items, "busy", start_position, float(budget_seconds), 0.0
    )


def _run(
    song_ids: list,
    save_one: Callable[[int], object],
    start_position: int,
    budget_seconds: float,
    cancelled: Callable[[], bool] | None,
    now: Callable[[], float] | None,
) -> dict:
    clock = time.monotonic if now is None else now
    items: list[dict] = []
    first_seen: dict[int, int] = {}
    stop_reason = ""
    started = 0.0
    last_elapsed = 0.0
    clock_ready = True
    try:
        started = float(clock())
    except Exception:
        clock_ready = False
        stop_reason = "internal"

    if not clock_ready:
        # No clock means no budget accounting is possible: fail closed before
        # the first execution while keeping the item/position invariants.
        for index, value in enumerate(song_ids):
            position = start_position + index
            duplicate = _duplicate_link(first_seen, value, position)
            items.append(_not_executed_item(position, value, duplicate))
    else:
        for index, value in enumerate(song_ids):
            position = start_position + index
            duplicate = _duplicate_link(first_seen, value, position)
            if stop_reason:
                items.append(_not_executed_item(position, value, duplicate))
                continue
            # 1. cancellation is re-checked before every position.
            if cancelled is not None:
                try:
                    cancel_requested = bool(cancelled())
                except Exception:
                    # A broken cancel check counts as cancelled (fail closed).
                    cancel_requested = True
                if cancel_requested:
                    stop_reason = "cancelled"
                    items.append(_not_executed_item(position, value, duplicate))
                    continue
            # 2. budget floor: stop when less than the fixed floor remains.
            try:
                used = max(0.0, float(clock()) - started)
            except Exception:
                stop_reason = "internal"
                items.append(_not_executed_item(position, value, duplicate))
                continue
            last_elapsed = used
            if budget_seconds - used < PLAYLIST_MIN_REMAINING_SECONDS:
                stop_reason = "budget_exhausted"
                items.append(_not_executed_item(position, value, duplicate))
                continue
            # 3. dispatch: placeholders are never executed.
            if value is None:
                items.append(_skipped_item(position, duplicate))
                continue
            try:
                result = save_one(value)
            except Exception:
                items.append(_internal_item(position, value, duplicate))
                stop_reason = "internal"
                continue
            item = _normalize_result(result, position, value, duplicate)
            items.append(item)
            if item["status"] == "failed" and item["code"] in PLAYLIST_ABORT_CODES:
                stop_reason = item["code"]

    elapsed = last_elapsed
    if clock_ready:
        try:
            elapsed = max(0.0, float(clock()) - started)
        except Exception:
            elapsed = last_elapsed
    return _finalize(
        items, stop_reason, start_position, budget_seconds, elapsed
    )


def save_playlist(
    song_ids: object,
    *,
    save_one: Callable[[int], object],
    start_position: object = 1,
    budget_seconds: object = PLAYLIST_TOTAL_BUDGET_SECONDS,
    cancelled: Callable[[], bool] | None = None,
    now: Callable[[], float] | None = None,
) -> dict:
    """Save positions serially and report a fixed, ordered envelope.

    ``song_ids`` is a non-empty ``list``/``tuple`` with at most
    ``PLAYLIST_MAX_TRACKS`` entries; each entry is a positive ``int`` song id or
    ``None`` for an intentionally skipped position. ``save_one`` is mandatory
    and is the only execution path; this module has no default executor.

    The whole batch holds ``_PLAYLIST_LOCK``. A second concurrent batch returns
    ``busy`` immediately without executing anything. The budget floor is
    ``PLAYLIST_MIN_REMAINING_SECONDS``; a position runs only when at least that
    much budget remains, so ``elapsed_seconds`` may exceed ``budget_seconds``
    when the last dispatch itself is slow. A cancelled check that raises counts
    as cancelled. In-flight ``save_one`` calls cannot be interrupted.
    """
    cleaned, invalid_code = _validate_inputs(
        song_ids, save_one, start_position, budget_seconds, cancelled, now
    )
    if invalid_code:
        return _failure(invalid_code)
    assert cleaned is not None
    if not _PLAYLIST_LOCK.acquire(blocking=False):
        return _busy_envelope(cleaned, start_position, budget_seconds)
    try:
        return _run(
            cleaned,
            save_one,
            start_position,
            float(budget_seconds),
            cancelled,
            now,
        )
    except Exception:
        # Engine bug, not an executor failure: fail closed with a fixed code.
        return _failure("internal")
    finally:
        _PLAYLIST_LOCK.release()
