"""Offline SQLite ledger for explicitly dispatched manual-save positions.

This module stores job intent and byte-publication outcomes only. It does not
authenticate callers, access media, publish files, or start background work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import secrets
import sqlite3
import unicodedata
from typing import Callable

try:  # Support both ``proxy.manual_save_jobs`` and a direct proxy path import.
    from .manual_save_core import Outcome, SaveResult
except ImportError:  # pragma: no cover - exercised by the repository's test style.
    from manual_save_core import Outcome, SaveResult


APPLICATION_ID = 0x464E4A42  # "FNJB"
SCHEMA_VERSION = 2
BUSY_TIMEOUT_MS = 1000
MAX_JOBS = 1000
MAX_POSITIONS = 10_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ENTRIES_PER_JOB = 1000
MAX_IDENTIFIER_CHARS = 128

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_JOB_ID_RE = re.compile(r"^[0-9a-f]{48}$")
_SOURCE_STATUSES = frozenset({"ok", "invalid_id", "unavailable", "missing"})
_POSITION_STATES = frozenset({"queued", "running"} | {item.value for item in Outcome})
# A playlist position has no persisted disposition column. Preserve the reviewed
# CONTINUE table here as outcome/reason pairs so a lost HTTP response cannot
# cause a later request (including a request from another page) to step past a
# previously completed ABORT. Any stored terminal pair outside this allow-list
# is a stop condition.
_CONTINUE_POSITION_RESULTS = frozenset({
    (Outcome.PUBLISHED.value, "bytes_published"),
    (Outcome.CONFLICT.value, "destination_conflict"),
    (Outcome.REJECTED.value, "service_unavailable"),
    (Outcome.REJECTED.value, "no_quality_available"),
    (Outcome.REJECTED.value, "quality_unverified"),
    (Outcome.REJECTED.value, "quality_identity_mismatch"),
    (Outcome.REJECTED.value, "trial_only"),
    (Outcome.REJECTED.value, "download_target_untrusted"),
    (Outcome.REJECTED.value, "download_failed"),
    (Outcome.REJECTED.value, "integrity_mismatch"),
    (Outcome.REJECTED.value, "invalid_audio"),
    (Outcome.REJECTED.value, "tag_failed"),
})
_OUTCOME_REASONS = {
    Outcome.PUBLISHED.value: frozenset({"bytes_published"}),
    Outcome.PUBLISHED_CLEANUP_PENDING.value: frozenset({"published_cleanup_pending"}),
    Outcome.CONFLICT.value: frozenset({"destination_conflict"}),
    Outcome.CANCELLED.value: frozenset({"cancelled_before_publish"}),
    # REJECTED/FAILED 的 reason 白名单同时覆盖两套固定码表：
    # 1) 单文件核心的 invalid_*/unsupported_*/staging_* 等（既有）；
    # 2) 管理页单曲/歌单保存路由的失败码（FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01 §5.5）。
    # 只扩展字符串白名单，不改变 DB schema：旧行继续合法，重开校验通过。
    Outcome.REJECTED.value: frozenset({
        "invalid_target", "invalid_format", "unsupported_ncm", "unsupported_format",
        "invalid_basename", "invalid_limit", "invalid_length", "invalid_plan",
        "invalid_chunks", "invalid_storage", "invalid_cancel_check", "too_many_chunks",
        "invalid_chunk", "empty_chunk", "chunk_too_large", "stream_too_long",
        "length_mismatch",
        # 路由 CONTINUE 失败码（逐位置可继续执行）。
        "service_unavailable", "no_quality_available", "quality_unverified",
        "quality_identity_mismatch", "trial_only", "download_target_untrusted",
        "download_failed", "integrity_mismatch", "invalid_audio", "tag_failed",
    }),
    Outcome.FAILED.value: frozenset({
        "stage_unavailable", "staging_failed", "cancel_check_failed",
        # 路由 ABORT 失败码（环境前置/并发，需停止本轮推进）。
        "disabled", "config_invalid", "busy",
    }),
    Outcome.COMMIT_UNKNOWN.value: frozenset({"commit_unknown"}),
}


_CREATE_JOBS = """CREATE TABLE jobs (
    job_id TEXT NOT NULL PRIMARY KEY,
    actor_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('single', 'playlist')),
    target_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    collection_version TEXT NOT NULL,
    enumeration_complete INTEGER NOT NULL CHECK (enumeration_complete IN (0, 1)),
    manifest_json BLOB NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    UNIQUE (actor_scope, idempotency_key)
)"""
_CREATE_POSITIONS = """CREATE TABLE positions (
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 1),
    source_id TEXT,
    source_status TEXT NOT NULL CHECK (source_status IN ('ok', 'invalid_id', 'unavailable', 'missing')),
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'published', 'published_cleanup_pending', 'conflict', 'cancelled', 'rejected', 'failed', 'commit_unknown')),
    outcome TEXT,
    reason TEXT,
    cleanup_pending INTEGER CHECK (cleanup_pending IN (0, 1)),
    claim_token TEXT,
    PRIMARY KEY (job_id, position),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
    CHECK (
        (state = 'queued' AND outcome IS NULL AND reason IS NULL AND cleanup_pending IS NULL AND claim_token IS NULL)
        OR (state = 'running' AND outcome IS NULL AND reason IS NULL AND cleanup_pending IS NULL AND claim_token IS NOT NULL)
        OR (state NOT IN ('queued', 'running') AND outcome = state AND reason IS NOT NULL AND cleanup_pending IS NOT NULL AND claim_token IS NULL)
    )
)"""
_CREATE_RUNNING_INDEX = "CREATE UNIQUE INDEX one_running_position_per_job ON positions(job_id) WHERE state = 'running'"
_CREATE_POSITION_DETAILS = """CREATE TABLE position_details (
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 1),
    source_id TEXT NOT NULL,
    detail_json BLOB NOT NULL,
    PRIMARY KEY (job_id, position),
    FOREIGN KEY (job_id, position) REFERENCES positions(job_id, position) ON DELETE CASCADE
)"""
_EXPECTED_TABLES = {
    "jobs": _CREATE_JOBS,
    "positions": _CREATE_POSITIONS,
    "position_details": _CREATE_POSITION_DETAILS,
}
_EXPECTED_INDEXES = {"one_running_position_per_job": _CREATE_RUNNING_INDEX}
_EXPECTED_INTERNAL_INDEXES = frozenset({
    ("index", "sqlite_autoindex_jobs_1"),
    ("index", "sqlite_autoindex_jobs_2"),
    ("index", "sqlite_autoindex_positions_1"),
    ("index", "sqlite_autoindex_position_details_1"),
})
_DETAIL_LEGACY_KEYS = frozenset({
    "status", "title", "artist", "album", "quality_actual",
    "quality_actual_status", "format", "cover_status", "cover_embedded",
    "lyric_status",
})
_DETAIL_KEYS = _DETAIL_LEGACY_KEYS | frozenset({"published_sha256", "published_size"})
_DETAIL_TEXT_LIMITS = {"title": 512, "artist": 512, "album": 512}
_DETAIL_QUALITY_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DETAIL_URL_RE = re.compile(r"(?:https?://|ftp://|www\.)", re.IGNORECASE)
_DETAIL_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:authorization|set-cookie|cookie|api[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|password|secret)\s*[:=]",
    re.IGNORECASE,
)
# Metadata is user-visible and durable, so reject absolute paths embedded in
# text without treating ordinary slash-separated titles (for example, "A/B")
# as paths. Generic multi-component paths use token boundaries; common roots
# and explicit path labels also catch single-component absolute paths.
_DETAIL_POSIX_ROOT_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?:vol(?:ume)?\d+|home|users?|tmp|var|mnt|media|root|etc|opt|"
    r"srv|proc|sys|dev|run|boot|data|storage|shares?|private|workspace|downloads?|"
    r"documents?)(?:/|$)",
    re.IGNORECASE,
)
_DETAIL_POSIX_WHOLE_PATH_RE = re.compile(
    r"^\s*/[^/\s]+(?:\s+[^/\s]+)*(?:/[^/\s]+(?:\s+[^/\s]+)*)*\s*$"
)
_DETAIL_POSIX_EMBEDDED_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_.-])/(?!\s)[^/\s]+(?:\s+[^/\s]+)*"
    r"(?:/[^/\s]+(?:\s+[^/\s]+)*)*"
)
_DETAIL_POSIX_LABELED_PATH_RE = re.compile(
    r"(?:^|[\s([{;,])(?:path|file|directory|folder|location|路径|文件路径|目录|位置)"
    r"\s*[:=：]\s*/[^/\s]+(?:\s+[^/\s]+)*(?:/[^/\s]+(?:\s+[^/\s]+)*)*",
    re.IGNORECASE,
)
_DETAIL_WINDOWS_PATH_RE = re.compile(r"(?<![A-Za-z0-9_])[a-z]:[\\/][^\r\n]*", re.IGNORECASE)
_DETAIL_UNC_PATH_RE = re.compile(r"(?<![\\/])(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+")
_DETAIL_FORMATS = frozenset({"flac", "mp3", "unknown"})
_DETAIL_COVER_STATUSES = frozenset({
    "preserved", "embedded", "unavailable", "unsupported", "error",
    "not_published", "not_attempted", "unknown",
})
_DETAIL_LYRIC_STATUSES = frozenset({
    "published", "unavailable", "error", "conflict", "not_attempted", "unknown",
})


def _unsafe_detail_text(value: str) -> bool:
    if any(unicodedata.category(character) in {"Cc", "Cs", "Zl", "Zp"} for character in value):
        return True
    # Formatting controls can visually split any sensitive marker, and
    # compatibility characters can disguise URL schemes, credentials, or path
    # boundaries. Normalize only the detection copy; the stored/displayed
    # metadata remains the original text when it is safe.
    detection_probe = unicodedata.normalize("NFKC", "".join(
        character for character in value
        if unicodedata.category(character) != "Cf"
        and not unicodedata.category(character).startswith("M")
    ))
    return any(pattern.search(detection_probe) for pattern in (
        _DETAIL_URL_RE,
        _DETAIL_SECRET_RE,
        _DETAIL_POSIX_ROOT_RE,
        _DETAIL_POSIX_WHOLE_PATH_RE,
        _DETAIL_POSIX_EMBEDDED_PATH_RE,
        _DETAIL_POSIX_LABELED_PATH_RE,
        _DETAIL_WINDOWS_PATH_RE,
        _DETAIL_UNC_PATH_RE,
    ))


class LedgerError(Exception):
    """Base error with a fixed, non-sensitive public code."""

    code = "ledger_error"

    def __init__(self, code: str | None = None):
        self.code = code or type(self).code
        super().__init__(self.code)


class ValidationError(LedgerError):
    code = "invalid_request"


class NotFoundError(LedgerError):
    code = "not_found"


class IdempotencyConflict(LedgerError):
    code = "idempotency_conflict"


class CapacityExceeded(LedgerError):
    code = "capacity_exceeded"


class SchemaError(LedgerError):
    code = "invalid_schema"


class CorruptLedgerError(LedgerError):
    code = "corrupt_ledger"


class LedgerUnavailable(LedgerError):
    code = "ledger_unavailable"


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    position: int
    source_id: str | None
    source_status: str
    state: str
    outcome: str | None
    reason: str | None
    cleanup_pending: bool | None
    detail: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    manifest: dict[str, object]
    manifest_sha256: str
    cancel_requested: bool
    state: str
    positions: tuple[PositionSnapshot, ...]
    counts: dict[str, int]
    audio_verified: bool = False
    production_ready: bool = False
    import_ready: bool = False


@dataclass(frozen=True, slots=True)
class RunReport:
    status: str
    position: PositionSnapshot | None = None
    result: SaveResult | None = None
    detail: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class PositionExecution:
    """One executor outcome plus an ephemeral, untrusted route response.

    ``save_response`` is never returned to callers or written as-is. The ledger
    extracts its fixed published whitelist only after validating the response
    and matching its song id to the claimed manifest position.
    """

    result: SaveResult
    save_response: object


def _validate_identifier(value: object) -> str:
    if type(value) is not str or len(value) > MAX_IDENTIFIER_CHARS or not _IDENTIFIER_RE.fullmatch(value):
        raise ValidationError("invalid_identifier")
    return value


def _canonical_positive_source_id(value: object) -> bool:
    return (
        type(value) is str
        and re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None
        and int(value, 10) <= (1 << 63) - 1
    )


def _validate_manifest(manifest: object) -> tuple[dict[str, object], bytes, str]:
    required = {
        "kind", "target_id", "provider_id", "collection_id", "collection_version",
        "enumeration_complete", "entries",
    }
    if type(manifest) is not dict or set(manifest) != required:
        raise ValidationError("invalid_manifest_fields")

    kind = manifest["kind"]
    if type(kind) is not str or kind not in {"single", "playlist"}:
        raise ValidationError("invalid_kind")
    target_id = _validate_identifier(manifest["target_id"])
    provider_id = _validate_identifier(manifest["provider_id"])
    collection_id = _validate_identifier(manifest["collection_id"])
    collection_version = _validate_identifier(manifest["collection_version"])
    complete = manifest["enumeration_complete"]
    if type(complete) is not bool:
        raise ValidationError("invalid_enumeration_complete")
    entries = manifest["entries"]
    if type(entries) is not list:
        raise ValidationError("invalid_entries")
    if kind == "single" and len(entries) != 1:
        raise ValidationError("single_requires_one_entry")
    if kind == "playlist" and not 1 <= len(entries) <= MAX_ENTRIES_PER_JOB:
        raise ValidationError("invalid_playlist_size")

    canonical_entries: list[dict[str, object]] = []
    for expected_position, entry in enumerate(entries, start=1):
        if type(entry) is not dict or set(entry) != {"position", "source_id", "source_status"}:
            raise ValidationError("invalid_entry_fields")
        position = entry["position"]
        if type(position) is not int or position != expected_position:
            raise ValidationError("invalid_position_order")
        source_id = entry["source_id"]
        if source_id is not None:
            source_id = _validate_identifier(source_id)
        source_status = entry["source_status"]
        if type(source_status) is not str or source_status not in _SOURCE_STATUSES:
            raise ValidationError("invalid_source_status")
        canonical_entries.append({
            "position": position,
            "source_id": source_id,
            "source_status": source_status,
        })

    canonical: dict[str, object] = {
        "kind": kind,
        "target_id": target_id,
        "provider_id": provider_id,
        "collection_id": collection_id,
        "collection_version": collection_version,
        "enumeration_complete": complete,
        "entries": canonical_entries,
    }
    try:
        manifest_bytes = json.dumps(
            canonical, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValidationError("invalid_manifest") from None
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValidationError("manifest_too_large")
    return canonical, manifest_bytes, hashlib.sha256(manifest_bytes).hexdigest()


def _manifest_from_bytes(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value.decode("utf-8"), parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()))
        canonical, encoded, _digest = _validate_manifest(decoded)
    except Exception:
        raise CorruptLedgerError() from None
    if encoded != value:
        raise CorruptLedgerError()
    return canonical


def _fixed_result(outcome: Outcome = Outcome.COMMIT_UNKNOWN) -> SaveResult:
    return SaveResult(outcome, "commit_unknown", cleanup_pending=True)


def _validated_result(value: object) -> SaveResult | None:
    if type(value) is not SaveResult:
        return None
    outcome = value.outcome
    if type(outcome) is not Outcome:
        return None
    if not _valid_outcome_fields(outcome.value, value.reason, value.cleanup_pending, database_value=False):
        return None
    if type(value.audio_verified) is not bool or value.audio_verified is not False:
        return None
    if type(value.production_ready) is not bool or value.production_ready is not False:
        return None
    return value


def _published_source_matches(save_response: object, source_id: str | None) -> bool:
    """Prove that a published response is bound to this exact manifest source id."""
    if type(save_response) is not dict or source_id is None:
        return False
    if not _canonical_positive_source_id(source_id):
        return False
    if save_response.get("ok") is not True or save_response.get("code") != "ok":
        return False
    data = save_response.get("data")
    if type(data) is not dict or data.get("status") != "published":
        return False
    song_id = data.get("song_id")
    if (
        type(song_id) is not int or song_id <= 0 or song_id > (1 << 63) - 1
        or str(song_id) != source_id
    ):
        return False
    return True


def _published_detail(save_response: object, source_id: str | None) -> dict[str, object] | None:
    """Extract only the bounded published metadata for the matching source id."""
    if not _published_source_matches(save_response, source_id):
        return None
    data = save_response["data"]

    clean_text: dict[str, str] = {}
    for name, limit in _DETAIL_TEXT_LIMITS.items():
        value = data.get(name)
        valid = type(value) is str and len(value) <= limit
        if valid:
            valid = not _unsafe_detail_text(value)
        # Text fields are independent display metadata. A bad/missing value is
        # represented as empty, while source-bound quality/format/cover/lyric
        # evidence below remains available.
        clean_text[name] = value if valid else ""

    quality_status = data.get("quality_actual_status")
    quality_actual = data.get("quality_actual")
    if (
        quality_status == "reported"
        and type(quality_actual) is str
        and _DETAIL_QUALITY_RE.fullmatch(quality_actual)
    ):
        actual_status, actual = "reported", quality_actual
    else:
        actual_status, actual = "unknown", ""

    media_format = data.get("format")
    if type(media_format) is not str or media_format not in {"flac", "mp3"}:
        media_format = "unknown"
    cover_status = data.get("cover_status")
    if type(cover_status) is not str or cover_status not in _DETAIL_COVER_STATUSES:
        cover_status = "unknown"
    cover_embedded = data.get("cover_embedded")
    if type(cover_embedded) is not bool:
        cover_embedded = None
    lyric_status = data.get("lyric_status")
    if type(lyric_status) is not str or lyric_status not in _DETAIL_LYRIC_STATUSES:
        lyric_status = "unknown"

    detail = {
        "status": "published",
        **clean_text,
        "quality_actual": actual,
        "quality_actual_status": actual_status,
        "format": media_format,
        "cover_status": cover_status,
        "cover_embedded": cover_embedded,
        "lyric_status": lyric_status,
    }
    digest = data.get("published_sha256")
    size = data.get("published_size")
    if (type(digest) is str and re.fullmatch(r"[0-9a-f]{64}", digest)
            and type(size) is int and not isinstance(size, bool) and 0 < size <= (2**63 - 1)):
        detail["published_sha256"] = digest
        detail["published_size"] = size
    # Missing or malformed evidence stays absent, so old records and legacy
    # engines remain readable and the UI can explicitly show it as unknown.
    return detail


def validate_position_detail(value: object) -> dict[str, object] | None:
    """Validate the exact durable detail schema; reject rather than truncate."""
    if type(value) is not dict or set(value) not in (_DETAIL_LEGACY_KEYS, _DETAIL_KEYS):
        return None
    if value.get("status") != "published":
        return None
    for name, limit in _DETAIL_TEXT_LIMITS.items():
        item = value.get(name)
        if type(item) is not str or len(item) > limit:
            return None
        if _unsafe_detail_text(item):
            return None
    quality_status = value.get("quality_actual_status")
    quality = value.get("quality_actual")
    if quality_status == "reported":
        if type(quality) is not str or _DETAIL_QUALITY_RE.fullmatch(quality) is None:
            return None
    elif quality_status == "unknown":
        if quality != "" or type(quality) is not str:
            return None
    else:
        return None
    if type(value.get("format")) is not str or value["format"] not in _DETAIL_FORMATS:
        return None
    if type(value.get("cover_status")) is not str or value["cover_status"] not in _DETAIL_COVER_STATUSES:
        return None
    if value.get("cover_embedded") is not None and type(value["cover_embedded"]) is not bool:
        return None
    if type(value.get("lyric_status")) is not str or value["lyric_status"] not in _DETAIL_LYRIC_STATUSES:
        return None
    if set(value) == _DETAIL_KEYS:
        digest = value.get("published_sha256")
        size = value.get("published_size")
        if (type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None
                or type(size) is not int or isinstance(size, bool) or not 0 < size <= (2**63 - 1)):
            return None
    keys = (
        "status", "title", "artist", "album", "quality_actual", "quality_actual_status",
        "format", "cover_status", "cover_embedded", "lyric_status",
    )
    if set(value) == _DETAIL_KEYS:
        keys += ("published_sha256", "published_size")
    return {key: value[key] for key in keys}


def _encode_detail(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=True, allow_nan=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _decode_detail(value: object) -> dict[str, object]:
    if type(value) is not bytes:
        raise CorruptLedgerError()
    try:
        decoded = json.loads(value.decode("utf-8"), parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()))
        clean = validate_position_detail(decoded)
        if clean is None or _encode_detail(clean) != value:
            raise ValueError()
    except Exception:
        raise CorruptLedgerError() from None
    return clean


def _valid_outcome_fields(outcome: object, reason: object, cleanup_pending: object, *, database_value: bool) -> bool:
    expected_cleanup_type = int if database_value else bool
    if type(outcome) is not str or outcome not in _OUTCOME_REASONS:
        return False
    if type(reason) is not str or reason not in _OUTCOME_REASONS[outcome]:
        return False
    if type(cleanup_pending) is not expected_cleanup_type:
        return False
    if cleanup_pending not in ((0, 1) if database_value else (False, True)):
        return False
    if outcome in {Outcome.COMMIT_UNKNOWN.value, Outcome.PUBLISHED_CLEANUP_PENDING.value}:
        return cleanup_pending in (1, True)
    if outcome == Outcome.PUBLISHED.value:
        return cleanup_pending in (0, False)
    return True


def _validate_stored_position(row: sqlite3.Row) -> bool | None:
    """Validate raw SQLite values before any snapshot conversion."""
    try:
        position = row["position"]
        source_id = row["source_id"]
        source_status = row["source_status"]
        state = row["state"]
        outcome = row["outcome"]
        reason = row["reason"]
        cleanup_pending = row["cleanup_pending"]
        claim_token = row["claim_token"]
        if type(position) is not int or position < 1:
            raise ValueError
        if source_id is not None:
            _validate_identifier(source_id)
        if type(source_status) is not str or source_status not in _SOURCE_STATUSES:
            raise ValueError
        if type(state) is not str or state not in _POSITION_STATES:
            raise ValueError
        if state == "queued":
            if any(value is not None for value in (outcome, reason, cleanup_pending, claim_token)):
                raise ValueError
            return None
        if state == "running":
            if (
                outcome is not None or reason is not None or cleanup_pending is not None
                or type(claim_token) is not str or not _JOB_ID_RE.fullmatch(claim_token)
            ):
                raise ValueError
            return None
        if (
            outcome != state
            or claim_token is not None
            or not _valid_outcome_fields(outcome, reason, cleanup_pending, database_value=True)
        ):
            raise ValueError
        return bool(cleanup_pending)
    except CorruptLedgerError:
        raise
    except Exception:
        raise CorruptLedgerError() from None


def _validate_job_row(job: sqlite3.Row) -> dict[str, object]:
    try:
        if type(job["job_id"]) is not str or not _JOB_ID_RE.fullmatch(job["job_id"]):
            raise ValueError
        _validate_identifier(job["actor_scope"])
        _validate_identifier(job["idempotency_key"])
        manifest_bytes = job["manifest_json"]
        if type(manifest_bytes) is not bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError
        manifest = _manifest_from_bytes(manifest_bytes)
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        if digest != job["manifest_sha256"]:
            raise ValueError
        if (
            job["kind"] != manifest["kind"]
            or job["target_id"] != manifest["target_id"]
            or job["provider_id"] != manifest["provider_id"]
            or job["collection_id"] != manifest["collection_id"]
            or job["collection_version"] != manifest["collection_version"]
            or type(job["enumeration_complete"]) is not int
            or job["enumeration_complete"] not in (0, 1)
            or bool(job["enumeration_complete"]) is not manifest["enumeration_complete"]
            or type(job["cancel_requested"]) is not int
            or job["cancel_requested"] not in (0, 1)
        ):
            raise ValueError
        return manifest
    except CorruptLedgerError:
        raise
    except Exception:
        raise CorruptLedgerError() from None


class ManualSaveJobsLedger:
    """A caller-owned, durable SQLite ledger with explicit synchronous dispatch."""

    def __init__(self, database_path: str | os.PathLike[str]):
        try:
            path = os.fspath(database_path)
        except TypeError:
            raise ValidationError("absolute_database_path_required") from None
        if type(path) is not str or not os.path.isabs(path):
            raise ValidationError("absolute_database_path_required")
        self._database_path = path
        self._initialize_or_validate()

    def _connect(self) -> sqlite3.Connection:
        connection = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            if foreign_keys != 1 or synchronous != 2:
                connection.close()
                raise LedgerUnavailable()
            return connection
        except LedgerError:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            raise
        except sqlite3.DatabaseError as error:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            corrupt_codes = {
                getattr(sqlite3, "SQLITE_CORRUPT", -1),
                getattr(sqlite3, "SQLITE_NOTADB", -2),
            }
            if getattr(error, "sqlite_errorcode", None) in corrupt_codes:
                raise CorruptLedgerError() from None
            raise LedgerUnavailable() from None
        except sqlite3.Error:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            raise LedgerUnavailable() from None

    def _initialize_or_validate(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            objects = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()

            is_new_database = not objects and application_id == 0 and schema_version == 0
            if not is_new_database:
                self._validate_schema(connection, application_id, schema_version, objects)

            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise CorruptLedgerError()

            if is_new_database:
                connection.execute(_CREATE_JOBS)
                connection.execute(_CREATE_POSITIONS)
                connection.execute(_CREATE_RUNNING_INDEX)
                connection.execute(_CREATE_POSITION_DETAILS)
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            self._validate_contents(connection)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.DatabaseError as error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            corrupt_codes = {
                getattr(sqlite3, "SQLITE_CORRUPT", -1),
                getattr(sqlite3, "SQLITE_NOTADB", -2),
            }
            if getattr(error, "sqlite_errorcode", None) in corrupt_codes:
                raise CorruptLedgerError() from None
            raise LedgerUnavailable() from None
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    @staticmethod
    def _validate_schema(connection, application_id, schema_version, objects) -> None:
        expected_objects = {
            ("table", name): sql for name, sql in _EXPECTED_TABLES.items()
        }
        expected_objects.update({
            ("index", name): sql for name, sql in _EXPECTED_INDEXES.items()
        })
        expected_objects.update({name: None for name in _EXPECTED_INTERNAL_INDEXES})
        actual_objects = {(row["type"], row["name"]): row["sql"] for row in objects}
        if len(actual_objects) != len(objects) or set(actual_objects) != set(expected_objects):
            raise SchemaError()
        if application_id != APPLICATION_ID or schema_version != SCHEMA_VERSION:
            raise SchemaError("unknown_schema_version")
        for object_key, expected_sql in expected_objects.items():
            actual_sql = actual_objects[object_key]
            if expected_sql is None:
                if actual_sql is not None:
                    raise SchemaError()
            elif type(actual_sql) is not str or actual_sql != expected_sql:
                raise SchemaError()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(positions)").fetchall()
        if len(foreign_keys) != 1 or tuple(foreign_keys[0][2:6]) != ("jobs", "job_id", "job_id", "NO ACTION"):
            raise SchemaError()
        if foreign_keys[0][6] != "CASCADE":
            raise SchemaError()
        detail_foreign_keys = connection.execute(
            "PRAGMA foreign_key_list(position_details)"
        ).fetchall()
        detail_foreign_keys.sort(key=lambda row: row[1])
        if (
            len(detail_foreign_keys) != 2
            or [tuple(row[2:6]) for row in detail_foreign_keys]
            != [("positions", "job_id", "job_id", "NO ACTION"),
                ("positions", "position", "position", "NO ACTION")]
            or any(row[6] != "CASCADE" for row in detail_foreign_keys)
        ):
            raise SchemaError()
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise SchemaError()

    @staticmethod
    def _validate_contents(connection: sqlite3.Connection) -> None:
        jobs = connection.execute("SELECT * FROM jobs ORDER BY job_id").fetchall()
        if len(jobs) > MAX_JOBS:
            raise CorruptLedgerError()
        position_total = connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        if position_total > MAX_POSITIONS:
            raise CorruptLedgerError()
        for job in jobs:
            manifest = _validate_job_row(job)
            entries = manifest["entries"]
            rows = connection.execute(
                "SELECT * FROM positions WHERE job_id = ? ORDER BY position", (job["job_id"],),
            ).fetchall()
            if len(rows) != len(entries):
                raise CorruptLedgerError()
            running_count = 0
            for expected, row in zip(entries, rows, strict=True):
                _validate_stored_position(row)
                if (
                    row["position"] != expected["position"]
                    or row["source_id"] != expected["source_id"]
                    or row["source_status"] != expected["source_status"]
                ):
                    raise CorruptLedgerError()
                if row["state"] == "running":
                    running_count += 1
            if running_count > 1:
                raise CorruptLedgerError()
        details = connection.execute(
            """SELECT d.job_id, d.position, d.source_id, d.detail_json,
                      p.source_id AS position_source_id, p.state
               FROM position_details AS d
               JOIN positions AS p ON p.job_id = d.job_id AND p.position = d.position
               ORDER BY d.job_id, d.position"""
        ).fetchall()
        for detail in details:
            decoded = _decode_detail(detail["detail_json"])
            if (
                detail["state"] != Outcome.PUBLISHED.value
                or detail["source_id"] != detail["position_source_id"]
                or not _canonical_positive_source_id(detail["source_id"])
                or decoded["status"] != "published"
            ):
                raise CorruptLedgerError()
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise CorruptLedgerError()

    def _begin_immediate(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            raise LedgerUnavailable() from None

    def create_job(
        self,
        actor_scope: object,
        idempotency_key: object,
        manifest: object,
        *,
        pause_on_non_continue: bool = False,
    ) -> JobSnapshot:
        scope = _validate_identifier(actor_scope)
        key = _validate_identifier(idempotency_key)
        if type(pause_on_non_continue) is not bool:
            raise ValidationError("invalid_pause_policy")
        canonical, manifest_bytes, digest = _validate_manifest(manifest)
        entries = canonical["entries"]
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            prior = connection.execute(
                "SELECT job_id, manifest_json FROM jobs WHERE actor_scope = ? AND idempotency_key = ?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if bytes(prior["manifest_json"]) != manifest_bytes:
                    raise IdempotencyConflict()
                connection.commit()
                return self._snapshot(
                    connection, prior["job_id"], pause_on_non_continue=pause_on_non_continue,
                )

            jobs_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            positions_count = connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            if jobs_count + 1 > MAX_JOBS or positions_count + len(entries) > MAX_POSITIONS:
                raise CapacityExceeded()

            job_id = secrets.token_hex(24)
            connection.execute(
                """INSERT INTO jobs (
                    job_id, actor_scope, idempotency_key, kind, target_id, provider_id,
                    collection_id, collection_version, enumeration_complete,
                    manifest_json, manifest_sha256, cancel_requested
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    job_id, scope, key, canonical["kind"], canonical["target_id"],
                    canonical["provider_id"], canonical["collection_id"], canonical["collection_version"],
                    int(canonical["enumeration_complete"]), sqlite3.Binary(manifest_bytes), digest,
                ),
            )
            connection.executemany(
                "INSERT INTO positions (job_id, position, source_id, source_status, state) VALUES (?, ?, ?, ?, 'queued')",
                [
                    (job_id, entry["position"], entry["source_id"], entry["source_status"])
                    for entry in entries
                ],
            )
            connection.commit()
            return self._snapshot(
                connection, job_id, pause_on_non_continue=pause_on_non_continue,
            )
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def get_job(
        self,
        actor_scope: object,
        job_id: object,
        *,
        pause_on_non_continue: bool = False,
    ) -> JobSnapshot:
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        if type(pause_on_non_continue) is not bool:
            raise ValidationError("invalid_pause_policy")
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE actor_scope = ? AND job_id = ?", (scope, identifier),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            return self._snapshot(
                connection, identifier, pause_on_non_continue=pause_on_non_continue,
            )
        except LedgerError:
            raise
        except sqlite3.Error:
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def cancel_job(self, actor_scope: object, job_id: object) -> JobSnapshot:
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE actor_scope = ? AND job_id = ?", (scope, identifier),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            connection.execute("UPDATE jobs SET cancel_requested = 1 WHERE job_id = ?", (identifier,))
            connection.execute(
                """UPDATE positions
                   SET state = 'cancelled', outcome = 'cancelled', reason = 'cancelled_before_publish',
                       cleanup_pending = 0, claim_token = NULL
                   WHERE job_id = ? AND state = 'queued'""",
                (identifier,),
            )
            connection.commit()
            return self._snapshot(connection, identifier)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def run_next(
        self,
        actor_scope: object,
        job_id: object,
        executor: Callable[[PositionSnapshot, Callable[[], bool]], object],
        *,
        pause_on_non_continue: bool = False,
    ) -> RunReport:
        """Claim one earliest position, commit it, then invoke the executor outside SQL.

        ``pause_on_non_continue`` is opt-in for the playlist adapter. The core
        ledger's default remains its existing generic queue semantics.
        """
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        if type(pause_on_non_continue) is not bool:
            raise ValidationError("invalid_pause_policy")
        if not callable(executor):
            raise ValidationError("invalid_executor")
        claimed = self._claim_next(
            scope, identifier, pause_on_non_continue=pause_on_non_continue,
        )
        if claimed == "paused":
            return RunReport("paused")
        if claimed is None:
            return RunReport("no_work")
        position, claim_token = claimed

        def cancellation_requested() -> bool:
            try:
                return self.get_job(scope, identifier).cancel_requested
            except Exception:
                return True

        try:
            candidate = executor(position, cancellation_requested)
        except Exception:
            candidate = None
        execution = candidate if type(candidate) is PositionExecution else None
        result = _validated_result(execution.result if execution is not None else candidate)
        if result is None:
            result = _fixed_result()
        detail = None
        if result.outcome is Outcome.PUBLISHED:
            source_matches = (
                execution is not None
                and _published_source_matches(execution.save_response, position.source_id)
            )
            if (execution is not None and not source_matches) or (
                pause_on_non_continue and execution is None
            ):
                # The engine says bytes were published, but this claim cannot be
                # bound to the manifest source id. Preserve the uncertainty and
                # freeze the batch; never report a false publish/conflict or
                # dispatch a later position.
                result = _fixed_result()
            elif execution is not None:
                # Identity is proven even if optional display metadata is not;
                # invalid/missing quality remains explicitly unknown.
                detail = _published_detail(execution.save_response, position.source_id)

        completion = self._complete(
            scope, identifier, position.position, claim_token, result, detail,
        )
        if completion is None:
            return RunReport("completion_unknown", result=_fixed_result())
        if completion is False:
            return RunReport("stale_completion", position=position, result=result)
        status = "commit_unknown" if result.outcome is Outcome.COMMIT_UNKNOWN else "completed"
        final_position = PositionSnapshot(
            position=position.position,
            source_id=position.source_id,
            source_status=position.source_status,
            state=result.outcome.value,
            outcome=result.outcome.value,
            reason=result.reason,
            cleanup_pending=result.cleanup_pending,
            detail=detail,
        )
        return RunReport(status, position=final_position, result=result, detail=detail)

    def _claim_next(
        self,
        actor_scope: str,
        job_id: str,
        *,
        pause_on_non_continue: bool = False,
    ) -> tuple[PositionSnapshot, str] | str | None:
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            job = connection.execute(
                "SELECT * FROM jobs WHERE actor_scope = ? AND job_id = ?", (actor_scope, job_id),
            ).fetchone()
            if job is None:
                raise NotFoundError()
            if connection.execute(
                "SELECT 1 FROM positions WHERE job_id = ? AND state = 'commit_unknown' LIMIT 1", (job_id,),
            ).fetchone() is not None:
                connection.commit()
                return None
            if connection.execute(
                "SELECT 1 FROM positions WHERE job_id = ? AND state = 'running' LIMIT 1", (job_id,),
            ).fetchone() is not None:
                connection.commit()
                return None
            if job["cancel_requested"]:
                connection.commit()
                return None
            if not job["enumeration_complete"]:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT * FROM positions WHERE job_id = ? AND state = 'queued' ORDER BY position LIMIT 1",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            # Check persisted terminal results in the same BEGIN IMMEDIATE
            # transaction as the claim, but only when queued work would
            # otherwise be consumed. A fully processed job with no queued work
            # remains ordinary no_work. Unknown and non-CONTINUE results pause.
            if pause_on_non_continue:
                terminal_rows = connection.execute(
                    "SELECT outcome, reason FROM positions WHERE job_id = ? AND state NOT IN ('queued', 'running')",
                    (job_id,),
                ).fetchall()
                if any(
                    (item["outcome"], item["reason"]) not in _CONTINUE_POSITION_RESULTS
                    for item in terminal_rows
                ):
                    connection.commit()
                    return "paused"
            token = secrets.token_hex(24)
            cursor = connection.execute(
                "UPDATE positions SET state = 'running', claim_token = ? WHERE job_id = ? AND position = ? AND state = 'queued'",
                (token, job_id, row["position"]),
            )
            if cursor.rowcount != 1:
                raise LedgerUnavailable()
            connection.commit()
            position = PositionSnapshot(
                position=row["position"],
                source_id=row["source_id"],
                source_status=row["source_status"],
                state="running",
                outcome=None,
                reason=None,
                cleanup_pending=None,
            )
            return position, token
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def _complete(
        self, actor_scope: str, job_id: str, position: int, claim_token: str,
        result: SaveResult, detail: dict[str, object] | None = None,
    ) -> bool | None:
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT p.source_id FROM positions AS p
                   WHERE p.job_id = ? AND p.position = ? AND p.state = 'running'
                     AND p.claim_token = ?
                     AND EXISTS (SELECT 1 FROM jobs AS j
                                 WHERE j.job_id = p.job_id AND j.actor_scope = ?)""",
                (job_id, position, claim_token, actor_scope),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            clean_detail = validate_position_detail(detail) if result.outcome is Outcome.PUBLISHED else None
            if clean_detail is not None and not _canonical_positive_source_id(row["source_id"]):
                clean_detail = None
            cursor = connection.execute(
                """UPDATE positions SET state = ?, outcome = ?, reason = ?, cleanup_pending = ?, claim_token = NULL
                   WHERE job_id = ? AND position = ? AND state = 'running' AND claim_token = ?
                   AND EXISTS (SELECT 1 FROM jobs WHERE job_id = ? AND actor_scope = ?)""",
                (
                    result.outcome.value, result.outcome.value, result.reason, int(result.cleanup_pending),
                    job_id, position, claim_token, job_id, actor_scope,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            if clean_detail is not None:
                connection.execute(
                    "INSERT INTO position_details(job_id, position, source_id, detail_json) VALUES (?, ?, ?, ?)",
                    (job_id, position, row["source_id"], _encode_detail(clean_detail)),
                )
            connection.commit()
            return True
        except LedgerError:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            return None
        except sqlite3.Error:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            return None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

    def recover_stale_running(
        self, actor_scope: object, job_id: object, *, worker_stopped: bool,
    ) -> JobSnapshot:
        """Explicitly freeze running positions after the caller asserts the old worker stopped."""
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        if type(worker_stopped) is not bool or not worker_stopped:
            raise ValidationError("worker_stopped_assertion_required")
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            job = connection.execute(
                "SELECT job_id FROM jobs WHERE actor_scope = ? AND job_id = ?", (scope, identifier),
            ).fetchone()
            if job is None:
                raise NotFoundError()
            connection.execute(
                """UPDATE positions
                   SET state = 'commit_unknown', outcome = 'commit_unknown', reason = 'commit_unknown',
                       cleanup_pending = 1, claim_token = NULL
                   WHERE job_id = ? AND state = 'running'""",
                (identifier,),
            )
            connection.commit()
            return self._snapshot(connection, identifier)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def _snapshot(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        pause_on_non_continue: bool = False,
    ) -> JobSnapshot:
        if connection.in_transaction:
            return self._snapshot_in_transaction(
                connection, job_id, pause_on_non_continue=pause_on_non_continue,
            )
        try:
            connection.execute("BEGIN")
            snapshot = self._snapshot_in_transaction(
                connection, job_id, pause_on_non_continue=pause_on_non_continue,
            )
            connection.commit()
            return snapshot
        except Exception:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise

    def _snapshot_in_transaction(
        self,
        connection: sqlite3.Connection,
        job_id: str,
        *,
        pause_on_non_continue: bool = False,
    ) -> JobSnapshot:
        job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job is None:
            raise NotFoundError()
        manifest = _validate_job_row(job)
        rows = connection.execute(
            "SELECT * FROM positions WHERE job_id = ? ORDER BY position", (job_id,),
        ).fetchall()
        entries = manifest["entries"]
        if len(rows) != len(entries):
            raise CorruptLedgerError()
        detail_rows = connection.execute(
            """SELECT d.position, d.source_id, d.detail_json,
                      p.source_id AS position_source_id, p.state
               FROM position_details AS d
               JOIN positions AS p ON p.job_id = d.job_id AND p.position = d.position
               WHERE d.job_id = ? ORDER BY d.position""",
            (job_id,),
        ).fetchall()
        details: dict[int, dict[str, object]] = {}
        for detail_row in detail_rows:
            if (
                detail_row["state"] != Outcome.PUBLISHED.value
                or detail_row["source_id"] != detail_row["position_source_id"]
                or not _canonical_positive_source_id(detail_row["source_id"])
            ):
                raise CorruptLedgerError()
            details[detail_row["position"]] = _decode_detail(detail_row["detail_json"])
        positions_list = []
        running_count = 0
        for expected, row in zip(entries, rows, strict=True):
            cleanup_pending = _validate_stored_position(row)
            if (
                row["position"] != expected["position"]
                or row["source_id"] != expected["source_id"]
                or row["source_status"] != expected["source_status"]
            ):
                raise CorruptLedgerError()
            if row["state"] == "running":
                running_count += 1
            positions_list.append(PositionSnapshot(
                position=row["position"],
                source_id=row["source_id"],
                source_status=row["source_status"],
                state=row["state"],
                outcome=row["outcome"],
                reason=row["reason"],
                cleanup_pending=cleanup_pending,
                detail=details.get(row["position"]),
            ))
        if running_count > 1:
            raise CorruptLedgerError()
        positions = tuple(positions_list)
        counts = {state: 0 for state in sorted(_POSITION_STATES)}
        for position in positions:
            counts[position.state] += 1
        if counts[Outcome.COMMIT_UNKNOWN.value]:
            summary_state = "reconciliation_required"
        elif counts["running"]:
            summary_state = "cancelling" if job["cancel_requested"] else "running"
        elif counts["queued"]:
            if job["cancel_requested"]:
                summary_state = "cancelled"
            elif pause_on_non_continue and any(
                (position.outcome, position.reason) not in _CONTINUE_POSITION_RESULTS
                for position in positions if position.state not in {"queued", "running"}
            ):
                summary_state = "paused"
            else:
                summary_state = "queued"
        elif counts["cancelled"] == len(positions):
            summary_state = "cancelled"
        elif counts[Outcome.PUBLISHED.value] == len(positions):
            summary_state = "completed_bytes_reported"
        elif counts[Outcome.PUBLISHED.value] or counts[Outcome.PUBLISHED_CLEANUP_PENDING.value]:
            summary_state = "partial"
        else:
            summary_state = "completed_with_issues"
        return JobSnapshot(
            job_id=job_id,
            manifest=manifest,
            manifest_sha256=job["manifest_sha256"],
            cancel_requested=bool(job["cancel_requested"]),
            state=summary_state,
            positions=positions,
            counts=counts,
            audio_verified=False,
            production_ready=False,
            import_ready=False,
        )

    @staticmethod
    def _validate_job_id(job_id: object) -> str:
        if type(job_id) is not str or not _JOB_ID_RE.fullmatch(job_id):
            raise NotFoundError()
        return job_id


__all__ = [
    "CapacityExceeded", "CorruptLedgerError", "IdempotencyConflict", "JobSnapshot",
    "LedgerError", "LedgerUnavailable", "ManualSaveJobsLedger", "NotFoundError",
    "PositionSnapshot", "RunReport", "SchemaError", "ValidationError",
]
