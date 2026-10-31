"""Contract tests for the offline Linux-only POSIX storage candidate.

The Linux tests require real dir_fd, O_NOFOLLOW, linkat-style hard-link, and
directory-fsync behavior. They are explicitly skipped on non-Linux hosts; a
skip is not Linux acceptance evidence.
"""

from __future__ import annotations

import errno
import importlib
import importlib.util
import multiprocessing
import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from unittest import mock

core = importlib.import_module("proxy.manual_save_core")
posix = importlib.import_module("proxy.manual_save_posix")


def _race_publish_child(destination_fd, staging_fd, payload, barrier, output):
    store = posix.OfflineLinuxStore("race-target", destination_fd, staging_fd)
    try:
        handle = store.begin_owned_stage("race-target")
        store.append(handle, payload)
        store.seal(handle)
        barrier.wait(timeout=15)
        result = store.publish_noreplace(handle, "shared.mp3")
        if result is core.StoreResult.PUBLISHED:
            store.release_owned_stage(handle)
        else:
            store.abort_owned_stage(handle)
        output.put(result.value)
    except Exception as exc:
        output.put(f"error:{type(exc).__name__}")
    finally:
        store.close()


class PosixStorePlatformTests(unittest.TestCase):
    def test_import_does_not_open_or_mutate_filesystem(self):
        module_name = "proxy._manual_save_posix_import_probe"
        source = Path(__file__).resolve().parents[1] / "proxy" / "manual_save_posix.py"
        spec = importlib.util.spec_from_file_location(module_name, source)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            with mock.patch.object(os, "open", side_effect=AssertionError("import performed open")), \
                 mock.patch.object(os, "dup", side_effect=AssertionError("import performed dup")), \
                 mock.patch.object(os, "link", side_effect=AssertionError("import performed link")), \
                 mock.patch.object(os, "unlink", side_effect=AssertionError("import performed unlink")):
                spec.loader.exec_module(module)
        finally:
            sys.modules.pop(module_name, None)

    def test_unsupported_platform_fails_before_descriptor_access(self):
        with mock.patch.object(posix.sys, "platform", "win32"), \
             mock.patch.object(posix.os, "dup", side_effect=AssertionError("descriptor touched")):
            with self.assertRaisesRegex(posix.UnsupportedPosixStore, "linux_required"):
                posix.OfflineLinuxStore("target", object(), object())

    def test_ambiguous_publication_state_blocks_retry_abort_and_release_without_io(self):
        for link_created in (False, True):
            with self.subTest(link_created=link_created):
                store = object.__new__(posix.OfflineLinuxStore)
                handle = object()
                store._target_id = "target-1"
                store._destination_fd = -1
                store._staging_fd = -1
                store._effective_uid = 0
                store._stages = {
                    handle: posix._Stage(
                        name=".fnmusic-offline-stage-fixture",
                        fd=-1,
                        device=1,
                        inode=2,
                        sealed=True,
                        publication_attempted=True,
                        link_created=link_created,
                        publication_confirmed=False,
                    ),
                }
                store._lock = threading.RLock()
                store._closed = False

                with mock.patch.object(posix.os, "link", side_effect=AssertionError("retry attempted")), \
                     mock.patch.object(posix.os, "unlink", side_effect=AssertionError("cleanup attempted")), \
                     mock.patch.object(posix.os, "stat", side_effect=AssertionError("stat attempted")), \
                     mock.patch.object(posix.os, "fstat", side_effect=AssertionError("fstat attempted")):
                    with self.assertRaisesRegex(RuntimeError, "stage_not_publishable"):
                        store.publish_noreplace(handle, "track.mp3")
                    with self.assertRaisesRegex(RuntimeError, "ambiguous_stage_not_abortable"):
                        store.abort_owned_stage(handle)
                    with self.assertRaisesRegex(RuntimeError, "stage_publication_not_confirmed"):
                        store.release_owned_stage(handle)

                self.assertIn(handle, store._stages)

    def test_identity_check_exception_freezes_before_retry_or_cleanup_without_io(self):
        store = object.__new__(posix.OfflineLinuxStore)
        handle = object()
        stage = posix._Stage(
            name=".fnmusic-offline-stage-fixture",
            fd=-1,
            device=1,
            inode=2,
            sealed=True,
        )
        store._target_id = "target-1"
        store._destination_fd = -1
        store._staging_fd = -1
        store._effective_uid = 0
        store._stages = {handle: stage}
        store._lock = threading.RLock()
        store._closed = False

        def fail_identity_check(candidate):
            self.assertIs(candidate, stage)
            self.assertTrue(candidate.publication_attempted)
            raise RuntimeError("stage_identity_unavailable")

        store._verify_stage_entry = fail_identity_check
        with mock.patch.object(posix.os, "link", side_effect=AssertionError("link attempted")), \
             mock.patch.object(posix.os, "unlink", side_effect=AssertionError("cleanup attempted")), \
             mock.patch.object(posix.os, "stat", side_effect=AssertionError("stat attempted")), \
             mock.patch.object(posix.os, "fstat", side_effect=AssertionError("fstat attempted")):
            with self.assertRaisesRegex(RuntimeError, "stage_identity_unavailable"):
                store.publish_noreplace(handle, "track.mp3")
            self.assertTrue(stage.publication_attempted)
            self.assertFalse(stage.link_created)
            self.assertFalse(stage.publication_confirmed)
            with self.assertRaisesRegex(RuntimeError, "stage_not_publishable"):
                store.publish_noreplace(handle, "track.mp3")
            with self.assertRaisesRegex(RuntimeError, "ambiguous_stage_not_abortable"):
                store.abort_owned_stage(handle)
            with self.assertRaisesRegex(RuntimeError, "stage_publication_not_confirmed"):
                store.release_owned_stage(handle)
        self.assertIn(handle, store._stages)


@unittest.skipUnless(
    sys.platform == "linux",
    "NOT RUN: real Linux descriptor and filesystem semantics require a Linux runner",
)
class LinuxPosixStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fnmusic-posix-store-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.destination = self.root / "library"
        self.staging = self.root / "staging"
        self.destination.mkdir(mode=0o700)
        self.staging.mkdir(mode=0o700)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        self.destination_fd = os.open(self.destination, directory_flags)
        self.staging_fd = os.open(self.staging, directory_flags)
        self.addCleanup(os.close, self.staging_fd)
        self.addCleanup(os.close, self.destination_fd)
        self.store = posix.OfflineLinuxStore("target-1", self.destination_fd, self.staging_fd)
        self.addCleanup(self.store.close)

    def _plan(self, basename="track.mp3", payload=b"audio"):
        plan = core.plan_single_file("target-1", basename, "mp3", len(payload), 1024)
        self.assertIsInstance(plan, core.SavePlan)
        return plan

    def _run(self, basename="track.mp3", payload=b"audio", cancelled=None):
        return core.execute_single_file(
            self._plan(basename, payload), [payload], self.store, cancelled=cancelled,
        )

    def _stage_names(self):
        return tuple(sorted(os.listdir(self.staging_fd)))

    def test_constructor_requires_capabilities_private_stage_and_distinct_directories(self):
        with mock.patch.object(posix.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(posix.UnsupportedPosixStore, "required_dirfd_primitives"):
                posix.OfflineLinuxStore("target-1", self.destination_fd, self.staging_fd)

        same_fd = os.open(self.destination, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with self.assertRaisesRegex(posix.InvalidPosixStore, "directories_must_be_distinct"):
                posix.OfflineLinuxStore("target-1", self.destination_fd, same_fd)
        finally:
            os.close(same_fd)

        loose = self.root / "loose"
        loose.mkdir(mode=0o700)
        os.chmod(loose, 0o755)
        loose_fd = os.open(loose, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            with self.assertRaisesRegex(posix.InvalidPosixStore, "staging_directory_not_private"):
                posix.OfflineLinuxStore("target-1", self.destination_fd, loose_fd)
        finally:
            os.close(loose_fd)

    def test_constructor_accepts_only_open_directory_descriptors(self):
        with mock.patch.object(posix.os, "dup", side_effect=AssertionError("path must be rejected first")):
            with self.assertRaisesRegex(posix.InvalidPosixStore, "directory_fd_required"):
                posix.OfflineLinuxStore("target-1", str(self.destination), self.staging_fd)

        file_path = self.root / "not-a-directory"
        file_path.write_bytes(b"x")
        file_fd = os.open(file_path, os.O_RDONLY | os.O_CLOEXEC)
        try:
            with self.assertRaisesRegex(posix.InvalidPosixStore, "destination_not_directory"):
                posix.OfflineLinuxStore("target-1", file_fd, self.staging_fd)
        finally:
            os.close(file_fd)

    def test_store_duplicates_descriptors_noninheritable_and_close_leaves_stage(self):
        self.assertFalse(os.get_inheritable(self.store._destination_fd))
        self.assertFalse(os.get_inheritable(self.store._staging_fd))
        handle = self.store.begin_owned_stage("target-1")
        self.store.append(handle, b"pending")
        stage_name = self.store._stages[handle].name
        self.store.close()
        self.store.close()
        self.assertTrue((self.staging / stage_name).is_file())
        with self.assertRaisesRegex(RuntimeError, "store_closed"):
            self.store.begin_owned_stage("target-1")

    def test_core_publishes_exact_bytes_without_replacing_or_setting_readiness(self):
        result = self._run(payload=b"exact audio bytes")
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED)
        self.assertEqual((self.destination / "track.mp3").read_bytes(), b"exact audio bytes")
        self.assertEqual(stat.S_IMODE((self.destination / "track.mp3").stat().st_mode), 0o600)
        self.assertEqual(self._stage_names(), ())
        self.assertFalse(result.audio_verified)
        self.assertFalse(result.production_ready)

    def test_existing_regular_file_symlink_and_directory_conflict_without_change(self):
        outside = self.root / "outside.bin"
        outside.write_bytes(b"outside stays")
        cases = ("regular", "symlink", "directory")
        for kind in cases:
            with self.subTest(kind=kind):
                basename = f"existing-{kind}.mp3"
                existing = self.destination / basename
                if kind == "regular":
                    existing.write_bytes(b"keep existing")
                elif kind == "symlink":
                    os.symlink(outside, existing)
                else:
                    existing.mkdir()

                result = self._run(basename, b"new payload")
                self.assertEqual(result.outcome, core.Outcome.CONFLICT)
                if kind == "regular":
                    self.assertEqual(existing.read_bytes(), b"keep existing")
                elif kind == "symlink":
                    self.assertTrue(existing.is_symlink())
                    self.assertEqual(os.readlink(existing), str(outside))
                    self.assertEqual(outside.read_bytes(), b"outside stays")
                else:
                    self.assertTrue(existing.is_dir())
                self.assertEqual(self._stage_names(), ())
                if kind == "regular":
                    existing.unlink()
                elif kind == "symlink":
                    existing.unlink()
                else:
                    existing.rmdir()

    def test_invalid_direct_basename_is_rejected_before_link(self):
        handle = self.store.begin_owned_stage("target-1")
        self.store.append(handle, b"payload")
        self.store.seal(handle)
        for invalid in ("../escape.mp3", "sub/file.mp3", "sub\\file.mp3", ".", "bad\x00name.mp3"):
            with self.subTest(name=repr(invalid)):
                with self.assertRaisesRegex(ValueError, "invalid_basename"):
                    self.store.publish_noreplace(handle, invalid)
        self.assertEqual(tuple(self.destination.iterdir()), ())
        self.store.abort_owned_stage(handle)
        self.assertEqual(self._stage_names(), ())

    def test_wrong_target_and_cross_instance_handle_are_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "target_mismatch"):
            self.store.begin_owned_stage("other-target")
        handle = self.store.begin_owned_stage("target-1")
        other = posix.OfflineLinuxStore("target-1", self.destination_fd, self.staging_fd)
        try:
            with self.assertRaisesRegex(RuntimeError, "unknown_stage"):
                other.abort_owned_stage(handle)
        finally:
            other.close()
        self.store.abort_owned_stage(handle)

    def test_directory_path_replacement_does_not_redirect_open_directory_fd(self):
        moved_destination = self.root / "moved-library"
        victim = self.root / "replacement"
        victim.mkdir(mode=0o700)
        os.rename(self.destination, moved_destination)
        os.symlink(victim, self.destination)

        result = self._run("anchored.mp3", b"anchored")
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED)
        self.assertEqual((moved_destination / "anchored.mp3").read_bytes(), b"anchored")
        self.assertFalse((victim / "anchored.mp3").exists())
        self.assertTrue(self.destination.is_symlink())

    def test_replaced_stage_entry_is_never_published_or_removed(self):
        outside = self.root / "outside-stage-target"
        outside.write_bytes(b"untouched")
        handle = self.store.begin_owned_stage("target-1")
        self.store.append(handle, b"staged")
        self.store.seal(handle)
        name = self.store._stages[handle].name
        os.unlink(name, dir_fd=self.store._staging_fd)
        os.symlink(outside, name, dir_fd=self.store._staging_fd)

        with self.assertRaisesRegex(RuntimeError, "stage_entry_changed"):
            self.store.publish_noreplace(handle, "never.mp3")
        with self.assertRaisesRegex(RuntimeError, "ambiguous_stage_not_abortable"):
            self.store.abort_owned_stage(handle)
        self.assertFalse((self.destination / "never.mp3").exists())
        self.assertTrue((self.staging / name).is_symlink())
        self.assertEqual(outside.read_bytes(), b"untouched")

    def test_enospc_write_error_cleans_owned_stage_before_publication(self):
        with mock.patch.object(posix.os, "write", side_effect=OSError(errno.ENOSPC, "full")):
            result = self._run("full.mp3", b"not published")
        self.assertEqual(result.outcome, core.Outcome.FAILED)
        self.assertFalse((self.destination / "full.mp3").exists())
        self.assertEqual(self._stage_names(), ())

    def test_cancel_during_seal_aborts_before_publication(self):
        original_fsync = posix.os.fsync
        cancelled = [False]

        def signal_cancel_after_file_sync(fd):
            result = original_fsync(fd)
            if stat.S_ISREG(os.fstat(fd).st_mode):
                cancelled[0] = True
            return result

        with mock.patch.object(posix.os, "fsync", side_effect=signal_cancel_after_file_sync):
            result = self._run(
                "cancelled-seal.mp3",
                b"cancel during seal",
                cancelled=lambda: cancelled[0],
            )
        self.assertEqual(result.outcome, core.Outcome.CANCELLED)
        self.assertFalse((self.destination / "cancelled-seal.mp3").exists())
        self.assertEqual(self._stage_names(), ())

    def test_short_writes_are_retried_until_chunk_is_complete(self):
        original_write = posix.os.write
        write_count = [0]

        def partial(fd, data):
            write_count[0] += 1
            view = memoryview(data)
            return original_write(fd, view[: max(1, len(view) // 2)])

        with mock.patch.object(posix.os, "write", side_effect=partial):
            result = self._run("partial.mp3", b"a sufficiently long payload")
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED)
        self.assertEqual((self.destination / "partial.mp3").read_bytes(), b"a sufficiently long payload")
        self.assertGreater(write_count[0], 1)

    def test_post_link_directory_fsync_failure_is_unknown_and_retains_stage(self):
        original_fsync = posix.os.fsync
        handles = []
        original_begin = self.store.begin_owned_stage

        def capture_handle(target_id):
            handle = original_begin(target_id)
            handles.append(handle)
            return handle

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("injected directory fsync failure")
            return original_fsync(fd)

        with mock.patch.object(self.store, "begin_owned_stage", side_effect=capture_handle), \
             mock.patch.object(posix.os, "fsync", side_effect=fail_directory_fsync):
            result = self._run("unknown.mp3", b"possibly published")
        self.assertEqual(result.outcome, core.Outcome.COMMIT_UNKNOWN)
        self.assertTrue(result.cleanup_pending)
        self.assertEqual((self.destination / "unknown.mp3").read_bytes(), b"possibly published")
        self.assertEqual(len(self._stage_names()), 1)
        handle = handles[0]
        with self.assertRaisesRegex(RuntimeError, "stage_not_publishable"):
            self.store.publish_noreplace(handle, "unknown.mp3")
        with self.assertRaisesRegex(RuntimeError, "ambiguous_stage_not_abortable"):
            self.store.abort_owned_stage(handle)
        with self.assertRaisesRegex(RuntimeError, "stage_publication_not_confirmed"):
            self.store.release_owned_stage(handle)
        self.assertEqual(len(self._stage_names()), 1)
        self.assertFalse(result.audio_verified)
        self.assertFalse(result.production_ready)

    def test_link_error_is_unknown_and_stage_cannot_be_retried_or_cleaned(self):
        handles = []
        original_begin = self.store.begin_owned_stage

        def capture_handle(target_id):
            handle = original_begin(target_id)
            handles.append(handle)
            return handle

        with mock.patch.object(self.store, "begin_owned_stage", side_effect=capture_handle), \
             mock.patch.object(posix.os, "link", side_effect=OSError(errno.EIO, "link error")) as link:
            result = self._run("link-error.mp3", b"unknown link result")

        self.assertEqual(result.outcome, core.Outcome.COMMIT_UNKNOWN)
        self.assertTrue(result.cleanup_pending)
        self.assertFalse((self.destination / "link-error.mp3").exists())
        self.assertEqual(link.call_count, 1)
        handle = handles[0]
        with self.assertRaisesRegex(RuntimeError, "stage_not_publishable"):
            self.store.publish_noreplace(handle, "link-error.mp3")
        with self.assertRaisesRegex(RuntimeError, "ambiguous_stage_not_abortable"):
            self.store.abort_owned_stage(handle)
        with self.assertRaisesRegex(RuntimeError, "stage_publication_not_confirmed"):
            self.store.release_owned_stage(handle)
        self.assertEqual(len(self._stage_names()), 1)

    def test_publish_identity_stat_error_becomes_unknown_and_freezes_stage(self):
        handles = []
        original_begin = self.store.begin_owned_stage
        original_stat = posix.os.stat
        stat_calls = [0]

        def capture_handle(target_id):
            handle = original_begin(target_id)
            handles.append(handle)
            return handle

        def fail_publish_identity_stat(path, *args, **kwargs):
            stat_calls[0] += 1
            if stat_calls[0] == 3:
                raise OSError(errno.EIO, "injected stage identity stat failure")
            return original_stat(path, *args, **kwargs)

        with mock.patch.object(self.store, "begin_owned_stage", side_effect=capture_handle), \
             mock.patch.object(posix.os, "stat", side_effect=fail_publish_identity_stat):
            result = self._run("identity-error.mp3", b"stage identity")

        self.assertEqual(stat_calls[0], 3)
        self.assertEqual(result.outcome, core.Outcome.COMMIT_UNKNOWN)
        self.assertTrue(result.cleanup_pending)
        self.assertFalse((self.destination / "identity-error.mp3").exists())
        self.assertEqual(len(self._stage_names()), 1)
        handle = handles[0]
        stage = self.store._stages[handle]
        self.assertTrue(stage.publication_attempted)
        self.assertFalse(stage.link_created)
        self.assertFalse(stage.publication_confirmed)

        with mock.patch.object(posix.os, "stat", side_effect=AssertionError("stat retried")), \
             mock.patch.object(posix.os, "link", side_effect=AssertionError("link retried")), \
             mock.patch.object(posix.os, "unlink", side_effect=AssertionError("stage removed")):
            with self.assertRaisesRegex(RuntimeError, "stage_not_publishable"):
                self.store.publish_noreplace(handle, "identity-error.mp3")
            with self.assertRaisesRegex(RuntimeError, "ambiguous_stage_not_abortable"):
                self.store.abort_owned_stage(handle)
            with self.assertRaisesRegex(RuntimeError, "stage_publication_not_confirmed"):
                self.store.release_owned_stage(handle)

        self.assertEqual(len(self._stage_names()), 1)

    def test_file_fsync_failure_is_cleaned_before_publication(self):
        original_fsync = posix.os.fsync

        def fail_file_fsync(fd):
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("injected file fsync failure")
            return original_fsync(fd)

        with mock.patch.object(posix.os, "fsync", side_effect=fail_file_fsync):
            result = self._run("sync-fail.mp3", b"not published")
        self.assertEqual(result.outcome, core.Outcome.FAILED)
        self.assertFalse((self.destination / "sync-fail.mp3").exists())
        self.assertEqual(self._stage_names(), ())

    def test_cleanup_failure_is_reported_without_removing_published_file(self):
        original_unlink = posix.os.unlink

        def fail_unlink(path, *args, **kwargs):
            if kwargs.get("dir_fd") == self.store._staging_fd:
                raise OSError("injected stage cleanup failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch.object(posix.os, "unlink", side_effect=fail_unlink):
            result = self._run("cleanup-pending.mp3", b"published")
        self.assertEqual(result.outcome, core.Outcome.PUBLISHED_CLEANUP_PENDING)
        self.assertTrue((self.destination / "cleanup-pending.mp3").is_file())
        self.assertEqual(len(self._stage_names()), 1)

    def test_two_processes_racing_for_same_name_have_one_winner(self):
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        output = context.Queue()
        payloads = (b"first complete audio", b"second complete audio")
        processes = [
            context.Process(
                target=_race_publish_child,
                args=(self.destination_fd, self.staging_fd, payload, barrier, output),
            )
            for payload in payloads
        ]
        for process in processes:
            process.start()
        results = [output.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=20)
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                self.fail("race worker did not terminate")
            self.assertEqual(process.exitcode, 0)

        self.assertCountEqual(results, [core.StoreResult.PUBLISHED.value, core.StoreResult.CONFLICT.value])
        self.assertIn((self.destination / "shared.mp3").read_bytes(), payloads)
        self.assertEqual(self._stage_names(), ())


if __name__ == "__main__":
    unittest.main()
