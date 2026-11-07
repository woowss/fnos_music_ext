"""Offline-only policy for selecting a verified best available quality tier.

This module consumes sanitized, already supplied evidence. It has no provider,
network, filesystem, account, audio, conversion, or production integration. A
selected tier is only a synthetic policy result and never authorizes a save.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
import json
from typing import Protocol

from proxy.manual_save_quality_policy import QualityTier


PRODUCTION_READY = False
MAX_IDENTIFIER_LENGTH = 64
MAX_REQUEST_AGE_SECONDS = 3600
MAX_EVIDENCE_AGE_SECONDS = 300
MAX_DURATION_SECONDS = 24 * 60 * 60
DURATION_TOLERANCE_SECONDS = 2
MAX_PLAYLIST_TRACKS = 500
QUALITY_ORDER = tuple(sorted(QualityTier, key=int, reverse=True))


class AvailabilityStatus(Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class UnavailableReason(Enum):
    NOT_OFFERED = "not_offered"
    ACCOUNT_NOT_ENTITLED = "account_not_entitled"
    SOURCE_TIMEOUT = "source_timeout"
    UNKNOWN = "unknown"


class RightsStatus(Enum):
    VERIFIED = "verified"
    NOT_ENTITLED = "not_entitled"
    UNKNOWN = "unknown"
    DENIED = "denied"


class TrustStatus(Enum):
    VERIFIED = "verified"
    UNKNOWN = "unknown"
    REJECTED = "rejected"


class CoverageStatus(Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    UNKNOWN = "unknown"


class EvaluationStatus(Enum):
    SELECTED = "selected"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class QualityRequest:
    """A user-selected song and one unique request attempt."""

    request_id: str
    attempt_id: str
    account_id: str
    song_id: int
    requested_tier: QualityTier
    duration_seconds: int
    requested_at: int


@dataclass(frozen=True, slots=True)
class QualityLadder:
    """The source's claimed full tier order and exact mapping version."""

    source_id: str
    mapping_id: str
    mapping_version: str
    tiers: tuple[QualityTier, ...]
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class TierEvidence:
    """One sanitized per-tier status bound to song, request, attempt, and time."""

    request_id: str
    attempt_id: str
    account_id: str
    song_id: int
    tier: QualityTier
    status: AvailabilityStatus
    unavailable_reason: UnavailableReason | None
    rights_status: RightsStatus
    no_trial: bool | None
    complete: bool | None
    duration_seconds: int | None
    source_id: str
    mapping_id: str
    mapping_version: str
    issued_at: int
    expires_at: int


@dataclass(frozen=True, slots=True)
class TrustReport:
    """Independent verifier attestation for this exact complete evidence set."""

    source_status: TrustStatus
    mapping_status: TrustStatus
    account_status: TrustStatus
    coverage_status: CoverageStatus
    source_id: str
    mapping_id: str
    mapping_version: str
    request_id: str
    attempt_id: str
    account_id: str
    song_id: int
    tier_order: tuple[QualityTier, ...]
    verified_statuses: tuple[tuple[QualityTier, AvailabilityStatus], ...]
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class QualityResult:
    status: EvaluationStatus
    reason: str
    requested_tier: QualityTier | None
    selected_tier: QualityTier | None = None
    fallback: bool = False
    production_ready: bool = False


@dataclass(frozen=True, slots=True)
class PlaylistResult:
    items: tuple[QualityResult, ...]
    completed: bool
    reason: str
    production_ready: bool = False

    @property
    def all_selected(self) -> bool:
        return bool(self.items) and self.completed and all(
            item.status is EvaluationStatus.SELECTED for item in self.items
        )


class TrustedClock(Protocol):
    def now(self) -> int: ...


class EvidenceVerifier(Protocol):
    """Authenticate the source, mapping version, ladder and every tier status."""

    def verify_snapshot(
        self,
        request: QualityRequest,
        ladder: QualityLadder,
        evidence: tuple[TierEvidence, ...],
    ) -> TrustReport: ...


def _reject(reason: str, requested: QualityTier | None = None) -> QualityResult:
    return QualityResult(
        status=EvaluationStatus.REJECTED,
        reason=reason,
        requested_tier=requested,
        selected_tier=None,
        fallback=False,
        production_ready=PRODUCTION_READY,
    )


def _safe_requested_tier(request: object) -> QualityTier | None:
    """Read an optional result label without trusting possibly damaged input."""
    try:
        if type(request) is not QualityRequest:
            return None
        value = object.__getattribute__(request, "requested_tier")
        return value if type(value) is QualityTier else None
    except Exception:
        return None


def _identifier(value: object) -> bool:
    return (
        type(value) is str
        and 1 <= len(value) <= MAX_IDENTIFIER_LENGTH
        and all(
            ch.isascii() and (ch.isalnum() or ch in "._-") for ch in value
        )
    )


def _mapping_version(value: object) -> bool:
    return _identifier(value)


def _tier_name(value: object) -> str | None:
    return value.name if type(value) is QualityTier else None


def _request_snapshot(request: QualityRequest) -> tuple[object, ...]:
    return (
        request.request_id,
        request.attempt_id,
        request.account_id,
        request.song_id,
        request.requested_tier,
        request.duration_seconds,
        request.requested_at,
    )


def _ladder_snapshot(ladder: QualityLadder) -> tuple[object, ...]:
    tiers = ladder.tiers
    if type(tiers) is tuple:
        tiers_snapshot: object = tuple(tiers)
    elif type(tiers) is list and len(tiers) <= len(QUALITY_ORDER):
        tiers_snapshot = ("invalid-list", id(tiers), tuple(tiers))
    else:
        tiers_snapshot = ("invalid-container", id(tiers))
    return (
        ladder.source_id,
        ladder.mapping_id,
        ladder.mapping_version,
        tiers_snapshot,
        ladder.issued_at,
        ladder.expires_at,
    )


def _evidence_snapshot(item: TierEvidence) -> tuple[object, ...]:
    return (
        item.request_id,
        item.attempt_id,
        item.account_id,
        item.song_id,
        item.tier,
        item.status,
        item.unavailable_reason,
        item.rights_status,
        item.no_trial,
        item.complete,
        item.duration_seconds,
        item.source_id,
        item.mapping_id,
        item.mapping_version,
        item.issued_at,
        item.expires_at,
    )


def _report_snapshot(report: object) -> tuple[object, ...] | None:
    if type(report) is not TrustReport:
        return None
    try:
        if type(report.tier_order) is not tuple or type(report.verified_statuses) is not tuple:
            return None
        if len(report.tier_order) > len(QUALITY_ORDER) or len(report.verified_statuses) > len(QUALITY_ORDER):
            return None
        if any(type(pair) is not tuple or len(pair) != 2 for pair in report.verified_statuses):
            return None
        return (
            report.source_status,
            report.mapping_status,
            report.account_status,
            report.coverage_status,
            report.source_id,
            report.mapping_id,
            report.mapping_version,
            report.request_id,
            report.attempt_id,
            report.account_id,
            report.song_id,
            tuple(report.tier_order),
            tuple(tuple(pair) for pair in report.verified_statuses),
            report.evidence_sha256,
        )
    except Exception:
        return None


def _read_clock(clock: object) -> tuple[int | None, str | None]:
    try:
        read = getattr(clock, "now", None)
        if not callable(read):
            return None, "clock_unavailable"
        value = read()
    except Exception:
        return None, "clock_unavailable"
    if type(value) is not int or value < 0:
        return None, "invalid_clock"
    return value, None


def _valid_request(request: object, now: int) -> str | None:
    if type(request) is not QualityRequest:
        return "invalid_request"
    if not _identifier(request.request_id) or not _identifier(request.attempt_id):
        return "invalid_request_id"
    if not _identifier(request.account_id):
        return "invalid_account_binding"
    if type(request.song_id) is not int or request.song_id <= 0:
        return "invalid_song_id"
    if type(request.requested_tier) is not QualityTier:
        return "invalid_requested_tier"
    if request.requested_tier is not QualityTier.ULTRA_MASTER:
        return "unsupported_requested_tier"
    if type(request.duration_seconds) is not int or not 1 <= request.duration_seconds <= MAX_DURATION_SECONDS:
        return "invalid_duration"
    if type(request.requested_at) is not int or request.requested_at < 0:
        return "invalid_request_time"
    if now < request.requested_at:
        return "clock_before_request"
    if now - request.requested_at > MAX_REQUEST_AGE_SECONDS:
        return "request_expired"
    return None


def _valid_ladder(request: QualityRequest, ladder: object, now: int) -> str | None:
    if type(ladder) is not QualityLadder:
        return "invalid_ladder"
    if not _identifier(ladder.source_id):
        return "invalid_source_id"
    if not _identifier(ladder.mapping_id) or not _mapping_version(ladder.mapping_version):
        return "invalid_mapping_identity"
    if (
        type(ladder.tiers) is not tuple
        or len(ladder.tiers) != len(QUALITY_ORDER)
        or any(actual is not expected for actual, expected in zip(ladder.tiers, QUALITY_ORDER, strict=True))
    ):
        return "ladder_incomplete_or_unordered"
    if type(ladder.issued_at) is not int or type(ladder.expires_at) is not int:
        return "invalid_ladder_time"
    if (
        ladder.issued_at < request.requested_at
        or ladder.issued_at > now
        or ladder.expires_at <= ladder.issued_at
    ):
        return "invalid_ladder_time"
    if ladder.expires_at <= now:
        return "ladder_expired"
    if now - ladder.issued_at > MAX_EVIDENCE_AGE_SECONDS:
        return "ladder_stale"
    return None


def _valid_tier_evidence(
    request: QualityRequest,
    ladder: QualityLadder,
    evidence: object,
    expected_tier: QualityTier,
    now: int,
) -> str | None:
    if type(evidence) is not TierEvidence:
        return "invalid_tier_evidence"
    if not _identifier(evidence.request_id) or not _identifier(evidence.attempt_id):
        return "invalid_evidence_id"
    if evidence.request_id != request.request_id:
        return "request_mismatch"
    if evidence.attempt_id != request.attempt_id:
        return "attempt_mismatch"
    if not _identifier(evidence.account_id):
        return "invalid_account_binding"
    if evidence.account_id != request.account_id:
        return "account_mismatch"
    if type(evidence.song_id) is not int or evidence.song_id != request.song_id:
        return "song_mismatch"
    if type(evidence.tier) is not QualityTier or evidence.tier is not expected_tier:
        return "tier_order_mismatch"
    if type(evidence.status) is not AvailabilityStatus:
        return "availability_unknown"
    if type(evidence.rights_status) is not RightsStatus:
        return "rights_unknown"
    if not _identifier(evidence.source_id) or not _identifier(evidence.mapping_id) or not _mapping_version(evidence.mapping_version):
        return "invalid_evidence_mapping"
    if evidence.source_id != ladder.source_id:
        return "source_mismatch"
    if evidence.mapping_id != ladder.mapping_id or evidence.mapping_version != ladder.mapping_version:
        return "mapping_version_mismatch"
    if evidence.status is AvailabilityStatus.UNKNOWN:
        if evidence.unavailable_reason not in (
            None,
            UnavailableReason.UNKNOWN,
            UnavailableReason.SOURCE_TIMEOUT,
        ):
            return "contradictory_tier_evidence"
        if evidence.rights_status in (RightsStatus.DENIED, RightsStatus.NOT_ENTITLED):
            return "contradictory_tier_evidence"
        if any(
            value is not None
            for value in (evidence.no_trial, evidence.complete, evidence.duration_seconds)
        ):
            return "contradictory_tier_evidence"
    elif evidence.status is AvailabilityStatus.UNAVAILABLE:
        if any(
            value is not None
            for value in (evidence.no_trial, evidence.complete, evidence.duration_seconds)
        ):
            return "contradictory_tier_evidence"
        if evidence.unavailable_reason is UnavailableReason.NOT_OFFERED:
            if evidence.rights_status is not RightsStatus.VERIFIED:
                return "rights_unknown" if evidence.rights_status is RightsStatus.UNKNOWN else "rights_denied"
        elif evidence.unavailable_reason is UnavailableReason.ACCOUNT_NOT_ENTITLED:
            if evidence.rights_status is RightsStatus.UNKNOWN:
                return "rights_unknown"
            if evidence.rights_status is not RightsStatus.NOT_ENTITLED:
                return "account_unavailability_unverified"
        elif evidence.unavailable_reason in (
            UnavailableReason.SOURCE_TIMEOUT,
            UnavailableReason.UNKNOWN,
        ):
            if evidence.rights_status in (RightsStatus.DENIED, RightsStatus.NOT_ENTITLED):
                return "contradictory_tier_evidence"
        else:
            return "unavailability_not_proven"
    elif evidence.status is AvailabilityStatus.AVAILABLE:
        if evidence.unavailable_reason is not None:
            return "contradictory_tier_evidence"
        if type(evidence.no_trial) not in (bool, type(None)):
            return "contradictory_tier_evidence"
        if type(evidence.complete) not in (bool, type(None)):
            return "contradictory_tier_evidence"
        if evidence.duration_seconds is not None and (
            type(evidence.duration_seconds) is not int
            or not 1 <= evidence.duration_seconds <= MAX_DURATION_SECONDS
        ):
            return "invalid_evidence_duration"
    else:
        return "availability_unknown"
    if type(evidence.issued_at) is not int or type(evidence.expires_at) is not int:
        return "invalid_evidence_time"
    if (
        evidence.issued_at < request.requested_at
        or evidence.issued_at > now
        or evidence.expires_at <= evidence.issued_at
    ):
        return "invalid_evidence_time"
    if evidence.expires_at <= now:
        return "evidence_expired"
    if now - evidence.issued_at > MAX_EVIDENCE_AGE_SECONDS:
        return "evidence_stale"
    return None


def _canonical_digest(
    request_fields: tuple[object, ...],
    ladder_fields: tuple[object, ...],
    evidence_fields: tuple[tuple[object, ...], ...],
) -> str:
    """Hash only fixed scalar/enum snapshots to bind the verifier report."""

    def value(item: object) -> object:
        if type(item) is QualityTier:
            return {"tier": item.name}
        if type(item) is AvailabilityStatus:
            return {"availability": item.value}
        if type(item) is UnavailableReason:
            return {"unavailable_reason": item.value}
        if type(item) is RightsStatus:
            return {"rights": item.value}
        return item

    payload = [
        [value(item) for item in request_fields],
        [
            ladder_fields[0],
            ladder_fields[1],
            ladder_fields[2],
            [value(tier) for tier in ladder_fields[3]],
            ladder_fields[4],
            ladder_fields[5],
        ],
        [[value(item) for item in fields] for fields in evidence_fields],
    ]
    encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    return sha256(encoded).hexdigest()


def _valid_report(
    report_fields: tuple[object, ...] | None,
    request: QualityRequest,
    ladder: QualityLadder,
    evidence: tuple[TierEvidence, ...],
    request_fields: tuple[object, ...],
    ladder_fields: tuple[object, ...],
    evidence_fields: tuple[tuple[object, ...], ...],
) -> str | None:
    if report_fields is None:
        return "trust_report_invalid"
    (
        source_status,
        mapping_status,
        account_status,
        coverage_status,
        source_id,
        mapping_id,
        mapping_version,
        request_id,
        attempt_id,
        account_id,
        song_id,
        tier_order,
        verified_statuses,
        digest,
    ) = report_fields
    if type(source_status) is not TrustStatus or source_status is not TrustStatus.VERIFIED:
        return "source_untrusted"
    if type(mapping_status) is not TrustStatus or mapping_status is not TrustStatus.VERIFIED:
        return "mapping_unverified"
    if type(account_status) is not TrustStatus or account_status is not TrustStatus.VERIFIED:
        return "account_unverified"
    if type(coverage_status) is not CoverageStatus or coverage_status is not CoverageStatus.COMPLETE:
        return "tier_coverage_incomplete"
    if (
        type(source_id) is not str
        or type(mapping_id) is not str
        or type(mapping_version) is not str
        or type(request_id) is not str
        or type(attempt_id) is not str
        or type(song_id) is not int
        or type(account_id) is not str
        or any(type(tier) is not QualityTier for tier in tier_order)
        or any(
            type(pair) is not tuple
            or len(pair) != 2
            or type(pair[0]) is not QualityTier
            or type(pair[1]) is not AvailabilityStatus
            for pair in verified_statuses
        )
    ):
        return "trust_report_invalid"
    if (source_id, mapping_id, mapping_version) != (
        ladder.source_id,
        ladder.mapping_id,
        ladder.mapping_version,
    ):
        return "mapping_version_mismatch"
    if (request_id, attempt_id, account_id, song_id) != (
        request.request_id,
        request.attempt_id,
        request.account_id,
        request.song_id,
    ):
        return "verifier_binding_mismatch"
    if type(tier_order) is not tuple or tier_order != QUALITY_ORDER:
        return "verifier_ladder_mismatch"
    expected_statuses = tuple((item.tier, item.status) for item in evidence)
    if type(verified_statuses) is not tuple or verified_statuses != expected_statuses:
        return "verifier_status_mismatch"
    if (
        type(digest) is not str
        or len(digest) != 64
        or any(ch not in "0123456789abcdef" for ch in digest)
    ):
        return "verifier_binding_mismatch"
    expected_digest = _canonical_digest(request_fields, ladder_fields, evidence_fields)
    if digest != expected_digest:
        return "verifier_binding_mismatch"
    return None


def _evaluate_unchecked(
    request: object,
    ladder: object,
    evidence: object,
    verifier: object,
    clock: object,
) -> QualityResult:
    requested = _safe_requested_tier(request)
    before, error = _read_clock(clock)
    if error is not None:
        return _reject(error, requested)
    assert before is not None
    invalid = _valid_request(request, before)
    if invalid is not None:
        return _reject(invalid, requested)
    invalid = _valid_ladder(request, ladder, before)
    if invalid is not None:
        return _reject(invalid, requested)
    if type(evidence) not in (tuple, list) or len(evidence) != len(QUALITY_ORDER):
        return _reject("tier_coverage_incomplete", requested)
    if any(type(item) is not TierEvidence for item in evidence):
        return _reject("invalid_tier_evidence", requested)

    request_fields = _request_snapshot(request)
    ladder_fields = _ladder_snapshot(ladder)
    evidence_refs = tuple(evidence)
    evidence_fields = tuple(_evidence_snapshot(item) for item in evidence_refs)
    for item, tier in zip(evidence_refs, QUALITY_ORDER, strict=True):
        invalid = _valid_tier_evidence(request, ladder, item, tier, before)
        if invalid is not None:
            return _reject(invalid, requested)

    request_copy = QualityRequest(*request_fields)
    ladder_copy = QualityLadder(*ladder_fields)
    evidence_copies = tuple(TierEvidence(*fields) for fields in evidence_fields)
    copy_fields = (
        _request_snapshot(request_copy),
        _ladder_snapshot(ladder_copy),
        tuple(_evidence_snapshot(item) for item in evidence_copies),
    )
    try:
        verify = getattr(verifier, "verify_snapshot", None)
        if not callable(verify):
            return _reject("verifier_unavailable", requested)
        report = verify(request_copy, ladder_copy, evidence_copies)
    except Exception:
        return _reject("trust_verification_failed", requested)

    report_fields = _report_snapshot(report)
    after, error = _read_clock(clock)
    if error is not None:
        return _reject(error, requested)
    assert after is not None
    if after < before:
        return _reject("clock_moved_backwards", requested)
    try:
        if type(request) is not QualityRequest or _request_snapshot(request) != request_fields:
            return _reject("request_changed_during_verification", requested)
        if type(ladder) is not QualityLadder or _ladder_snapshot(ladder) != ladder_fields:
            return _reject("ladder_changed_during_verification", requested)
        if type(evidence) is tuple:
            if len(evidence) != len(evidence_refs) or any(
                evidence[index] is not evidence_refs[index] for index in range(len(evidence_refs))
            ):
                return _reject("evidence_structure_changed_during_verification", requested)
        elif type(evidence) is list:
            if len(evidence) != len(evidence_refs) or any(
                evidence[index] is not evidence_refs[index] for index in range(len(evidence_refs))
            ):
                return _reject("evidence_structure_changed_during_verification", requested)
        else:
            return _reject("evidence_structure_changed_during_verification", requested)
        if any(
            type(item) is not TierEvidence or _evidence_snapshot(item) != fields
            for item, fields in zip(evidence_refs, evidence_fields, strict=True)
        ):
            return _reject("evidence_changed_during_verification", requested)
        if (
            _request_snapshot(request_copy),
            _ladder_snapshot(ladder_copy),
            tuple(_evidence_snapshot(item) for item in evidence_copies),
        ) != copy_fields:
            return _reject("verifier_input_changed", requested)
        if type(report) is not TrustReport or _report_snapshot(report) != report_fields:
            return _reject("trust_report_changed", requested)
    except Exception:
        return _reject("evaluation_failed", requested)

    invalid = _valid_request(request, after)
    if invalid is not None:
        return _reject(invalid, requested)
    invalid = _valid_ladder(request, ladder, after)
    if invalid is not None:
        return _reject(invalid, requested)
    for item, tier in zip(evidence_refs, QUALITY_ORDER, strict=True):
        invalid = _valid_tier_evidence(request, ladder, item, tier, after)
        if invalid is not None:
            return _reject(invalid, requested)
    invalid = _valid_report(
        report_fields,
        request_copy,
        ladder_copy,
        evidence_copies,
        request_fields,
        ladder_fields,
        evidence_fields,
    )
    if invalid is not None:
        return _reject(invalid, requested)

    selected = None
    for item in evidence_copies:
        if item.status is AvailabilityStatus.UNKNOWN:
            return _reject("availability_unknown", requested)
        if item.status is AvailabilityStatus.UNAVAILABLE:
            if item.unavailable_reason in (
                UnavailableReason.NOT_OFFERED,
                UnavailableReason.ACCOUNT_NOT_ENTITLED,
            ):
                continue
            return _reject(
                "source_timeout" if item.unavailable_reason is UnavailableReason.SOURCE_TIMEOUT else "availability_unknown",
                requested,
            )
        if item.rights_status is not RightsStatus.VERIFIED:
            return _reject("rights_unknown" if item.rights_status is RightsStatus.UNKNOWN else "rights_denied", requested)
        if item.no_trial is not True:
            return _reject("trial_or_trial_status_unknown", requested)
        if item.complete is not True:
            return _reject("audio_incomplete_or_unknown", requested)
        if type(item.duration_seconds) is not int:
            return _reject("invalid_evidence_duration", requested)
        if abs(item.duration_seconds - request_copy.duration_seconds) > DURATION_TOLERANCE_SECONDS:
            return _reject("duration_mismatch", requested)
        selected = item.tier
        break
    if selected is None:
        return _reject("no_available_tier", requested)
    fallback = selected is not request_copy.requested_tier
    return QualityResult(
        status=EvaluationStatus.SELECTED,
        reason="highest_available_fallback" if fallback else "preferred_tier_available",
        requested_tier=request_copy.requested_tier,
        selected_tier=selected,
        fallback=fallback,
        production_ready=PRODUCTION_READY,
    )


def evaluate_best_available(
    request: object,
    ladder: object,
    evidence: object,
    verifier: object,
    clock: object,
) -> QualityResult:
    """Choose the highest available tier only from complete, fresh proof.

    Every tier in the canonical ladder must have a verified status. Unknown,
    contradictory, stale, incomplete, trial, or rights-unknown evidence rejects
    the request; no provider label or codec is interpreted here.
    """
    try:
        return _evaluate_unchecked(request, ladder, evidence, verifier, clock)
    except Exception:
        return _reject("evaluation_failed", _safe_requested_tier(request))


@dataclass(frozen=True, slots=True)
class _PlaylistSnapshot:
    item: object
    pair_length: int | None
    pair_refs: tuple[object, ...] | None
    request: object
    ladder: object
    evidence: object
    evidence_refs: tuple[object, ...] | None
    request_fields: tuple[object, ...] | None
    ladder_fields: tuple[object, ...] | None
    evidence_fields: tuple[tuple[object, ...], ...] | None
    request_copy: object
    ladder_copy: object
    evidence_copies: tuple[TierEvidence, ...] | None


def _snapshot_playlist_item(item: object) -> _PlaylistSnapshot:
    pair_length = len(item) if type(item) in (tuple, list) else None
    refs = tuple(item) if pair_length is not None and pair_length <= 3 else None
    request = ladder = evidence = None
    evidence_refs = None
    request_fields = ladder_fields = evidence_fields = None
    request_copy = ladder_copy = None
    evidence_copies = None
    if refs is not None and pair_length == 3:
        request, ladder, evidence = refs
        if type(request) is QualityRequest:
            request_fields = _request_snapshot(request)
            request_copy = QualityRequest(*request_fields)
        if type(ladder) is QualityLadder:
            ladder_fields = _ladder_snapshot(ladder)
            ladder_copy = QualityLadder(*ladder_fields)
        if type(evidence) in (tuple, list) and len(evidence) <= len(QUALITY_ORDER):
            evidence_refs = tuple(evidence)
            if all(type(entry) is TierEvidence for entry in evidence_refs):
                evidence_fields = tuple(_evidence_snapshot(entry) for entry in evidence_refs)
                evidence_copies = tuple(TierEvidence(*fields) for fields in evidence_fields)
    return _PlaylistSnapshot(
        item,
        pair_length,
        refs,
        request,
        ladder,
        evidence,
        evidence_refs,
        request_fields,
        ladder_fields,
        evidence_fields,
        request_copy,
        ladder_copy,
        evidence_copies,
    )


def _snapshot_guards(snapshots: tuple[_PlaylistSnapshot, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            captured.item,
            captured.pair_length,
            captured.pair_refs,
            captured.request,
            captured.ladder,
            captured.evidence,
            captured.evidence_refs,
            captured.request_fields,
            captured.ladder_fields,
            captured.evidence_fields,
            captured.request_copy,
            captured.ladder_copy,
            captured.evidence_copies,
        )
        for captured in snapshots
    )


def _snapshot_guards_unchanged(
    snapshots: tuple[_PlaylistSnapshot, ...],
    guards: tuple[tuple[object, ...], ...],
) -> bool:
    if len(snapshots) != len(guards):
        return False
    try:
        for captured, guard in zip(snapshots, guards, strict=True):
            current = (
                captured.item,
                captured.pair_length,
                captured.pair_refs,
                captured.request,
                captured.ladder,
                captured.evidence,
                captured.evidence_refs,
                captured.request_fields,
                captured.ladder_fields,
                captured.evidence_fields,
                captured.request_copy,
                captured.ladder_copy,
                captured.evidence_copies,
            )
            if current[1] != guard[1] or any(
                current[index] is not guard[index]
                for index in (0, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)
            ):
                return False
            if captured.request_copy is not None and (
                type(captured.request_copy) is not QualityRequest
                or _request_snapshot(captured.request_copy) != captured.request_fields
            ):
                return False
            if captured.ladder_copy is not None and (
                type(captured.ladder_copy) is not QualityLadder
                or _ladder_snapshot(captured.ladder_copy) != captured.ladder_fields
            ):
                return False
            if captured.evidence_copies is not None:
                if captured.evidence_fields is None or len(captured.evidence_copies) != len(captured.evidence_fields):
                    return False
                if any(
                    type(entry) is not TierEvidence or _evidence_snapshot(entry) != fields
                    for entry, fields in zip(captured.evidence_copies, captured.evidence_fields, strict=True)
                ):
                    return False
        return True
    except Exception:
        return False


class _MonotonicPlaylistClock:
    def __init__(self, clock: object):
        self._clock = clock
        self._last: int | None = None
        self.failure: str | None = None
        self._reading = False

    def now(self) -> int:
        if self.failure is not None:
            raise RuntimeError("playlist clock invalid")
        if self._reading:
            self.failure = "clock_unavailable"
            raise RuntimeError("playlist clock reentered")
        self._reading = True
        try:
            value, error = _read_clock(self._clock)
        finally:
            self._reading = False
        if error is not None:
            self.failure = error
            raise RuntimeError("playlist clock invalid")
        assert value is not None
        if self._last is not None and value < self._last:
            self.failure = "clock_moved_backwards"
            raise RuntimeError("playlist clock moved backwards")
        self._last = value
        return value


def _freshness_failure(snapshot: _PlaylistSnapshot, now: int) -> str | None:
    if (
        snapshot.request_copy is None
        or snapshot.ladder_copy is None
        or snapshot.evidence_copies is None
    ):
        return "invalid_playlist_item"
    request = snapshot.request_copy
    source_ladder = snapshot.ladder_copy
    invalid = _valid_request(request, now)
    if invalid is not None:
        return invalid
    invalid = _valid_ladder(request, source_ladder, now)
    if invalid is not None:
        return invalid
    if len(snapshot.evidence_copies) != len(QUALITY_ORDER):
        return "tier_coverage_incomplete"
    for evidence, expected_tier in zip(snapshot.evidence_copies, QUALITY_ORDER, strict=True):
        invalid = _valid_tier_evidence(request, source_ladder, evidence, expected_tier, now)
        if invalid is not None:
            return invalid
    return None


def _playlist_clock_failure(
    snapshots: tuple[_PlaylistSnapshot, ...],
    reason: str,
) -> PlaylistResult:
    rejected = tuple(
        _reject(reason, _safe_requested_tier(snapshot.request_copy))
        for snapshot in snapshots
    )
    return PlaylistResult(rejected, False, reason)


def _playlist_unchanged(
    items: object,
    root_refs: tuple[object, ...],
    snapshots: tuple[_PlaylistSnapshot, ...],
) -> bool:
    if type(items) is tuple:
        if len(items) != len(root_refs) or any(items[i] is not root_refs[i] for i in range(len(root_refs))):
            return False
    elif type(items) is list:
        if len(items) != len(root_refs) or any(items[i] is not root_refs[i] for i in range(len(root_refs))):
            return False
    else:
        return False
    try:
        for captured in snapshots:
            if captured.pair_refs is None:
                if (
                    captured.pair_length is None
                    or type(captured.item) not in (tuple, list)
                    or len(captured.item) != captured.pair_length
                    or captured.pair_length <= 3
                ):
                    return False
                continue
            item = captured.item
            if type(item) not in (tuple, list) or len(item) != captured.pair_length:
                return False
            if any(item[i] is not captured.pair_refs[i] for i in range(captured.pair_length)):
                return False
            if captured.request_fields is not None and (
                type(captured.request) is not QualityRequest
                or _request_snapshot(captured.request) != captured.request_fields
            ):
                return False
            if captured.ladder_fields is not None and (
                type(captured.ladder) is not QualityLadder
                or _ladder_snapshot(captured.ladder) != captured.ladder_fields
            ):
                return False
            if captured.evidence_refs is not None:
                evidence = captured.evidence
                if type(evidence) not in (tuple, list) or len(evidence) != len(captured.evidence_refs):
                    return False
                if any(evidence[i] is not captured.evidence_refs[i] for i in range(len(captured.evidence_refs))):
                    return False
                if captured.evidence_fields is not None and any(
                    type(entry) is not TierEvidence or _evidence_snapshot(entry) != fields
                    for entry, fields in zip(captured.evidence_refs, captured.evidence_fields, strict=True)
                ):
                    return False
        return True
    except Exception:
        return False


def _result_for_snapshot(
    snapshot: _PlaylistSnapshot,
    verifier: object,
    clock: object,
) -> QualityResult:
    if (
        snapshot.request_copy is None
        or snapshot.ladder_copy is None
        or snapshot.evidence_copies is None
    ):
        return _reject("invalid_playlist_item")
    return evaluate_best_available(
        snapshot.request_copy,
        snapshot.ladder_copy,
        snapshot.evidence_copies,
        verifier,
        clock,
    )


def evaluate_playlist(
    items: object,
    verifier: object,
    clock: object,
) -> PlaylistResult:
    """Evaluate a bounded playlist with monotonic, playlist-wide freshness checks."""
    snapshots: tuple[_PlaylistSnapshot, ...] = ()
    results: list[QualityResult] = []
    playlist_clock = _MonotonicPlaylistClock(clock)
    try:
        if type(items) not in (tuple, list):
            return PlaylistResult((), False, "invalid_playlist")
        if not items:
            return PlaylistResult((), False, "empty_playlist")
        if len(items) > MAX_PLAYLIST_TRACKS:
            return PlaylistResult((), False, "playlist_too_large")
        root_refs = tuple(items)
        snapshots = tuple(_snapshot_playlist_item(item) for item in root_refs)
        guards = _snapshot_guards(snapshots)
        if (
            not _snapshot_guards_unchanged(snapshots, guards)
            or not _playlist_unchanged(items, root_refs, snapshots)
        ):
            return PlaylistResult((), False, "playlist_snapshot_failed")
        if any(
            captured.pair_length != 3
            or captured.request_fields is None
            or captured.ladder_fields is None
            or captured.evidence_refs is None
            or captured.evidence_copies is None
            for captured in snapshots
        ):
            rejected = tuple(_reject("invalid_playlist_item") for _ in snapshots)
            return PlaylistResult(rejected, True, "playlist_item_invalid")

        identities: list[tuple[str, str] | None] = []
        for captured in snapshots:
            fields = captured.request_fields
            if fields is not None and _identifier(fields[0]) and _identifier(fields[1]):
                identities.append((fields[0], fields[1]))
            else:
                identities.append(None)
        counts: dict[tuple[str, str], int] = {}
        for identity in identities:
            if identity is not None:
                counts[identity] = counts.get(identity, 0) + 1

        for index, captured in enumerate(snapshots):
            identity = identities[index]
            if identity is not None and counts[identity] > 1:
                result = _reject("duplicate_playlist_attempt")
            else:
                result = _result_for_snapshot(captured, verifier, playlist_clock)
            if playlist_clock.failure is not None:
                return _playlist_clock_failure(snapshots, playlist_clock.failure)
            if (
                not _snapshot_guards_unchanged(snapshots, guards)
                or not _playlist_unchanged(items, root_refs, snapshots)
            ):
                rejected = tuple(
                    _reject("playlist_changed_during_verification")
                    for _ in snapshots
                )
                return PlaylistResult(rejected, False, "playlist_changed_during_verification")
            results.append(result)

        if (
            not _snapshot_guards_unchanged(snapshots, guards)
            or not _playlist_unchanged(items, root_refs, snapshots)
        ):
            rejected = tuple(
                _reject("playlist_changed_during_verification")
                for _ in snapshots
            )
            return PlaylistResult(rejected, False, "playlist_changed_during_verification")

        try:
            final_now = playlist_clock.now()
        except Exception:
            return _playlist_clock_failure(
                snapshots,
                playlist_clock.failure or "clock_unavailable",
            )
        if (
            not _snapshot_guards_unchanged(snapshots, guards)
            or not _playlist_unchanged(items, root_refs, snapshots)
        ):
            rejected = tuple(
                _reject("playlist_changed_during_verification")
                for _ in snapshots
            )
            return PlaylistResult(rejected, False, "playlist_changed_during_verification")

        for index, (captured, result) in enumerate(zip(snapshots, results, strict=True)):
            if result.status is not EvaluationStatus.SELECTED:
                continue
            invalid = _freshness_failure(captured, final_now)
            if invalid is not None:
                results[index] = _reject(invalid, result.requested_tier)

        if (
            not _snapshot_guards_unchanged(snapshots, guards)
            or not _playlist_unchanged(items, root_refs, snapshots)
        ):
            rejected = tuple(
                _reject("playlist_changed_during_verification")
                for _ in snapshots
            )
            return PlaylistResult(rejected, False, "playlist_changed_during_verification")
        return PlaylistResult(tuple(results), True, "playlist_evaluated")
    except Exception:
        if snapshots:
            rejected = tuple(
                _reject("playlist_evaluation_failed", _safe_requested_tier(snapshot.request_copy))
                for snapshot in snapshots
            )
            return PlaylistResult(rejected, False, "playlist_evaluation_failed")
        return PlaylistResult((), False, "playlist_evaluation_failed")

__all__ = [
    "AvailabilityStatus",
    "CoverageStatus",
    "EvaluationStatus",
    "MAX_PLAYLIST_TRACKS",
    "PRODUCTION_READY",
    "PlaylistResult",
    "QualityLadder",
    "QualityRequest",
    "QualityResult",
    "RightsStatus",
    "TierEvidence",
    "TrustReport",
    "TrustStatus",
    "UnavailableReason",
    "evaluate_best_available",
    "evaluate_playlist",
]
