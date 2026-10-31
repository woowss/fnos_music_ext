"""Synthetic-only tests for strict best-available quality selection."""

from __future__ import annotations

from dataclasses import replace
import unittest
from unittest import mock

from proxy import manual_save_best_quality as policy


NOW = 1050


class FixedClock:
    def __init__(self, value=NOW):
        self.value = value

    def now(self):
        return self.value


class SequenceClock:
    def __init__(self, *values):
        self.values = list(values)

    def now(self):
        if not self.values:
            raise RuntimeError("clock unavailable")
        return self.values.pop(0)


def request(**changes):
    values = {
        "request_id": "req-01",
        "attempt_id": "attempt-01",
        "account_id": "account-01",
        "song_id": 489970550,
        "requested_tier": policy.QualityTier.ULTRA_MASTER,
        "duration_seconds": 235,
        "requested_at": 1000,
    }
    values.update(changes)
    return policy.QualityRequest(**values)


def ladder(**changes):
    values = {
        "source_id": "synthetic-source-v1",
        "mapping_id": "synthetic-map",
        "mapping_version": "mapping-v1",
        "tiers": policy.QUALITY_ORDER,
        "issued_at": 1010,
        "expires_at": 1200,
    }
    values.update(changes)
    return policy.QualityLadder(**values)


def tier_evidence(req=None, unavailable=(), unknown=(), **overrides):
    req = req or request()
    unavailable = set(unavailable)
    unknown = set(unknown)
    result = []
    for tier in policy.QUALITY_ORDER:
        if tier in unavailable:
            values = {
                "request_id": req.request_id,
                "attempt_id": req.attempt_id,
                "account_id": req.account_id,
                "song_id": req.song_id,
                "tier": tier,
                "status": policy.AvailabilityStatus.UNAVAILABLE,
                "unavailable_reason": policy.UnavailableReason.NOT_OFFERED,
                "rights_status": policy.RightsStatus.VERIFIED,
                "no_trial": None,
                "complete": None,
                "duration_seconds": None,
                "source_id": "synthetic-source-v1",
                "mapping_id": "synthetic-map",
                "mapping_version": "mapping-v1",
                "issued_at": 1010,
                "expires_at": 1200,
            }
        elif tier in unknown:
            values = {
                "request_id": req.request_id,
                "attempt_id": req.attempt_id,
                "account_id": req.account_id,
                "song_id": req.song_id,
                "tier": tier,
                "status": policy.AvailabilityStatus.UNKNOWN,
                "unavailable_reason": None,
                "rights_status": policy.RightsStatus.UNKNOWN,
                "no_trial": None,
                "complete": None,
                "duration_seconds": None,
                "source_id": "synthetic-source-v1",
                "mapping_id": "synthetic-map",
                "mapping_version": "mapping-v1",
                "issued_at": 1010,
                "expires_at": 1200,
            }
        else:
            values = {
                "request_id": req.request_id,
                "attempt_id": req.attempt_id,
                "account_id": req.account_id,
                "song_id": req.song_id,
                "tier": tier,
                "status": policy.AvailabilityStatus.AVAILABLE,
                "unavailable_reason": None,
                "rights_status": policy.RightsStatus.VERIFIED,
                "no_trial": True,
                "complete": True,
                "duration_seconds": req.duration_seconds,
                "source_id": "synthetic-source-v1",
                "mapping_id": "synthetic-map",
                "mapping_version": "mapping-v1",
                "issued_at": 1010,
                "expires_at": 1200,
            }
        values.update(overrides.get(tier, {}))
        result.append(policy.TierEvidence(**values))
    return tuple(result)


def trust_report(req, source_ladder, evidence, **changes):
    fields = (
        policy._request_snapshot(req),
        policy._ladder_snapshot(source_ladder),
        tuple(policy._evidence_snapshot(item) for item in evidence),
    )
    values = {
        "source_status": policy.TrustStatus.VERIFIED,
        "mapping_status": policy.TrustStatus.VERIFIED,
        "account_status": policy.TrustStatus.VERIFIED,
        "coverage_status": policy.CoverageStatus.COMPLETE,
        "source_id": source_ladder.source_id,
        "mapping_id": source_ladder.mapping_id,
        "mapping_version": source_ladder.mapping_version,
        "request_id": req.request_id,
        "attempt_id": req.attempt_id,
        "account_id": req.account_id,
        "song_id": req.song_id,
        "tier_order": policy.QUALITY_ORDER,
        "verified_statuses": tuple((item.tier, item.status) for item in evidence),
        "evidence_sha256": policy._canonical_digest(*fields),
    }
    values.update(changes)
    return policy.TrustReport(**values)


class FakeVerifier:
    def __init__(self, *, report_changes=None, fail=False, callback=None):
        self.report_changes = report_changes or {}
        self.fail = fail
        self.callback = callback
        self.calls = 0

    def verify_snapshot(self, req, source_ladder, evidence):
        self.calls += 1
        report = trust_report(req, source_ladder, evidence, **self.report_changes)
        if self.callback is not None:
            self.callback(req, source_ladder, evidence)
        if self.fail:
            raise RuntimeError("Cookie=secret https://provider.invalid/raw response")
        return report


def evaluate(req=None, source_ladder=None, proof=None, verifier=None, clock=None):
    req = req or request()
    source_ladder = source_ladder or ladder()
    proof = proof or tier_evidence(req)
    verifier = verifier or FakeVerifier()
    return policy.evaluate_best_available(
        req, source_ladder, proof, verifier, clock or FixedClock()
    )


class BestAvailableQualityTests(unittest.TestCase):
    def assert_rejected(self, result, reason):
        self.assertIs(result.status, policy.EvaluationStatus.REJECTED)
        self.assertEqual(result.reason, reason)
        self.assertIsNone(result.selected_tier)
        self.assertFalse(result.fallback)
        self.assertFalse(result.production_ready)
        self.assertFalse(policy.PRODUCTION_READY)

    def test_selects_preferred_master_when_verified_available(self):
        result = evaluate()
        self.assertIs(result.status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.requested_tier, policy.QualityTier.ULTRA_MASTER)
        self.assertIs(result.selected_tier, policy.QualityTier.ULTRA_MASTER)
        self.assertFalse(result.fallback)
        self.assertEqual(result.reason, "preferred_tier_available")
        self.assertFalse(result.production_ready)

    def test_falls_back_only_to_highest_explicitly_available_tier(self):
        req = request()
        unavailable = (policy.QualityTier.ULTRA_MASTER,)
        proof = tier_evidence(req, unavailable=unavailable)
        result = evaluate(req, proof=proof)
        self.assertIs(result.status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.selected_tier, policy.QualityTier.HI_RES)
        self.assertTrue(result.fallback)
        self.assertEqual(result.reason, "highest_available_fallback")

    def test_fallback_skips_only_explicit_not_offered_higher_tiers(self):
        req = request()
        unavailable = (
            policy.QualityTier.ULTRA_MASTER,
            policy.QualityTier.HI_RES,
        )
        proof = tier_evidence(req, unavailable=unavailable)
        result = evaluate(req, proof=proof)
        self.assertIs(result.selected_tier, policy.QualityTier.LOSSLESS)
        self.assertTrue(result.fallback)

    def test_no_available_tier_is_not_reported_as_success(self):
        req = request()
        proof = tier_evidence(req, unavailable=policy.QUALITY_ORDER)
        result = evaluate(req, proof=proof)
        self.assert_rejected(result, "no_available_tier")

    def test_unknown_tier_status_fails_closed_before_any_fallback(self):
        req = request()
        proof = tier_evidence(req, unknown=(policy.QualityTier.ULTRA_MASTER,))
        verifier = FakeVerifier()
        result = evaluate(req, proof=proof, verifier=verifier)
        self.assert_rejected(result, "availability_unknown")
        self.assertEqual(verifier.calls, 1)

    def test_unknown_lower_availability_does_not_block_a_verified_higher_candidate(self):
        req = request()
        proof = tier_evidence(req, unknown=policy.QUALITY_ORDER[1:])
        result = evaluate(req, proof=proof)
        self.assertIs(result.status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.selected_tier, policy.QualityTier.ULTRA_MASTER)
        self.assertFalse(result.fallback)

        req = request()
        proof = tier_evidence(
            req,
            unavailable=(policy.QualityTier.ULTRA_MASTER,),
            unknown=(
                policy.QualityTier.LOSSLESS,
                policy.QualityTier.HIGH,
                policy.QualityTier.STANDARD,
            ),
        )
        result = evaluate(req, proof=proof)
        self.assertIs(result.status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.selected_tier, policy.QualityTier.HI_RES)
        self.assertTrue(result.fallback)

        proof = list(tier_evidence(req, unavailable=(policy.QualityTier.ULTRA_MASTER,)))
        proof[2] = replace(proof[2], rights_status=policy.RightsStatus.UNKNOWN)
        proof[3] = replace(proof[3], no_trial=False)
        result = evaluate(req, proof=proof)
        self.assertIs(result.status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.selected_tier, policy.QualityTier.HI_RES)
        self.assertTrue(result.fallback)

    def test_unknown_higher_availability_still_blocks_a_lower_candidate(self):
        req = request()
        proof = tier_evidence(
            req,
            unknown=(policy.QualityTier.ULTRA_MASTER,),
        )
        result = evaluate(req, proof=proof)
        self.assert_rejected(result, "availability_unknown")

    def test_unknown_lower_evidence_still_requires_valid_binding_time_and_shape(self):
        req = request()
        proof = list(tier_evidence(req, unknown=policy.QUALITY_ORDER[1:]))
        proof[1] = replace(proof[1], account_id="different-account")
        self.assert_rejected(evaluate(req, proof=proof), "account_mismatch")

        proof = list(tier_evidence(req, unknown=policy.QUALITY_ORDER[1:]))
        proof[1] = replace(proof[1], expires_at=NOW)
        self.assert_rejected(evaluate(req, proof=proof), "evidence_expired")

        proof = list(tier_evidence(req, unknown=policy.QUALITY_ORDER[1:]))
        proof[1] = replace(proof[1], no_trial=True)
        self.assert_rejected(evaluate(req, proof=proof), "contradictory_tier_evidence")

    def test_account_entitlement_is_a_fallback_signal_only_with_bound_verified_evidence(self):
        req = request()
        proof = list(tier_evidence(req, unavailable=(policy.QualityTier.ULTRA_MASTER,)))
        proof[0] = replace(
            proof[0],
            unavailable_reason=policy.UnavailableReason.ACCOUNT_NOT_ENTITLED,
            rights_status=policy.RightsStatus.NOT_ENTITLED,
        )
        result = evaluate(req, proof=proof)
        self.assertIs(result.status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.selected_tier, policy.QualityTier.HI_RES)
        self.assertTrue(result.fallback)

        other_account = list(proof)
        other_account[0] = replace(other_account[0], account_id="account-02")
        self.assert_rejected(evaluate(req, proof=other_account), "account_mismatch")

        for unavailable_reason, expected in (
            (policy.UnavailableReason.SOURCE_TIMEOUT, "source_timeout"),
            (policy.UnavailableReason.UNKNOWN, "availability_unknown"),
        ):
            invalid_proof = list(tier_evidence(req, unavailable=(policy.QualityTier.ULTRA_MASTER,)))
            invalid_proof[0] = replace(invalid_proof[0], unavailable_reason=unavailable_reason)
            with self.subTest(reason=unavailable_reason):
                self.assert_rejected(evaluate(req, proof=invalid_proof), expected)

        unverified_entitlement = list(tier_evidence(req, unavailable=(policy.QualityTier.ULTRA_MASTER,)))
        unverified_entitlement[0] = replace(
            unverified_entitlement[0],
            unavailable_reason=policy.UnavailableReason.ACCOUNT_NOT_ENTITLED,
        )
        self.assert_rejected(evaluate(req, proof=unverified_entitlement), "account_unavailability_unverified")

        unknown_entitlement = list(proof)
        unknown_entitlement[0] = replace(unknown_entitlement[0], rights_status=policy.RightsStatus.UNKNOWN)
        self.assert_rejected(evaluate(req, proof=unknown_entitlement), "rights_unknown")

        forged_report = FakeVerifier(report_changes={"account_id": "account-02"})
        self.assert_rejected(evaluate(req, proof=proof, verifier=forged_report), "verifier_binding_mismatch")

        unauthenticated = FakeVerifier(report_changes={"source_status": policy.TrustStatus.UNKNOWN})
        self.assert_rejected(evaluate(req, proof=proof, verifier=unauthenticated), "source_untrusted")

        account_unverified = FakeVerifier(report_changes={"account_status": policy.TrustStatus.UNKNOWN})
        self.assert_rejected(evaluate(req, proof=proof, verifier=account_unverified), "account_unverified")

        expired = list(proof)
        expired[0] = replace(expired[0], expires_at=NOW)
        self.assert_rejected(evaluate(req, proof=expired), "evidence_expired")

    def test_missing_tier_or_incomplete_ladder_is_rejected(self):
        req = request()
        self.assert_rejected(evaluate(req, proof=tier_evidence(req)[:-1]), "tier_coverage_incomplete")
        incomplete = ladder(tiers=policy.QUALITY_ORDER[:-1])
        self.assert_rejected(evaluate(req, source_ladder=incomplete), "ladder_incomplete_or_unordered")

    def test_wrong_order_duplicate_tier_or_mapping_version_is_rejected(self):
        req = request()
        reversed_ladder = ladder(tiers=tuple(reversed(policy.QUALITY_ORDER)))
        self.assert_rejected(evaluate(req, source_ladder=reversed_ladder), "ladder_incomplete_or_unordered")
        duplicate_ladder = ladder(tiers=(policy.QUALITY_ORDER[0],) * len(policy.QUALITY_ORDER))
        self.assert_rejected(evaluate(req, source_ladder=duplicate_ladder), "ladder_incomplete_or_unordered")
        proof = list(tier_evidence(req))
        proof[0] = replace(proof[0], mapping_version="mapping-v2")
        self.assert_rejected(evaluate(req, proof=proof), "mapping_version_mismatch")

    def test_untrusted_source_mapping_or_partial_verifier_coverage_rejects(self):
        req = request()
        for report_changes, reason in (
            ({"source_status": policy.TrustStatus.UNKNOWN}, "source_untrusted"),
            ({"mapping_status": policy.TrustStatus.REJECTED}, "mapping_unverified"),
            ({"coverage_status": policy.CoverageStatus.INCOMPLETE}, "tier_coverage_incomplete"),
        ):
            with self.subTest(reason=reason):
                result = evaluate(req, verifier=FakeVerifier(report_changes=report_changes))
                self.assert_rejected(result, reason)

    def test_verifier_must_bind_report_to_request_attempt_song_order_and_evidence(self):
        req = request()
        bad_reports = (
            {"request_id": "other-request"},
            {"attempt_id": "old-attempt"},
            {"account_id": "other-account"},
            {"song_id": req.song_id + 1},
            {"tier_order": tuple(reversed(policy.QUALITY_ORDER))},
            {"verified_statuses": ()},
            {"evidence_sha256": "0" * 64},
            {"mapping_version": "other-version"},
        )
        for changes in bad_reports:
            with self.subTest(changes=changes):
                result = evaluate(req, verifier=FakeVerifier(report_changes=changes))
                self.assert_rejected(result, "mapping_version_mismatch" if "mapping_version" in changes else "verifier_binding_mismatch" if any(k in changes for k in ("request_id", "attempt_id", "account_id", "song_id", "evidence_sha256")) else "verifier_ladder_mismatch" if "tier_order" in changes else "verifier_status_mismatch")

    def test_rights_trial_incomplete_and_duration_unknown_or_conflicting_reject(self):
        req = request()
        cases = (
            ({"rights_status": policy.RightsStatus.UNKNOWN}, "rights_unknown"),
            ({"rights_status": policy.RightsStatus.DENIED}, "rights_denied"),
            ({"no_trial": False}, "trial_or_trial_status_unknown"),
            ({"no_trial": None}, "trial_or_trial_status_unknown"),
            ({"complete": False}, "audio_incomplete_or_unknown"),
            ({"complete": None}, "audio_incomplete_or_unknown"),
            ({"duration_seconds": 100}, "duration_mismatch"),
        )
        for changed, reason in cases:
            with self.subTest(changed=changed):
                proof = list(tier_evidence(req))
                proof[0] = replace(proof[0], **changed)
                self.assert_rejected(evaluate(req, proof=proof), reason)

    def test_contradictory_available_or_unavailable_fields_reject(self):
        req = request()
        proof = list(tier_evidence(req, unavailable=(policy.QualityTier.ULTRA_MASTER,)))
        proof[0] = replace(proof[0], no_trial=True)
        self.assert_rejected(evaluate(req, proof=proof), "contradictory_tier_evidence")
        proof = list(tier_evidence(req))
        proof[0] = replace(proof[0], unavailable_reason=policy.UnavailableReason.NOT_OFFERED)
        self.assert_rejected(evaluate(req, proof=proof), "contradictory_tier_evidence")

    def test_song_request_attempt_and_source_mismatches_reject(self):
        req = request()
        mismatch_cases = (
            ("request_id", "other-request", "request_mismatch"),
            ("attempt_id", "old-attempt", "attempt_mismatch"),
            ("song_id", req.song_id + 1, "song_mismatch"),
            ("source_id", "other-source", "source_mismatch"),
        )
        for field, value, reason in mismatch_cases:
            with self.subTest(field=field):
                proof = list(tier_evidence(req))
                proof[0] = replace(proof[0], **{field: value})
                self.assert_rejected(evaluate(req, proof=proof), reason)

    def test_expired_stale_and_future_evidence_reject(self):
        req = request()
        for changes, reason in (
            ({"expires_at": NOW}, "evidence_expired"),
            ({"issued_at": NOW + 1}, "invalid_evidence_time"),
        ):
            proof = list(tier_evidence(req))
            proof[0] = replace(proof[0], **changes)
            with self.subTest(changes=changes):
                self.assert_rejected(evaluate(req, proof=proof), reason)
        stale_request = request(requested_at=700)
        proof = list(tier_evidence(stale_request))
        proof[0] = replace(
            proof[0], issued_at=NOW - policy.MAX_EVIDENCE_AGE_SECONDS - 1
        )
        self.assert_rejected(evaluate(stale_request, proof=proof), "evidence_stale")

    def test_verification_reads_clock_before_and_after_and_rejects_expiry_or_rollback(self):
        req = request()
        proof = list(tier_evidence(req))
        proof[0] = replace(proof[0], expires_at=1060)
        self.assert_rejected(
            evaluate(req, proof=proof, clock=SequenceClock(1050, 1060)),
            "evidence_expired",
        )
        self.assert_rejected(
            evaluate(req, clock=SequenceClock(1050, 1049)),
            "clock_moved_backwards",
        )
        self.assert_rejected(
            evaluate(req, clock=SequenceClock(1050)),
            "clock_unavailable",
        )

    def test_clock_and_verifier_exceptions_are_fixed_and_redacted(self):
        req = request()
        result = evaluate(req, verifier=FakeVerifier(fail=True))
        self.assert_rejected(result, "trust_verification_failed")
        self.assertNotIn("secret", result.reason)
        self.assertNotIn("provider.invalid", result.reason)
        self.assert_rejected(evaluate(req, clock=SequenceClock("bad")), "invalid_clock")

    def test_verifier_mutations_of_inputs_and_original_evidence_fail_closed(self):
        req = request()
        proof = list(tier_evidence(req))

        def mutate_clone(clone_req, clone_ladder, clone_evidence):
            object.__setattr__(clone_evidence[0], "song_id", clone_req.song_id + 1)

        result = evaluate(req, proof=proof, verifier=FakeVerifier(callback=mutate_clone))
        self.assert_rejected(result, "verifier_input_changed")

        proof = list(tier_evidence(req))

        def mutate_original(_clone_req, _clone_ladder, _clone_evidence):
            object.__setattr__(proof[1], "song_id", 999)

        result = evaluate(req, proof=proof, verifier=FakeVerifier(callback=mutate_original))
        self.assert_rejected(result, "evidence_changed_during_verification")

        proof = list(tier_evidence(req))

        def replace_original(_clone_req, _clone_ladder, _clone_evidence):
            proof.append(proof[0])

        result = evaluate(req, proof=proof, verifier=FakeVerifier(callback=replace_original))
        self.assert_rejected(result, "evidence_structure_changed_during_verification")

    def test_deleted_request_or_evidence_fields_are_sanitized_before_and_during_verification(self):
        req = request()
        object.__delattr__(req, "requested_tier")
        result = evaluate(req)
        self.assertIs(result.status, policy.EvaluationStatus.REJECTED)
        self.assertEqual(result.reason, "evaluation_failed")
        self.assertIsNone(result.requested_tier)
        self.assertFalse(result.production_ready)

        req = request()
        proof = list(tier_evidence(req))

        def delete_clone_field(clone_req, _clone_ladder, _clone_evidence):
            object.__delattr__(clone_req, "requested_tier")

        result = evaluate(req, proof=proof, verifier=FakeVerifier(callback=delete_clone_field))
        self.assert_rejected(result, "evaluation_failed")

        req = request()
        proof = list(tier_evidence(req))

        def delete_original_field(_clone_req, _clone_ladder, _clone_evidence):
            object.__delattr__(req, "requested_tier")

        result = evaluate(req, proof=proof, verifier=FakeVerifier(callback=delete_original_field))
        self.assert_rejected(result, "evaluation_failed")

        req = request()
        proof = list(tier_evidence(req))
        object.__delattr__(proof[0], "account_id")
        result = evaluate(req, proof=proof)
        self.assert_rejected(result, "evaluation_failed")

    def test_verifier_cannot_use_provider_labels_or_codec_as_policy_inputs(self):
        result = evaluate()
        self.assertIs(result.selected_tier, policy.QualityTier.ULTRA_MASTER)
        self.assertFalse(hasattr(result, "codec"))
        self.assertFalse(hasattr(result, "provider_label"))
        self.assertFalse(result.production_ready)

    def test_playlist_detects_cross_song_field_and_structure_mutation(self):
        req_a = request(request_id="req-a", attempt_id="attempt-a", song_id=10)
        req_b = request(request_id="req-b", attempt_id="attempt-b", song_id=20)
        row_a = (req_a, ladder(), tier_evidence(req_a))
        row_b = (req_b, ladder(), tier_evidence(req_b))
        items = [row_a, row_b]

        def tamper_later_song(_copy_req, _copy_ladder, _copy_evidence):
            object.__setattr__(req_b, "song_id", 999)

        result = policy.evaluate_playlist(items, FakeVerifier(callback=tamper_later_song), FixedClock())
        self.assertFalse(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "playlist_changed_during_verification")
        self.assertTrue(all(item.status is policy.EvaluationStatus.REJECTED for item in result.items))

        items = [row_a, row_b]

        def replace_row(_copy_req, _copy_ladder, _copy_evidence):
            items[1] = tuple([*items[1]])

        result = policy.evaluate_playlist(items, FakeVerifier(callback=replace_row), FixedClock())
        self.assertFalse(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "playlist_changed_during_verification")

    def test_playlist_rechecks_all_selected_evidence_at_final_time(self):
        req_a = request(request_id="req-a", attempt_id="attempt-a", song_id=10)
        req_b = request(request_id="req-b", attempt_id="attempt-b", song_id=20)
        proof_a = list(tier_evidence(req_a))
        proof_a[-1] = replace(proof_a[-1], expires_at=1054)
        rows = [
            (req_a, ladder(expires_at=1054), proof_a),
            (req_b, ladder(), tier_evidence(req_b)),
        ]
        verifier = FakeVerifier()
        result = policy.evaluate_playlist(
            rows, verifier, SequenceClock(1050, 1051, 1052, 1053, 1054)
        )
        self.assertTrue(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "playlist_evaluated")
        self.assertIs(result.items[0].status, policy.EvaluationStatus.REJECTED)
        self.assertEqual(result.items[0].reason, "ladder_expired")
        self.assertIs(result.items[1].status, policy.EvaluationStatus.SELECTED)
        self.assertEqual(verifier.calls, 2)

        proof_a = list(tier_evidence(req_a))
        proof_a[-1] = replace(proof_a[-1], expires_at=1054)
        rows[0] = (req_a, ladder(), proof_a)
        result = policy.evaluate_playlist(
            rows, FakeVerifier(), SequenceClock(1050, 1051, 1052, 1053, 1054)
        )
        self.assertEqual(result.items[0].reason, "evidence_expired")
        self.assertFalse(result.all_selected)

    def test_playlist_final_recheck_enforces_request_and_age_limits(self):
        stale_req = request(
            request_id="req-a", attempt_id="attempt-a", song_id=10, requested_at=1
        )
        later_req = request(
            request_id="req-b", attempt_id="attempt-b", song_id=20, requested_at=2000
        )
        rows = [
            (stale_req, ladder(expires_at=6000), tier_evidence(stale_req)),
            (later_req, ladder(issued_at=2010, expires_at=6000), tier_evidence(later_req)),
        ]
        final = stale_req.requested_at + policy.MAX_REQUEST_AGE_SECONDS + 1
        result = policy.evaluate_playlist(
            rows, FakeVerifier(),
            SequenceClock(1050, 1051, 1052, final),
        )
        self.assertTrue(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.items[0].reason, "request_expired")

        boundary_req = request(
            request_id="req-boundary", attempt_id="attempt-boundary", song_id=30,
            requested_at=2800,
        )
        boundary_proof = list(tier_evidence(boundary_req))
        boundary_proof = [
            replace(item, issued_at=6390, expires_at=7000)
            for item in boundary_proof
        ]
        rows = [
            (boundary_req, ladder(issued_at=6390, expires_at=7000), boundary_proof)
        ]
        boundary_now = boundary_req.requested_at + policy.MAX_REQUEST_AGE_SECONDS
        result = policy.evaluate_playlist(
            rows, FakeVerifier(),
            SequenceClock(boundary_now, boundary_now, boundary_now),
        )
        self.assertTrue(result.all_selected)

        req_a = request(
            request_id="req-a", attempt_id="attempt-a", song_id=10, requested_at=700
        )
        req_b = request(request_id="req-b", attempt_id="attempt-b", song_id=20, requested_at=700)
        old_proof = [
            replace(item, issued_at=1039, expires_at=2000)
            for item in tier_evidence(req_a)
        ]
        fresh_proof = [
            replace(item, issued_at=1039, expires_at=2000)
            for item in tier_evidence(req_b)
        ]
        rows = [
            (req_a, ladder(issued_at=1040, expires_at=2000), old_proof),
            (req_b, ladder(issued_at=1040, expires_at=2000), fresh_proof),
        ]
        boundary = 1039 + policy.MAX_EVIDENCE_AGE_SECONDS
        result = policy.evaluate_playlist(
            rows, FakeVerifier(),
            SequenceClock(1040, 1040, 1040, 1040, boundary),
        )
        self.assertTrue(result.all_selected)
        result = policy.evaluate_playlist(
            rows, FakeVerifier(),
            SequenceClock(1040, 1040, 1040, 1040, boundary + 1),
        )
        self.assertEqual(result.items[0].reason, "evidence_stale")
        self.assertFalse(result.all_selected)

        rows[0] = (
            req_a,
            ladder(issued_at=1010, expires_at=2000),
            tier_evidence(req_a),
        )
        rows[1] = (
            req_b,
            ladder(expires_at=2000),
            tier_evidence(req_b),
        )
        result = policy.evaluate_playlist(
            rows, FakeVerifier(), SequenceClock(1050, 1050, 1050, 1050, 1311)
        )
        self.assertEqual(result.items[0].reason, "ladder_stale")
        self.assertFalse(result.all_selected)

    def test_playlist_detects_clock_rollback_between_songs(self):
        req_a = request(request_id="req-a", attempt_id="attempt-a", song_id=10)
        req_b = request(request_id="req-b", attempt_id="attempt-b", song_id=20)
        rows = [
            (req_a, ladder(), tier_evidence(req_a)),
            (req_b, ladder(), tier_evidence(req_b)),
        ]
        result = policy.evaluate_playlist(
            rows, FakeVerifier(), SequenceClock(1050, 1051, 1050, 1052)
        )
        self.assertFalse(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "clock_moved_backwards")
        self.assertEqual(len(result.items), 2)
        self.assertTrue(all(item.status is policy.EvaluationStatus.REJECTED for item in result.items))
        self.assertTrue(all(item.reason == "clock_moved_backwards" for item in result.items))

    def test_playlist_final_clock_failure_or_rollback_clears_all_selections(self):
        req_a = request(request_id="req-a", attempt_id="attempt-a", song_id=10)
        req_b = request(request_id="req-b", attempt_id="attempt-b", song_id=20)
        rows = [
            (req_a, ladder(), tier_evidence(req_a)),
            (req_b, ladder(), tier_evidence(req_b)),
        ]
        for tail, reason in (
            (("bad",), "invalid_clock"),
            ((), "clock_unavailable"),
            ((1052,), "clock_moved_backwards"),
        ):
            with self.subTest(reason=reason):
                result = policy.evaluate_playlist(
                    rows, FakeVerifier(), SequenceClock(1050, 1051, 1052, 1053, *tail)
                )
                self.assertFalse(result.completed)
                self.assertFalse(result.all_selected)
                self.assertEqual(result.reason, reason)
                self.assertEqual(len(result.items), 2)
                self.assertTrue(all(item.status is policy.EvaluationStatus.REJECTED for item in result.items))
                self.assertTrue(all(item.reason == reason for item in result.items))

    def test_playlist_checks_input_and_internal_snapshots_after_final_clock_callback(self):
        req_a = request(request_id="req-a", attempt_id="attempt-a", song_id=10)
        req_b = request(request_id="req-b", attempt_id="attempt-b", song_id=20)
        rows = [
            (req_a, ladder(), tier_evidence(req_a)),
            (req_b, ladder(), tier_evidence(req_b)),
        ]

        class MutatingFinalClock:
            def __init__(self, mutation):
                self.calls = 0
                self.mutation = mutation

            def now(self):
                self.calls += 1
                if self.calls == 5:
                    self.mutation()
                return 1050 + self.calls - 1

        result = policy.evaluate_playlist(
            rows, FakeVerifier(),
            MutatingFinalClock(lambda: object.__setattr__(req_b, "song_id", 999)),
        )
        self.assertFalse(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "playlist_changed_during_verification")
        self.assertTrue(all(item.status is policy.EvaluationStatus.REJECTED for item in result.items))

        req_a = request(request_id="req-c", attempt_id="attempt-c", song_id=30)
        req_b = request(request_id="req-d", attempt_id="attempt-d", song_id=40)
        rows = [
            (req_a, ladder(), tier_evidence(req_a)),
            (req_b, ladder(), tier_evidence(req_b)),
        ]
        captured = []
        original_snapshot = policy._snapshot_playlist_item

        def recording_snapshot(item):
            snapshot = original_snapshot(item)
            captured.append(snapshot)
            return snapshot

        with mock.patch.object(policy, "_snapshot_playlist_item", recording_snapshot):
            result = policy.evaluate_playlist(
                rows, FakeVerifier(),
                MutatingFinalClock(lambda: object.__setattr__(captured[0].request_copy, "song_id", 999)),
            )
        self.assertFalse(result.completed)
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "playlist_changed_during_verification")
        self.assertTrue(all(item.status is policy.EvaluationStatus.REJECTED for item in result.items))

    def test_playlist_preserves_per_song_failure_without_claiming_full_success(self):
        good = request(request_id="req-good", attempt_id="attempt-good", song_id=1)
        bad = request(request_id="req-bad", attempt_id="attempt-bad", song_id=2)
        bad_proof = list(tier_evidence(bad))
        bad_proof[0] = replace(bad_proof[0], status=policy.AvailabilityStatus.UNKNOWN)
        result = policy.evaluate_playlist(
            [(good, ladder(), tier_evidence(good)), (bad, ladder(), bad_proof)],
            FakeVerifier(),
            FixedClock(),
        )
        self.assertTrue(result.completed)
        self.assertFalse(result.all_selected)
        self.assertIs(result.items[0].status, policy.EvaluationStatus.SELECTED)
        self.assertIs(result.items[1].status, policy.EvaluationStatus.REJECTED)

    def test_playlist_rejects_duplicate_attempts_and_enforces_500_item_limit(self):
        req = request()
        duplicate_rows = [
            (req, ladder(), tier_evidence(req)),
            (req, ladder(), tier_evidence(req)),
        ]
        result = policy.evaluate_playlist(duplicate_rows, FakeVerifier(), FixedClock())
        self.assertTrue(result.completed)
        self.assertFalse(result.all_selected)
        self.assertTrue(all(item.reason == "duplicate_playlist_attempt" for item in result.items))

        rows = []
        for index in range(policy.MAX_PLAYLIST_TRACKS):
            current = request(
                request_id=f"req-{index}",
                attempt_id=f"attempt-{index}",
                song_id=index + 1,
            )
            rows.append((current, ladder(), tier_evidence(current)))
        result = policy.evaluate_playlist(rows, FakeVerifier(), FixedClock())
        self.assertTrue(result.completed)
        self.assertEqual(len(result.items), 500)
        self.assertTrue(result.all_selected)
        too_many = policy.evaluate_playlist(rows + [rows[0]], FakeVerifier(), FixedClock())
        self.assertFalse(too_many.completed)
        self.assertEqual(too_many.reason, "playlist_too_large")
        self.assertFalse(too_many.all_selected)

    def test_malformed_playlist_shape_rejects_without_verifier(self):
        verifier = FakeVerifier()
        result = policy.evaluate_playlist([(request(), ladder())], verifier, FixedClock())
        self.assertFalse(result.all_selected)
        self.assertEqual(result.reason, "playlist_item_invalid")
        self.assertEqual(verifier.calls, 0)


if __name__ == "__main__":
    unittest.main()
