"""Offline contract tests; the fake store never touches the filesystem."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

core = importlib.import_module("proxy.manual_save_core")


def _load_import_probe(module_name):
    probe_name = f"proxy._{module_name}_import_probe"
    source_path = Path(__file__).resolve().parents[1] / "proxy" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(probe_name, source_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load import probe for {module_name}")
    module = importlib.util.module_from_spec(spec)
    if probe_name in sys.modules:
        raise AssertionError(f"import probe already loaded: {probe_name}")
    sys.modules[probe_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(probe_name, None)


class MemoryStore:
    def __init__(self, existing=None, failures=None):
        self.existing = dict(existing or {})
        self.failures = dict(failures or {})
        self.events = []
        self.stages = {}
        self.next_stage = 0

    def _fail(self, method):
        if method in self.failures:
            action = self.failures[method]
            if isinstance(action, BaseException):
                raise action
            return action

    def begin_owned_stage(self, target_id):
        self.events.append(("begin", target_id))
        self._fail("begin")
        self.next_stage += 1
        stage = f"stage-{self.next_stage}"
        self.stages[stage] = bytearray()
        return stage

    def append(self, stage, data):
        self.events.append(("append", bytes(data)))
        self._fail("append")
        self.stages[stage].extend(data)

    def seal(self, stage):
        self.events.append(("seal", stage))
        self._fail("seal")

    def publish_noreplace(self, stage, basename):
        self.events.append(("publish_noreplace", basename))
        action = self._fail("publish")
        if action == "unknown":
            # Simulate ambiguous completion without claiming a final state.
            raise OSError("redacted")
        if basename in self.existing:
            return core.StoreResult.CONFLICT
        self.existing[basename] = bytes(self.stages[stage])
        return core.StoreResult.PUBLISHED

    def abort_owned_stage(self, stage):
        self.events.append(("abort", stage))
        self._fail("abort")
        self.stages.pop(stage, None)

    def release_owned_stage(self, stage):
        self.events.append(("release", stage))
        self._fail("release")
        self.stages.pop(stage, None)


def plan(name="track.mp3", fmt="mp3", size=4, max_bytes=16):
    result = core.plan_single_file("target-1", name, fmt, size, max_bytes)
    if isinstance(result, core.SaveResult):
        raise AssertionError(result)
    return result


class ManualSaveCoreTests(unittest.TestCase):
    def assert_unverified(self, result):
        self.assertFalse(result.audio_verified)
        self.assertFalse(result.production_ready)

    def test_invalid_requests_are_rejected_before_storage(self):
        cases = [
            ("../x.mp3", "mp3", 1, 8),
            ("C:\\x.mp3", "mp3", 1, 8),
            ("CON.mp3", "mp3", 1, 8),
            ("CON .mp3", "mp3", 1, 8),
            ("cOn.foo.mp3", "mp3", 1, 8),
            ("CON .foo.mp3", "mp3", 1, 8),
            ("nUl.backup.flac", "flac", 1, 8),
            ("NuL .backup.flac", "flac", 1, 8),
            ("pRn.archive.mp3", "mp3", 1, 8),
            ("x.mp3 ", "mp3", 1, 8),
            ("x.flac", "mp3", 1, 8),
            ("x.nCm", "mp3", 1, 8),
            ("x.mp3", "ncm", 1, 8),
            ("x.mp3", "mp3", True, 8),
            ("x.mp3", "mp3", 1, True),
            ("x.mp3", "mp3", 9, 8),
        ]
        for name, fmt, expected, limit in cases:
            with self.subTest(name=name, fmt=fmt):
                store = MemoryStore()
                result = core.plan_single_file("target-1", name, fmt, expected, limit)
                self.assertIsInstance(result, core.SaveResult)
                self.assertEqual(store.events, [])
                self.assert_unverified(result)

        for name, fmt in (("CONSOLE.foo.mp3", "mp3"), ("NULX.backup.flac", "flac"), ("CON_foo.mp3", "mp3")):
            with self.subTest(legal_near_miss=name):
                result = core.plan_single_file("target-1", name, fmt, 1, 8)
                self.assertIsInstance(result, core.SavePlan)

    def test_manually_constructed_invalid_plan_is_revalidated_before_storage(self):
        store = MemoryStore()
        invalid = core.SavePlan("target-1", "../outside.mp3", "mp3", 4, 8)
        result = core.execute_single_file(invalid, [b"abcd"], store)
        self.assertEqual(result.reason, "invalid_basename")
        self.assertEqual(store.events, [])

    def test_normal_chunks_are_published_byte_exactly_in_order(self):
        store = MemoryStore()
        result = core.execute_single_file(plan(), [b"ab", b"cd"], store)
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED)
        self.assertEqual(store.existing, {"track.mp3": b"abcd"})
        self.assertEqual([event[0] for event in store.events], ["begin", "append", "append", "seal", "publish_noreplace", "release"])
        self.assert_unverified(result)

    def test_ncm_magic_detected_across_chunk_boundary_and_fake_flac(self):
        for chunks, name, fmt in [([b"CTEN", b"FDAMx"], "x.mp3", "mp3"), ([b"CTENFDAMx"], "x.flac", "flac")]:
            with self.subTest(name=name):
                store = MemoryStore()
                p = core.plan_single_file("target-1", name, fmt, sum(map(len, chunks)), 64)
                result = core.execute_single_file(p, chunks, store)
                self.assertEqual(result.reason, "unsupported_ncm")
                self.assertFalse(any(e[0] == "publish_noreplace" for e in store.events))
                self.assertEqual(store.stages, {})
                self.assert_unverified(result)

    def test_source_ncm_rejected_without_storage_calls(self):
        store = MemoryStore()
        result = core.plan_single_file("target-1", "x.NCM", "ncm", 8, 64)
        self.assertEqual(result.reason, "unsupported_ncm")
        self.assertEqual(store.events, [])

    def test_stream_length_and_chunk_limits_do_not_publish(self):
        cases = [
            ([b"abcde"], 4, "stream_too_long"),
            ([b"abc"], 4, "length_mismatch"),
            ([bytearray(b"abcd")], 4, "invalid_chunk"),
            ([b""], 1, "empty_chunk"),
            ([b"x" * (core.MAX_CHUNK_BYTES + 1)], core.MAX_CHUNK_BYTES + 1, "chunk_too_large"),
        ]
        for chunks, length, reason in cases:
            with self.subTest(reason=reason):
                store = MemoryStore()
                p = core.plan_single_file("target-1", "x.mp3", "mp3", length, core.HARD_MAX_BYTES)
                result = core.execute_single_file(p, chunks, store)
                self.assertEqual(result.reason, reason)
                self.assertFalse(any(e[0] == "publish_noreplace" for e in store.events))
                self.assertEqual(store.stages, {})

    def test_chunk_count_and_iterator_errors_abort_stage(self):
        store = MemoryStore()
        chunks = [b"x"] * (core.MAX_CHUNKS + 1)
        p = core.plan_single_file("target-1", "x.mp3", "mp3", len(chunks), len(chunks))
        result = core.execute_single_file(p, chunks, store)
        self.assertEqual(result.reason, "too_many_chunks")
        self.assertEqual(store.stages, {})

        def broken():
            yield b"a"
            raise RuntimeError("secret text")

        store = MemoryStore()
        result = core.execute_single_file(plan(size=2), broken(), store)
        self.assertEqual(result.reason, "staging_failed")
        self.assertNotIn("secret", result.reason)
        self.assertEqual(store.stages, {})

    def test_begin_append_and_seal_failures_have_fixed_outcomes_and_cleanup(self):
        for method in ("begin", "append", "seal"):
            with self.subTest(method=method):
                store = MemoryStore(failures={method: RuntimeError("private path")})
                result = core.execute_single_file(plan(), [b"abcd"], store)
                self.assertEqual(result.outcome, core.Outcome.FAILED)
                self.assertEqual(result.reason, "stage_unavailable" if method == "begin" else "staging_failed")
                self.assertEqual(store.stages, {})
                self.assert_unverified(result)

    def test_pre_publish_cancel_aborts_and_post_publish_cancel_does_not_remove(self):
        store = MemoryStore()
        result = core.execute_single_file(plan(), [b"abcd"], store, cancelled=lambda: True)
        self.assertEqual(result.outcome, core.Outcome.CANCELLED)
        self.assertEqual(store.existing, {})
        self.assertEqual(store.stages, {})

        cancelled = [False]

        class CancelAfterPublish(MemoryStore):
            def publish_noreplace(self, stage, basename):
                published = super().publish_noreplace(stage, basename)
                cancelled[0] = True
                return published

        store = CancelAfterPublish()
        result = core.execute_single_file(plan(), [b"abcd"], store, cancelled=lambda: cancelled[0])
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED)
        self.assertEqual(store.existing["track.mp3"], b"abcd")

    def test_cancel_during_seal_is_rechecked_before_publish(self):
        cancelled = [False]

        class CancelDuringSeal(MemoryStore):
            def seal(self, stage):
                super().seal(stage)
                cancelled[0] = True

        store = CancelDuringSeal()
        result = core.execute_single_file(plan(), [b"abcd"], store, cancelled=lambda: cancelled[0])
        self.assertEqual(result.outcome, core.Outcome.CANCELLED)
        self.assertEqual(result.reason, "cancelled_before_publish")
        self.assertEqual(store.existing, {})
        self.assertEqual(store.stages, {})
        self.assertNotIn("publish_noreplace", [event[0] for event in store.events])

    def test_cancel_check_exception_after_seal_fails_safely_and_cleans_stage(self):
        checks = [0]

        def cancelled():
            checks[0] += 1
            if checks[0] == 3:  # chunk boundary, pre-seal, then post-seal check
                raise RuntimeError("private cancellation detail")
            return False

        store = MemoryStore()
        result = core.execute_single_file(plan(), [b"abcd"], store, cancelled=cancelled)
        self.assertEqual(result.outcome, core.Outcome.FAILED)
        self.assertEqual(result.reason, "cancel_check_failed")
        self.assertFalse(result.cleanup_pending)
        self.assertEqual(store.stages, {})
        self.assertEqual(store.existing, {})
        self.assertNotIn("publish_noreplace", [event[0] for event in store.events])
        self.assertNotIn("private", result.reason)

    def test_conflict_preserves_existing_content(self):
        store = MemoryStore(existing={"track.mp3": b"old"})
        result = core.execute_single_file(plan(), [b"abcd"], store)
        self.assertEqual(result.outcome, core.Outcome.CONFLICT)
        self.assertEqual(store.existing["track.mp3"], b"old")
        self.assertEqual(store.stages, {})

    def test_sequential_same_name_attempt_has_one_winner_and_one_conflict(self):
        """Sequential calls cover conflict preservation, not concurrent atomicity."""
        store = MemoryStore()
        first = core.execute_single_file(plan(), [b"abcd"], store)
        second = core.execute_single_file(plan(), [b"efgh"], store)
        self.assertEqual(first.outcome, core.Outcome.PUBLISHED)
        self.assertEqual(second.outcome, core.Outcome.CONFLICT)
        self.assertEqual(store.existing["track.mp3"], b"abcd")

    def test_every_outcome_keeps_audio_and_production_unverified(self):
        results = [
            core.plan_single_file("target-1", "bad/name.mp3", "mp3", 1, 8),
            core.execute_single_file(plan(), [b"abcd"], MemoryStore()),
            core.execute_single_file(plan(), [b"abcd"], MemoryStore(existing={"track.mp3": b"old"})),
            core.execute_single_file(plan(), [b"abcd"], MemoryStore(), cancelled=lambda: True),
            core.execute_single_file(plan(), [b"abcd"], MemoryStore(failures={"publish": "unknown"})),
            core.execute_single_file(plan(), [b"abcd"], MemoryStore(failures={"release": RuntimeError()})),
            core.execute_single_file(plan(), [b"abcd"], MemoryStore(failures={"begin": RuntimeError()})),
        ]
        self.assertEqual(
            {result.outcome for result in results},
            set(core.Outcome),
        )
        for result in results:
            with self.subTest(outcome=result.outcome):
                self.assert_unverified(result)

    def test_unknown_commit_retains_stage_and_never_retries_or_deletes(self):
        store = MemoryStore(failures={"publish": "unknown"})
        result = core.execute_single_file(plan(), [b"abcd"], store)
        self.assertEqual(result.outcome, core.Outcome.COMMIT_UNKNOWN)
        self.assertEqual(result.reason, "commit_unknown")
        self.assertTrue(result.cleanup_pending)
        self.assertEqual(store.stages, {"stage-1": bytearray(b"abcd")})
        self.assertEqual([e[0] for e in store.events].count("publish_noreplace"), 1)
        self.assertNotIn("abort", [e[0] for e in store.events])
        self.assert_unverified(result)

    def test_cleanup_failures_preserve_primary_result_without_final_delete(self):
        store = MemoryStore(failures={"abort": RuntimeError("private")})
        p = core.plan_single_file("target-1", "x.mp3", "mp3", 5, 8)
        result = core.execute_single_file(p, [b"abcd"], store)
        self.assertEqual(result.outcome, core.Outcome.REJECTED)
        self.assertTrue(result.cleanup_pending)
        self.assertFalse(any(e[0] == "delete_final" for e in store.events))

        store = MemoryStore(failures={"release": RuntimeError("private")})
        result = core.execute_single_file(plan(), [b"abcd"], store)
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED_CLEANUP_PENDING)
        self.assertEqual(store.existing["track.mp3"], b"abcd")

    def test_import_has_no_filesystem_network_process_environment_or_thread_effect(self):
        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
             mock.patch("socket.socket", side_effect=AssertionError("socket")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
             mock.patch("os.getenv", side_effect=AssertionError("environment")), \
             mock.patch("threading.Thread", side_effect=AssertionError("thread")):
            probe = _load_import_probe("manual_save_core")
        self.assertTrue(callable(probe.plan_single_file))


if __name__ == "__main__":
    unittest.main()
