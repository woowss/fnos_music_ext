#!/usr/bin/env python3
"""G-X3: semantic (non-schema) invariant validator for P1 acceptance EvidenceSet JSON.

Boundary
--------
Read-only, offline tool. It is **not** part of the product, is not wired into any
route, does not access devices, does not write evidence directories, and does not
replace JSON Schema validation: the input is assumed to be a *candidate* P1
EvidenceSet (docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md, Rev-3) and this tool
only checks the cross-field semantic invariants that JSON Schema cannot express
(§2.6, G-X3, V-3).

Covered invariants (minimum scope required by G-X3)
---------------------------------------------------
- INV-15: position uniqueness/range; position array order as a **named ascending
  proxy** (``INV-15.manifest_order_ascending_proxy``; ascending is only "proxy
  consistent", never proof of manifest order); position count vs
  ``job.positions_total`` / ``summary.positions_total``.
- INV-16: summary counts vs per-position ``verdict.verdict``; sum vs total;
  ``overall_verdict`` derivation rule.
- INV-17: state <-> delivered_evidence/failure matrix (published requires
  delivered and no failure; rejected/failed/commit_unknown require failure and a
  fixed code; conflict requires null delivered + allowed resolution; other
  non-published states require null delivered).
- INV-2/INV-3/INV-23: expected_outcome <-> verdict mapping (expected failure
  confirmed is pass; unexpected published is fail; commit_unknown is
  blocked/unknown; pass gates per expected_outcome; fail verdict needs fixed
  reasons and justification; expected/actual mismatch must not be a silent pass).
- INV-22: ``job.source_fingerprint_origin`` <-> ``source_fingerprint`` and
  ``environment.deployed.provenance_status`` <-> package/git hashes.
  ``collection_version_manifest`` allows a null fingerprint; a non-null value
  must be 64hex (review G-4: not stricter than INV-22/schema).
- INV-18: ``quality.fallback=true`` requires attempts and coverage of all higher
  tiers; a reported level different from the requested tier requires fallback.
  An empty/unknown ``tier_requested`` with a reported level is **unknown**
  (``requested_tier_unknown_for_reported``), not a silent pass (review G-3).
- Position count vs ``positions_total`` (part of INV-15).

Rev-2 review fixes (docs/SEMANTICS_VALIDATOR_REVIEW_20261107.md, G-1..G-8)
--------------------------------------------------------------------------
- G-2: ``schema``/``schema_version`` are echoed only as their fixed constants;
  any other value is replaced by ``<unexpected>``/``<invalid>`` and the
  structure precondition fails closed (no arbitrary text on stdout).
- G-3: empty/unknown ``tier_requested`` + reported level is ``unknown`` (exit 2).
- G-4: ``collection_version_manifest`` null is allowed per INV-22/schema;
  non-null must be 64hex.
- G-5: the manifest-order check is renamed ``INV-15.manifest_order_ascending_proxy``
  and carries ``verification=ascending_proxy_only`` so a pass cannot be read as
  "manifest order verified".
- G-1 (INV-17 wording) needed no implementation change; implemented per §2.6.

Interpretation notes
--------------------
- INV-15 "与 scope 声明一致": the EvidenceSet declares position totals through
  ``job.positions_total`` and ``summary.positions_total`` (``scope`` only lists
  contract/acceptance items, not a position set), so the check compares
  ``len(positions)`` against both declared totals.
- INV-17 is implemented per the schema document (§2.6 + §2.4.6): ``failure`` is
  required for ``state in {rejected,failed,commit_unknown}``; ``state=conflict``
  uses ``duplicate_resolution`` (and must not carry delivered evidence); other
  non-published states must not carry delivered evidence. The requirement text
  "non-published must have a failure" is therefore read as "failure *states*
  must have a failure with a fixed code", because conflict/cancelled/etc. are
  non-published but intentionally carry no ``failure`` in the schema.

Fail-closed contract
--------------------
Every check emits ``pass``/``fail``/``unknown`` plus a fixed reason code (``null``
on pass). The CLI exits 0 **only** when every check passes; any ``fail`` or
``unknown`` (including unparsable/invalid input) exits 2. Nothing defaults to
pass.

Not machine-checkable here (reported in ``not_checked``)
--------------------------------------------------------
Manifest position order itself (only ``manifest_sha256`` is in the EvidenceSet),
ledger refresh consistency, fixed human-readable ``reason_message`` text,
``record_sha256``/manifest recomputation (INV-13 canonicalization), evidence-ref/
artifact binding (INV-12), the remaining dimension invariants that require
artifacts, and the "no successful file" filesystem check. These need the evidence
store/artifacts/ledger and are deliberately out of scope for this validator.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from typing import Any

TOOL = "verify_acceptance_evidence_semantics"
TOOL_VERSION = 1

# The only identifiers this tool ever echoes back. Anything else is replaced by
# a fixed token in the report and fails closed (review G-2: schema_version was
# previously echoed verbatim, allowing arbitrary text to reach stdout).
SCHEMA_CONST = "fnmusic.p1.acceptance-evidence"
SCHEMA_VERSION_CONST = "0.1-draft-20261107"

EXIT_PASS = 0
EXIT_FAIL_CLOSED = 2

_TIERS = ("jymaster", "hires", "lossless", "exhigh")  # ladder order: index 0 = highest
_TIER_RANK = {tier: rank for rank, tier in enumerate(_TIERS)}
_TIER_REQUESTED_VALUES = frozenset(_TIERS) | {""}
_STATES = frozenset({
    "published", "conflict", "rejected", "failed", "commit_unknown",
    "cancelled", "queued", "running", "not_executed",
})
_FAILURE_STATES = frozenset({"rejected", "failed", "commit_unknown"})
_NON_PUBLISHED_NO_DELIVERY = frozenset({
    "cancelled", "queued", "running", "not_executed",
})
_EXPECTED_OUTCOMES = frozenset({"published", "conflict", "failure", "cancelled"})
_VERDICTS = frozenset({"pass", "fail", "unknown", "blocked"})
_DIMENSIONS = ("bytes", "quality", "tags", "cover", "lyric", "duplicate", "index_play")
_DIMENSION_VALUES = frozenset({"pass", "fail", "unknown", "advisory", "not_applicable"})
_ATTEMPT_OUTCOMES = frozenset({"available", "unavailable", "trial", "unverified", "identity_mismatch"})
_DUPLICATE_RESOLUTIONS = frozenset({
    None, "conflict_expected", "distinct_basenames", "ambiguous_expected",
    "unresolved", "not_applicable", "unknown",
})
_FINGERPRINT_ORIGINS = frozenset({"snapshot_response", "collection_version_manifest", "unknown"})
_PROVENANCE_STATUSES = frozenset({"observed", "partial", "unknown"})
_CONFLICT_RESOLUTIONS = frozenset({"conflict_expected", "unresolved"})

# Fixed reason-code set for failure.code / verdict.fixed_reasons (schema
# §2.5 $defs.reason_code; Appendix C says it must be reconciled with code
# constants before use -- this tool only checks membership in this frozen draft
# set so drift is visible rather than silently accepted).
_EVIDENCE_REASON_CODES = frozenset({
    "service_unavailable", "no_quality_available", "quality_unverified",
    "quality_identity_mismatch", "trial_only", "download_target_untrusted",
    "download_failed", "integrity_mismatch", "invalid_audio", "tag_failed",
    "conflict", "disabled", "config_invalid", "busy", "staging_unavailable",
    "publish_failed", "internal", "invalid_song_id", "too_many_positions",
    "budget_exhausted", "not_executed", "commit_unknown", "completion_unknown",
    "cancelled", "evidence_missing_quality_claim", "evidence_quality_contradiction",
    "evidence_tag_mismatch", "evidence_cover_mismatch", "evidence_lyric_mismatch",
    "evidence_basename_unresolved", "evidence_byte_binding_unavailable",
    "evidence_scanner_unknown", "evidence_index_unverified",
    "evidence_playback_unverified", "evidence_capture_failed",
    "evidence_redaction_violation", "evidence_schema_invalid",
})

# Fixed reason codes emitted by this validator (never free text/exception text).
_REASON_CODES = frozenset({
    "evidence_schema_invalid",
    "schema_version_not_fixed",
    "input_unreadable",
    "evidence_input_invalid_json",
    "positions_duplicate",
    "position_out_of_range",
    "manifest_order_unavailable",
    "positions_total_mismatch",
    "summary_count_mismatch",
    "summary_total_mismatch",
    "overall_verdict_mismatch",
    "published_failure_present",
    "published_without_delivered",
    "failure_missing",
    "failure_code_not_fixed",
    "fixed_reason_code_not_fixed",
    "conflict_delivered_present",
    "conflict_resolution_invalid",
    "non_published_delivered_present",
    "expected_published_pass_gates_unmet",
    "expected_conflict_pass_conditions_unmet",
    "expected_failure_confirmation_invalid",
    "expected_cancelled_pass_conditions_unmet",
    "commit_unknown_verdict_invalid",
    "fail_verdict_without_fixed_reasons",
    "fail_verdict_unjustified",
    "expected_actual_mismatch_silent_pass",
    "expected_actual_mismatch_invalid_verdict",
    "expected_actual_mismatch_fixed_reasons_missing",
    "unexpected_published_not_fail",
    "fingerprint_origin_unknown_not_null",
    "fingerprint_snapshot_not_64hex",
    "fingerprint_declared_not_64hex",
    "fingerprint_origin_invalid",
    "deployed_observed_hashes_missing",
    "fallback_attempts_missing",
    "fallback_attempts_invalid_entry",
    "fallback_attempts_tier_coverage",
    "reported_diff_requires_fallback",
    "requested_tier_unknown_for_reported",
})

_HEX64_RE = re.compile(r"^[a-f0-9]{64}$")
_HEX40_RE = re.compile(r"^[a-f0-9]{40}$")
_HEX32_RE = re.compile(r"^[a-f0-9]{32}$")
_HEX48_RE = re.compile(r"^[a-f0-9]{48}$")
_MAX_DETAIL_VIOLATIONS = 50


# ---------------------------------------------------------------------------
# Small type helpers (the input is untrusted JSON: never raise on shape issues).
# ---------------------------------------------------------------------------


def _is_int(value: object) -> bool:
    return type(value) is int and not isinstance(value, bool)


def _is_hex64(value: object) -> bool:
    return isinstance(value, str) and _HEX64_RE.fullmatch(value) is not None


def _is_hex40(value: object) -> bool:
    return isinstance(value, str) and _HEX40_RE.fullmatch(value) is not None


def _is_hex32(value: object) -> bool:
    return isinstance(value, str) and _HEX32_RE.fullmatch(value) is not None


def _as_dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


# ---------------------------------------------------------------------------
# Check collection
# ---------------------------------------------------------------------------


class _Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, check_id: str, invariant: str, status: str, *,
            reason: str | None = None,
            details: Any = None) -> None:
        if status not in {"pass", "fail", "unknown"}:
            raise ValueError("invalid check status")
        if status == "pass":
            if reason is not None:
                raise ValueError("pass check must not carry a reason code")
        else:
            if reason not in _REASON_CODES:
                raise ValueError("unfixed reason code: %r" % (reason,))
        item = {
            "id": check_id,
            "invariant": invariant,
            "status": status,
            "reason": reason if status != "pass" else None,
        }
        if details is not None:
            item["details"] = _cap_details(details)
        self.items.append(item)

    def evaluate(self, check_id: str, invariant: str, violations: list[Any],
                 reason: str, *, unknown: bool = False,
                 details: Any = None) -> None:
        if not violations:
            self.add(check_id, invariant, "pass")
        elif unknown:
            self.add(check_id, invariant, "unknown", reason=reason,
                     details=details if details is not None else {"violations": violations})
        else:
            self.add(check_id, invariant, "fail", reason=reason,
                     details=details if details is not None else {"violations": violations})


def _cap_details(details: Any, limit: int = _MAX_DETAIL_VIOLATIONS) -> Any:
    if isinstance(details, dict):
        capped = dict(details)
        for key in ("violations", "issues"):
            value = capped.get(key)
            if isinstance(value, list) and len(value) > limit:
                capped[key] = value[:limit]
                capped["truncated"] = True
        return capped
    if isinstance(details, list) and len(details) > limit:
        return details[:limit] + ["...[truncated]"]
    return details


def _position_label(position: dict[str, Any]) -> int | None:
    value = position.get("position")
    return value if _is_int(value) else None


# ---------------------------------------------------------------------------
# Structure precondition
# ---------------------------------------------------------------------------


def _structure_issues(document: object) -> list[dict[str, str]]:
    """Minimal shape preconditions needed to evaluate the semantics.

    This is deliberately not a JSON Schema replacement; it only prevents the
    semantic checks from operating on missing/wrong-typed nodes.
    """
    issues: list[dict[str, str]] = []

    def need(condition: bool, path: str, problem: str) -> None:
        if not condition and len(issues) < 40:
            issues.append({"path": path, "problem": problem})

    if not isinstance(document, dict):
        return [{"path": "$", "problem": "EvidenceSet must be a JSON object"}]
    doc: dict[str, Any] = document

    need(doc.get("schema") == SCHEMA_CONST, "$.schema", "not the fixed schema identifier")
    need(doc.get("schema_version") == SCHEMA_VERSION_CONST,
         "$.schema_version", "not the fixed schema version")
    need(isinstance(doc.get("evidence_set_id"), str), "$.evidence_set_id", "missing or not a string")
    need(isinstance(doc.get("created_at"), str), "$.created_at", "missing or not a string")

    job = doc.get("job")
    need(isinstance(job, dict), "$.job", "missing or not an object")
    if isinstance(job, dict):
        need(_is_hex64(job.get("manifest_sha256")), "$.job.manifest_sha256", "missing or not 64hex")
        need("source_fingerprint" in job, "$.job.source_fingerprint", "key missing (null allowed)")
        need(job.get("source_fingerprint_origin") in _FINGERPRINT_ORIGINS,
             "$.job.source_fingerprint_origin", "missing or not an allowed enum value")
        need(_is_int(job.get("positions_total")), "$.job.positions_total", "missing or not an integer")

    summary = doc.get("summary")
    need(isinstance(summary, dict), "$.summary", "missing or not an object")
    if isinstance(summary, dict):
        for key in ("positions_total", "pass", "fail", "unknown", "blocked"):
            need(_is_int(summary.get(key)), "$.summary." + key, "missing or not an integer")
        need(summary.get("overall_verdict") in _VERDICTS,
             "$.summary.overall_verdict", "missing or not an allowed enum value")

    environment = doc.get("environment")
    need(isinstance(environment, dict), "$.environment", "missing or not an object")
    if isinstance(environment, dict):
        deployed = environment.get("deployed")
        need(isinstance(deployed, dict), "$.environment.deployed", "missing or not an object")
        if isinstance(deployed, dict):
            need(deployed.get("provenance_status") in _PROVENANCE_STATUSES,
                 "$.environment.deployed.provenance_status",
                 "missing or not an allowed enum value")

    positions = doc.get("positions")
    need(isinstance(positions, list) and len(positions) > 0,
         "$.positions", "missing or not a non-empty array")
    for index, position in enumerate(_as_list(positions)):
        base = "$.positions[%d]" % index
        need(isinstance(position, dict), base, "not an object")
        if not isinstance(position, dict):
            continue
        need(_is_int(position.get("position")), base + ".position", "missing or not an integer")
        need(position.get("state") in _STATES, base + ".state", "missing or not an allowed enum value")
        need(position.get("expected_outcome") in _EXPECTED_OUTCOMES,
             base + ".expected_outcome", "missing or not an allowed enum value")
        need("failure" in position, base + ".failure", "key missing (null allowed)")
        need("delivered_evidence" in position, base + ".delivered_evidence", "key missing (null allowed)")
        verdict = position.get("verdict")
        need(isinstance(verdict, dict), base + ".verdict", "missing or not an object")
        if isinstance(verdict, dict):
            need(verdict.get("verdict") in _VERDICTS,
                 base + ".verdict.verdict", "missing or not an allowed enum value")
            dimensions = verdict.get("dimensions")
            need(isinstance(dimensions, dict), base + ".verdict.dimensions",
                 "missing or not an object")
            if isinstance(dimensions, dict):
                for dimension in _DIMENSIONS:
                    need(dimensions.get(dimension) in _DIMENSION_VALUES,
                         base + ".verdict.dimensions." + dimension,
                         "missing or not an allowed enum value")
            need("duplicate_resolution" in verdict,
                 base + ".verdict.duplicate_resolution", "key missing (null allowed)")
            fixed_reasons = verdict.get("fixed_reasons")
            need(fixed_reasons is None or (
                isinstance(fixed_reasons, list)
                and all(isinstance(code, str) for code in fixed_reasons)),
                base + ".verdict.fixed_reasons", "not an array of strings")
        source_evidence = position.get("source_evidence")
        need(isinstance(source_evidence, dict), base + ".source_evidence",
             "missing or not an object")
        if isinstance(source_evidence, dict):
            quality = source_evidence.get("quality")
            need(isinstance(quality, dict), base + ".source_evidence.quality",
                 "missing or not an object")
            if isinstance(quality, dict):
                need(quality.get("tier_requested") in _TIER_REQUESTED_VALUES,
                     base + ".source_evidence.quality.tier_requested",
                     "missing or not an allowed enum value")
                need(quality.get("reported_status") in {"reported", "unknown"},
                     base + ".source_evidence.quality.reported_status",
                     "missing or not an allowed enum value")
                need("reported_level" in quality,
                     base + ".source_evidence.quality.reported_level", "key missing")
                need(type(quality.get("fallback")) is bool,
                     base + ".source_evidence.quality.fallback", "missing or not a boolean")
                if "attempts" in quality:
                    need(isinstance(quality.get("attempts"), list),
                         base + ".source_evidence.quality.attempts", "not an array")
    return issues


# ---------------------------------------------------------------------------
# Semantic checks
# ---------------------------------------------------------------------------


def _expected_matches_state(expected_outcome: str, state: str) -> bool:
    if expected_outcome == "published":
        return state == "published"
    if expected_outcome == "conflict":
        return state == "conflict"
    if expected_outcome == "failure":
        return state in {"rejected", "failed"}
    if expected_outcome == "cancelled":
        return state == "cancelled"
    return False


def _verdict(position: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(position.get("verdict"))


def _dimensions(position: dict[str, Any]) -> dict[str, Any]:
    return _as_dict(_verdict(position).get("dimensions"))


def _failure(position: dict[str, Any]) -> dict[str, Any] | None:
    value = position.get("failure")
    return value if isinstance(value, dict) else None


def _failure_code_is_fixed(position: dict[str, Any]) -> bool:
    failure = _failure(position)
    return failure is not None and failure.get("code") in _EVIDENCE_REASON_CODES


def _failure_is_invalid(position: dict[str, Any]) -> bool:
    state = position.get("state")
    failure = _failure(position)
    if state in _FAILURE_STATES:
        return failure is None or failure.get("code") not in _EVIDENCE_REASON_CODES
    return failure is not None and failure.get("code") not in _EVIDENCE_REASON_CODES


def _check_inv15(checks: _Checks, positions: list[dict[str, Any]],
                 job: dict[str, Any], summary: dict[str, Any]) -> None:
    values = [position.get("position") for position in positions]

    bad_range = [
        {"index": index, "position": value}
        for index, value in enumerate(values)
        if not _is_int(value) or not 1 <= value <= 1000
    ]
    duplicates: list[int] = []
    if all(_is_int(value) for value in values):
        seen: set[int] = set()
        for value in values:
            if value in seen and value not in duplicates:
                duplicates.append(value)
            seen.add(value)
    if bad_range:
        checks.add("INV-15.positions_unique", "INV-15", "fail",
                   reason="position_out_of_range", details={"violations": bad_range})
    elif duplicates:
        checks.add("INV-15.positions_unique", "INV-15", "fail",
                   reason="positions_duplicate",
                   details={"violations": [{"duplicate_position": value} for value in duplicates]})
    else:
        checks.add("INV-15.positions_unique", "INV-15", "pass")

    if all(_is_int(value) for value in values):
        inversions = [
            {"index": index, "current": values[index], "next": values[index + 1]}
            for index in range(len(values) - 1)
            if values[index] >= values[index + 1]
        ]
        if not inversions:
            # Review G-5: the manifest order is not part of the EvidenceSet, so
            # ascending order can only be *consistent with* a manifest order,
            # never prove it. The check id says _proxy on purpose and carries an
            # explicit verification field so downstream cannot read it as proof.
            checks.add("INV-15.manifest_order_ascending_proxy", "INV-15", "pass",
                       details={"verification": "ascending_proxy_only",
                                "manifest_order_available": False})
        else:
            # The manifest itself is not part of the EvidenceSet (only its
            # sha256), so a non-ascending list cannot be proven inconsistent;
            # fail closed as unknown instead of silently accepting it.
            checks.add("INV-15.manifest_order_ascending_proxy", "INV-15", "unknown",
                       reason="manifest_order_unavailable",
                       details={"violations": inversions[:10],
                                "verification": "ascending_proxy_only",
                                "manifest_order_available": False})
    else:
        checks.add("INV-15.manifest_order_ascending_proxy", "INV-15", "unknown",
                   reason="manifest_order_unavailable",
                   details={"reason": "positions are not all integers",
                            "verification": "ascending_proxy_only",
                            "manifest_order_available": False})

    declared_job = job.get("positions_total")
    declared_summary = summary.get("positions_total")
    actual = len(positions)
    violations = []
    if declared_job != actual:
        violations.append({"field": "job.positions_total", "declared": declared_job, "actual": actual})
    if declared_summary != actual:
        violations.append({"field": "summary.positions_total", "declared": declared_summary, "actual": actual})
    if declared_job != declared_summary:
        violations.append({"field": "job vs summary", "job": declared_job, "summary": declared_summary})
    checks.evaluate("INV-15.positions_total", "INV-15", violations,
                    "positions_total_mismatch",
                    details={"violations": violations, "actual": actual})


def _check_inv16(checks: _Checks, positions: list[dict[str, Any]],
                 summary: dict[str, Any]) -> None:
    derived = {"pass": 0, "fail": 0, "unknown": 0, "blocked": 0}
    for position in positions:
        verdict = _verdict(position).get("verdict")
        if verdict in derived:
            derived[verdict] += 1
    declared = {key: summary.get(key) for key in derived}

    count_violations = [
        {"verdict": key, "declared": declared[key], "derived": derived[key]}
        for key in derived if declared[key] != derived[key]
    ]
    sum_declared = sum(value for value in declared.values() if _is_int(value))
    total_declared = summary.get("positions_total")
    total_violations = []
    if _is_int(total_declared) and sum_declared != total_declared:
        total_violations.append({"declared_sum": sum_declared,
                                 "summary.positions_total": total_declared})
    if sum_declared != len(positions):
        total_violations.append({"declared_sum": sum_declared,
                                 "positions_length": len(positions)})
    if count_violations:
        checks.add("INV-16.summary_counts", "INV-16", "fail",
                   reason="summary_count_mismatch",
                   details={"violations": count_violations})
    elif total_violations:
        checks.add("INV-16.summary_counts", "INV-16", "fail",
                   reason="summary_total_mismatch",
                   details={"violations": total_violations})
    else:
        checks.add("INV-16.summary_counts", "INV-16", "pass")

    if derived["fail"] > 0:
        derived_overall = "fail"
    elif derived["blocked"] > 0:
        derived_overall = "blocked"
    elif derived["unknown"] > 0:
        derived_overall = "unknown"
    else:
        derived_overall = "pass"
    declared_overall = summary.get("overall_verdict")
    if declared_overall == derived_overall:
        checks.add("INV-16.overall_verdict", "INV-16", "pass")
    else:
        checks.add("INV-16.overall_verdict", "INV-16", "fail",
                   reason="overall_verdict_mismatch",
                   details={"violations": [{"declared": declared_overall,
                                            "derived": derived_overall,
                                            "derived_counts": derived}]})


def _check_inv17(checks: _Checks, positions: list[dict[str, Any]]) -> None:
    published_without_delivered: list[dict[str, Any]] = []
    published_failure_present: list[dict[str, Any]] = []
    failure_missing: list[dict[str, Any]] = []
    failure_code_not_fixed: list[dict[str, Any]] = []
    conflict_delivered_present: list[dict[str, Any]] = []
    conflict_resolution_invalid: list[dict[str, Any]] = []
    delivered_present_non_published: list[dict[str, Any]] = []

    for position in positions:
        state = position.get("state")
        delivered = position.get("delivered_evidence")
        failure = _failure(position)
        label = _position_label(position)
        if state == "published":
            if delivered is None:
                published_without_delivered.append({"position": label})
            if failure is not None:
                published_failure_present.append({"position": label})
        elif state in _FAILURE_STATES:
            if failure is None:
                failure_missing.append({"position": label, "state": state})
        if failure is not None and failure.get("code") not in _EVIDENCE_REASON_CODES:
            # Never echo the raw non-fixed code: it may contain URLs, paths or
            # tokens. The position label is enough for the operator.
            failure_code_not_fixed.append({"position": label})
        if state == "conflict":
            if delivered is not None:
                conflict_delivered_present.append({"position": label})
            resolution = _verdict(position).get("duplicate_resolution")
            if resolution not in _CONFLICT_RESOLUTIONS:
                conflict_resolution_invalid.append({"position": label})
        elif state in _NON_PUBLISHED_NO_DELIVERY and delivered is not None:
            delivered_present_non_published.append({"position": label, "state": state})

    checks.evaluate("INV-17.published_requires_delivered", "INV-17",
                    published_without_delivered, "published_without_delivered",
                    details={"violations": published_without_delivered})
    checks.evaluate("INV-17.published_failure_null", "INV-17",
                    published_failure_present, "published_failure_present")
    checks.evaluate("INV-17.failure_states_require_failure", "INV-17",
                    failure_missing, "failure_missing")
    checks.evaluate("INV-17.failure_code_fixed", "INV-17",
                    failure_code_not_fixed, "failure_code_not_fixed")
    checks.evaluate("INV-17.conflict_delivered_null", "INV-17",
                    conflict_delivered_present, "conflict_delivered_present",
                    details={"violations": conflict_delivered_present})
    checks.evaluate("INV-17.conflict_resolution", "INV-17",
                    conflict_resolution_invalid, "conflict_resolution_invalid")
    checks.evaluate("INV-17.non_published_delivered_null", "INV-17",
                    delivered_present_non_published, "non_published_delivered_present")


def _check_expected_outcome_verdict(checks: _Checks,
                                    positions: list[dict[str, Any]]) -> None:
    published_gates: list[dict[str, Any]] = []
    conflict_conditions: list[dict[str, Any]] = []
    failure_confirmation: list[dict[str, Any]] = []
    cancelled_conditions: list[dict[str, Any]] = []
    commit_unknown_invalid: list[dict[str, Any]] = []

    for position in positions:
        expected = position.get("expected_outcome")
        state = position.get("state")
        verdict = _verdict(position)
        verdict_value = verdict.get("verdict")
        dimensions = _dimensions(position)
        delivered = position.get("delivered_evidence")
        label = _position_label(position)

        if expected == "published" and verdict_value == "pass":
            unmet = []
            for dimension in ("bytes", "quality", "tags", "index_play"):
                if dimensions.get(dimension) != "pass":
                    unmet.append({"dimension": dimension, "value": dimensions.get(dimension)})
            if dimensions.get("duplicate") not in ("pass", "not_applicable"):
                unmet.append({"dimension": "duplicate", "value": dimensions.get("duplicate")})
            for dimension in ("cover", "lyric"):
                if dimensions.get(dimension) not in ("pass", "advisory"):
                    unmet.append({"dimension": dimension, "value": dimensions.get(dimension)})
            if unmet:
                published_gates.append({"position": label, "unmet": unmet})

        if expected == "conflict" and verdict_value == "pass":
            problems = []
            if state != "conflict":
                problems.append("state!=conflict")
            if verdict.get("duplicate_resolution") != "conflict_expected":
                problems.append("duplicate_resolution!=conflict_expected")
            if dimensions.get("duplicate") != "pass":
                problems.append("dimensions.duplicate!=pass")
            if delivered is not None:
                problems.append("delivered_evidence!=null")
            if problems:
                conflict_conditions.append({"position": label, "problems": problems})

        if expected == "failure" and verdict_value == "pass":
            problems = []
            if state not in {"rejected", "failed"}:
                problems.append("state not rejected/failed")
            if not _failure_code_is_fixed(position):
                problems.append("failure/code invalid")
            if problems:
                failure_confirmation.append({"position": label, "problems": problems})

        if expected == "cancelled" and verdict_value == "pass":
            problems = []
            if state != "cancelled":
                problems.append("state!=cancelled")
            if delivered is not None:
                problems.append("delivered_evidence!=null")
            if problems:
                cancelled_conditions.append({"position": label, "problems": problems})

        if state == "commit_unknown" and verdict_value not in ("blocked", "unknown"):
            commit_unknown_invalid.append({"position": label, "verdict": verdict_value})

    checks.evaluate("INV-2.expected_published_pass_gates", "INV-2",
                    published_gates, "expected_published_pass_gates_unmet")
    checks.evaluate("INV-2.expected_conflict_pass_conditions", "INV-2",
                    conflict_conditions, "expected_conflict_pass_conditions_unmet")
    checks.evaluate("INV-2.expected_failure_pass_confirmation", "INV-2",
                    failure_confirmation, "expected_failure_confirmation_invalid")
    checks.evaluate("INV-2.expected_cancelled_pass_conditions", "INV-2",
                    cancelled_conditions, "expected_cancelled_pass_conditions_unmet")
    checks.evaluate("INV-2.commit_unknown_verdict", "INV-2",
                    commit_unknown_invalid, "commit_unknown_verdict_invalid")


def _check_inv3_and_inv23(checks: _Checks,
                          positions: list[dict[str, Any]]) -> None:
    fail_without_reasons: list[dict[str, Any]] = []
    fail_unjustified: list[dict[str, Any]] = []
    mismatch_silent_pass: list[dict[str, Any]] = []
    mismatch_invalid_verdict: list[dict[str, Any]] = []
    mismatch_missing_reasons: list[dict[str, Any]] = []
    unexpected_published_not_fail: list[dict[str, Any]] = []

    for position in positions:
        expected = position.get("expected_outcome")
        state = position.get("state")
        verdict_value = _verdict(position).get("verdict")
        fixed_reasons = _verdict(position).get("fixed_reasons")
        fixed_reasons_ok = isinstance(fixed_reasons, list) and len(fixed_reasons) > 0
        label = _position_label(position)

        mismatch = False
        if state == "commit_unknown":
            mismatch = False  # handled by the dedicated A16/commit_unknown rule
        else:
            mismatch = not _expected_matches_state(expected, state)

        if verdict_value == "fail":
            if not fixed_reasons_ok:
                fail_without_reasons.append({"position": label})
            has_fail_dimension = any(value == "fail" for value in _dimensions(position).values())
            if not (has_fail_dimension or mismatch or _failure_is_invalid(position)):
                fail_unjustified.append({"position": label})

        if mismatch:
            if verdict_value == "pass":
                mismatch_silent_pass.append(
                    {"position": label, "expected_outcome": expected, "state": state})
            elif verdict_value not in ("fail", "unknown"):
                mismatch_invalid_verdict.append(
                    {"position": label, "expected_outcome": expected,
                     "state": state, "verdict": verdict_value})
            elif not fixed_reasons_ok:
                mismatch_missing_reasons.append(
                    {"position": label, "expected_outcome": expected,
                     "state": state, "verdict": verdict_value})
            if expected == "failure" and state == "published" and verdict_value != "fail":
                unexpected_published_not_fail.append(
                    {"position": label, "verdict": verdict_value})

    checks.evaluate("INV-3.fail_verdict_without_fixed_reasons", "INV-3",
                    fail_without_reasons, "fail_verdict_without_fixed_reasons")
    checks.evaluate("INV-3.fail_verdict_unjustified", "INV-3",
                    fail_unjustified, "fail_verdict_unjustified")
    if mismatch_silent_pass:
        checks.add("INV-23.expected_actual_mismatch", "INV-23", "fail",
                   reason="expected_actual_mismatch_silent_pass",
                   details={"violations": mismatch_silent_pass})
    elif unexpected_published_not_fail:
        checks.add("INV-23.expected_actual_mismatch", "INV-23", "fail",
                   reason="unexpected_published_not_fail",
                   details={"violations": unexpected_published_not_fail})
    elif mismatch_invalid_verdict:
        checks.add("INV-23.expected_actual_mismatch", "INV-23", "fail",
                   reason="expected_actual_mismatch_invalid_verdict",
                   details={"violations": mismatch_invalid_verdict})
    elif mismatch_missing_reasons:
        checks.add("INV-23.expected_actual_mismatch", "INV-23", "fail",
                   reason="expected_actual_mismatch_fixed_reasons_missing",
                   details={"violations": mismatch_missing_reasons})
    else:
        checks.add("INV-23.expected_actual_mismatch", "INV-23", "pass")


def _check_inv19(checks: _Checks, positions: list[dict[str, Any]]) -> None:
    violations: list[dict[str, Any]] = []
    for position in positions:
        reasons = _verdict(position).get("fixed_reasons")
        if not isinstance(reasons, list):
            continue
        bad_indexes = [index for index, code in enumerate(reasons)
                       if code not in _EVIDENCE_REASON_CODES]
        if bad_indexes:
            # Never echo the raw code string: it may contain URLs/paths/tokens.
            violations.append({"position": _position_label(position),
                               "bad_indexes": bad_indexes})
    checks.evaluate("INV-19.fixed_reasons_codes", "INV-19", violations,
                    "fixed_reason_code_not_fixed")


def _check_inv22(checks: _Checks, document: dict[str, Any]) -> None:
    job = _as_dict(document.get("job"))
    environment = _as_dict(document.get("environment"))
    deployed = _as_dict(environment.get("deployed"))

    provenance_status = deployed.get("provenance_status")
    deployed_violations = []
    if provenance_status == "observed":
        if not _is_hex64(deployed.get("package_sha256")):
            deployed_violations.append({"field": "package_sha256",
                                        "problem": "observed provenance requires 64hex"})
        if not _is_hex40(deployed.get("git_head")):
            deployed_violations.append({"field": "git_head",
                                        "problem": "observed provenance requires 40hex"})
    checks.evaluate("INV-22.deployed_provenance", "INV-22",
                    deployed_violations, "deployed_observed_hashes_missing")

    origin = job.get("source_fingerprint_origin")
    fingerprint = job.get("source_fingerprint")
    fingerprint_violations = []
    if origin == "unknown":
        if fingerprint is not None:
            fingerprint_violations.append(
                {"origin": origin, "problem": "unknown origin requires null fingerprint"})
    elif origin == "snapshot_response":
        if not _is_hex64(fingerprint):
            fingerprint_violations.append(
                {"origin": origin, "problem": "snapshot_response requires 64hex fingerprint"})
    elif origin == "collection_version_manifest":
        # Review G-4: INV-22/schema only constrain unknown=>null and
        # snapshot_response=>64hex. For this origin null is allowed; a non-null
        # value must still be 64hex (do not be stricter than the document).
        if fingerprint is not None and not _is_hex64(fingerprint):
            fingerprint_violations.append(
                {"origin": origin, "problem": "non-null fingerprint must be 64hex"})
    else:
        fingerprint_violations.append({"problem": "invalid origin enum"})
    if not fingerprint_violations:
        checks.add("INV-22.fingerprint_origin", "INV-22", "pass")
    else:
        if any("unknown origin" in item["problem"] for item in fingerprint_violations):
            reason = "fingerprint_origin_unknown_not_null"
        elif any("snapshot_response" in item["problem"] for item in fingerprint_violations):
            reason = "fingerprint_snapshot_not_64hex"
        elif any("non-null fingerprint" in item["problem"] for item in fingerprint_violations):
            reason = "fingerprint_declared_not_64hex"
        else:
            reason = "fingerprint_origin_invalid"
        checks.add("INV-22.fingerprint_origin", "INV-22", "fail", reason=reason,
                   details={"violations": fingerprint_violations})


def _check_inv18(checks: _Checks, positions: list[dict[str, Any]]) -> None:
    attempts_missing: list[dict[str, Any]] = []
    attempts_invalid: list[dict[str, Any]] = []
    attempts_coverage: list[dict[str, Any]] = []
    reported_diff_no_fallback: list[dict[str, Any]] = []
    unknown_requested_tier: list[dict[str, Any]] = []

    for position in positions:
        quality = _as_dict(_as_dict(position.get("source_evidence")).get("quality"))
        fallback = quality.get("fallback")
        attempts = quality.get("attempts")
        requested = quality.get("tier_requested")
        label = _position_label(position)

        if fallback is True:
            if not isinstance(attempts, list) or len(attempts) == 0:
                attempts_missing.append({"position": label})
            else:
                invalid_entries = []
                present_tiers: set[str] = set()
                for index, attempt in enumerate(attempts):
                    if not isinstance(attempt, dict):
                        invalid_entries.append({"index": index, "problem": "not an object"})
                        continue
                    tier = attempt.get("tier")
                    outcome = attempt.get("outcome")
                    if tier not in _TIER_RANK:
                        invalid_entries.append({"index": index, "problem": "invalid tier"})
                    else:
                        present_tiers.add(tier)
                    if outcome not in _ATTEMPT_OUTCOMES:
                        invalid_entries.append({"index": index, "problem": "invalid outcome"})
                if invalid_entries:
                    attempts_invalid.append({"position": label,
                                             "entries": invalid_entries[:10]})
                if requested in _TIER_RANK:
                    required_higher = set(_TIERS[: _TIER_RANK[requested]])
                    missing = sorted(required_higher - present_tiers, key=_TIER_RANK.get)
                    if missing:
                        attempts_coverage.append(
                            {"position": label,
                             "tier_requested": requested,
                             "missing_higher_tiers": missing})

        reported_status = quality.get("reported_status")
        reported_level = quality.get("reported_level")
        if reported_status == "reported" and isinstance(reported_level, str) and reported_level:
            if requested not in _TIER_RANK:
                # Review G-3: with an empty/unknown requested tier, the
                # reported!=requested => fallback comparison cannot be decided.
                # Do not skip it silently: unknown status (fail-closed, exit 2).
                unknown_requested_tier.append(
                    {"position": label, "tier_requested": requested})
            elif reported_level != requested and fallback is not True:
                # Do not echo reported_level: provider text may contain arbitrary
                # (even sensitive) content; the enum fields are enough evidence.
                reported_diff_no_fallback.append(
                    {"position": label, "tier_requested": requested})

    checks.evaluate("INV-18.fallback_attempts_present", "INV-18",
                    attempts_missing, "fallback_attempts_missing")
    if attempts_invalid:
        checks.add("INV-18.fallback_tier_coverage", "INV-18", "fail",
                   reason="fallback_attempts_invalid_entry",
                   details={"violations": attempts_invalid})
    else:
        checks.evaluate("INV-18.fallback_tier_coverage", "INV-18",
                        attempts_coverage, "fallback_attempts_tier_coverage")
    if reported_diff_no_fallback:
        checks.add("INV-18.reported_diff_requires_fallback", "INV-18", "fail",
                   reason="reported_diff_requires_fallback",
                   details={"violations": reported_diff_no_fallback})
    elif unknown_requested_tier:
        checks.add("INV-18.reported_diff_requires_fallback", "INV-18", "unknown",
                   reason="requested_tier_unknown_for_reported",
                   details={"violations": unknown_requested_tier})
    else:
        checks.add("INV-18.reported_diff_requires_fallback", "INV-18", "pass")


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------


def _not_checked_items() -> list[dict[str, str]]:
    return [
        {"item": "manifest_position_order",
         "reason": "EvidenceSet stores only manifest_sha256; ascending order is used as a conservative proxy (non-ascending -> unknown)."},
        {"item": "ledger_refresh_consistency",
         "reason": "requires two ledger snapshots/refresh comparison; not part of the EvidenceSet."},
        {"item": "reason_message_fixed_text",
         "reason": "the fixed human-readable contract table is not part of the EvidenceSet; text is not checked."},
        {"item": "record_sha256_manifest_recompute",
         "reason": "INV-13 requires the original position record/artifacts and utf8-sorted-json-v1 recomputation; out of scope for this validator."},
        {"item": "evidence_ref_artifact_binding",
         "reason": "INV-12 requires the evidence store manifest/artifacts; not available from the JSON document alone."},
        {"item": "dimension_invariants_requiring_artifacts",
         "reason": "INV-4..8,10..12,19..21 need provider/file artifacts or the contract table; here only fixed-code membership (INV-19) and cross-field counters are checked."},
        {"item": "no_successful_file_filesystem_check",
         "reason": "INV-2/INV-17 delivered_evidence=null is checked structurally; proving no successful file exists needs filesystem observation."},
    ]


def _assemble(document: object, *, input_meta: dict[str, Any],
              checks: _Checks) -> dict[str, Any]:
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for item in checks.items:
        counts[item["status"]] += 1
    if counts["fail"] > 0:
        result = "fail"
    elif counts["unknown"] > 0:
        result = "unknown"
    else:
        result = "pass"
    violations = [
        {"check": item["id"], "reason": item["reason"], "details": item.get("details")}
        for item in checks.items if item["status"] == "fail"
    ]
    unknowns = [
        {"check": item["id"], "reason": item["reason"], "details": item.get("details")}
        for item in checks.items if item["status"] == "unknown"
    ]
    return {
        "tool": TOOL,
        "tool_version": TOOL_VERSION,
        "input": input_meta,
        "result": result,
        "fail_closed": result != "pass",
        "exit_code": EXIT_PASS if result == "pass" else EXIT_FAIL_CLOSED,
        "counts": {"checks": len(checks.items), **counts},
        "checks": checks.items,
        "violations": violations,
        "unknowns": unknowns,
        "not_checked": _not_checked_items(),
    }


def _apply_document_meta(document: object, meta: dict[str, Any]) -> None:
    """Copy only the known-fixed identifiers into the report.

    Review G-2: ``schema_version`` used to be echoed for any string up to 64
    chars, which could leak arbitrary text to stdout. Only the fixed schema
    version is echoed now; every other value is replaced by ``<invalid>`` and
    the structure precondition fails closed.
    """
    if not isinstance(document, dict):
        return
    meta["schema"] = (
        SCHEMA_CONST if document.get("schema") == SCHEMA_CONST
        else ("<unexpected>" if "schema" in document else "<missing>")
    )
    meta["schema_version"] = (
        SCHEMA_VERSION_CONST if document.get("schema_version") == SCHEMA_VERSION_CONST
        else "<invalid>"
    )
    evidence_set_id = document.get("evidence_set_id")
    meta["evidence_set_id"] = (
        evidence_set_id
        if isinstance(evidence_set_id, str) and _HEX48_RE.fullmatch(evidence_set_id)
        else "<invalid>"
    )


def verify_document(document: object, *,
                    input_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    """Verify one already-parsed EvidenceSet document.

    Never raises for malformed input; returns a fail-closed result instead.
    """
    meta = {"kind": "memory"} if input_meta is None else dict(input_meta)
    _apply_document_meta(document, meta)
    checks = _Checks()
    if isinstance(document, dict) and document.get("schema_version") != SCHEMA_VERSION_CONST:
        # Review G-2: explicit fail-closed check for a non-fixed schema version;
        # the raw value is never echoed (details carry fixed tokens only).
        checks.add("input.schema_version", "input", "fail",
                   reason="schema_version_not_fixed",
                   details={"expected": SCHEMA_VERSION_CONST, "reported": "<invalid>"})
    issues = _structure_issues(document)
    if issues:
        checks.add("structure.preconditions", "schema", "fail",
                   reason="evidence_schema_invalid",
                   details={"violations": issues})
        return _assemble(document, input_meta=meta, checks=checks)

    doc = document  # type: ignore[assignment]
    positions = [position for position in _as_list(doc.get("positions"))
                 if isinstance(position, dict)]
    job = _as_dict(doc.get("job"))
    summary = _as_dict(doc.get("summary"))

    _check_inv15(checks, positions, job, summary)
    _check_inv16(checks, positions, summary)
    _check_inv17(checks, positions)
    _check_inv19(checks, positions)
    _check_expected_outcome_verdict(checks, positions)
    _check_inv3_and_inv23(checks, positions)
    _check_inv22(checks, doc)
    _check_inv18(checks, positions)
    return _assemble(doc, input_meta=meta, checks=checks)


def _emit(result: dict[str, Any], *, pretty: bool) -> None:
    if pretty:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    else:
        sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")


def _input_failure(reason: str, *, raw: bytes | None = None,
                   input_meta: dict[str, Any] | None = None) -> dict[str, Any]:
    checks = _Checks()
    checks.add("input.readable", "input", "fail", reason=reason)
    meta = {"kind": "stdin"} if input_meta is None else dict(input_meta)
    if raw is not None:
        meta["sha256"] = _sha256_bytes(raw)
        meta["bytes"] = len(raw)
    return _assemble(None, input_meta=meta, checks=checks)


def read_and_verify(path: str | None, *, stdin: Any = None) -> dict[str, Any]:
    """Read (file or stdin) and verify. Never raises for I/O/JSON problems."""
    if path in (None, "-"):
        stream = stdin if stdin is not None else sys.stdin
        try:
            raw = stream.buffer.read() if hasattr(stream, "buffer") else stream.read()
        except (OSError, ValueError):
            return _input_failure("input_unreadable", input_meta={"kind": "stdin"})
        meta: dict[str, Any] = {"kind": "stdin"}
    else:
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError:
            return _input_failure("input_unreadable",
                                  input_meta={"kind": "file"})
        meta = {"kind": "file"}
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    meta["sha256"] = _sha256_bytes(raw)
    meta["bytes"] = len(raw)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _input_failure("evidence_input_invalid_json", raw=raw, input_meta=meta)
    if isinstance(document, dict):
        # Never echo arbitrary input strings; only known-fixed identifiers are
        # copied into the report, everything else is replaced by a fixed token.
        _apply_document_meta(document, meta)
    return verify_document(document, input_meta=meta)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="G-X3 semantic invariant validator for P1 acceptance EvidenceSet JSON "
                    "(read-only; never defaults to pass).")
    parser.add_argument(
        "evidence", nargs="?", default="-",
        help="path to the EvidenceSet JSON file, or - to read stdin (default: -)")
    parser.add_argument("--pretty", action="store_true", help="pretty-print the result JSON")
    args = parser.parse_args(argv)
    try:
        result = read_and_verify(args.evidence)
    except Exception:  # noqa: BLE001 - ultimate fail-closed guard: never traceback
        result = _input_failure("evidence_schema_invalid", input_meta={"kind": "internal"})
    _emit(result, pretty=args.pretty)
    return int(result.get("exit_code", EXIT_FAIL_CLOSED))


if __name__ == "__main__":
    raise SystemExit(main())
