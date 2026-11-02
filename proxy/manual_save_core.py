"""Offline-only single-file byte publication protocol.

This module does not authenticate callers, inspect audio, open paths, or provide
a production storage adapter. A caller must supply an implementation whose
``publish_noreplace`` operation is atomic and never replaces an existing name.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re
import unicodedata


HARD_MAX_BYTES = 512 * 1024 * 1024
MAX_CHUNKS = 4096
MAX_CHUNK_BYTES = 1024 * 1024
MAX_BASENAME_CHARS = 180
MAX_TARGET_ID_CHARS = 128
SUPPORTED_FORMATS = frozenset({"aac", "aiff", "ape", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"})
_NCM_MAGIC = b"CTENFDAM"
_FORMAT_RE = re.compile(r"^[a-z0-9]{2,8}$", re.IGNORECASE)
_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_WINDOWS_RESERVED = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


class Outcome(str, Enum):
    PUBLISHED = "published"
    PUBLISHED_CLEANUP_PENDING = "published_cleanup_pending"
    CONFLICT = "conflict"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    FAILED = "failed"
    COMMIT_UNKNOWN = "commit_unknown"


class StoreResult(str, Enum):
    PUBLISHED = "published"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class SavePlan:
    target_id: str
    basename: str
    source_format: str
    expected_bytes: int
    max_bytes: int


@dataclass(frozen=True, slots=True)
class SaveResult:
    outcome: Outcome
    reason: str
    cleanup_pending: bool = False
    audio_verified: bool = False
    production_ready: bool = False


def _valid_basename(name: object, source_format: str) -> bool:
    if type(name) is not str or not name or len(name) > MAX_BASENAME_CHARS:
        return False
    if name in {".", ".."} or name[-1] in {" ", "."}:
        return False
    if any(ch in name for ch in ("/", "\\", ":")):
        return False
    if any(unicodedata.category(ch) in {"Cc", "Cs"} for ch in name):
        return False
    if name.startswith(("/", "\\")):
        return False
    stem, dot, extension = name.rpartition(".")
    if not dot or not stem or extension.casefold() != source_format:
        return False
    device_component = name.split(".", 1)[0].rstrip(" .").upper()
    if device_component in _WINDOWS_RESERVED:
        return False
    return True


def plan_single_file(
    target_id: object,
    basename: object,
    source_format: object,
    expected_bytes: object,
    max_bytes: object = HARD_MAX_BYTES,
) -> SavePlan | SaveResult:
    """Validate an internal save request without performing storage operations."""
    if type(target_id) is not str or not _TARGET_RE.fullmatch(target_id):
        return SaveResult(Outcome.REJECTED, "invalid_target")
    if type(source_format) is not str or not _FORMAT_RE.fullmatch(source_format):
        return SaveResult(Outcome.REJECTED, "invalid_format")
    fmt = source_format.casefold()
    if fmt == "ncm" or (type(basename) is str and basename.casefold().endswith(".ncm")):
        return SaveResult(Outcome.REJECTED, "unsupported_ncm")
    if fmt not in SUPPORTED_FORMATS:
        return SaveResult(Outcome.REJECTED, "unsupported_format")
    if not _valid_basename(basename, fmt):
        return SaveResult(Outcome.REJECTED, "invalid_basename")
    if type(max_bytes) is not int or not 1 <= max_bytes <= HARD_MAX_BYTES:
        return SaveResult(Outcome.REJECTED, "invalid_limit")
    if type(expected_bytes) is not int or not 1 <= expected_bytes <= max_bytes:
        return SaveResult(Outcome.REJECTED, "invalid_length")
    return SavePlan(target_id, basename, fmt, expected_bytes, max_bytes)


def execute_single_file(plan: object, chunks: object, storage: object, cancelled: object = None) -> SaveResult:
    """Run the injected byte staging/publish protocol once.

    Exceptions are converted to fixed reasons. A publish exception has an
    unknown outcome, so the core deliberately retains its stage for controlled
    reconciliation and never retries or removes a possible final object.
    """
    if not isinstance(plan, SavePlan):
        return SaveResult(Outcome.REJECTED, "invalid_plan")
    validated_plan = plan_single_file(
        plan.target_id, plan.basename, plan.source_format,
        plan.expected_bytes, plan.max_bytes,
    )
    if not isinstance(validated_plan, SavePlan):
        return validated_plan
    plan = validated_plan
    if not callable(getattr(chunks, "__iter__", None)):
        return SaveResult(Outcome.REJECTED, "invalid_chunks")
    if not all(callable(getattr(storage, method, None)) for method in (
        "begin_owned_stage", "append", "seal", "publish_noreplace",
        "abort_owned_stage", "release_owned_stage",
    )):
        return SaveResult(Outcome.REJECTED, "invalid_storage")
    if cancelled is not None and not callable(cancelled):
        return SaveResult(Outcome.REJECTED, "invalid_cancel_check")

    stage = None
    count = 0
    chunk_count = 0
    prefix = bytearray()

    def clean_failure(outcome: Outcome, reason: str) -> SaveResult:
        if stage is None:
            return SaveResult(outcome, reason)
        try:
            storage.abort_owned_stage(stage)
        except Exception:
            return SaveResult(outcome, reason, cleanup_pending=True)
        return SaveResult(outcome, reason)

    try:
        stage = storage.begin_owned_stage(plan.target_id)
        if stage is None:
            return SaveResult(Outcome.FAILED, "stage_unavailable")
    except Exception:
        return SaveResult(Outcome.FAILED, "stage_unavailable")

    try:
        iterator = iter(chunks)
        for chunk in iterator:
            if cancelled is not None and cancelled():
                return clean_failure(Outcome.CANCELLED, "cancelled_before_publish")
            if chunk_count >= MAX_CHUNKS:
                return clean_failure(Outcome.REJECTED, "too_many_chunks")
            if type(chunk) is not bytes:
                return clean_failure(Outcome.REJECTED, "invalid_chunk")
            if not chunk:
                return clean_failure(Outcome.REJECTED, "empty_chunk")
            if len(chunk) > MAX_CHUNK_BYTES:
                return clean_failure(Outcome.REJECTED, "chunk_too_large")
            chunk_count += 1
            count += len(chunk)
            if count > plan.expected_bytes or count > plan.max_bytes:
                return clean_failure(Outcome.REJECTED, "stream_too_long")
            if len(prefix) < len(_NCM_MAGIC):
                prefix.extend(chunk[: len(_NCM_MAGIC) - len(prefix)])
            if bytes(prefix) == _NCM_MAGIC:
                return clean_failure(Outcome.REJECTED, "unsupported_ncm")
            storage.append(stage, chunk)
        if count != plan.expected_bytes:
            return clean_failure(Outcome.REJECTED, "length_mismatch")
        if cancelled is not None and cancelled():
            return clean_failure(Outcome.CANCELLED, "cancelled_before_publish")
        storage.seal(stage)
    except Exception:
        return clean_failure(Outcome.FAILED, "staging_failed")

    # Sealing can take time. Recheck cancellation after it completes and before
    # publishing so a request that arrived during seal cannot publish the file.
    if cancelled is not None:
        try:
            if cancelled():
                return clean_failure(Outcome.CANCELLED, "cancelled_before_publish")
        except Exception:
            return clean_failure(Outcome.FAILED, "cancel_check_failed")

    try:
        published = storage.publish_noreplace(stage, plan.basename)
    except Exception:
        return SaveResult(Outcome.COMMIT_UNKNOWN, "commit_unknown", cleanup_pending=True)

    if published == StoreResult.CONFLICT or published == StoreResult.CONFLICT.value:
        return clean_failure(Outcome.CONFLICT, "destination_conflict")
    if published != StoreResult.PUBLISHED and published != StoreResult.PUBLISHED.value:
        return SaveResult(Outcome.COMMIT_UNKNOWN, "commit_unknown", cleanup_pending=True)

    # A cancellation racing after publication cannot retract the published file.
    try:
        storage.release_owned_stage(stage)
    except Exception:
        return SaveResult(Outcome.PUBLISHED_CLEANUP_PENDING, "published_cleanup_pending", cleanup_pending=True)
    return SaveResult(Outcome.PUBLISHED, "bytes_published")
