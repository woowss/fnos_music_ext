"""Linux-only integration checks for the SQLite ledger, byte core, and dir_fd store.

Every payload is synthetic and tiny. These tests exercise storage protocol
outcomes; they do not decode or validate audio and do not enable production use.
"""

from __future__ import annotations

import errno
import importlib
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

jobs = importlib.import_module("proxy.manual_save_jobs")
core = importlib.import_module("proxy.manual_save_core")
posix = importlib.import_module("proxy.manual_save_posix")

_ACTOR = "posix-pipeline-test-actor"
_TARGET = "synthetic-library"


def _manifest(entries):
    return {
        "kind": "playlist",
        "target_id": _TARGET,
        "provider_id": "synthetic-provider",
        "collection_id": "synthetic-collection",
        "collection_version": "fixture-v1",
        "enumeration_complete": True,
        "entries": entries,
    }


@unittest.skipUnless(
    sys.platform == "linux",
    "NOT RUN: real Linux dir_fd and filesystem semantics require a Linux runner",
)
class LinuxPosixPipelineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fnmusic-posix-pipeline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.library = self.root / "library"
        self.staging = self.root / "staging"
        self.library.mkdir(mode=0o700)
        self.staging.mkdir(mode=0o700)
        self.database = self.root / "ledger.sqlite3"
        self.ledger = jobs.ManualSaveJobsLedger(self.database)

        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        self.library_fd = os.open(self.library, flags)
        self.addCleanup(os.close, self.library_fd)
        self.staging_fd = os.open(self.staging, flags)
        self.addCleanup(os.close, self.staging_fd)
        self.store = posix.OfflineLinuxStore(_TARGET, self.library_fd, self.staging_fd)
        self.addCleanup(self.store.close)

    def create_job(self, entries, key):
        result = self.ledger.create_job(_ACTOR, key, _manifest(entries))
        self.assertFalse(result.audio_verified)
        self.assertFalse(result.production_ready)
        self.assertFalse(result.import_ready)
        return result.job_id

    def executor(self, payloads, *, expected_sizes=None, calls=None):
        expected_sizes = expected_sizes or {}

        def execute(position, cancelled):
            if calls is not None:
                calls.append(position.position)
            payload = payloads[position.source_id]
            basename, source_format, chunks = payload
            actual_size = sum(map(len, chunks))
            expected = expected_sizes.get(position.position, actual_size)
            plan = core.plan_single_file(
                _TARGET, basename, source_format, expected,
                max(1, expected, actual_size),
            )
            if isinstance(plan, core.SaveResult):
                return plan
            return core.execute_single_file(plan, chunks, self.store, cancelled=cancelled)

        return execute

    def snapshot(self, ledger, job_id):
        result = ledger.get_job(_ACTOR, job_id)
        self.assertFalse(result.audio_verified)
        self.assertFalse(result.production_ready)
        self.assertFalse(result.import_ready)
        return result

    def stage_names(self):
        return tuple(sorted(os.listdir(self.staging_fd)))

    def test_order_formats_duplicate_and_existing_conflicts_survive_reopen(self):
        existing = self.library / "existing.mp3"
        existing.write_bytes(b"pre-existing user bytes")
        before_existing = existing.read_bytes()
        job_id = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
            {"position": 2, "source_id": "song-b", "source_status": "ok"},
            {"position": 3, "source_id": "song-a", "source_status": "ok"},
            {"position": 4, "source_id": "song-existing", "source_status": "ok"},
        ], "ordered-batch-key")
        execute = self.executor({
            "song-a": ("alpha.mp3", "mp3", [b"alpha-", b"synthetic"]),
            "song-b": ("beta.flac", "flac", [b"beta-synthetic"]),
            "song-existing": ("existing.mp3", "mp3", [b"replacement-bytes"]),
        })

        reports = [self.ledger.run_next(_ACTOR, job_id, execute) for _ in range(4)]
        self.assertEqual(
            [(report.position.position, report.position.outcome) for report in reports],
            [(1, "published"), (2, "published"), (3, "conflict"), (4, "conflict")],
        )
        self.assertEqual((self.library / "alpha.mp3").read_bytes(), b"alpha-synthetic")
        self.assertEqual((self.library / "beta.flac").read_bytes(), b"beta-synthetic")
        self.assertEqual(existing.read_bytes(), before_existing)
        self.assertEqual(self.stage_names(), ())
        self.assertTrue(all(not report.result.audio_verified for report in reports))
        self.assertTrue(all(not report.result.production_ready for report in reports))

        reopened = jobs.ManualSaveJobsLedger(self.database)
        persisted = self.snapshot(reopened, job_id)
        self.assertEqual(
            [(item.position, item.state, item.reason) for item in persisted.positions],
            [
                (1, "published", "bytes_published"),
                (2, "published", "bytes_published"),
                (3, "conflict", "destination_conflict"),
                (4, "conflict", "destination_conflict"),
            ],
        )
        self.assertEqual(persisted.counts["published"], 2)
        self.assertEqual(persisted.counts["conflict"], 2)

    def test_disguised_ncm_and_truncated_payload_reject_and_clean_only_owned_stages(self):
        sentinel = self.staging / "unowned-sentinel"
        sentinel.write_bytes(b"leave this file alone")
        job_id = self.create_job([
            {"position": 1, "source_id": "disguised", "source_status": "ok"},
            {"position": 2, "source_id": "short", "source_status": "ok"},
        ], "rejection-key")
        execute = self.executor(
            {
                "disguised": ("mislabelled.flac", "flac", [b"CTEN", b"FDAM-synthetic"]),
                "short": ("truncated.mp3", "mp3", [b"small"]),
            },
            expected_sizes={2: len(b"small") + 1},
        )

        first = self.ledger.run_next(_ACTOR, job_id, execute)
        second = self.ledger.run_next(_ACTOR, job_id, execute)
        persisted = self.snapshot(jobs.ManualSaveJobsLedger(self.database), job_id)
        self.assertEqual(
            [(first.position.outcome, first.result.reason), (second.position.outcome, second.result.reason)],
            [("rejected", "unsupported_ncm"), ("rejected", "length_mismatch")],
        )
        self.assertEqual([item.reason for item in persisted.positions], ["unsupported_ncm", "length_mismatch"])
        self.assertFalse((self.library / "mislabelled.flac").exists())
        self.assertFalse((self.library / "truncated.mp3").exists())
        self.assertEqual(self.stage_names(), ("unowned-sentinel",))
        self.assertEqual(sentinel.read_bytes(), b"leave this file alone")
        for report in (first, second):
            self.assertFalse(report.result.audio_verified)
            self.assertFalse(report.result.production_ready)

    def test_cancel_after_real_seal_is_persisted_and_does_not_dispatch_next_position(self):
        job_id = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
            {"position": 2, "source_id": "song-b", "source_status": "ok"},
        ], "cancel-after-seal-key")
        original_seal = self.store.seal

        def seal_then_cancel(stage):
            original_seal(stage)
            self.ledger.cancel_job(_ACTOR, job_id)

        self.store.seal = seal_then_cancel
        calls = []
        execute = self.executor({
            "song-a": ("cancelled.mp3", "mp3", [b"sealed-synthetic"]),
            "song-b": ("must-not-run.flac", "flac", [b"later-synthetic"]),
        }, calls=calls)

        report = self.ledger.run_next(_ACTOR, job_id, execute)
        self.assertEqual((report.position.position, report.position.outcome), (1, "cancelled"))
        self.assertEqual(report.result.reason, "cancelled_before_publish")
        self.assertEqual(calls, [1])
        self.assertFalse((self.library / "cancelled.mp3").exists())
        self.assertFalse((self.library / "must-not-run.flac").exists())
        self.assertEqual(self.stage_names(), ())

        reopened = jobs.ManualSaveJobsLedger(self.database)
        persisted = self.snapshot(reopened, job_id)
        self.assertTrue(persisted.cancel_requested)
        self.assertEqual([item.state for item in persisted.positions], ["cancelled", "cancelled"])
        replay = reopened.run_next(_ACTOR, job_id, execute)
        self.assertEqual(replay.status, "no_work")
        self.assertEqual(calls, [1])

    def test_destination_directory_fsync_failure_after_link_is_unknown_and_frozen(self):
        job_id = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
            {"position": 2, "source_id": "song-b", "source_status": "ok"},
        ], "directory-fsync-failure-key")
        original_fsync = posix.os.fsync
        destination_fd = self.store._destination_fd
        failed = []

        def fail_only_destination_directory_fsync(fd):
            if fd == destination_fd and stat.S_ISDIR(os.fstat(fd).st_mode) and not failed:
                failed.append(fd)
                raise OSError(errno.EIO, "injected destination directory fsync failure")
            return original_fsync(fd)

        execute = self.executor({
            "song-a": ("unknown.mp3", "mp3", [b"possibly-published"]),
            "song-b": ("must-not-run.flac", "flac", [b"later-synthetic"]),
        })
        with mock.patch.object(posix.os, "fsync", side_effect=fail_only_destination_directory_fsync):
            report = self.ledger.run_next(_ACTOR, job_id, execute)

        self.assertEqual(failed, [destination_fd])
        self.assertEqual(report.status, "commit_unknown")
        self.assertEqual(report.result.reason, "commit_unknown")
        self.assertTrue(report.result.cleanup_pending)
        self.assertEqual((self.library / "unknown.mp3").read_bytes(), b"possibly-published")
        self.assertEqual(len(self.stage_names()), 1)
        self.store.close()
        self.assertEqual(len(self.stage_names()), 1)

        reopened = jobs.ManualSaveJobsLedger(self.database)
        persisted = self.snapshot(reopened, job_id)
        self.assertEqual(persisted.state, "reconciliation_required")
        self.assertEqual([item.state for item in persisted.positions], ["commit_unknown", "queued"])
        self.assertEqual((self.library / "unknown.mp3").read_bytes(), b"possibly-published")
        dispatches = []
        replay = reopened.run_next(
            _ACTOR, job_id,
            lambda *_args: dispatches.append(True) or core.SaveResult(
                core.Outcome.PUBLISHED, "bytes_published",
            ),
        )
        self.assertEqual(replay.status, "no_work")
        self.assertEqual(dispatches, [])
        self.assertEqual(len(self.stage_names()), 1)
        self.assertFalse(report.result.audio_verified)
        self.assertFalse(report.result.production_ready)

    def test_cleanup_unlink_failure_is_persisted_and_adapter_close_keeps_stage(self):
        job_id = self.create_job([
            {"position": 1, "source_id": "song-a", "source_status": "ok"},
        ], "cleanup-pending-key")
        original_unlink = posix.os.unlink
        staging_fd = self.store._staging_fd
        failed = []

        def fail_only_owned_stage_unlink(path, *args, **kwargs):
            if (
                kwargs.get("dir_fd") == staging_fd
                and type(path) is str
                and path.startswith(".fnmusic-offline-stage-")
                and not failed
            ):
                failed.append(path)
                raise OSError(errno.EIO, "injected owned-stage cleanup failure")
            return original_unlink(path, *args, **kwargs)

        execute = self.executor({"song-a": ("published.mp3", "mp3", [b"published-synthetic"])})
        with mock.patch.object(posix.os, "unlink", side_effect=fail_only_owned_stage_unlink):
            report = self.ledger.run_next(_ACTOR, job_id, execute)

        self.assertEqual(len(failed), 1)
        self.assertEqual(report.position.outcome, "published_cleanup_pending")
        self.assertEqual(report.result.reason, "published_cleanup_pending")
        self.assertTrue(report.result.cleanup_pending)
        self.assertEqual((self.library / "published.mp3").read_bytes(), b"published-synthetic")
        self.assertEqual(len(self.stage_names()), 1)
        self.store.close()
        self.assertEqual(len(self.stage_names()), 1)

        reopened = jobs.ManualSaveJobsLedger(self.database)
        persisted = self.snapshot(reopened, job_id)
        self.assertEqual(persisted.positions[0].state, "published_cleanup_pending")
        self.assertTrue(persisted.positions[0].cleanup_pending)
        self.assertEqual((self.library / "published.mp3").read_bytes(), b"published-synthetic")
        self.assertEqual(len(self.stage_names()), 1)
        self.assertFalse(report.result.audio_verified)
        self.assertFalse(report.result.production_ready)


if __name__ == "__main__":
    unittest.main()
