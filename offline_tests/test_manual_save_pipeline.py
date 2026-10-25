"""Offline integration checks for the SQLite ledger and byte-save core.

The temporary disk adapter in this file is a test fixture only. It is not a
production filesystem adapter and must not be imported by application code.
"""

from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path
import secrets
import tempfile
import unittest

jobs = importlib.import_module("proxy.manual_save_jobs")
core = importlib.import_module("proxy.manual_save_core")

_ACTOR = "pipeline-test-actor"


def _manifest(entries, *, kind="playlist"):
    return {
        "kind": kind,
        "target_id": "test-library",
        "provider_id": "synthetic-provider",
        "collection_id": "synthetic-collection",
        "collection_version": "fixture-v1",
        "enumeration_complete": True,
        "entries": entries,
    }


class TemporaryDiskStore:
    """A deliberately narrow, test-only adapter rooted in one temp directory."""

    def __init__(self, root: Path):
        self.root = root
        self._stages: dict[str, dict[str, object]] = {}
        self.on_seal = None
        self.raise_after_link = False
        self.fail_release = False

    def _stage(self, stage):
        if type(stage) is not str or stage not in self._stages:
            raise RuntimeError("unknown test stage")
        return self._stages[stage]

    def begin_owned_stage(self, target_id):
        if target_id != "test-library":
            raise RuntimeError("unexpected test target")
        for _ in range(8):
            stage = secrets.token_hex(16)
            path = self.root / f".fnmusic-test-stage-{stage}"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
            try:
                descriptor = os.open(path, flags, 0o600)
            except FileExistsError:
                continue
            stream = os.fdopen(descriptor, "wb")
            self._stages[stage] = {"path": path, "stream": stream, "sealed": False}
            return stage
        raise OSError("test stage allocation exhausted")

    def append(self, stage, data):
        state = self._stage(stage)
        stream = state["stream"]
        if stream is None or state["sealed"]:
            raise RuntimeError("test stage is not writable")
        remaining = memoryview(data)
        while remaining:
            written = stream.write(remaining)
            if written is None or written <= 0:
                raise OSError("short test-stage write")
            remaining = remaining[written:]

    def seal(self, stage):
        state = self._stage(stage)
        stream = state["stream"]
        if stream is None or state["sealed"]:
            raise RuntimeError("test stage is not open")
        stream.flush()
        os.fsync(stream.fileno())
        stream.close()
        state["stream"] = None
        state["sealed"] = True
        if self.on_seal is not None:
            self.on_seal(stage)

    def publish_noreplace(self, stage, basename):
        state = self._stage(stage)
        if not state["sealed"]:
            raise RuntimeError("test stage is not sealed")
        source = state["path"]
        destination = self.root / basename
        try:
            # Same-directory hard-link creation is atomic and does not replace
            # an existing destination on the host filesystem.
            os.link(source, destination)
        except FileExistsError:
            return core.StoreResult.CONFLICT
        if self.raise_after_link:
            self.raise_after_link = False
            raise OSError("injected post-publication ambiguity")
        return core.StoreResult.PUBLISHED

    def abort_owned_stage(self, stage):
        self._remove_stage(stage)

    def release_owned_stage(self, stage):
        if self.fail_release:
            self.fail_release = False
            raise OSError("injected test cleanup failure")
        self._remove_stage(stage)

    def _remove_stage(self, stage):
        state = self._stage(stage)
        stream = state["stream"]
        if stream is not None:
            stream.close()
        path = state["path"]
        Path(path).unlink()
        del self._stages[stage]

    def stage_paths(self):
        return tuple(sorted(self.root.glob(".fnmusic-test-stage-*")))


class ManualSavePipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fnmusic-save-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / "ledger.sqlite3"
        self.ledger = jobs.ManualSaveJobsLedger(self.database)
        self.store = TemporaryDiskStore(self.root)

    def assert_readiness_false(self, snapshot):
        self.assertFalse(snapshot.audio_verified)
        self.assertFalse(snapshot.production_ready)
        self.assertFalse(snapshot.import_ready)

    def assert_result_unverified(self, result):
        self.assertFalse(result.audio_verified)
        self.assertFalse(result.production_ready)

    def create_job(self, entries, *, kind="playlist", key="pipeline-key"):
        return self.ledger.create_job(_ACTOR, key, _manifest(entries, kind=kind))

    def executor(self, payloads, *, expected_sizes=None):
        expected_sizes = expected_sizes or {}

        def execute(position, cancellation_check):
            payload = payloads.get(position.source_id)
            if payload is None:
                return core.SaveResult(core.Outcome.REJECTED, "invalid_target")
            basename, source_format, chunks = payload
            actual_size = sum(len(chunk) for chunk in chunks)
            expected = expected_sizes.get(position.position, actual_size)
            plan = core.plan_single_file(
                "test-library", basename, source_format, expected,
                max(expected, actual_size, 1),
            )
            if isinstance(plan, core.SaveResult):
                return plan
            return core.execute_single_file(
                plan, chunks, self.store, cancelled=cancellation_check,
            )

        return execute

    def snapshot(self, job_id):
        result = self.ledger.get_job(_ACTOR, job_id)
        self.assert_readiness_false(result)
        return result

    def assert_no_stages(self):
        self.assertEqual(self.store.stage_paths(), ())

    def test_playlist_preserves_order_formats_duplicate_and_existing_bytes(self):
        created = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
            {"position": 2, "source_id": "song-b", "source_status": "ok"},
            {"position": 3, "source_id": "song-a", "source_status": "ok"},
        ])
        payloads = {
            "song-a": ("alpha.mp3", "mp3", [b"alpha-", b"audio"]),
            "song-b": ("beta.flac", "flac", [b"beta-audio"]),
        }
        executor = self.executor(payloads)
        reports = [
            self.ledger.run_next(_ACTOR, created.job_id, executor),
            self.ledger.run_next(_ACTOR, created.job_id, executor),
        ]
        alpha = self.root / "alpha.mp3"
        alpha_hash_before_duplicate = hashlib.sha256(alpha.read_bytes()).digest()
        reports.append(self.ledger.run_next(_ACTOR, created.job_id, executor))

        snapshot = self.snapshot(created.job_id)
        self.assertEqual([report.position.outcome for report in reports], [
            "published", "published", "conflict",
        ])
        self.assertEqual([item.state for item in snapshot.positions], [
            "published", "published", "conflict",
        ])
        self.assertEqual(snapshot.counts["published"], 2)
        self.assertEqual(snapshot.counts["conflict"], 1)
        self.assertEqual((self.root / "alpha.mp3").read_bytes(), b"alpha-audio")
        self.assertEqual((self.root / "beta.flac").read_bytes(), b"beta-audio")
        self.assertEqual(
            hashlib.sha256(alpha.read_bytes()).digest(), alpha_hash_before_duplicate,
        )
        self.assertEqual(sorted(path.name for path in self.root.iterdir() if path.suffix in {".mp3", ".flac"}), [
            "alpha.mp3", "beta.flac",
        ])
        self.assert_no_stages()
        for report in reports:
            self.assert_result_unverified(report.result)

    def test_existing_different_bytes_are_not_replaced(self):
        destination = self.root / "alpha.mp3"
        destination.write_bytes(b"preexisting user data")
        before = hashlib.sha256(destination.read_bytes()).digest()
        created = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
        ], kind="single")

        report = self.ledger.run_next(
            _ACTOR, created.job_id,
            self.executor({"song-a": ("alpha.mp3", "mp3", [b"new bytes"])}),
        )
        snapshot = self.snapshot(created.job_id)
        self.assertEqual(report.position.outcome, "conflict")
        self.assertEqual(snapshot.positions[0].reason, "destination_conflict")
        self.assertEqual(destination.read_bytes(), b"preexisting user data")
        self.assertEqual(hashlib.sha256(destination.read_bytes()).digest(), before)
        self.assert_no_stages()
        self.assert_result_unverified(report.result)

    def test_ncm_magic_split_across_chunks_cannot_be_published_as_flac(self):
        created = self.create_job([
            {"position": 1, "source_id": "ncm-song", "source_status": "ok"},
        ], kind="single")
        report = self.ledger.run_next(
            _ACTOR, created.job_id,
            self.executor({"ncm-song": ("fake.flac", "flac", [b"CTEN", b"FDAMpayload"])}),
        )
        snapshot = self.snapshot(created.job_id)
        self.assertEqual(report.position.outcome, "rejected")
        self.assertEqual(report.result.reason, "unsupported_ncm")
        self.assertFalse((self.root / "fake.flac").exists())
        self.assert_no_stages()
        self.assert_result_unverified(report.result)
        self.assertEqual(snapshot.positions[0].reason, "unsupported_ncm")

    def test_short_stream_is_recorded_without_publishing(self):
        created = self.create_job([
            {"position": 1, "source_id": "short-song", "source_status": "ok"},
        ], kind="single")
        report = self.ledger.run_next(
            _ACTOR, created.job_id,
            self.executor(
                {"short-song": ("short.mp3", "mp3", [b"short"])},
                expected_sizes={1: len(b"short") + 1},
            ),
        )
        snapshot = self.snapshot(created.job_id)
        self.assertEqual(report.position.outcome, "rejected")
        self.assertEqual(report.result.reason, "length_mismatch")
        self.assertFalse((self.root / "short.mp3").exists())
        self.assert_no_stages()
        self.assert_result_unverified(report.result)
        self.assertEqual(snapshot.positions[0].reason, "length_mismatch")

    def test_cancel_during_seal_prevents_publish_and_cancels_queued_positions(self):
        created = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
            {"position": 2, "source_id": "song-b", "source_status": "ok"},
        ])
        self.store.on_seal = lambda _stage: self.ledger.cancel_job(_ACTOR, created.job_id)
        report = self.ledger.run_next(
            _ACTOR, created.job_id,
            self.executor({
                "song-a": ("alpha.mp3", "mp3", [b"alpha"]),
                "song-b": ("beta.flac", "flac", [b"beta"]),
            }),
        )
        snapshot = self.snapshot(created.job_id)
        self.assertEqual(report.position.outcome, "cancelled")
        self.assertEqual(report.result.reason, "cancelled_before_publish")
        self.assertTrue(snapshot.cancel_requested)
        self.assertEqual([item.state for item in snapshot.positions], ["cancelled", "cancelled"])
        self.assertFalse((self.root / "alpha.mp3").exists())
        self.assertFalse((self.root / "beta.flac").exists())
        self.assert_no_stages()
        self.assert_result_unverified(report.result)

    def test_post_link_exception_is_unknown_frozen_and_never_retried_after_reopen(self):
        created = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
            {"position": 2, "source_id": "song-b", "source_status": "ok"},
        ])
        self.store.raise_after_link = True
        executor = self.executor({
            "song-a": ("alpha.mp3", "mp3", [b"alpha"]),
            "song-b": ("beta.flac", "flac", [b"beta"]),
        })
        report = self.ledger.run_next(_ACTOR, created.job_id, executor)
        snapshot = self.snapshot(created.job_id)
        self.assertEqual(report.status, "commit_unknown")
        self.assertEqual(report.result.reason, "commit_unknown")
        self.assertEqual(snapshot.state, "reconciliation_required")
        self.assertEqual([item.state for item in snapshot.positions], ["commit_unknown", "queued"])
        self.assertEqual((self.root / "alpha.mp3").read_bytes(), b"alpha")
        self.assertEqual(len(self.store.stage_paths()), 1)

        reopened = jobs.ManualSaveJobsLedger(self.database)
        executions = []
        replay = reopened.run_next(
            _ACTOR, created.job_id,
            lambda *_args: executions.append(True) or core.SaveResult(
                core.Outcome.PUBLISHED, "bytes_published",
            ),
        )
        reopened_snapshot = reopened.get_job(_ACTOR, created.job_id)
        self.assertEqual(replay.status, "no_work")
        self.assertEqual(executions, [])
        self.assertEqual([item.state for item in reopened_snapshot.positions], ["commit_unknown", "queued"])
        self.assertEqual((self.root / "alpha.mp3").read_bytes(), b"alpha")
        self.assertEqual(len(self.store.stage_paths()), 1)
        self.assert_readiness_false(reopened_snapshot)
        self.assert_result_unverified(report.result)

    def test_published_cleanup_pending_survives_ledger_reopen(self):
        created = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
        ], kind="single")
        self.store.fail_release = True
        report = self.ledger.run_next(
            _ACTOR, created.job_id,
            self.executor({"song-a": ("alpha.mp3", "mp3", [b"alpha"])}),
        )
        snapshot = self.snapshot(created.job_id)
        self.assertEqual(report.position.outcome, "published_cleanup_pending")
        self.assertTrue(report.result.cleanup_pending)
        self.assertEqual((self.root / "alpha.mp3").read_bytes(), b"alpha")
        self.assertEqual(len(self.store.stage_paths()), 1)

        reopened = jobs.ManualSaveJobsLedger(self.database)
        persisted = reopened.get_job(_ACTOR, created.job_id)
        self.assertEqual(persisted.positions[0].state, "published_cleanup_pending")
        self.assertTrue(persisted.positions[0].cleanup_pending)
        self.assertEqual((self.root / "alpha.mp3").read_bytes(), b"alpha")
        self.assertEqual(len(self.store.stage_paths()), 1)
        self.assert_readiness_false(persisted)
        self.assert_result_unverified(report.result)
        self.assert_readiness_false(snapshot)


if __name__ == "__main__":
    unittest.main()
