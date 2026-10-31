"""Synthetic-only tests for the strict master-quality evidence policy."""

from __future__ import annotations

from dataclasses import replace
from threading import Thread
import unittest

from proxy import manual_save_quality_policy as policy


NOW = 1050


class FixedClock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


class SequenceClock:
    def __init__(self, values):
        self.values = list(values)

    def now(self):
        if not self.values:
            raise RuntimeError("clock unavailable")
        return self.values.pop(0)


class FakeVerifier:
    def __init__(self, report=None, fail=False, after_call=None):
        self.report = report if report is not None else trust_report()
        self.fail = fail
        self.after_call = after_call
        self.calls = []

    def verify_quality_evidence(self, request, evidence):
        self.calls.append((request, evidence))
        if self.fail:
            raise RuntimeError("Cookie=secret https://provider.invalid/raw-response")
        if self.after_call is not None:
            self.after_call()
        return self.report


def request(**changes):
    values = {
        "request_id": "req-01",
        "attempt_id": "attempt-01",
        "track_id": 489970550,
        "requested_tier": policy.QualityTier.ULTRA_MASTER,
        "display_duration_seconds": 235,
        "requested_at": 1000,
    }
    values.update(changes)
    return policy.QualityRequest(**values)


def evidence(for_request=None, **changes):
    selected = for_request or request()
    values = {
        "request_id": selected.request_id,
        "attempt_id": selected.attempt_id,
        "track_id": selected.track_id,
        "confirmed": True,
        "actual_tier": policy.QualityTier.ULTRA_MASTER,
        "rights_status": policy.RightsStatus.VERIFIED,
        "no_trial": True,
        "complete": True,
        "duration_seconds": 235,
        "mapping_id": "netease-master-map-v1",
        "issued_at": 1001,
        "expires_at": 1200,
    }
    values.update(changes)
    return policy.QualityEvidence(**values)


def trust_report(**changes):
    values = {
        "source_status": policy.TrustStatus.VERIFIED,
        "mapping_status": policy.TrustStatus.VERIFIED,
        "mapping_id": "netease-master-map-v1",
    }
    values.update(changes)
    return policy.TrustReport(**values)


class StrictMasterQualityPolicyTests(unittest.TestCase):
    def assert_rejected(self, result, reason):
        self.assertIs(result.status, policy.EvaluationStatus.REJECTED)
        self.assertEqual(result.reason, reason)
        self.assertIsNone(result.confirmed_tier)
        self.assertFalse(result.production_ready)
        self.assertFalse(policy.PRODUCTION_READY)

    def evaluate(self, req=None, proof=None, verifier=None, now=NOW, clock=None):
        return policy.evaluate_quality(
            req if req is not None else request(),
            proof if proof is not None else evidence(),
            verifier if verifier is not None else FakeVerifier(),
            clock if clock is not None else FixedClock(now),
        )

    def test_fully_verified_synthetic_evidence_matches_but_never_production_ready(self):
        verifier = FakeVerifier()
        result = self.evaluate(verifier=verifier)
        self.assertIs(result.status, policy.EvaluationStatus.MATCHED)
        self.assertEqual(result.reason, "synthetic_evidence_matches_request")
        self.assertIs(result.confirmed_tier, policy.QualityTier.ULTRA_MASTER)
        self.assertFalse(result.production_ready)
        self.assertFalse(policy.PRODUCTION_READY)
        self.assertEqual(len(verifier.calls), 1)

    def test_missing_and_wrong_shape_evidence_fail_closed(self):
        self.assert_rejected(
            policy.evaluate_quality(request(), None, FakeVerifier(), FixedClock(NOW)),
            "evidence_missing",
        )
        self.assert_rejected(
            policy.evaluate_quality(request(), {"Cookie": "secret"}, FakeVerifier(), FixedClock(NOW)),
            "invalid_evidence",
        )

    def test_deleted_request_or_evidence_fields_fail_closed_before_verification(self):
        malformed_request = request()
        object.__delattr__(malformed_request, "track_id")
        self.assert_rejected(
            policy.evaluate_quality(
                malformed_request,
                evidence(),
                FakeVerifier(),
                FixedClock(NOW),
            ),
            "evaluation_failed",
        )

        malformed_evidence = evidence()
        object.__delattr__(malformed_evidence, "mapping_id")
        self.assert_rejected(
            policy.evaluate_quality(
                request(),
                malformed_evidence,
                FakeVerifier(),
                FixedClock(NOW),
            ),
            "evaluation_failed",
        )

    def test_verifier_deleting_request_or_mapping_fields_is_sanitized(self):
        req = request()
        proof = evidence(req)

        class DeletingRequestVerifier:
            def verify_quality_evidence(self, request, evidence):
                object.__delattr__(request, "track_id")
                return trust_report()

        self.assert_rejected(
            policy.evaluate_quality(req, proof, DeletingRequestVerifier(), FixedClock(NOW)),
            "evaluation_failed",
        )

        req = request()
        proof = evidence(req)

        class DeletingMappingVerifier:
            def verify_quality_evidence(self, request, evidence):
                object.__delattr__(evidence, "mapping_id")
                return trust_report()

        result = policy.evaluate_quality(req, proof, DeletingMappingVerifier(), FixedClock(NOW))
        self.assert_rejected(result, "evaluation_failed")
        self.assertNotIn("AttributeError", repr(result))
        self.assertNotIn("mapping_id", repr(result))
    def test_request_and_track_binding_are_mandatory(self):
        req = request()
        self.assert_rejected(
            self.evaluate(proof=evidence(request_id="req-other")),
            "request_mismatch",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(track_id=489970551)),
            "track_mismatch",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(attempt_id="attempt-other")),
            "attempt_mismatch",
        )

    def test_retry_cannot_reuse_previous_attempt_evidence(self):
        retry = request(attempt_id="attempt-02")
        old_proof = evidence(request())
        self.assert_rejected(
            policy.evaluate_quality(retry, old_proof, FakeVerifier(), FixedClock(NOW)),
            "attempt_mismatch",
        )

    def test_missing_or_invalid_verifier_never_trusts_claimed_fields(self):
        self.assert_rejected(
            policy.evaluate_quality(request(), evidence(), None, FixedClock(NOW)),
            "verifier_unavailable",
        )

        class BrokenVerifier:
            @property
            def verify_quality_evidence(self):
                raise RuntimeError("secret")

        self.assert_rejected(
            policy.evaluate_quality(request(), evidence(), BrokenVerifier(), FixedClock(NOW)),
            "verifier_unavailable",
        )

    def test_source_and_mapping_must_both_be_independently_verified(self):
        self.assert_rejected(
            self.evaluate(verifier=FakeVerifier(trust_report(
                source_status=policy.TrustStatus.UNKNOWN,
            ))),
            "source_untrusted",
        )
        self.assert_rejected(
            self.evaluate(verifier=FakeVerifier(trust_report(
                mapping_status=policy.TrustStatus.UNKNOWN,
            ))),
            "mapping_unverified",
        )
        self.assert_rejected(
            self.evaluate(verifier=FakeVerifier(trust_report(
                mapping_id="different-map",
            ))),
            "mapping_mismatch",
        )
        self.assert_rejected(
            self.evaluate(verifier=FakeVerifier(report={"source": "trusted"})),
            "trust_report_invalid",
        )

    def test_expired_stale_future_and_pre_request_evidence_fail_closed(self):
        self.assert_rejected(
            self.evaluate(proof=evidence(expires_at=1050)),
            "evidence_expired",
        )
        stale = evidence(issued_at=1001, expires_at=5000)
        self.assert_rejected(
            self.evaluate(proof=stale, now=1302),
            "evidence_stale",
        )
        self.assert_rejected(
            self.evaluate(now=999),
            "clock_before_request",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(issued_at=1051)),
            "invalid_evidence_time",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(issued_at=999)),
            "invalid_evidence_time",
        )
        self.assert_rejected(
            self.evaluate(now=5001),
            "request_expired",
        )

    def test_evidence_expiring_while_verifier_runs_is_rejected(self):
        clock = FixedClock(1050)
        verifier = FakeVerifier(
            after_call=lambda: setattr(clock, "value", 1200),
        )
        result = policy.evaluate_quality(
            request(),
            evidence(expires_at=1200),
            verifier,
            clock,
        )
        self.assert_rejected(result, "evidence_expired")
        self.assertEqual(len(verifier.calls), 1)

    def test_verifier_crossing_freshness_and_expiry_boundaries_is_rejected(self):
        stale = policy.evaluate_quality(
            request(),
            evidence(issued_at=1000, expires_at=5000),
            FakeVerifier(),
            SequenceClock([1299, 1301]),
        )
        self.assert_rejected(stale, "evidence_stale")

        expired_at_boundary = policy.evaluate_quality(
            request(),
            evidence(issued_at=1001, expires_at=1100),
            FakeVerifier(),
            SequenceClock([1099, 1100]),
        )
        self.assert_rejected(expired_at_boundary, "evidence_expired")

    def test_clock_moving_backwards_during_verification_fails_closed(self):
        result = policy.evaluate_quality(
            request(),
            evidence(),
            FakeVerifier(),
            SequenceClock([1050, 1049]),
        )
        self.assert_rejected(result, "clock_moved_backwards")

    def test_clock_errors_and_invalid_values_fail_closed(self):
        broken_clock = FixedClock(NOW)
        object.__delattr__(broken_clock, "value")
        self.assert_rejected(
            policy.evaluate_quality(request(), evidence(), FakeVerifier(), broken_clock),
            "clock_unavailable",
        )
        self.assert_rejected(
            policy.evaluate_quality(request(), evidence(), FakeVerifier(), SequenceClock([])),
            "clock_unavailable",
        )
        self.assert_rejected(
            policy.evaluate_quality(request(), evidence(), FakeVerifier(), SequenceClock([1050])),
            "clock_unavailable",
        )
        self.assert_rejected(
            policy.evaluate_quality(request(), evidence(), FakeVerifier(), SequenceClock([1050, True])),
            "invalid_clock",
        )

    def test_verifier_cannot_mutate_request_or_evidence_under_evaluation(self):
        req = request()
        proof = evidence(req)
        verifier = FakeVerifier(
            after_call=lambda: object.__setattr__(proof, "expires_at", 5000),
        )
        result = policy.evaluate_quality(req, proof, verifier, FixedClock(NOW))
        self.assert_rejected(result, "evidence_changed_during_verification")
    def test_quality_tier_mismatch_and_lower_quality_do_not_downshift(self):
        verifier = FakeVerifier()
        self.assert_rejected(
            self.evaluate(
                proof=evidence(actual_tier=policy.QualityTier.LOSSLESS),
                verifier=verifier,
            ),
            "quality_below_requested",
        )
        self.assertEqual(verifier.calls, [])
        self.assert_rejected(
            self.evaluate(
                proof=evidence(actual_tier=policy.QualityTier.HI_RES),
                verifier=verifier,
            ),
            "quality_below_requested",
        )
        self.assert_rejected(
            self.evaluate(
                proof=evidence(actual_tier="jymaster"),
                verifier=verifier,
            ),
            "invalid_actual_tier",
        )
        self.assertEqual(verifier.calls, [])

    def test_provider_label_format_and_bitrate_alone_never_prove_master(self):
        self.assert_rejected(
            self.evaluate(proof=evidence(actual_tier="jymaster")),
            "invalid_actual_tier",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(actual_tier="flac")),
            "invalid_actual_tier",
        )
        self.assert_rejected(
            policy.evaluate_quality(
                request(),
                {"quality": "jymaster", "format": "flac", "bitrate": 9000000},
                FakeVerifier(),
                FixedClock(NOW),
            ),
            "invalid_evidence",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(mapping_id="https://provider.invalid/map")),
            "invalid_mapping_id",
        )

    def test_rights_confirmation_trial_completeness_and_duration_are_separate_gates(self):
        self.assert_rejected(
            self.evaluate(proof=evidence(rights_status=policy.RightsStatus.UNKNOWN)),
            "rights_unknown",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(rights_status=policy.RightsStatus.DENIED)),
            "rights_denied",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(confirmed=False)),
            "not_confirmed",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(confirmed=1)),
            "not_confirmed",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(no_trial=False)),
            "trial_or_trial_status_unknown",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(no_trial=1)),
            "trial_or_trial_status_unknown",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(complete=False)),
            "audio_incomplete_or_unknown",
        )
        self.assert_rejected(
            self.evaluate(proof=evidence(duration_seconds=180)),
            "duration_mismatch",
        )

    def test_untrusted_inputs_and_verifier_exceptions_are_not_echoed(self):
        secret = "Cookie=secret https://provider.invalid/raw-response"
        result = policy.evaluate_quality(
            request(),
            {"provider_response": secret},
            FakeVerifier(fail=True),
            FixedClock(NOW),
        )
        self.assert_rejected(result, "invalid_evidence")
        self.assertNotIn(secret, repr(result))
        self.assertNotIn("Cookie", repr(result))
        self.assertNotIn("https://", repr(result))

        result = self.evaluate(verifier=FakeVerifier(fail=True))
        self.assert_rejected(result, "trust_verification_failed")
        self.assertNotIn(secret, repr(result))
        self.assertNotIn("Cookie", repr(result))
        self.assertNotIn("https://", repr(result))

    def test_playlist_evaluates_every_track_with_its_own_evidence(self):
        req_a = request(request_id="req-a", attempt_id="attempt-a", track_id=101)
        req_b = request(request_id="req-b", attempt_id="attempt-b", track_id=102)
        req_c = request(request_id="req-c", attempt_id="attempt-c", track_id=103)
        proof_a = evidence(req_a)
        proof_b_for_wrong_track = evidence(req_b, track_id=999)
        playlist = policy.evaluate_playlist(
            (
                (req_a, proof_a),
                (req_b, proof_b_for_wrong_track),
                (req_c, None),
            ),
            FakeVerifier(),
            FixedClock(NOW),
        )
        self.assertEqual(
            tuple(item.status for item in playlist.items),
            (
                policy.EvaluationStatus.MATCHED,
                policy.EvaluationStatus.REJECTED,
                policy.EvaluationStatus.REJECTED,
            ),
        )
        self.assertEqual(playlist.items[1].reason, "track_mismatch")
        self.assertEqual(playlist.items[2].reason, "evidence_missing")
        self.assertFalse(playlist.all_matched)
        self.assertFalse(playlist.production_ready)

    def test_playlist_shape_and_size_are_bounded(self):
        invalid = policy.evaluate_playlist("not-a-sequence", FakeVerifier(), FixedClock(NOW))
        self.assertEqual(invalid.items[0].reason, "invalid_playlist")
        oversized = policy.evaluate_playlist(
            tuple(() for _ in range(policy.MAX_PLAYLIST_TRACKS + 1)),
            FakeVerifier(),
            FixedClock(NOW),
        )
        self.assertEqual(oversized.items[0].reason, "playlist_too_large")
        malformed_item = policy.evaluate_playlist(
            ((request(), evidence()), [request(), evidence()]),
            FakeVerifier(),
            FixedClock(NOW),
        )
        self.assertEqual(malformed_item.items[1].reason, "invalid_playlist_item")

    def test_playlist_fails_closed_when_verifier_mutates_source_list(self):
        first_request = request(request_id="first", attempt_id="attempt-first")
        second_request = request(request_id="second", attempt_id="attempt-second")
        third_request = request(request_id="third", attempt_id="attempt-third")
        first_pair = (first_request, evidence(first_request))
        second_pair = (second_request, evidence(second_request))
        third_pair = (third_request, evidence(third_request))

        def append_from_another_thread(playlist):
            worker = Thread(target=playlist.append, args=(third_pair,))
            worker.start()
            worker.join()

        mutations = {
            "append": lambda playlist: playlist.append(third_pair),
            "concurrent_append": append_from_another_thread,
            "delete": lambda playlist: playlist.pop(),
            "replace": lambda playlist: playlist.__setitem__(1, third_pair),
        }
        for name, mutate in mutations.items():
            with self.subTest(mutation=name):
                playlist_items = [first_pair, second_pair]
                verifier = FakeVerifier(after_call=lambda: mutate(playlist_items))
                result = policy.evaluate_playlist(
                    playlist_items, verifier, FixedClock(NOW)
                )
                self.assertFalse(result.all_matched)
                self.assertEqual(len(result.items), 1)
                self.assert_rejected(
                    result.items[0], "playlist_changed_during_evaluation"
                )
                self.assertEqual(len(verifier.calls), 1)

    def test_playlist_freezes_every_request_and_evidence_field_before_callbacks(self):
        first_request = request(request_id="first", attempt_id="attempt-first", track_id=101)
        second_request = request(request_id="second", attempt_id="attempt-second", track_id=102)
        first_evidence = evidence(first_request)
        second_evidence = evidence(second_request)
        verifier = FakeVerifier(
            after_call=lambda: (
                object.__setattr__(second_request, "track_id", 999),
                object.__setattr__(second_evidence, "track_id", 999),
            ),
        )
        result = policy.evaluate_playlist(
            [(first_request, first_evidence), (second_request, second_evidence)],
            verifier,
            FixedClock(NOW),
        )
        self.assertFalse(result.all_matched)
        self.assertEqual(len(result.items), 1)
        self.assert_rejected(result.items[0], "playlist_changed_during_evaluation")
        self.assertEqual(len(verifier.calls), 1)
        self.assertEqual(verifier.calls[0][0].track_id, 101)
        self.assertIsNot(verifier.calls[0][0], first_request)
        self.assertIsNot(verifier.calls[0][1], first_evidence)

    def test_playlist_detects_mutation_of_an_already_evaluated_track(self):
        first_request = request(request_id="first", attempt_id="attempt-first", track_id=101)
        second_request = request(request_id="second", attempt_id="attempt-second", track_id=102)
        last_request = request(request_id="last", attempt_id="attempt-last", track_id=103)
        first_evidence = evidence(first_request)
        second_evidence = evidence(second_request)
        last_evidence = evidence(last_request)

        class MutateEarlierTrackVerifier:
            def __init__(self):
                self.calls = []

            def verify_quality_evidence(self, selected_request, selected_evidence):
                self.calls.append((selected_request, selected_evidence))
                if selected_request.request_id == "last":
                    object.__setattr__(first_request, "track_id", 999)
                    object.__setattr__(first_evidence, "track_id", 999)
                return trust_report()

        verifier = MutateEarlierTrackVerifier()
        result = policy.evaluate_playlist(
            [
                (first_request, first_evidence),
                (second_request, second_evidence),
                (last_request, last_evidence),
            ],
            verifier,
            FixedClock(NOW),
        )
        self.assertFalse(result.all_matched)
        self.assertEqual(len(result.items), 1)
        self.assert_rejected(result.items[0], "playlist_changed_during_evaluation")
        self.assertEqual(len(verifier.calls), 3)

    def test_playlist_detects_deleted_fields_and_concurrent_record_mutation(self):
        for mutation in (
            "delete_request_field",
            "delete_evidence_field",
            "concurrent_field_change",
        ):
            with self.subTest(mutation=mutation):
                first_request = request(request_id="first", attempt_id="attempt-first", track_id=101)
                second_request = request(request_id="second", attempt_id="attempt-second", track_id=102)
                first_evidence = evidence(first_request)
                second_evidence = evidence(second_request)

                def mutate_later_record():
                    if mutation == "delete_request_field":
                        object.__delattr__(second_request, "track_id")
                    elif mutation == "delete_evidence_field":
                        object.__delattr__(second_evidence, "mapping_id")
                    else:
                        worker = Thread(
                            target=lambda: (
                                object.__setattr__(second_request, "track_id", 999),
                                object.__setattr__(second_evidence, "track_id", 999),
                            )
                        )
                        worker.start()
                        worker.join()

                verifier = FakeVerifier(after_call=mutate_later_record)
                result = policy.evaluate_playlist(
                    [(first_request, first_evidence), (second_request, second_evidence)],
                    verifier,
                    FixedClock(NOW),
                )
                self.assertFalse(result.all_matched)
                self.assertEqual(len(result.items), 1)
                self.assert_rejected(
                    result.items[0], "playlist_changed_during_evaluation"
                )
                self.assertNotIn("AttributeError", repr(result))
                self.assertEqual(len(verifier.calls), 1)

    def test_playlist_cannot_snapshot_a_fieldless_item(self):
        broken_request = request()
        object.__delattr__(broken_request, "track_id")
        verifier = FakeVerifier()
        result = policy.evaluate_playlist(
            [(broken_request, evidence())], verifier, FixedClock(NOW)
        )
        self.assertFalse(result.all_matched)
        self.assertEqual(len(result.items), 1)
        self.assert_rejected(result.items[0], "evaluation_failed")
        self.assertNotIn("AttributeError", repr(result))
        self.assertEqual(verifier.calls, [])

    def test_playlist_limit_accepts_exact_boundary_and_rejects_above_it(self):
        pair = (request(), evidence())
        verifier = FakeVerifier()
        exact_limit = policy.evaluate_playlist(
            [pair] * policy.MAX_PLAYLIST_TRACKS, verifier, FixedClock(NOW)
        )
        self.assertTrue(exact_limit.all_matched)
        self.assertEqual(len(exact_limit.items), policy.MAX_PLAYLIST_TRACKS)
        self.assertEqual(len(verifier.calls), policy.MAX_PLAYLIST_TRACKS)

        oversized_verifier = FakeVerifier()
        oversized = policy.evaluate_playlist(
            [pair] * (policy.MAX_PLAYLIST_TRACKS + 1),
            oversized_verifier,
            FixedClock(NOW),
        )
        self.assertFalse(oversized.all_matched)
        self.assertEqual(len(oversized.items), 1)
        self.assert_rejected(oversized.items[0], "playlist_too_large")
        self.assertEqual(oversized_verifier.calls, [])

    def test_playlist_rejects_custom_iterables_without_invoking_them(self):
        class ExplodingList(list):
            def __iter__(self):
                raise RuntimeError("untrusted iterator")

        playlist_items = ExplodingList([(request(), evidence())])
        result = policy.evaluate_playlist(
            playlist_items, FakeVerifier(), FixedClock(NOW)
        )
        self.assertFalse(result.all_matched)
        self.assertEqual(result.items[0].reason, "invalid_playlist")

    def test_non_master_request_is_rejected_without_quality_fallback(self):
        self.assert_rejected(
            self.evaluate(req=request(requested_tier=policy.QualityTier.LOSSLESS)),
            "unsupported_requested_tier",
        )


if __name__ == "__main__":
    unittest.main()





