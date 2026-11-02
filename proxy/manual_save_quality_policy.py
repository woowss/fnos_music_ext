"""Offline-only strict quality evidence policy.

This module evaluates already supplied evidence. It has no provider, network,
filesystem, account, or production integration. Its positive result is a
synthetic policy match only; production readiness is permanently disabled.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import Protocol


PRODUCTION_READY = False
MAX_IDENTIFIER_LENGTH = 64
MAX_EVIDENCE_AGE_SECONDS = 300
MAX_REQUEST_AGE_SECONDS = 3600
MAX_DURATION_SECONDS = 24 * 60 * 60
DURATION_TOLERANCE_SECONDS = 2
MAX_PLAYLIST_TRACKS = 500


class QualityTier(IntEnum):
    """Abstract internal tiers; provider-specific labels are not accepted."""

    STANDARD = 1
    HIGH = 2
    LOSSLESS = 3
    HI_RES = 4
    ULTRA_MASTER = 5


class RightsStatus(Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    DENIED = "denied"


class TrustStatus(Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class EvaluationStatus(Enum):
    MATCHED = "matched"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class QualityRequest:
    """One user-selected track and one unique request attempt."""

    request_id: str
    attempt_id: str
    track_id: int
    requested_tier: QualityTier
    display_duration_seconds: int
    requested_at: int


@dataclass(frozen=True, slots=True)
class QualityEvidence:
    """Sanitized evidence fields, not a provider response or media locator.

    A separate verifier must authenticate the source and its quality mapping.
    The booleans and enum fields alone never establish trust.
    """

    request_id: str
    attempt_id: str
    track_id: int
    confirmed: bool
    actual_tier: QualityTier
    rights_status: RightsStatus
    no_trial: bool
    complete: bool
    duration_seconds: int
    mapping_id: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class TrustReport:
    """Independent source and mapping verification result."""

    source_status: TrustStatus
    mapping_status: TrustStatus
    mapping_id: str


@dataclass(frozen=True, slots=True)
class QualityResult:
    status: EvaluationStatus
    reason: str
    confirmed_tier: QualityTier | None = None
    production_ready: bool = False


@dataclass(frozen=True, slots=True)
class PlaylistResult:
    items: tuple[QualityResult, ...]
    production_ready: bool = False

    @property
    def all_matched(self) -> bool:
        return bool(self.items) and all(
            item.status is EvaluationStatus.MATCHED for item in self.items
        )


class TrustedClock(Protocol):
    """Injected trusted wall clock, read again after source verification."""

    def now(self) -> int: ...


class EvidenceVerifier(Protocol):
    """Must authenticate source claims and the exact mapping independently."""

    def verify_quality_evidence(
        self, request: QualityRequest, evidence: QualityEvidence
    ) -> TrustReport: ...


def _reject(reason: str) -> QualityResult:
    return QualityResult(
        status=EvaluationStatus.REJECTED,
        reason=reason,
        confirmed_tier=None,
        production_ready=PRODUCTION_READY,
    )


def _valid_identifier(value: object) -> bool:
    if type(value) is not str or not 1 <= len(value) <= MAX_IDENTIFIER_LENGTH:
        return False
    # Identifiers are correlation labels only; reject URLs, whitespace, and
    # arbitrary response text so result handling never needs to echo them.
    return all(
        character.isascii()
        and (character.isalnum() or character in "._-")
        for character in value
    )


def _valid_request(request: object, now: object) -> str | None:
    if type(request) is not QualityRequest:
        return "invalid_request"
    if not _valid_identifier(request.request_id) or not _valid_identifier(request.attempt_id):
        return "invalid_request_id"
    if type(request.track_id) is not int or request.track_id <= 0:
        return "invalid_track_id"
    if type(request.requested_tier) is not QualityTier:
        return "invalid_requested_tier"
    if request.requested_tier is not QualityTier.ULTRA_MASTER:
        return "unsupported_requested_tier"
    if (
        type(request.display_duration_seconds) is not int
        or not 1 <= request.display_duration_seconds <= MAX_DURATION_SECONDS
    ):
        return "invalid_duration"
    if type(request.requested_at) is not int or request.requested_at < 0:
        return "invalid_request_time"
    if type(now) is not int or now < 0:
        return "invalid_clock"
    if now < request.requested_at:
        return "clock_before_request"
    if now - request.requested_at > MAX_REQUEST_AGE_SECONDS:
        return "request_expired"
    return None


def _validate_evidence(
    request: QualityRequest, evidence: object, now: int
) -> str | None:
    if evidence is None:
        return "evidence_missing"
    if type(evidence) is not QualityEvidence:
        return "invalid_evidence"
    if not _valid_identifier(evidence.request_id) or not _valid_identifier(evidence.attempt_id):
        return "invalid_evidence_id"
    if type(evidence.track_id) is not int or evidence.track_id <= 0:
        return "invalid_evidence_track"
    if evidence.request_id != request.request_id:
        return "request_mismatch"
    if evidence.attempt_id != request.attempt_id:
        return "attempt_mismatch"
    if evidence.track_id != request.track_id:
        return "track_mismatch"
    if type(evidence.confirmed) is not bool or evidence.confirmed is not True:
        return "not_confirmed"
    if type(evidence.actual_tier) is not QualityTier:
        return "invalid_actual_tier"
    if evidence.actual_tier < request.requested_tier:
        return "quality_below_requested"
    if evidence.actual_tier is not request.requested_tier:
        return "quality_tier_mismatch"
    if type(evidence.rights_status) is not RightsStatus:
        return "rights_unknown"
    if evidence.rights_status is RightsStatus.DENIED:
        return "rights_denied"
    if evidence.rights_status is not RightsStatus.VERIFIED:
        return "rights_unknown"
    if type(evidence.no_trial) is not bool or evidence.no_trial is not True:
        return "trial_or_trial_status_unknown"
    if type(evidence.complete) is not bool or evidence.complete is not True:
        return "audio_incomplete_or_unknown"
    if type(evidence.duration_seconds) is not int or not 1 <= evidence.duration_seconds <= MAX_DURATION_SECONDS:
        return "invalid_evidence_duration"
    if abs(evidence.duration_seconds - request.display_duration_seconds) > DURATION_TOLERANCE_SECONDS:
        return "duration_mismatch"
    if not _valid_identifier(evidence.mapping_id):
        return "invalid_mapping_id"
    if (
        type(evidence.issued_at) is not int
        or type(evidence.expires_at) is not int
        or evidence.issued_at < request.requested_at
        or evidence.issued_at > now
        or evidence.expires_at <= evidence.issued_at
    ):
        return "invalid_evidence_time"
    if evidence.expires_at <= now:
        return "evidence_expired"
    if now - evidence.issued_at > MAX_EVIDENCE_AGE_SECONDS:
        return "evidence_stale"
    return None


def _read_clock(clock: object) -> tuple[int | None, str | None]:
    try:
        read = getattr(clock, "now", None)
    except Exception:
        return None, "clock_unavailable"
    if not callable(read):
        return None, "clock_unavailable"
    try:
        value = read()
    except Exception:
        return None, "clock_unavailable"
    if type(value) is not int or value < 0:
        return None, "invalid_clock"
    return value, None


def _request_snapshot(request: QualityRequest) -> tuple[object, ...]:
    return (
        request.request_id,
        request.attempt_id,
        request.track_id,
        request.requested_tier,
        request.display_duration_seconds,
        request.requested_at,
    )


def _evidence_snapshot(evidence: QualityEvidence) -> tuple[object, ...]:
    return (
        evidence.request_id,
        evidence.attempt_id,
        evidence.track_id,
        evidence.confirmed,
        evidence.actual_tier,
        evidence.rights_status,
        evidence.no_trial,
        evidence.complete,
        evidence.duration_seconds,
        evidence.mapping_id,
        evidence.issued_at,
        evidence.expires_at,
    )


def _evaluate_quality_unchecked(
    request: object,
    evidence: object,
    verifier: object,
    clock: object,
) -> QualityResult:
    """Return a strict synthetic match only for fresh, track-bound evidence.

    The injected clock is read before and after source verification. Any
    backward, invalid, or failed reading rejects the result. No tier fallback
    is attempted; a positive result never authorizes a file operation.
    """
    before, clock_error = _read_clock(clock)
    if clock_error is not None:
        return _reject(clock_error)
    invalid = _valid_request(request, before)
    if invalid is not None:
        return _reject(invalid)
    evidence_error = _validate_evidence(request, evidence, before)
    if evidence_error is not None:
        return _reject(evidence_error)

    request_before = _request_snapshot(request)
    evidence_before = _evidence_snapshot(evidence)

    try:
        verify = getattr(verifier, "verify_quality_evidence", None)
    except Exception:
        return _reject("verifier_unavailable")
    if not callable(verify):
        return _reject("verifier_unavailable")

    try:
        report = verify(request, evidence)
    except Exception:
        # Never include exception text: it may contain credentials, URLs, or
        # an unredacted provider response.
        return _reject("trust_verification_failed")

    after, clock_error = _read_clock(clock)
    if clock_error is not None:
        return _reject(clock_error)
    if after < before:
        return _reject("clock_moved_backwards")
    if type(request) is not QualityRequest or _request_snapshot(request) != request_before:
        return _reject("request_changed_during_verification")
    if type(evidence) is not QualityEvidence or _evidence_snapshot(evidence) != evidence_before:
        return _reject("evidence_changed_during_verification")
    invalid = _valid_request(request, after)
    if invalid is not None:
        return _reject(invalid)
    evidence_error = _validate_evidence(request, evidence, after)
    if evidence_error is not None:
        return _reject(evidence_error)

    if type(report) is not TrustReport:
        return _reject("trust_report_invalid")
    if type(report.source_status) is not TrustStatus:
        return _reject("source_untrusted")
    if report.source_status is not TrustStatus.VERIFIED:
        return _reject("source_untrusted")
    if type(report.mapping_status) is not TrustStatus:
        return _reject("mapping_unverified")
    if report.mapping_status is not TrustStatus.VERIFIED:
        return _reject("mapping_unverified")
    if (
        not _valid_identifier(report.mapping_id)
        or report.mapping_id != evidence.mapping_id
    ):
        return _reject("mapping_mismatch")

    return QualityResult(
        status=EvaluationStatus.MATCHED,
        reason="synthetic_evidence_matches_request",
        confirmed_tier=request.requested_tier,
        production_ready=PRODUCTION_READY,
    )


def evaluate_quality(
    request: object,
    evidence: object,
    verifier: object,
    clock: object,
) -> QualityResult:
    """Fail closed with a fixed code if any hostile object read raises."""
    try:
        return _evaluate_quality_unchecked(request, evidence, verifier, clock)
    except Exception:
        # Frozen dataclasses can still have fields removed with object.__delattr__.
        # Never expose an exception or any object content at this boundary.
        return _reject("evaluation_failed")


@dataclass(frozen=True, slots=True)
class _PlaylistItemSnapshot:
    """Bounded references and immutable field values captured before callbacks."""

    item: object
    pair_fields: tuple[object, ...] | None
    pair_length: int | None
    request: object
    evidence: object
    request_fields: tuple[object, ...] | None
    evidence_fields: tuple[object, ...] | None
    request_for_evaluation: object
    evidence_for_evaluation: object


def _playlist_structure_matches(
    items: object, snapshot: tuple[object, ...]
) -> bool:
    """Detect persistent changes to a caller-owned top-level list."""
    if type(items) is tuple:
        return len(items) == len(snapshot) and all(
            items[index] is original for index, original in enumerate(snapshot)
        )
    if type(items) is not list:
        return False
    try:
        return len(items) == len(snapshot) and all(
            items[index] is original for index, original in enumerate(snapshot)
        )
    except Exception:
        return False


def _snapshot_playlist_item(item: object) -> _PlaylistItemSnapshot:
    """Capture all request/evidence fields before any verifier can run."""
    pair_fields: tuple[object, ...] | None = None
    pair_length: int | None = None
    request: object = None
    evidence: object = None
    request_fields: tuple[object, ...] | None = None
    evidence_fields: tuple[object, ...] | None = None
    request_for_evaluation: object = None
    evidence_for_evaluation: object = None

    if type(item) in (tuple, list):
        pair_length = len(item)
        # Only a two-field pair can be evaluated. Avoid copying an unbounded
        # malformed nested list merely to report the fixed invalid-item result.
        if pair_length <= 2:
            pair_fields = tuple(item)
        if type(item) is tuple and pair_length == 2:
            request, evidence = item
            if type(request) is QualityRequest:
                request_fields = _request_snapshot(request)
                request_for_evaluation = QualityRequest(*request_fields)
            else:
                request_for_evaluation = request
            if type(evidence) is QualityEvidence:
                evidence_fields = _evidence_snapshot(evidence)
                evidence_for_evaluation = QualityEvidence(*evidence_fields)
            else:
                evidence_for_evaluation = evidence

    return _PlaylistItemSnapshot(
        item=item,
        pair_fields=pair_fields,
        pair_length=pair_length,
        request=request,
        evidence=evidence,
        request_fields=request_fields,
        evidence_fields=evidence_fields,
        request_for_evaluation=request_for_evaluation,
        evidence_for_evaluation=evidence_for_evaluation,
    )


def _playlist_input_matches(
    items: object,
    snapshot: tuple[object, ...],
    item_snapshots: tuple[_PlaylistItemSnapshot, ...],
) -> bool:
    """Verify the source shape and every captured request/evidence field."""
    if not _playlist_structure_matches(items, snapshot):
        return False
    try:
        for captured in item_snapshots:
            item = captured.item
            if type(item) in (tuple, list) and captured.pair_length is not None:
                if len(item) != captured.pair_length:
                    return False
                if captured.pair_fields is not None and any(
                    item[index] is not original
                    for index, original in enumerate(captured.pair_fields)
                ):
                    return False
            if captured.request_fields is not None:
                if (
                    type(captured.request) is not QualityRequest
                    or _request_snapshot(captured.request) != captured.request_fields
                ):
                    return False
            if captured.evidence_fields is not None:
                if (
                    type(captured.evidence) is not QualityEvidence
                    or _evidence_snapshot(captured.evidence) != captured.evidence_fields
                ):
                    return False
    except Exception:
        # Deleted fields and broken objects are mutation, never evidence.
        return False
    return True


def _playlist_rejected(reason: str) -> PlaylistResult:
    return PlaylistResult((_reject(reason),), False)


def evaluate_playlist(
    items: object,
    verifier: object,
    clock: object,
) -> PlaylistResult:
    """Evaluate a bounded value snapshot and reject any observed source change."""
    if type(items) not in (tuple, list):
        return _playlist_rejected("invalid_playlist")

    try:
        initial_length = len(items)
    except Exception:
        return _playlist_rejected("invalid_playlist")
    if initial_length > MAX_PLAYLIST_TRACKS:
        return _playlist_rejected("playlist_too_large")

    try:
        snapshot = items if type(items) is tuple else tuple(items)
        if len(snapshot) != initial_length or not _playlist_structure_matches(items, snapshot):
            return _playlist_rejected("playlist_changed_during_evaluation")
        item_snapshots = tuple(_snapshot_playlist_item(item) for item in snapshot)
        if not _playlist_input_matches(items, snapshot, item_snapshots):
            return _playlist_rejected("playlist_changed_during_evaluation")
    except Exception:
        # A fieldless/corrupt request or evidence object cannot be frozen.
        return _playlist_rejected("evaluation_failed")

    results: list[QualityResult] = []
    try:
        for captured in item_snapshots:
            if not _playlist_input_matches(items, snapshot, item_snapshots):
                return _playlist_rejected("playlist_changed_during_evaluation")
            if type(captured.item) is not tuple or len(captured.item) != 2:
                result = _reject("invalid_playlist_item")
            else:
                result = evaluate_quality(
                    captured.request_for_evaluation,
                    captured.evidence_for_evaluation,
                    verifier,
                    clock,
                )
            results.append(result)
            # Recheck all songs, including already evaluated and not-yet-
            # evaluated entries, after each callback and once more at return.
            if not _playlist_input_matches(items, snapshot, item_snapshots):
                return _playlist_rejected("playlist_changed_during_evaluation")
    except Exception:
        return _playlist_rejected("evaluation_failed")
    if not _playlist_input_matches(items, snapshot, item_snapshots):
        return _playlist_rejected("playlist_changed_during_evaluation")
    return PlaylistResult(tuple(results), False)
