"""Playlist save job adapter: producer client, ledger manifest, result mapping, thin service.

Contract ``FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01`` (``docs/MANUAL_SAVE_PLAYLIST_JOB.md``):

* §4.2-§4.3 - the snapshot producer is the *only* NetEase adapter. This module only
  performs an HTTP GET to a loopback host (``127.0.0.1`` / ``localhost`` / ``::1``,
  ``http`` only, redirects refused), re-checks the producer payload instead of
  trusting its boolean flags, and builds the closed seven-field ledger manifest
  (``docs/OFFLINE_SAVE_JOBS.md:13-31``).
* §4.4-§4.6 - position limits, duplicate retention and invalid-position retention.
* §5.1-§5.5 - the executor adapter and the authoritative per-code audit mapping.
* §7.1 - one advance per request under the module-level gateway save lock.

Hard boundaries kept by this module:

* Importing it never opens the SQLite ledger and never sends a request. Only an
  explicit ``PlaylistJobService`` mutation or ``PlaylistSnapshotClient.fetch_snapshot``
  call reaches a database / socket.
* All ledger writes go through ``ManualSaveJobsLedger``. This module embeds no SQL.
* No logging calls exist here at all, so signed URLs, cookies, tokens, raw user
  claims, local paths and raw exception text cannot be emitted by this module.
* Every public error carries one fixed, non-interpolated code (``str(error)`` is
  that code) and every public message comes from the fixed ``_MESSAGES`` table.

Ledger reason extension dependency (contract §5.5, P1): the route failure reasons
below are not part of the original single-file core whitelist. They are collected in
``RESULT_REASON_EXTENSION`` so the dependency is explicit; this module must never
modify ``proxy/manual_save_jobs.py`` itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib import error as urllib_error
from urllib import request as urllib_request
from urllib.parse import urlsplit

try:  # Imported as ``proxy.manual_save_playlist_jobs`` (repository test style).
    from .manual_save_core import Outcome, SaveResult
    from .manual_save_jobs import (
        ManualSaveJobsLedger, PositionExecution, PositionSnapshot, validate_position_detail,
    )
except ImportError:  # pragma: no cover - flat import style (PYTHONPATH=proxy).
    from manual_save_core import Outcome, SaveResult
    from manual_save_jobs import (
        ManualSaveJobsLedger, PositionExecution, PositionSnapshot, validate_position_detail,
    )


# ---------------------------------------------------------------------------
# Closed field values (contract §4.3.2, §5.2)
# ---------------------------------------------------------------------------

TARGET_ID = "manual-save"
PROVIDER_ID = "netease-1.0.0-fnmusic"
PROVIDER_NAME = "netease"
SNAPSHOT_SCHEMA_VERSION = 1
PLAYLIST_COLLECTION_PREFIX = "netease-playlist-"
COLLECTION_VERSION_PREFIX = "sha256-"
MAX_TRACKS = 1000
FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")

DISPOSITION_CONTINUE = "continue"
DISPOSITION_ABORT = "abort"
DISPOSITION_FREEZE = "freeze"
DISPOSITIONS = frozenset({DISPOSITION_CONTINUE, DISPOSITION_ABORT, DISPOSITION_FREEZE})

# Producer status values (contract §4.2 payload sample).
SNAPSHOT_STATUSES = frozenset({
    "complete",
    "incomplete",
    "source_changed",
    "unsupported",
    "upstream_error",
    "not_logged_in",
})
# Fixed producer error strings that may be passed through unchanged (§4.2 switch).
PRODUCER_ERROR_CODES = frozenset({
    "manifest_disabled",
    "source_artifact_mismatch",
    "invalid_request",
})

# Fixed error codes raised by this module (all appear in ``_MESSAGES``).
# The snapshot-failure codes follow contract §3.3/§3.9.1 so the P5 gateway can
# map them to the reviewed HTTP statuses without re-deriving categories.
PRODUCER_NOT_LOOPBACK = "producer_not_loopback"
PRODUCER_CONFIG_INVALID = "producer_config_invalid"
PRODUCER_TIMEOUT = "producer_timeout"
PRODUCER_UNREACHABLE = "producer_unreachable"
PRODUCER_HTTP_STATUS = "producer_http_status"
PRODUCER_RESPONSE_TOO_LARGE = "producer_response_too_large"
PRODUCER_MALFORMED_RESPONSE = "producer_malformed_response"
PRODUCER_UNEXPECTED_URL = "producer_unexpected_url"
INVALID_PLAYLIST_ID = "invalid_playlist_id"
MANIFEST_INCOMPLETE = "manifest_incomplete"
PLAYLIST_TOO_LARGE = "playlist_too_large"
PLAYLIST_UNSUPPORTED = "playlist_unsupported"
PLAYLIST_UNAVAILABLE = "playlist_unavailable"
SOURCE_CHANGED = "source_changed"
NOT_LOGGED_IN = "not_logged_in"
SOURCE_ARTIFACT_MISMATCH = "source_artifact_mismatch"
DISABLED = "disabled"
PLAYLIST_DISABLED = "playlist_disabled"
CONFIG_INVALID = "config_invalid"
BUSY = "busy"
INVALID_ACTOR = "invalid_actor"

# Fixed, non-interpolated public messages. Values never contain format markers;
# ``fixed_message`` is the only public accessor. Texts mirror contract §3.9.1.
# Ledger reason aliases (``stage_unavailable``/``staging_failed``) are included so
# rendering a persisted reason never falls back to the generic internal message.
_MESSAGES = {
    "ok": "",
    "disabled": "手动保存未启用",
    "config_invalid": "手动保存配置无效",
    "invalid_request": "请求无效",
    "invalid_actor": "调用方标识无效",
    "not_found": "任务不存在",
    "busy": "已有保存任务进行中",
    "no_work": "没有可推进的位置",
    "completed": "已完成一次推进",
    "commit_unknown": "保存结果未知，需人工核对",
    "completion_unknown": "保存结果写入失败，需人工核对",
    "stale_completion": "迟到的执行结果未覆盖记录",
    "internal": "内部错误",
    "manifest_disabled": "歌单快照读取未启用",
    "manifest_incomplete": "歌单成员清单不完整",
    "playlist_too_large": "歌单成员超过 1000 个",
    "playlist_unsupported": "该歌单不支持可验证快照",
    "playlist_unavailable": "音乐服务不可用",
    "playlist_disabled": "整歌单保存未启用",
    "source_changed": "歌单在确认后发生变化，请重新读取",
    "not_logged_in": "网易云账号未登录",
    "source_artifact_mismatch": "音乐服务版本与固定证据不一致",
    "invalid_playlist_id": "歌单 ID 必须是正整数",
    "producer_not_loopback": "快照服务地址不受信任",
    "producer_config_invalid": "快照服务配置无效",
    "producer_timeout": "快照服务读取超时",
    "producer_unreachable": "快照服务不可达",
    "producer_http_status": "快照服务返回异常状态",
    "producer_response_too_large": "快照响应超出上限",
    "producer_malformed_response": "快照响应无法核验",
    "producer_unexpected_url": "快照服务返回了非预期地址",
    "conflict": "目标文件已存在",
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
    "stage_unavailable": "暂存不可用",
    "staging_failed": "暂存不可用",
    "cancel_check_failed": "取消检查失败",
    "publish_failed": "发布失败",
    "invalid_target": "目标无效",
    "invalid_song_id": "song_id 必须是正整数",
}
# Contract §5.4 fixed banner: shown whenever any position is commit_unknown.
RECONCILIATION_REQUIRED_BANNER = (
    "存在结果未知的位置，已停止推进。请核对 NAS 上是否已出现该文件后再人工处理。"
)


def fixed_message(code: object) -> str:
    """Return the fixed, non-interpolated public message for one fixed code."""
    if type(code) is str and code in _MESSAGES:
        return _MESSAGES[code]
    return _MESSAGES["internal"]


# ---------------------------------------------------------------------------
# Fixed-code errors
# ---------------------------------------------------------------------------

class PlaylistJobError(Exception):
    """Base error; ``str(error)`` is always the fixed code, never raw text."""

    code = "internal"

    def __init__(self, code: str | None = None):
        self.code = code or type(self).code
        super().__init__(self.code)


class ProducerError(PlaylistJobError):
    code = PRODUCER_MALFORMED_RESPONSE


class ManifestError(PlaylistJobError):
    code = MANIFEST_INCOMPLETE


class ServiceError(PlaylistJobError):
    code = "invalid_request"


# ---------------------------------------------------------------------------
# §5.2 authoritative per-code audit mapping
# ---------------------------------------------------------------------------
# Every row: route ``save_track`` code -> (ledger Outcome, ledger reason, disposition).
# Row 1 (published) additionally requires ``data.status == "published"`` and is
# handled in ``map_save_result``; row 19 pre-checks live in ``make_executor``;
# rows 20/21 are the fixed ``commit_unknown`` fallback.
_CODE_MAP: dict[str, tuple[Outcome, str, str]] = {
    # Row 2 - conflict is a per-position CONTINUE outcome (never overwrite/rename).
    "conflict": (Outcome.CONFLICT, "destination_conflict", DISPOSITION_CONTINUE),
    # Rows 3-12 - route CONTINUE codes.
    "service_unavailable": (Outcome.REJECTED, "service_unavailable", DISPOSITION_CONTINUE),
    "no_quality_available": (Outcome.REJECTED, "no_quality_available", DISPOSITION_CONTINUE),
    "quality_unverified": (Outcome.REJECTED, "quality_unverified", DISPOSITION_CONTINUE),
    "quality_identity_mismatch": (Outcome.REJECTED, "quality_identity_mismatch", DISPOSITION_CONTINUE),
    "trial_only": (Outcome.REJECTED, "trial_only", DISPOSITION_CONTINUE),
    "download_target_untrusted": (Outcome.REJECTED, "download_target_untrusted", DISPOSITION_CONTINUE),
    "download_failed": (Outcome.REJECTED, "download_failed", DISPOSITION_CONTINUE),
    "integrity_mismatch": (Outcome.REJECTED, "integrity_mismatch", DISPOSITION_CONTINUE),
    "invalid_audio": (Outcome.REJECTED, "invalid_audio", DISPOSITION_CONTINUE),
    "tag_failed": (Outcome.REJECTED, "tag_failed", DISPOSITION_CONTINUE),
    # Rows 13-16 - route ABORT codes (environment prerequisites). §7.5: an abort
    # does NOT cancel the job; remaining positions stay queued and can resume.
    "disabled": (Outcome.FAILED, "disabled", DISPOSITION_ABORT),
    "config_invalid": (Outcome.FAILED, "config_invalid", DISPOSITION_ABORT),
    "busy": (Outcome.FAILED, "busy", DISPOSITION_ABORT),
    # §5.2 row 16 literal cell says ``staging_unavailable`` but the same row cites
    # "existing reason (manual_save_jobs.py:50)"; the existing ledger reason there
    # is ``stage_unavailable`` (the ledger whitelist has no ``staging_unavailable``,
    # and §5.5 does not add one). Mapping to the existing reason keeps the
    # authoritative FAILED+ABORT semantics guaranteed writable; flagged to Lead.
    "staging_unavailable": (Outcome.FAILED, "stage_unavailable", DISPOSITION_ABORT),
    # Rows 17-18 - publish failure / unexpected exception: outcome unknown, freeze.
    "publish_failed": (Outcome.COMMIT_UNKNOWN, "commit_unknown", DISPOSITION_FREEZE),
    "internal": (Outcome.COMMIT_UNKNOWN, "commit_unknown", DISPOSITION_FREEZE),
    # Row 19 - broken manifest contract: reject this position, abort dispatch.
    "invalid_song_id": (Outcome.REJECTED, "invalid_target", DISPOSITION_ABORT),
}

ROUTE_CONTINUE_CODES = frozenset(
    code for code, (_outcome, _reason, disposition) in _CODE_MAP.items()
    if disposition == DISPOSITION_CONTINUE
)
# ``invalid_song_id`` (row 19) is the adapter's defensive reject for a broken
# manifest; it is not part of the batch engine's fixed code table. ``freeze`` is
# the stricter sub-class of ``abort`` (stop dispatch AND reconcile manually), so
# the abort classification equals every non-continue batch code (§5.2).
ADAPTER_ONLY_CODES = frozenset({"invalid_song_id"})
ROUTE_ABORT_CODES = frozenset(
    code for code, (_outcome, _reason, disposition) in _CODE_MAP.items()
    if disposition in (DISPOSITION_ABORT, DISPOSITION_FREEZE)
) - ADAPTER_ONLY_CODES
FREEZE_CODES = frozenset(
    code for code, (_outcome, _reason, disposition) in _CODE_MAP.items()
    if disposition == DISPOSITION_FREEZE
)
# Reverse lookup for ``RunReport.result`` -> disposition (target pair is unique).
_DISPOSITION_BY_TARGET: dict[tuple[str, str], str] = {
    (outcome.value, reason): disposition
    for (outcome, reason, disposition) in _CODE_MAP.values()
}
_DISPOSITION_BY_TARGET[(Outcome.PUBLISHED.value, "bytes_published")] = DISPOSITION_CONTINUE

# Reason strings this adapter depends on the P1 ledger extension (§5.5) to accept.
# Centralised here; the ledger itself is out of this phase's write scope.
RESULT_REASON_EXTENSION: dict[str, frozenset[str]] = {
    Outcome.REJECTED.value: frozenset({
        "service_unavailable", "no_quality_available", "quality_unverified",
        "quality_identity_mismatch", "trial_only", "download_target_untrusted",
        "download_failed", "integrity_mismatch", "invalid_audio", "tag_failed",
    }),
    Outcome.FAILED.value: frozenset({"disabled", "config_invalid", "busy"}),
}

_COMMIT_UNKNOWN_TRIPLE = (Outcome.COMMIT_UNKNOWN, "commit_unknown", DISPOSITION_FREEZE)


def _commit_unknown() -> tuple[Outcome, str, str]:
    return _COMMIT_UNKNOWN_TRIPLE


def map_save_result(result: object) -> tuple[Outcome, str, str]:
    """Map one ``save_track`` envelope onto ``(Outcome, reason, disposition)``.

    Contract §5.2. This function never raises: non-Mapping values, non-boolean
    ``ok``, non-string ``code``, non-dict ``data``, unknown codes, inconsistent
    ``ok``/``code`` pairs and unrecognised success statuses all fall back to
    ``(COMMIT_UNKNOWN, 'commit_unknown', 'freeze')`` (rows 20-21). Raw response
    content is never echoed.
    """
    try:
        return _map_save_result_unchecked(result)
    except Exception:
        return _commit_unknown()


def _map_save_result_unchecked(result: object) -> tuple[Outcome, str, str]:
    if not isinstance(result, Mapping):
        return _commit_unknown()
    ok = result.get("ok")
    code = result.get("code")
    data = result.get("data")
    if type(ok) is not bool:
        return _commit_unknown()
    if type(code) is not str:
        return _commit_unknown()
    if not isinstance(data, dict):
        return _commit_unknown()
    if ok:
        # Only one success shape exists (row 1); any other success is unknown.
        if code != "ok" or data.get("status") != "published":
            return _commit_unknown()
        return (Outcome.PUBLISHED, "bytes_published", DISPOSITION_CONTINUE)
    # ``ok is False``; ``code == "ok"`` is an inconsistent envelope (row 20 class).
    if code == "ok":
        return _commit_unknown()
    mapped = _CODE_MAP.get(code)
    if mapped is None:
        return _commit_unknown()
    return mapped


def build_save_result(outcome: Outcome, reason: str) -> SaveResult:
    """Build the exact ``SaveResult`` the ledger validates.

    ``commit_unknown`` must carry ``cleanup_pending=True`` (ledger rule); every
    other outcome carries ``False``. Readiness flags are always strictly false.
    """
    cleanup_pending = outcome is Outcome.COMMIT_UNKNOWN
    return SaveResult(
        outcome,
        reason,
        cleanup_pending=cleanup_pending,
        audio_verified=False,
        production_ready=False,
    )


def disposition_for_save_result(result: object) -> str:
    """Derive the UI loop disposition from a persisted ``SaveResult`` (fail-closed)."""
    try:
        outcome = result.outcome
        reason = result.reason
        key_outcome = outcome.value if isinstance(outcome, Outcome) else outcome
        if type(key_outcome) is not str or type(reason) is not str:
            return DISPOSITION_FREEZE
        return _DISPOSITION_BY_TARGET.get((key_outcome, reason), DISPOSITION_FREEZE)
    except Exception:
        return DISPOSITION_FREEZE


# ---------------------------------------------------------------------------
# §5.1 executor adapter
# ---------------------------------------------------------------------------

def _position_song_id(position: object) -> int | None:
    """Return the positive int source id, or ``None`` when the position is invalid."""
    try:
        if position.source_status != "ok":
            return None
        source_id = position.source_id
        if type(source_id) is not str or re.fullmatch(r"[0-9]+", source_id) is None:
            return None
        song_id = int(source_id, 10)
        return song_id if song_id > 0 else None
    except Exception:
        return None


def _resolve_default_engine() -> object:
    """Import the route engine lazily so module import can never touch the network."""
    try:
        from . import manual_save_route as route
    except ImportError:  # pragma: no cover - flat import style.
        import manual_save_route as route
    return route


def make_executor(engine: object | None = None) -> Callable[[object, object], object]:
    """Return the ``run_next`` executor (contract §5.1).

    The executor passes only the numeric ``source_id`` to ``engine.save_track``,
    never passes ``deps`` (production default seam), ignores ``cancellation_check``
    (a single in-flight track is not cancellable; cancellation takes effect at the
    next position boundary) and never retries any continue/abort code.
    """
    resolved = _resolve_default_engine() if engine is None else engine
    save_track = getattr(resolved, "save_track", None)

    def executor(position: object, cancellation_check: object = None) -> SaveResult:
        # ``cancellation_check`` is deliberately not called (§5.1 rule 4).
        song_id = _position_song_id(position)
        if song_id is None:
            return build_save_result(Outcome.REJECTED, "invalid_target")
        try:
            raw = save_track(song_id)
            outcome, reason, _disposition = map_save_result(raw)
            result = build_save_result(outcome, reason)
            if outcome is Outcome.PUBLISHED and reason == "bytes_published":
                return PositionExecution(result, raw)
            return result
        except Exception:
            # Engine exceptions cannot rule out side effects: fail closed.
            return build_save_result(Outcome.COMMIT_UNKNOWN, "commit_unknown")

    return executor


# ---------------------------------------------------------------------------
# Producer client (loopback only, bounded response, structure re-checked)
# ---------------------------------------------------------------------------

# Contract §8.3: outbound producer requests are http-only loopback with bounded
# timeout and bounded body. ``::1`` is explicitly allowed by the contract; the
# whole 127/8 range is deliberately *not* allowed (only the canonical literal).
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
ALLOWED_PRODUCER_SCHEMES = frozenset({"http"})
SNAPSHOT_PATH_RE = "/api/v1/user/playlists/{playlist_id}/member-snapshot"
# Gateway-budget timeout is injectable (§8.3 ``MANUAL_SAVE_PLAYLIST_READ_TIMEOUT``);
# the contract's reviewed default is 120 s and it must never exceed the gateway's
# 1200 s save budget.
PLAYLIST_READ_TIMEOUT_ENV = "MANUAL_SAVE_PLAYLIST_READ_TIMEOUT"
DEFAULT_PLAYLIST_READ_TIMEOUT = 120.0
MAX_PLAYLIST_READ_TIMEOUT = 1200.0
# Response body cap is contract §8.3 "≤1 MiB", same magnitude as the route's
# MAX_JSON_BYTES; both the default and the hard ceiling are 1 MiB.
MAX_JSON_BYTES = 1 << 20
DEFAULT_MAX_RESPONSE_BYTES = MAX_JSON_BYTES
MAX_RESPONSE_BYTES_LIMIT = MAX_JSON_BYTES
MAX_BASE_URL_CHARS = 2048


def _strict_int(value: object) -> int | None:
    return value if type(value) is int else None


def _canonical_loopback_base_url(base_url: object) -> str:
    """Validate one producer base URL; every non-loopback target is rejected.

    Contract §8.3: scheme must be ``http``, host ∈ {``127.0.0.1``, ``localhost``,
    ``::1``}, and there is no path/query (an empty path and the technical ``/``
    form are both accepted and canonicalized away).
    """
    if type(base_url) is not str or not base_url:
        raise ProducerError(PRODUCER_CONFIG_INVALID)
    if len(base_url) > MAX_BASE_URL_CHARS:
        raise ProducerError(PRODUCER_NOT_LOOPBACK)
    for character in base_url:
        code = ord(character)
        if code < 0x20 or code == 0x7F or character in (" ", "\\"):
            raise ProducerError(PRODUCER_NOT_LOOPBACK)
    try:
        parts = urlsplit(base_url)
        port = parts.port
    except ValueError:
        raise ProducerError(PRODUCER_NOT_LOOPBACK) from None
    scheme = parts.scheme.casefold()
    if scheme not in ALLOWED_PRODUCER_SCHEMES:
        raise ProducerError(PRODUCER_NOT_LOOPBACK)
    if "@" in parts.netloc:
        raise ProducerError(PRODUCER_NOT_LOOPBACK)
    host = parts.hostname
    if type(host) is not str or host.casefold() not in LOOPBACK_HOSTS:
        raise ProducerError(PRODUCER_NOT_LOOPBACK)
    if parts.path not in ("", "/"):
        raise ProducerError(PRODUCER_NOT_LOOPBACK)
    if parts.query or parts.fragment:
        raise ProducerError(PRODUCER_NOT_LOOPBACK)
    host = host.casefold()
    canonical = scheme + "://" + ("[" + host + "]" if ":" in host else host)
    if port is not None:
        canonical += ":" + str(port)
    return canonical


def load_read_timeout(environ: Mapping[str, str] | None = None) -> float:
    """Read the bounded producer read timeout (contract §8.3).

    Invalid or out-of-range values fail closed with ``producer_config_invalid``
    instead of silently falling back to a different budget.
    """
    if environ is None:
        source: Mapping[str, str] = os.environ
    elif isinstance(environ, Mapping):
        source = environ
    else:
        raise ProducerError(PRODUCER_CONFIG_INVALID)
    raw = source.get(PLAYLIST_READ_TIMEOUT_ENV)
    if raw is None or raw == "":
        return DEFAULT_PLAYLIST_READ_TIMEOUT
    if type(raw) is not str:
        raise ProducerError(PRODUCER_CONFIG_INVALID)
    try:
        value = float(raw.strip())
    except (TypeError, ValueError):
        raise ProducerError(PRODUCER_CONFIG_INVALID) from None
    if not math.isfinite(value) or not 0 < value <= MAX_PLAYLIST_READ_TIMEOUT:
        raise ProducerError(PRODUCER_CONFIG_INVALID)
    return value


def _positive_playlist_id(playlist_id: object) -> int:
    value = _strict_int(playlist_id)
    if value is None or value <= 0:
        raise ProducerError(INVALID_PLAYLIST_ID)
    return value


def _reject_json_constant(_value: str) -> object:
    raise ValueError("invalid_constant")


def _parse_json_bytes(body: bytes) -> object:
    decoded = body.decode("utf-8")
    return json.loads(decoded, parse_constant=_reject_json_constant)


class _NoRedirectHandler(urllib_request.HTTPRedirectHandler):
    """Refuse every redirect: a loopback-only target must never hop elsewhere.

    ``HTTPRedirectHandler.redirect_request`` returning ``None`` makes urllib
    surface the 3xx as ``HTTPError`` instead of following it, so a producer
    response can never be satisfied by an external (or different) address.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


_HTTP_OPENER: object | None = None


def _get_http_opener() -> object:
    """Lazily build the redirect-free opener (import/build performs no request)."""
    global _HTTP_OPENER
    if _HTTP_OPENER is None:
        _HTTP_OPENER = urllib_request.build_opener(_NoRedirectHandler())
    return _HTTP_OPENER


def _default_http_get(url: str, timeout: float, max_bytes: int) -> tuple[int, bytes]:
    """Bounded GET seam: returns ``(status, body)``; never a request at import time.

    Redirects are refused twice (handler disabled + exact final-URL check), so a
    loopback URL can never be answered from another host/path.
    """
    request = urllib_request.Request(url, method="GET", headers={"Accept": "application/json"})
    opener = _get_http_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            if response.geturl() != url:
                raise ProducerError(PRODUCER_UNEXPECTED_URL)
            return response.status, response.read(max_bytes + 1)
    except ProducerError:
        raise
    except urllib_error.HTTPError as error:
        try:
            if 300 <= error.code < 400:
                # Any 3xx is rejected, loopback target or not (§8.3).
                raise ProducerError(PRODUCER_UNEXPECTED_URL) from None
            return error.code, error.read(max_bytes + 1)
        finally:
            try:
                error.close()
            except Exception:
                pass


def _unpack_http_response(response: object) -> tuple[int, bytes]:
    if not isinstance(response, (tuple, list)) or len(response) != 2:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    status, body = response
    if type(status) is not int or status < 100 or status > 599:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if type(body) is bytearray or isinstance(body, (bytes, bytearray)):
        return status, bytes(body)
    raise ProducerError(PRODUCER_MALFORMED_RESPONSE)


def _producer_error_from_body(body: bytes, max_bytes: int) -> str | None:
    """Fold a non-2xx fixed producer error body into its fixed code, or ``None``."""
    try:
        if len(body) > max_bytes:
            return None
        payload = _parse_json_bytes(body)
        if type(payload) is not dict:
            return None
        if payload.get("ok") is not False:
            return None
        error = payload.get("error")
        if type(error) is str and error in PRODUCER_ERROR_CODES:
            return error
        return None
    except Exception:
        return None


def _validate_snapshot_envelope(payload: object, playlist_id: int) -> dict:
    """Re-check the producer response structure instead of trusting ``ok``.

    The producer may report failures either as ``ok:false`` with a fixed
    ``error`` (HTTP-layer switch rejection) or as ``status`` + ``error_code``
    (snapshot-layer failure, contract §4.2). Both shapes are structurally
    accepted here; the status -> fixed-code classification happens in
    ``build_manifest`` so a failure payload is never misreported as malformed.
    """
    if type(payload) is not dict:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if "ok" not in payload:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    ok = payload["ok"]
    if type(ok) is not bool:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if ok is False:
        error = payload.get("error")
        if type(error) is str and error in PRODUCER_ERROR_CODES:
            raise ProducerError(error)
        # Fall through: producer snapshot failures carry status/error_code.
    for key in ("status", "provider", "schema_version", "playlist_id"):
        if key not in payload:
            raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    status = payload["status"]
    if type(status) is not str or status not in SNAPSHOT_STATUSES:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if status == "complete" and ok is not True:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if payload["provider"] != PROVIDER_NAME:
        raise ProducerError(SOURCE_ARTIFACT_MISMATCH)
    if _strict_int(payload["schema_version"]) != SNAPSHOT_SCHEMA_VERSION:
        raise ProducerError(SOURCE_ARTIFACT_MISMATCH)
    if status != "complete":
        # Snapshot failures may carry ``playlist_id: null`` (the producer folds
        # pre-read failures), but a *different* positive id is still rejected.
        served = payload["playlist_id"]
        if served is not None and _strict_int(served) != playlist_id:
            raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
        # No trustworthy enumeration exists; build_manifest maps the status to a
        # fixed code and never dispatches anything.
        return payload
    served_playlist_id = _strict_int(payload["playlist_id"])
    if served_playlist_id is None or served_playlist_id != playlist_id:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    for key in ("track_count", "position_count", "enumeration_complete",
                "source_fingerprint", "positions"):
        if key not in payload:
            raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if _strict_int(payload["track_count"]) is None or payload["track_count"] < 0:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if _strict_int(payload["position_count"]) is None or payload["position_count"] < 0:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if type(payload["enumeration_complete"]) is not bool:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if type(payload["source_fingerprint"]) is not str:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    if type(payload["positions"]) is not list:
        raise ProducerError(PRODUCER_MALFORMED_RESPONSE)
    return payload


class PlaylistSnapshotClient:
    """Loopback-only producer client; the HTTP seam is injectable for offline tests."""

    def __init__(
        self,
        base_url: object,
        *,
        http_get: Callable[[str, float, int], object] | None = None,
        timeout: object = None,
        max_response_bytes: object = DEFAULT_MAX_RESPONSE_BYTES,
    ):
        self._base_url = _canonical_loopback_base_url(base_url)
        if timeout is None:
            # P5 injects the gateway budget; the default comes from the reviewed
            # contract env key instead of a private hard-coded number.
            timeout = load_read_timeout()
        if (
            type(timeout) not in (int, float)
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or not 0 < float(timeout) <= MAX_PLAYLIST_READ_TIMEOUT
        ):
            raise ProducerError(PRODUCER_CONFIG_INVALID)
        if (
            _strict_int(max_response_bytes) is None
            or not 1 <= max_response_bytes <= MAX_RESPONSE_BYTES_LIMIT
        ):
            raise ProducerError(PRODUCER_CONFIG_INVALID)
        self._timeout = float(timeout)
        self._max_response_bytes = int(max_response_bytes)
        self._http_get = http_get if http_get is not None else _default_http_get

    def snapshot_url(self, playlist_id: object) -> str:
        value = _positive_playlist_id(playlist_id)
        return self._base_url + SNAPSHOT_PATH_RE.format(playlist_id=value)

    def fetch_snapshot(self, playlist_id: object) -> dict:
        """Fetch and structurally re-check one member snapshot payload."""
        value = _positive_playlist_id(playlist_id)
        url = self._base_url + SNAPSHOT_PATH_RE.format(playlist_id=value)
        try:
            raw_response = self._http_get(url, self._timeout, self._max_response_bytes)
        except ProducerError:
            raise
        except Exception as error:
            if isinstance(error, TimeoutError) or isinstance(
                getattr(error, "reason", None), TimeoutError
            ):
                raise ProducerError(PRODUCER_TIMEOUT) from None
            if isinstance(error, (socket.timeout, OSError, urllib_error.URLError)):
                raise ProducerError(PRODUCER_UNREACHABLE) from None
            raise ProducerError(PRODUCER_UNREACHABLE) from None
        status, body = _unpack_http_response(raw_response)
        if status == 200:
            if len(body) > self._max_response_bytes:
                raise ProducerError(PRODUCER_RESPONSE_TOO_LARGE)
            try:
                payload = _parse_json_bytes(body)
            except Exception:
                raise ProducerError(PRODUCER_MALFORMED_RESPONSE) from None
            return _validate_snapshot_envelope(payload, value)
        folded = _producer_error_from_body(body, self._max_response_bytes)
        raise ProducerError(folded or PRODUCER_HTTP_STATUS)


# ---------------------------------------------------------------------------
# §4.3-§4.6 ledger manifest construction
# ---------------------------------------------------------------------------

# Snapshot status -> fixed error code (contract §3.3/§3.9.1; never dispatch a
# non-complete snapshot). ``unsupported`` is split by the producer's
# ``artifact_error_code`` (-> 500 source_artifact_mismatch) and by the position
# limit error codes (-> 409 playlist_too_large); anything else stays 409
# playlist_unsupported.
_POSITION_LIMIT_ERROR_CODES = frozenset({"POSITION_LIMIT_EXCEEDED", "SOURCE_LIMIT_REACHED"})


def _failure_code_for_status(payload: Mapping) -> str:
    """Map a non-complete snapshot status onto its reviewed fixed code."""
    status = payload.get("status")
    if status == "unsupported":
        if payload.get("artifact_error_code") == SOURCE_ARTIFACT_MISMATCH:
            return SOURCE_ARTIFACT_MISMATCH
        error_code = payload.get("error_code")
        if type(error_code) is str and error_code in _POSITION_LIMIT_ERROR_CODES:
            return PLAYLIST_TOO_LARGE
        return PLAYLIST_UNSUPPORTED
    if status == "incomplete":
        return MANIFEST_INCOMPLETE
    if status == "source_changed":
        return SOURCE_CHANGED
    if status == "upstream_error":
        return PLAYLIST_UNAVAILABLE
    if status == "not_logged_in":
        return NOT_LOGGED_IN
    # Unknown/unlisted status fails closed as an incomplete manifest.
    return MANIFEST_INCOMPLETE


def build_manifest(playlist_id: object, payload: object) -> dict:
    """Build the closed seven-field ledger manifest from one snapshot payload.

    Structural violations raise ``ManifestError(fixed_code)`` (§4.3.1): any
    mismatch is rejected, never silently repaired by truncating or padding.
    Semantic incompleteness (a claimed-false flag or an invalid track id) is
    *retained* per §4.5: every position stays in ``entries`` (duplicates too) and
    ``enumeration_complete`` becomes false, so the creation layer refuses the job
    while the manifest still shows exactly what the producer returned.
    """
    try:
        return _build_manifest(playlist_id, payload)
    except ManifestError:
        raise
    except Exception:
        raise ManifestError(MANIFEST_INCOMPLETE) from None


def _build_manifest(playlist_id: object, payload: object) -> dict:
    value = _strict_int(playlist_id)
    if value is None or value <= 0:
        raise ManifestError(INVALID_PLAYLIST_ID)
    if not isinstance(payload, Mapping):
        raise ManifestError(MANIFEST_INCOMPLETE)
    status = payload.get("status")
    if status != "complete":
        raise ManifestError(_failure_code_for_status(payload))
    if payload.get("ok") is not True:
        raise ManifestError(MANIFEST_INCOMPLETE)
    if payload.get("provider") != PROVIDER_NAME:
        raise ManifestError(SOURCE_ARTIFACT_MISMATCH)
    if _strict_int(payload.get("schema_version")) != SNAPSHOT_SCHEMA_VERSION:
        raise ManifestError(SOURCE_ARTIFACT_MISMATCH)
    served_playlist_id = _strict_int(payload.get("playlist_id"))
    if served_playlist_id is None or served_playlist_id != value:
        raise ManifestError(MANIFEST_INCOMPLETE)

    track_count = _strict_int(payload.get("track_count"))
    position_count = _strict_int(payload.get("position_count"))
    if track_count is None or position_count is None:
        raise ManifestError(MANIFEST_INCOMPLETE)
    if track_count > MAX_TRACKS:
        raise ManifestError(PLAYLIST_TOO_LARGE)
    if track_count < 1:
        raise ManifestError(MANIFEST_INCOMPLETE)
    positions = payload.get("positions")
    if type(positions) is not list:
        raise ManifestError(MANIFEST_INCOMPLETE)
    # §4.6: never "fix" the declared count from the returned positions.
    if len(positions) != track_count or position_count != track_count:
        raise ManifestError(MANIFEST_INCOMPLETE)
    fingerprint = payload.get("source_fingerprint")
    if type(fingerprint) is not str or FINGERPRINT_RE.fullmatch(fingerprint) is None:
        raise ManifestError(MANIFEST_INCOMPLETE)
    claimed_complete = payload.get("enumeration_complete")
    if type(claimed_complete) is not bool:
        raise ManifestError(MANIFEST_INCOMPLETE)

    entries: list[dict[str, object]] = []
    every_position_valid = True
    for expected_position, item in enumerate(positions, start=1):
        if not isinstance(item, Mapping):
            raise ManifestError(MANIFEST_INCOMPLETE)
        if "track_id" not in item or "error_code" not in item:
            raise ManifestError(MANIFEST_INCOMPLETE)
        position = item.get("position")
        if _strict_int(position) != expected_position:
            raise ManifestError(MANIFEST_INCOMPLETE)
        error_code = item.get("error_code")
        if error_code is not None and error_code != "INVALID_TRACK_ID":
            raise ManifestError(MANIFEST_INCOMPLETE)
        track_id = item.get("track_id")
        valid = (
            error_code is None
            and _strict_int(track_id) is not None
            and track_id > 0
        )
        if valid:
            # Duplicate source ids are kept at their original positions (§4.5).
            entries.append({
                "position": expected_position,
                "source_id": str(track_id),
                "source_status": "ok",
            })
        else:
            # §4.5: invalid positions stay visible (as invalid_id) but force the
            # whole enumeration to be incomplete; the creation layer refuses it.
            entries.append({
                "position": expected_position,
                "source_id": None,
                "source_status": "invalid_id",
            })
            every_position_valid = False

    return {
        "kind": "playlist",
        "target_id": TARGET_ID,
        "provider_id": PROVIDER_ID,
        "collection_id": PLAYLIST_COLLECTION_PREFIX + str(value),
        "collection_version": COLLECTION_VERSION_PREFIX + fingerprint,
        "enumeration_complete": bool(claimed_complete and every_position_valid),
        "entries": entries,
    }


# ---------------------------------------------------------------------------
# §2.4 actor scope derivation (trusted gateway-side claim; never parsed from headers)
# ---------------------------------------------------------------------------

JOBS_DB_ENV = "FNMUSIC_MANUAL_SAVE_JOBS_DB"
PLAYLIST_ENABLED_ENV = "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED"
MAX_ACTOR_CLAIM_BYTES = 256
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"", "0", "false", "no", "off"})
MAX_DB_PATH_CHARS = 4096


def derive_actor_scope(user_id_claim: object) -> str:
    """Derive ``"u" + sha256(claim_bytes).hexdigest()[:40]`` (contract §2.4).

    The caller must pass the already-validated canonical claim; this function
    never reads request headers. The raw claim is hashed and discarded.
    """
    if type(user_id_claim) is not str:
        raise ServiceError(INVALID_ACTOR)
    try:
        claim_bytes = user_id_claim.encode("utf-8")
    except UnicodeError:
        raise ServiceError(INVALID_ACTOR) from None
    if not 1 <= len(claim_bytes) <= MAX_ACTOR_CLAIM_BYTES:
        raise ServiceError(INVALID_ACTOR)
    if any(character in user_id_claim for character in ("\x00", "\r", "\n")):
        raise ServiceError(INVALID_ACTOR)
    return "u" + hashlib.sha256(claim_bytes).hexdigest()[:40]


def _parse_enabled(value: object) -> bool:
    if type(value) is not str:
        return False
    normalized = value.strip().casefold()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    # Unknown values fail closed (default off).
    return False


def _valid_database_path(value: object) -> bool:
    if type(value) is not str or not value or len(value) > MAX_DB_PATH_CHARS:
        return False
    if any(character in value for character in ("\x00", "\r", "\n")):
        return False
    try:
        return bool(os.path.isabs(value))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True, slots=True, repr=False)
class PlaylistJobServiceConfig:
    """Service configuration; ``repr`` never exposes the database path."""

    db_path: str | None = None
    enabled: bool = False

    def __repr__(self) -> str:
        database = "<set>" if self.db_path else "<unset>"
        return f"PlaylistJobServiceConfig(enabled={self.enabled}, db_path={database})"


def load_service_config(environ: Mapping[str, str] | None = None) -> PlaylistJobServiceConfig:
    """Read the only two service switches; defaults are off / no database."""
    source: Mapping[str, str]
    if environ is None:
        source = os.environ
    elif isinstance(environ, Mapping):
        source = environ
    else:
        raise ServiceError(CONFIG_INVALID)
    enabled = _parse_enabled(source.get(PLAYLIST_ENABLED_ENV))
    raw_path = source.get(JOBS_DB_ENV)
    db_path = raw_path if _valid_database_path(raw_path) else None
    return PlaylistJobServiceConfig(db_path=db_path, enabled=enabled)


# ---------------------------------------------------------------------------
# Thin service wrapper (all ledger work delegated; no SQL here)
# ---------------------------------------------------------------------------

# Contract §7.1: the gateway-wide save lock. Acquired before claim, released in
# ``finally``; a lost race returns ``busy`` without touching the ledger.
MANUAL_SAVE_PLAYLIST_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class AdvanceReport:
    """Result of exactly one ``run_next`` attempt (one track at most)."""

    status: str
    position: PositionSnapshot | None = None
    result: SaveResult | None = None
    disposition: str | None = None
    detail: dict[str, object] | None = None


def position_detail_for_wire(value: object) -> dict[str, object] | None:
    """Re-check the exact details whitelist at the HTTP serialization boundary."""
    return validate_position_detail(value)


def _default_idempotency_key(playlist_id: int, manifest: Mapping) -> str:
    collection_version = manifest.get("collection_version")
    fingerprint = ""
    if type(collection_version) is str and collection_version.startswith(COLLECTION_VERSION_PREFIX):
        fingerprint = collection_version[len(COLLECTION_VERSION_PREFIX):]
    return "p" + str(playlist_id) + "-" + fingerprint


class PlaylistJobService:
    """Thin service layer over ``ManualSaveJobsLedger`` (contract §5.1, §7)."""

    def __init__(
        self,
        config: PlaylistJobServiceConfig,
        producer: object,
        *,
        engine: object | None = None,
        ledger: object | None = None,
    ):
        self._config = config
        self._producer = producer
        self._engine = engine
        self._ledger = ledger
        self._executor: object | None = None

    # -- internals ---------------------------------------------------------

    def _require_enabled(self) -> None:
        if not self._config.enabled:
            # The playlist-jobs switch is the reviewed "整歌单保存未启用" (403
            # playlist_disabled, §3.9.1), not the engine's single-track code.
            raise ServiceError(PLAYLIST_DISABLED)
        if self._config.db_path is None and self._ledger is None:
            raise ServiceError(CONFIG_INVALID)

    def _get_ledger(self) -> object:
        if self._ledger is None:
            if self._config.db_path is None:
                raise ServiceError(CONFIG_INVALID)
            self._ledger = ManualSaveJobsLedger(self._config.db_path)
        return self._ledger

    def _get_executor(self) -> object:
        if self._executor is None:
            self._executor = make_executor(self._engine)
        return self._executor

    # -- public API --------------------------------------------------------

    def create_job(
        self,
        actor_scope: object,
        playlist_id: object,
        payload: object,
        *,
        idempotency_key: object | None = None,
    ) -> object:
        """Build the manifest and create one job; incomplete manifests are refused."""
        self._require_enabled()
        manifest = build_manifest(playlist_id, payload)
        if not manifest["enumeration_complete"]:
            raise ServiceError(MANIFEST_INCOMPLETE)
        value = _strict_int(playlist_id)
        key = idempotency_key
        if key is None:
            key = _default_idempotency_key(value, manifest)
        return self._get_ledger().create_job(
            actor_scope, key, manifest, pause_on_non_continue=True,
        )

    def create_from_producer(
        self,
        actor_scope: object,
        playlist_id: object,
        *,
        idempotency_key: object | None = None,
    ) -> object:
        """Fetch the snapshot through the loopback producer, then create the job."""
        self._require_enabled()
        payload = self._producer.fetch_snapshot(playlist_id)
        return self.create_job(
            actor_scope, playlist_id, payload, idempotency_key=idempotency_key,
        )

    def get_job(self, actor_scope: object, job_id: object) -> object:
        self._require_enabled()
        return self._get_ledger().get_job(
            actor_scope, job_id, pause_on_non_continue=True,
        )

    def cancel_job(self, actor_scope: object, job_id: object) -> object:
        self._require_enabled()
        return self._get_ledger().cancel_job(actor_scope, job_id)

    def advance_once(self, actor_scope: object, job_id: object) -> AdvanceReport:
        """Advance at most one position under the gateway lock (contract §7.1)."""
        if not self._config.enabled:
            return AdvanceReport(PLAYLIST_DISABLED)
        if not MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False):
            # No ledger write and no position consumed: ``busy`` is not a failure.
            return AdvanceReport(BUSY)
        try:
            ledger = self._get_ledger()
            executor = self._get_executor()
            run_report = ledger.run_next(
                actor_scope, job_id, executor, pause_on_non_continue=True,
            )
        finally:
            MANUAL_SAVE_PLAYLIST_LOCK.release()
        return _advance_report(run_report)


def _advance_report(run_report: object) -> AdvanceReport:
    status = getattr(run_report, "status", None)
    position = getattr(run_report, "position", None)
    result = getattr(run_report, "result", None)
    if status == "completed":
        return AdvanceReport(
            "completed",
            position=position,
            result=result,
            disposition=disposition_for_save_result(result),
            detail=getattr(run_report, "detail", None),
        )
    if status == "paused":
        return AdvanceReport("paused", position=position, result=result,
                             disposition=DISPOSITION_ABORT)
    if status in {"no_work", "stale_completion"}:
        return AdvanceReport(status, position=position, result=result, disposition=None)
    if status in {"commit_unknown", "completion_unknown"}:
        return AdvanceReport(
            status, position=position, result=result, disposition=DISPOSITION_FREEZE,
        )
    return AdvanceReport("internal", position=position, result=result, disposition=DISPOSITION_FREEZE)


__all__ = [
    # Errors / fixed codes.
    "PlaylistJobError",
    "ProducerError",
    "ManifestError",
    "ServiceError",
    "fixed_message",
    "RECONCILIATION_REQUIRED_BANNER",
    # §5.2 mapping.
    "map_save_result",
    "build_save_result",
    "disposition_for_save_result",
    "position_detail_for_wire",
    "make_executor",
    "ROUTE_CONTINUE_CODES",
    "ROUTE_ABORT_CODES",
    "ADAPTER_ONLY_CODES",
    "FREEZE_CODES",
    "DISPOSITIONS",
    "DISPOSITION_CONTINUE",
    "DISPOSITION_ABORT",
    "DISPOSITION_FREEZE",
    "RESULT_REASON_EXTENSION",
    # §4.2-§4.6 producer / manifest.
    "PlaylistSnapshotClient",
    "build_manifest",
    "SNAPSHOT_STATUSES",
    "PRODUCER_ERROR_CODES",
    "TARGET_ID",
    "PROVIDER_ID",
    "MAX_TRACKS",
    "LOOPBACK_HOSTS",
    "DEFAULT_PLAYLIST_READ_TIMEOUT",
    "MAX_PLAYLIST_READ_TIMEOUT",
    "PLAYLIST_READ_TIMEOUT_ENV",
    "MAX_JSON_BYTES",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "MAX_RESPONSE_BYTES_LIMIT",
    "load_read_timeout",
    # §3.3/§3.9.1 snapshot failure codes.
    "PRODUCER_UNEXPECTED_URL",
    "PLAYLIST_DISABLED",
    "PLAYLIST_UNSUPPORTED",
    "PLAYLIST_UNAVAILABLE",
    "SOURCE_CHANGED",
    "NOT_LOGGED_IN",
    # §2.4 / config / service.
    "derive_actor_scope",
    "load_service_config",
    "PlaylistJobServiceConfig",
    "PlaylistJobService",
    "AdvanceReport",
    "MANUAL_SAVE_PLAYLIST_LOCK",
    "JOBS_DB_ENV",
    "PLAYLIST_ENABLED_ENV",
]
