"""Synthetic-only verified manual-save pipeline contract.

This module has no network or filesystem adapter. All effects are supplied by
the caller, and the repository's offline tests supply in-memory fakes only.
Nothing in this module establishes production readiness.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Protocol


PRODUCTION_READY = False
HARD_MAX_BYTES = 512 * 1024 * 1024
MAX_CHUNK_BYTES = 1024 * 1024
MAX_CHUNKS = 4096
MAX_DURATION_SECONDS = 24 * 60 * 60
DURATION_TOLERANCE_SECONDS = 2
VERIFY_TIMEOUT_SECONDS = 30
SUPPORTED_FORMATS = frozenset(
    {"aac", "aiff", "ape", "flac", "m4a", "mp3", "ogg", "opus", "wav", "wma"}
)
NCM_MAGIC = b"CTENFDAM"


class RightsStatus(str, Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    DENIED = "denied"


class ResultStatus(str, Enum):
    PUBLISHED = "published"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    CONFLICT = "conflict"
    COMMIT_UNKNOWN = "commit_unknown"


class PublicationStatus(str, Enum):
    PUBLISHED = "published"
    CONFLICT = "conflict"
    UNKNOWN = "unknown"


class VerificationExecutionStatus(str, Enum):
    COMPLETED = "completed"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class ManualSaveRequest:
    """Minimal user selection; it deliberately contains no path or payload."""

    track_id: int
    display_duration_seconds: int
    is_preview: bool | None
    rights_status: RightsStatus
    declared_length_bytes: int


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Result from an injected verifier, never inferred from a filename."""

    complete: bool
    is_preview: bool | None
    rights_status: RightsStatus
    is_ncm: bool | None
    media_format: str
    byte_count: int
    duration_seconds: int


@dataclass(frozen=True, slots=True)
class VerificationExecution:
    """Supervisor outcome; termination must be confirmed before it returns."""

    status: VerificationExecutionStatus
    terminated: bool
    report: object | None = None


@dataclass(frozen=True, slots=True)
class PipelineResult:
    status: ResultStatus
    reason: str
    verified_format: str | None = None
    production_ready: bool = False


class ChunkSource(Protocol):
    def iter_chunks(self, track_id: int) -> Iterable[bytes]: ...


class Stager(Protocol):
    def begin_stage(self, track_id: int) -> object: ...

    def append_chunk(self, stage: object, chunk: bytes) -> None: ...

    def seal_stage(self, stage: object, byte_count: int) -> object: ...


class Verifier(Protocol):
    def verify(self, sealed_stage: object, expected_bytes: int) -> VerificationReport: ...


class VerifierSupervisor(Protocol):
    """Required wrapper that must terminate/reap timed-out verifier work.

    A production implementation must enforce this in its execution boundary;
    a timeout value passed to a synchronous verifier is not enforcement.
    """

    def run_with_timeout(
        self,
        verifier: Verifier,
        sealed_stage: object,
        expected_bytes: int,
        timeout_seconds: int,
    ) -> VerificationExecution: ...


class Publisher(Protocol):
    def publish_noreplace(
        self,
        sealed_stage: object,
        track_id: int,
        verified_format: str,
        byte_count: int,
    ) -> PublicationStatus: ...


def _result(status: ResultStatus, reason: str, media_format: str | None = None) -> PipelineResult:
    return PipelineResult(
        status=status,
        reason=reason,
        verified_format=media_format,
        production_ready=PRODUCTION_READY,
    )


def _has_callable(target: object, name: str) -> bool:
    try:
        return callable(getattr(target, name, None))
    except Exception:
        return False


def _preflight(request: object) -> PipelineResult | None:
    if type(request) is not ManualSaveRequest:
        return _result(ResultStatus.REJECTED, "invalid_request")
    if type(request.track_id) is not int or request.track_id <= 0:
        return _result(ResultStatus.REJECTED, "invalid_track_id")
    if (
        type(request.display_duration_seconds) is not int
        or not 1 <= request.display_duration_seconds <= MAX_DURATION_SECONDS
    ):
        return _result(ResultStatus.REJECTED, "invalid_duration")
    if type(request.declared_length_bytes) is not int or not 1 <= request.declared_length_bytes <= HARD_MAX_BYTES:
        return _result(ResultStatus.REJECTED, "invalid_length")
    if request.is_preview is True:
        return _result(ResultStatus.REJECTED, "preview_rejected")
    if request.is_preview is not False:
        return _result(ResultStatus.REJECTED, "preview_unknown")
    if request.rights_status is RightsStatus.DENIED:
        return _result(ResultStatus.REJECTED, "rights_denied")
    if request.rights_status is not RightsStatus.VERIFIED:
        return _result(ResultStatus.REJECTED, "rights_unknown")
    return None


def _validate_report(
    report: object, expected_bytes: int, expected_duration: int
) -> tuple[str | None, str | None]:
    """Return (verified format, fixed rejection reason); never expose details."""
    if type(report) is not VerificationReport:
        return None, "verification_invalid"
    if report.is_ncm is True:
        return None, "ncm_unsupported"
    if report.is_ncm is not False:
        return None, "ncm_status_unknown"
    if type(report.media_format) is not str:
        return None, "verified_format_unsupported"
    if report.media_format == "ncm":
        return None, "ncm_unsupported"
    if report.is_preview is True:
        return None, "preview_rejected"
    if report.is_preview is not False:
        return None, "preview_unknown"
    if report.rights_status is RightsStatus.DENIED:
        return None, "rights_denied"
    if report.rights_status is not RightsStatus.VERIFIED:
        return None, "rights_unknown"
    if report.complete is not True:
        return None, "audio_incomplete"
    if type(report.byte_count) is not int or report.byte_count != expected_bytes:
        return None, "verified_length_mismatch"
    if (
        type(report.duration_seconds) is not int
        or not 1 <= report.duration_seconds <= MAX_DURATION_SECONDS
    ):
        return None, "verified_duration_invalid"
    if abs(report.duration_seconds - expected_duration) > DURATION_TOLERANCE_SECONDS:
        return None, "verified_duration_mismatch"
    if type(report.media_format) is not str or report.media_format not in SUPPORTED_FORMATS:
        return None, "verified_format_unsupported"
    return report.media_format, None


def run_manual_save(
    request: object,
    source: object,
    stager: object,
    verifier: object,
    verifier_supervisor: object,
    publisher: object,
    cancelled: object = None,
) -> PipelineResult:
    """Stage, seal, verify, check cancellation, then publish exactly once.

    The source, stager, verifier, verifier supervisor and publisher are
    injected interfaces. The supervisor must terminate and reap timed-out
    verification work before returning. This module contains no concrete
    supervisor and cannot enforce a hard wall-clock timeout itself. A publisher
    exception or ``UNKNOWN`` response is treated as ambiguous: this function
    never retries, deletes, or tries to clean up that stage.
    """
    invalid = _preflight(request)
    if invalid is not None:
        return invalid
    if cancelled is not None and not callable(cancelled):
        return _result(ResultStatus.REJECTED, "invalid_cancel_check")
    if not _has_callable(source, "iter_chunks"):
        return _result(ResultStatus.REJECTED, "invalid_source")
    if not all(_has_callable(stager, method) for method in ("begin_stage", "append_chunk", "seal_stage")):
        return _result(ResultStatus.REJECTED, "invalid_stager")
    if not _has_callable(verifier, "verify"):
        return _result(ResultStatus.REJECTED, "invalid_verifier")
    if not _has_callable(verifier_supervisor, "run_with_timeout"):
        return _result(ResultStatus.REJECTED, "verifier_supervisor_unavailable")
    if not _has_callable(publisher, "publish_noreplace"):
        return _result(ResultStatus.REJECTED, "invalid_publisher")

    def check_cancel() -> PipelineResult | None:
        if cancelled is None:
            return None
        try:
            cancel_state = cancelled()
            if cancel_state is True:
                return _result(ResultStatus.CANCELLED, "cancelled_before_publish")
            if cancel_state is not False:
                return _result(ResultStatus.FAILED, "cancel_check_invalid")
        except Exception:
            return _result(ResultStatus.FAILED, "cancel_check_failed")
        return None

    stopped = check_cancel()
    if stopped is not None:
        return stopped

    try:
        stage = stager.begin_stage(request.track_id)
        if stage is None:
            return _result(ResultStatus.FAILED, "stage_unavailable")
    except Exception:
        return _result(ResultStatus.FAILED, "stage_failed")

    byte_count = 0
    chunk_count = 0
    prefix = bytearray()
    try:
        chunks = source.iter_chunks(request.track_id)
        iterator = iter(chunks)
        for chunk in iterator:
            stopped = check_cancel()
            if stopped is not None:
                return stopped
            if chunk_count >= MAX_CHUNKS:
                return _result(ResultStatus.REJECTED, "too_many_chunks")
            if type(chunk) is not bytes:
                return _result(ResultStatus.REJECTED, "invalid_chunk")
            if not chunk:
                return _result(ResultStatus.REJECTED, "empty_chunk")
            if len(chunk) > MAX_CHUNK_BYTES:
                return _result(ResultStatus.REJECTED, "chunk_too_large")
            chunk_count += 1
            byte_count += len(chunk)
            if byte_count > request.declared_length_bytes or byte_count > HARD_MAX_BYTES:
                return _result(ResultStatus.REJECTED, "stream_too_long")

            if len(prefix) < len(NCM_MAGIC):
                prefix.extend(chunk[: len(NCM_MAGIC) - len(prefix)])
            if bytes(prefix) == NCM_MAGIC:
                return _result(ResultStatus.REJECTED, "ncm_unsupported")
            stager.append_chunk(stage, chunk)
    except Exception:
        return _result(ResultStatus.FAILED, "source_or_stage_failed")

    if byte_count != request.declared_length_bytes:
        return _result(ResultStatus.REJECTED, "stream_length_mismatch")

    stopped = check_cancel()
    if stopped is not None:
        return stopped

    try:
        sealed_stage = stager.seal_stage(stage, byte_count)
        if sealed_stage is None:
            return _result(ResultStatus.FAILED, "seal_failed")
    except Exception:
        return _result(ResultStatus.FAILED, "seal_failed")

    try:
        execution = verifier_supervisor.run_with_timeout(
            verifier,
            sealed_stage,
            request.declared_length_bytes,
            VERIFY_TIMEOUT_SECONDS,
        )
    except Exception:
        return _result(ResultStatus.FAILED, "verification_supervisor_failed")
    if type(execution) is not VerificationExecution:
        return _result(ResultStatus.FAILED, "verification_supervisor_invalid")
    if execution.terminated is not True:
        return _result(ResultStatus.FAILED, "verifier_termination_unconfirmed")
    if execution.status is VerificationExecutionStatus.TIMED_OUT:
        return _result(ResultStatus.FAILED, "verification_timeout")
    if execution.status is VerificationExecutionStatus.FAILED:
        return _result(ResultStatus.FAILED, "verification_failed")
    if execution.status is not VerificationExecutionStatus.COMPLETED:
        return _result(ResultStatus.FAILED, "verification_supervisor_invalid")
    verified_format, rejection = _validate_report(
        execution.report,
        request.declared_length_bytes,
        request.display_duration_seconds,
    )
    if rejection is not None or verified_format is None:
        if rejection is None:
            rejection = "verification_invalid"
        return _result(ResultStatus.REJECTED, rejection)

    # This is the final cancellation boundary. No publisher call occurs after
    # a cancellation result or a broken cancellation callback.
    stopped = check_cancel()
    if stopped is not None:
        return stopped

    try:
        published = publisher.publish_noreplace(
            sealed_stage,
            request.track_id,
            verified_format,
            byte_count,
        )
    except Exception:
        return _result(ResultStatus.COMMIT_UNKNOWN, "publication_unknown", verified_format)

    if published is PublicationStatus.CONFLICT:
        return _result(ResultStatus.CONFLICT, "destination_conflict", verified_format)
    if published is PublicationStatus.UNKNOWN:
        return _result(ResultStatus.COMMIT_UNKNOWN, "publication_unknown", verified_format)
    if published is not PublicationStatus.PUBLISHED:
        return _result(ResultStatus.COMMIT_UNKNOWN, "publication_unknown", verified_format)
    return _result(ResultStatus.PUBLISHED, "verified_pipeline_complete", verified_format)
