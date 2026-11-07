"""Synthetic tests for the verified manual-save pipeline; no I/O is used."""

from __future__ import annotations

import unittest

from proxy import manual_save_verified_pipeline as pipeline


class FakeSource:
    def __init__(self, chunks, events, fail=False):
        self.chunks = chunks
        self.events = events
        self.fail = fail

    def iter_chunks(self, track_id):
        self.events.append(("source", track_id))
        if self.fail:
            raise RuntimeError("redacted source detail")
        yield from self.chunks


class FakeStage:
    def __init__(self, key):
        self.key = key
        self.data = bytearray()


class FakeStager:
    def __init__(self, events, fail=None):
        self.events = events
        self.fail = fail
        self.stage = None

    def _maybe_fail(self, action):
        if self.fail == action:
            raise RuntimeError("redacted staging detail")

    def begin_stage(self, track_id):
        self.events.append(("begin", track_id))
        self._maybe_fail("begin")
        self.stage = FakeStage("stage-1")
        return self.stage

    def append_chunk(self, stage, chunk):
        self.events.append(("append", bytes(chunk)))
        self._maybe_fail("append")
        stage.data.extend(chunk)

    def seal_stage(self, stage, byte_count):
        self.events.append(("seal", byte_count))
        self._maybe_fail("seal")
        if len(stage.data) != byte_count:
            raise AssertionError("fake stage length mismatch")
        return stage


class FakeVerifier:
    def __init__(self, events, report=None, fail=False):
        self.events = events
        self.report = report
        self.fail = fail

    def verify(self, sealed_stage, expected_bytes):
        self.events.append(("verify", expected_bytes))
        if self.fail:
            raise RuntimeError("redacted verifier detail")
        return self.report


class FakeVerifierSupervisor:
    def __init__(self, events, status=pipeline.VerificationExecutionStatus.COMPLETED, terminated=True):
        self.events = events
        self.status = status
        self.terminated = terminated
        self.calls = []

    def run_with_timeout(self, verifier, sealed_stage, expected_bytes, timeout_seconds):
        self.calls.append((sealed_stage, expected_bytes, timeout_seconds))
        self.events.append(("supervise_verify", timeout_seconds))
        if self.status is pipeline.VerificationExecutionStatus.TIMED_OUT:
            return pipeline.VerificationExecution(self.status, self.terminated)
        if self.status is pipeline.VerificationExecutionStatus.FAILED:
            return pipeline.VerificationExecution(self.status, self.terminated)
        try:
            result = verifier.verify(sealed_stage, expected_bytes)
        except Exception:
            return pipeline.VerificationExecution(
                pipeline.VerificationExecutionStatus.FAILED,
                self.terminated,
            )
        return pipeline.VerificationExecution(self.status, self.terminated, result)


class FakePublisher:
    def __init__(self, events, status=pipeline.PublicationStatus.PUBLISHED, fail=False):
        self.events = events
        self.status = status
        self.fail = fail
        self.calls = []
        self.destination = {"target": b"existing"} if status is pipeline.PublicationStatus.CONFLICT else {}

    def publish_noreplace(self, sealed_stage, track_id, verified_format, byte_count):
        self.calls.append((sealed_stage, track_id, verified_format, byte_count))
        self.events.append(("publish", track_id, verified_format, byte_count))
        if self.fail:
            raise RuntimeError("redacted publication detail")
        if self.status is pipeline.PublicationStatus.PUBLISHED:
            self.destination["target"] = bytes(sealed_stage.data)
        return self.status


class BrokenAdapter:
    @property
    def iter_chunks(self):
        raise RuntimeError("sensitive adapter detail")


def request(**changes):
    values = {
        "track_id": 489970550,
        "display_duration_seconds": 235,
        "is_preview": False,
        "rights_status": pipeline.RightsStatus.VERIFIED,
        "declared_length_bytes": 4,
    }
    values.update(changes)
    return pipeline.ManualSaveRequest(**values)


def report(**changes):
    values = {
        "complete": True,
        "is_preview": False,
        "rights_status": pipeline.RightsStatus.VERIFIED,
        "is_ncm": False,
        "media_format": "flac",
        "byte_count": 4,
        "duration_seconds": 235,
    }
    values.update(changes)
    return pipeline.VerificationReport(**values)


class VerifiedPipelineTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.source = FakeSource([b"ab", b"cd"], self.events)
        self.stager = FakeStager(self.events)
        self.verifier = FakeVerifier(self.events, report())
        self.verifier_supervisor = FakeVerifierSupervisor(self.events)
        self.publisher = FakePublisher(self.events)

    def run_pipeline(self, save_request=None, cancelled=None):
        return pipeline.run_manual_save(
            save_request or request(),
            self.source,
            self.stager,
            self.verifier,
            self.verifier_supervisor,
            self.publisher,
            cancelled=cancelled,
        )

    def assert_not_production_ready(self, result):
        self.assertFalse(result.production_ready)
        self.assertFalse(pipeline.PRODUCTION_READY)

    def assert_not_published(self, result):
        self.assertNotEqual(result.status, pipeline.ResultStatus.PUBLISHED)
        self.assertEqual(self.publisher.calls, [])
        self.assert_not_production_ready(result)

    def test_happy_path_has_order_and_uses_validator_format(self):
        result = self.run_pipeline()
        self.assertEqual(result.status, pipeline.ResultStatus.PUBLISHED)
        self.assertEqual(result.verified_format, "flac")
        self.assertEqual(self.publisher.calls[0][2:], ("flac", 4))
        self.assertEqual(self.verifier_supervisor.calls[0][2], pipeline.VERIFY_TIMEOUT_SECONDS)
        names = [event[0] for event in self.events]
        self.assertLess(names.index("begin"), names.index("seal"))
        self.assertLess(names.index("seal"), names.index("verify"))
        self.assertLess(names.index("verify"), names.index("publish"))
        self.assertEqual(names.count("publish"), 1)
        self.assertFalse(hasattr(request(), "path"))
        self.assertFalse(hasattr(request(), "payload"))
        self.assert_not_production_ready(result)

    def test_preview_or_unknown_rights_are_rejected_before_staging(self):
        cases = (
            (request(is_preview=True), "preview_rejected"),
            (request(is_preview=None), "preview_unknown"),
            (request(rights_status=pipeline.RightsStatus.UNKNOWN), "rights_unknown"),
            (request(rights_status=pipeline.RightsStatus.DENIED), "rights_denied"),
        )
        for save_request, reason in cases:
            with self.subTest(reason=reason):
                self.events.clear()
                result = self.run_pipeline(save_request)
                self.assertEqual(result.reason, reason)
                self.assertEqual(self.events, [])
                self.assert_not_published(result)

    def test_invalid_request_does_not_call_injected_components(self):
        result = self.run_pipeline(request(track_id=True))
        self.assertEqual(result.reason, "invalid_track_id")
        self.assertEqual(self.events, [])
        self.assert_not_published(result)

    def test_adapter_introspection_exception_is_fixed_and_fails_closed(self):
        result = pipeline.run_manual_save(
            request(),
            BrokenAdapter(),
            self.stager,
            self.verifier,
            self.verifier_supervisor,
            self.publisher,
        )
        self.assertEqual(result.reason, "invalid_source")
        self.assertNotIn("sensitive", result.reason)
        self.assertEqual(self.events, [])
        self.assert_not_published(result)

    def test_validator_failures_never_publish(self):
        cases = (
            (report(is_preview=True), "preview_rejected"),
            (report(is_preview=None), "preview_unknown"),
            (report(rights_status=pipeline.RightsStatus.UNKNOWN), "rights_unknown"),
            (report(rights_status=pipeline.RightsStatus.DENIED), "rights_denied"),
            (report(is_ncm=True, media_format="ncm"), "ncm_unsupported"),
            (report(is_ncm=None), "ncm_status_unknown"),
            (report(complete=False), "audio_incomplete"),
            (report(byte_count=3), "verified_length_mismatch"),
            (report(duration_seconds=0), "verified_duration_invalid"),
            (report(media_format="unknown"), "verified_format_unsupported"),
        )
        for invalid_report, reason in cases:
            with self.subTest(reason=reason):
                self.events.clear()
                self.verifier = FakeVerifier(self.events, invalid_report)
                self.verifier_supervisor = FakeVerifierSupervisor(self.events)
                result = self.run_pipeline()
                self.assertEqual(result.reason, reason)
                self.assert_not_published(result)

    def test_ncm_magic_is_rejected_across_chunk_boundary(self):
        self.source = FakeSource([b"CTEN", b"FDAMpayload"], self.events)
        result = self.run_pipeline(request(declared_length_bytes=15))
        self.assertEqual(result.reason, "ncm_unsupported")
        self.assert_not_published(result)

    def test_stream_length_chunk_and_count_limits_fail_closed(self):
        cases = (
            ([b"abc"], 4, "stream_length_mismatch"),
            ([b"abcde"], 4, "stream_too_long"),
            ([b""], 1, "empty_chunk"),
            ([bytearray(b"abcd")], 4, "invalid_chunk"),
            ([b"x" * (pipeline.MAX_CHUNK_BYTES + 1)], pipeline.MAX_CHUNK_BYTES + 1, "chunk_too_large"),
            ([b"x"] * (pipeline.MAX_CHUNKS + 1), pipeline.MAX_CHUNKS + 1, "too_many_chunks"),
        )
        for chunks, length, reason in cases:
            with self.subTest(reason=reason):
                self.events.clear()
                self.source = FakeSource(chunks, self.events)
                self.verifier = FakeVerifier(self.events, report(byte_count=length))
                result = self.run_pipeline(request(declared_length_bytes=length))
                self.assertEqual(result.reason, reason)
                self.assert_not_published(result)

    def test_cancellation_after_verification_prevents_publish(self):
        cancel_checks = 0

        def cancelled():
            nonlocal cancel_checks
            cancel_checks += 1
            self.events.append(("cancel_check", cancel_checks))
            return cancel_checks >= 5

        result = self.run_pipeline(cancelled=cancelled)
        self.assertEqual(result.status, pipeline.ResultStatus.CANCELLED)
        names = [event[0] for event in self.events]
        self.assertLess(names.index("verify"), names.index("cancel_check", names.index("verify")))
        self.assert_not_published(result)

    def test_duration_matches_within_inclusive_two_second_tolerance(self):
        for verified_seconds in (233, 237):
            with self.subTest(verified_seconds=verified_seconds):
                self.events.clear()
                self.verifier = FakeVerifier(self.events, report(duration_seconds=verified_seconds))
                self.verifier_supervisor = FakeVerifierSupervisor(self.events)
                self.publisher = FakePublisher(self.events)
                result = self.run_pipeline()
                self.assertEqual(result.status, pipeline.ResultStatus.PUBLISHED)
                self.assertEqual(len(self.publisher.calls), 1)
                self.assert_not_production_ready(result)

    def test_duration_mismatch_fails_closed_inside_and_outside_tolerance(self):
        for verified_seconds in (1, 232, 238):
            with self.subTest(verified_seconds=verified_seconds):
                self.events.clear()
                self.verifier = FakeVerifier(self.events, report(duration_seconds=verified_seconds))
                self.verifier_supervisor = FakeVerifierSupervisor(self.events)
                self.publisher = FakePublisher(self.events)
                result = self.run_pipeline()
                self.assertEqual(result.reason, "verified_duration_mismatch")
                self.assert_not_published(result)

    def test_duration_match_does_not_replace_rights_or_completeness_gates(self):
        reports = (
            (report(rights_status=pipeline.RightsStatus.UNKNOWN), "rights_unknown"),
            (report(complete=False), "audio_incomplete"),
        )
        for verification, reason in reports:
            with self.subTest(reason=reason):
                self.events.clear()
                self.verifier = FakeVerifier(self.events, verification)
                self.verifier_supervisor = FakeVerifierSupervisor(self.events)
                self.publisher = FakePublisher(self.events)
                result = self.run_pipeline()
                self.assertEqual(result.reason, reason)
                self.assert_not_published(result)

    def test_verifier_timeout_or_unconfirmed_termination_fails_closed(self):
        cases = (
            (
                pipeline.VerificationExecutionStatus.TIMED_OUT,
                True,
                "verification_timeout",
            ),
            (
                pipeline.VerificationExecutionStatus.TIMED_OUT,
                False,
                "verifier_termination_unconfirmed",
            ),
            (
                pipeline.VerificationExecutionStatus.COMPLETED,
                False,
                "verifier_termination_unconfirmed",
            ),
        )
        for status, terminated, reason in cases:
            with self.subTest(status=status, terminated=terminated):
                self.events.clear()
                self.verifier_supervisor = FakeVerifierSupervisor(
                    self.events,
                    status=status,
                    terminated=terminated,
                )
                result = self.run_pipeline()
                self.assertEqual(result.reason, reason)
                self.assertEqual(len(self.verifier_supervisor.calls), 1)
                self.assertEqual(
                    self.verifier_supervisor.calls[0][2],
                    pipeline.VERIFY_TIMEOUT_SECONDS,
                )
                self.assert_not_published(result)

    def test_missing_timeout_supervisor_fails_before_staging(self):
        result = pipeline.run_manual_save(
            request(),
            self.source,
            self.stager,
            self.verifier,
            None,
            self.publisher,
        )
        self.assertEqual(result.reason, "verifier_supervisor_unavailable")
        self.assertEqual(self.events, [])
        self.assert_not_published(result)

    def test_non_boolean_cancel_state_fails_closed(self):
        result = self.run_pipeline(cancelled=lambda: None)
        self.assertEqual(result.reason, "cancel_check_invalid")
        self.assert_not_published(result)

    def test_cancel_callback_exception_is_sanitized_and_fails_closed(self):
        def broken_cancel():
            raise RuntimeError("private path and token must not leak")

        result = self.run_pipeline(cancelled=broken_cancel)
        self.assertEqual(result.reason, "cancel_check_failed")
        self.assertNotIn("private", result.reason)
        self.assert_not_published(result)

    def test_source_staging_and_verifier_exceptions_are_sanitized(self):
        scenarios = (
            ("source", "source_or_stage_failed"),
            ("begin", "stage_failed"),
            ("append", "source_or_stage_failed"),
            ("seal", "seal_failed"),
            ("verify", "verification_failed"),
        )
        for failure, reason in scenarios:
            with self.subTest(failure=failure):
                self.events.clear()
                self.source = FakeSource([b"abcd"], self.events, fail=failure == "source")
                self.stager = FakeStager(self.events, fail=failure if failure in {"begin", "append", "seal"} else None)
                self.verifier = FakeVerifier(self.events, report(), fail=failure == "verify")
                self.verifier_supervisor = FakeVerifierSupervisor(self.events)
                result = self.run_pipeline()
                self.assertEqual(result.reason, reason)
                self.assertNotIn("redacted", result.reason)
                self.assert_not_published(result)

    def test_conflict_does_not_overwrite_retry_or_cleanup(self):
        self.publisher = FakePublisher(self.events, pipeline.PublicationStatus.CONFLICT)
        original_destination = dict(self.publisher.destination)
        result = self.run_pipeline()
        self.assertEqual(result.status, pipeline.ResultStatus.CONFLICT)
        self.assertEqual(self.publisher.destination, original_destination)
        self.assertEqual(len(self.publisher.calls), 1)
        self.assertFalse(any(name in {"delete", "cleanup", "retry"} for name, *_ in self.events))
        self.assert_not_production_ready(result)

    def test_unknown_or_exceptional_publish_is_never_retried_or_cleaned(self):
        cases = (
            FakePublisher(self.events, pipeline.PublicationStatus.UNKNOWN),
            FakePublisher(self.events, fail=True),
        )
        for publisher in cases:
            with self.subTest(exception=publisher.fail):
                self.events.clear()
                self.publisher = publisher
                result = self.run_pipeline()
                self.assertEqual(result.status, pipeline.ResultStatus.COMMIT_UNKNOWN)
                self.assertEqual(result.reason, "publication_unknown")
                self.assertEqual(len(publisher.calls), 1)
                self.assertFalse(any(name in {"delete", "cleanup", "retry"} for name, *_ in self.events))
                self.assert_not_production_ready(result)


if __name__ == "__main__":
    unittest.main()
