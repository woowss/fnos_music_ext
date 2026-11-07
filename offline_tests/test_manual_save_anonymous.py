"""Synthetic, isolated tests for the unconnected anonymous staging candidate."""

from __future__ import annotations

import errno
import importlib
import json
import multiprocessing
import os
from pathlib import Path
import queue
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

core = importlib.import_module("proxy.manual_save_core")
anonymous = importlib.import_module("proxy.manual_save_anonymous")

from offline_tests._audit_support import WriteAudit  # noqa: E402 - after proxy imports

_REPO_ROOT = Path(__file__).resolve().parents[1]
_NAMESPACE_SCENARIO_HELPER = Path(__file__).with_name("_anonymous_ns_scenario.py")

_ISOLATION_ENV = "FNMUSIC_OFFLINE_TEST_ISOLATION"
_ROOT_ENV = "FNMUSIC_OFFLINE_TEST_ROOT"
_ISOLATION_MARKER = ".fnmusic-offline-isolated-runner"
_ISOLATION_MARKER_CONTENT = b"fnmusic-offline-isolated-runner-v1\n"


def _verified_isolated_runner_root() -> Path | None:
    """Return a provisioned disposable-runner root or refuse Linux file tests."""
    if os.environ.get(_ISOLATION_ENV) != "dedicated-disposable-runner-v1":
        return None
    raw_root = os.environ.get(_ROOT_ENV, "")
    if not raw_root or not os.path.isabs(raw_root):
        return None
    root_fd = -1
    marker_fd = -1
    try:
        root = Path(raw_root).resolve(strict=True)
        if os.path.abspath(raw_root) != os.fspath(root):
            return None
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        root_info = os.fstat(root_fd)
        if (
            not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.geteuid()
            or stat.S_IMODE(root_info.st_mode) != 0o700
        ):
            return None

        marker_fd = os.open(
            _ISOLATION_MARKER,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        marker_info = os.fstat(marker_fd)
        if (
            not stat.S_ISREG(marker_info.st_mode)
            or marker_info.st_uid != os.geteuid()
            or stat.S_IMODE(marker_info.st_mode) != 0o400
            or os.read(marker_fd, len(_ISOLATION_MARKER_CONTENT) + 1) != _ISOLATION_MARKER_CONTENT
        ):
            return None

        protected = [Path("/vol1"), Path("/data"), Path("/repo"), Path("/var/apps")]
        protected.append(Path(__file__).resolve().parents[1])
        protected.append(Path.cwd().resolve())
        for protected_path in protected:
            if root == protected_path or root in protected_path.parents or protected_path in root.parents:
                return None
    except (OSError, RuntimeError, ValueError):
        return None
    finally:
        if marker_fd >= 0:
            os.close(marker_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return root


def _test_only_publish_fd_via_procfs(source_fd: int, destination_dir_fd: int, basename: str) -> None:
    """Emulate linkat's atomic no-replace effect for isolated test cases.

    This is deliberately test-only. It exercises the adapter's conflict and
    post-publication state handling without claiming that the host permits the
    production AT_EMPTY_PATH operation.
    """
    os.link(
        f"/proc/self/fd/{source_fd}",
        basename,
        dst_dir_fd=destination_dir_fd,
        follow_symlinks=True,
    )


def _same_name_race_worker(destination: str, staging: str, payload: bytes, barrier, results) -> None:
    """Process entry point for the isolated, deterministic same-name race."""
    store = None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        destination_fd = os.open(destination, directory_flags)
        staging_fd = os.open(staging, directory_flags)
        try:
            store = anonymous.AnonymousLinuxStore("target-1", destination_fd, staging_fd)
        finally:
            os.close(staging_fd)
            os.close(destination_fd)
        handle = store.begin_owned_stage("target-1")
        store.append(handle, payload)
        store.seal(handle)
        barrier.wait(timeout=8)
        with mock.patch.object(anonymous, "_linkat_empty_path", side_effect=_test_only_publish_fd_via_procfs):
            result = store.publish_noreplace(handle, "same-name.mp3")
        if result == core.StoreResult.PUBLISHED:
            store.release_owned_stage(handle)
        else:
            store.abort_owned_stage(handle)
        store.close()
        results.put((result.value, None))
    except BaseException as exc:
        results.put(("error", f"{type(exc).__name__}:{exc}"))
        if store is not None:
            try:
                store.close()
            except Exception:
                # Process exit closes only this worker's anonymous descriptors.
                pass


def _tree_entries(root: Path) -> tuple[tuple[str, str, int, int], ...]:
    """Snapshot a fixture tree without following symlinks or changing it."""
    entries = []
    for current, directories, files in os.walk(root):
        for name in sorted(directories + files):
            full = Path(current) / name
            info = os.lstat(full)
            if stat.S_ISDIR(info.st_mode):
                kind = "dir"
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            elif stat.S_ISLNK(info.st_mode):
                kind = "symlink"
            else:
                kind = "other"
            entries.append(
                (os.fspath(full.relative_to(root)), kind, stat.S_IMODE(info.st_mode), info.st_size)
            )
    return tuple(sorted(entries))


class AnonymousStorePlatformTests(unittest.TestCase):
    def test_linux_file_runner_is_refused_without_explicit_isolation_marker(self):
        with mock.patch.dict(
            os.environ,
            {
                _ISOLATION_ENV: "untrusted-runner",
                _ROOT_ENV: os.path.abspath(os.curdir),
            },
        ):
            self.assertIsNone(_verified_isolated_runner_root())

    def test_non_linux_fails_before_touching_directory_descriptors(self):
        with mock.patch.object(anonymous.sys, "platform", "win32"), \
             mock.patch.object(anonymous.os, "dup", side_effect=AssertionError("fd touched")):
            with self.assertRaisesRegex(anonymous.UnsupportedAnonymousStaging, "linux_required"):
                anonymous.AnonymousLinuxStore("target-1", object(), object())

    def test_basename_validation_never_accepts_path_components(self):
        self.assertEqual(anonymous._safe_basename("Artist - Title.mp3"), "Artist - Title.mp3")
        for invalid in ("../escape.mp3", "sub/file.mp3", "sub\\file.mp3", ".", "bad\x00name.mp3"):
            with self.subTest(name=repr(invalid)):
                with self.assertRaisesRegex(ValueError, "invalid_basename"):
                    anonymous._safe_basename(invalid)

    def test_linkat_uses_empty_path_and_at_empty_path_and_reports_permission(self):
        linkat = mock.Mock(return_value=-1)
        libc = mock.Mock()
        libc.linkat = linkat
        with mock.patch.object(anonymous.ctypes, "CDLL", return_value=libc), \
             mock.patch.object(anonymous.ctypes, "get_errno", return_value=errno.EACCES):
            with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                anonymous._linkat_empty_path(41, 42, "track.mp3")
        self.assertEqual(caught.exception.reason, "destination_permission_denied")
        linkat.assert_called_once_with(41, b"", 42, b"track.mp3", anonymous._AT_EMPTY_PATH)

    def test_at_empty_path_permission_is_not_assumed_for_an_ordinary_uid(self):
        libc = mock.Mock()
        libc.linkat = mock.Mock(return_value=-1)
        with mock.patch.object(anonymous.ctypes, "CDLL", return_value=libc), \
             mock.patch.object(anonymous.ctypes, "get_errno", return_value=errno.EPERM):
            with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                anonymous._linkat_empty_path(51, 52, "track.mp3")
        self.assertEqual(caught.exception.reason, "at_empty_path_capability_required")
        libc.linkat.assert_called_once_with(51, b"", 52, b"track.mp3", anonymous._AT_EMPTY_PATH)
        self.assertFalse(anonymous.PIPELINE_INTEGRATION_ALLOWED)

    def test_unreachable_errno_mappings_are_mock_only_and_fixed(self):
        """MOCK ONLY: fixed errno-to-reason mapping for paths a real kernel
        cannot produce for this adapter.

        The adapter verifies ``st_nlink == 0`` immediately before its single
        ``linkat`` and both directory descriptors must share one device, so:

        * ``EMLINK`` is unreachable: reaching a link limit requires pre-existing
          links, while the source inode must have zero links before publication
          (a conforming filesystem with a link limit >= 1 cannot return EMLINK
          for a 0-link inode's first link).
        * ``EXDEV`` is unreachable: the constructor rejects directories on
          different devices before any publication attempt.

        This exercises the fixed reason table only; it is not real kernel
        failure-path coverage. The real EROFS and EACCES publications are
        covered by the isolated Linux tests below.
        """
        cases = {
            errno.EMLINK: ("filesystem_link_limit", "filesystem_link_limit"),
            errno.EXDEV: ("staging_and_destination_not_linkable", "staging_and_destination_not_linkable"),
            errno.EINVAL: ("at_empty_path_unsupported", "at_empty_path_unsupported"),
            errno.EOPNOTSUPP: ("at_empty_path_unsupported", "at_empty_path_unsupported"),
            errno.ENOSYS: ("at_empty_path_unsupported", "at_empty_path_unsupported"),
        }
        for error_number, (expected_reason, _label) in cases.items():
            with self.subTest(errno=errno.errorcode.get(error_number, error_number)):
                libc = mock.Mock()
                libc.linkat = mock.Mock(return_value=-1)
                with mock.patch.object(anonymous.ctypes, "CDLL", return_value=libc), \
                     mock.patch.object(anonymous.ctypes, "get_errno", return_value=error_number):
                    with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                        anonymous._linkat_empty_path(61, 62, "track.mp3")
                self.assertEqual(caught.exception.reason, expected_reason)


@unittest.skipUnless(sys.platform == "linux", "Linux O_TMPFILE/linkat integration only")
class AnonymousStoreLinuxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        runner_root = _verified_isolated_runner_root()
        if runner_root is None:
            raise RuntimeError(
                "REFUSED: Linux file tests require a dedicated disposable runner; "
                f"provision {_ISOLATION_MARKER} and set {_ISOLATION_ENV}/{_ROOT_ENV}"
            )

        # Verify O_TMPFILE on the runner filesystem before creating fixtures.
        root_fd = os.open(runner_root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            probe_fd = os.open(
                ".",
                os.O_TMPFILE | os.O_DIRECTORY | os.O_RDWR | os.O_CLOEXEC,
                0o600,
                dir_fd=root_fd,
            )
            os.close(probe_fd)
        except OSError as exc:
            raise RuntimeError(
                f"REFUSED: isolated runner filesystem lacks working O_TMPFILE ({exc.errno})"
            ) from None
        finally:
            os.close(root_fd)
        cls._runner_root = runner_root

    def setUp(self):
        # Preserve every fixture for inspection. No recursive cleanup is
        # allowed: a same-UID process could have introduced an unknown entry.
        self.root = Path(tempfile.mkdtemp(prefix="fnmusic-anonymous-test-", dir=self._runner_root))
        self.addCleanup(self._report_preserved_fixture)
        os.chmod(self.root, 0o700)
        self.destination = self.root / "destination"
        self.staging = self.root / "staging"
        self.destination.mkdir(mode=0o700)
        self.staging.mkdir(mode=0o700)
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
        self.destination_fd = os.open(self.destination, directory_flags)
        self.staging_fd = os.open(self.staging, directory_flags)
        self.addCleanup(os.close, self.staging_fd)
        self.addCleanup(os.close, self.destination_fd)
        self.store = anonymous.AnonymousLinuxStore("target-1", self.destination_fd, self.staging_fd)
        self.addCleanup(self._close_store_for_test)

    def _report_preserved_fixture(self):
        if self.root.exists():
            print(f"OFFLINE_TEST_FIXTURE_PRESERVED={self.root}")

    def _begin_required_stage(self):
        try:
            return self.store.begin_owned_stage("target-1")
        except anonymous.UnsupportedAnonymousStaging as exc:
            self.fail(f"required anonymous staging unavailable; Linux test not validated: {exc.reason}")

    def _force_close_after_ambiguous_for_test(self):
        """Release only test-owned fixture descriptors after safety assertions."""
        for stage in self.store._stages.values():
            if stage.fd >= 0 and not stage.fd_close_attempted:
                os.close(stage.fd)
                stage.fd = -1
                stage.fd_close_attempted = True
        self.store._stages.clear()
        if not self.store._closed:
            self.store._closed = True
            for fd in (self.store._staging_fd, self.store._destination_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _close_store_for_test(self):
        if self.store._closed:
            return
        if any(
            stage.publication_attempted and not stage.publication_confirmed
            for stage in self.store._stages.values()
        ):
            self._force_close_after_ambiguous_for_test()
            return
        self.store.close()

    def _run_namespace_scenario(self, scenario: str, base: Path) -> dict:
        """Run one real-kernel failure scenario in a private user+mount namespace.

        The helper mounts its own tmpfs inside the fresh mount namespace, so no
        device mount, service, configuration, or global mount is touched; the
        mounts disappear when the helper process exits. The scenario test is a
        failure (not a skip) when the host forbids unprivileged user namespaces,
        because such a host cannot validate the required kernel path.
        """
        command = [
            "unshare",
            "--user",
            "--map-root-user",
            "--mount",
            "--propagation",
            "private",
            sys.executable,
            "-I",
            "-B",
            str(_NAMESPACE_SCENARIO_HELPER),
            scenario,
            str(base),
            str(_REPO_ROOT),
        ]
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=180,
            )
        except FileNotFoundError:
            self.fail("real failure-path test requires the 'unshare' utility (util-linux)")
        except subprocess.TimeoutExpired:
            self.fail(f"namespace scenario {scenario} exceeded 180 seconds")
        if completed.returncode != 0:
            self.fail(
                f"namespace scenario {scenario} failed (rc={completed.returncode}): "
                f"stdout={completed.stdout[-2000:]!r} stderr={completed.stderr[-3000:]!r}"
            )
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        self.assertTrue(
            lines,
            f"namespace scenario {scenario} produced no JSON: {completed.stderr[-2000:]!r}",
        )
        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError:
            self.fail(f"namespace scenario {scenario} returned non-JSON output: {lines[-1][:1000]!r}")

    def test_missing_libc_linkat_fails_before_descriptor_duplication(self):
        with mock.patch.object(anonymous.ctypes, "CDLL", return_value=object()), \
             mock.patch.object(anonymous.os, "dup", side_effect=AssertionError("fd touched")):
            with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                anonymous.AnonymousLinuxStore("target-1", self.destination_fd, self.staging_fd)
        self.assertEqual(caught.exception.reason, "linkat_unavailable")

    def test_o_tmpfile_is_anonymous_private_and_abort_never_unlinks_a_name(self):
        handle = self._begin_required_stage()
        stage = self.store._stages[handle]
        self.assertFalse(os.get_inheritable(stage.fd))
        info = os.fstat(stage.fd)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(info.st_uid, os.geteuid())
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 0)
        self.assertEqual(tuple(self.staging.iterdir()), ())

        self.store.append(handle, b"synthetic bytes only")
        self.store.seal(handle)
        self.store.abort_owned_stage(handle)
        self.assertEqual(tuple(self.staging.iterdir()), ())
        with self.assertRaises(OSError):
            os.fstat(stage.fd)

    def test_acl_inherited_owner_rwx_is_normalized_to_exact_0600(self):
        """An ACL-inheriting filesystem may report 0700 for a 0600 O_TMPFILE."""
        real_open = os.open
        real_fchmod = os.fchmod
        opened_fds: list[int] = []

        def acl_inheriting_open(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            opened_fds.append(fd)
            # Simulate fnOS btrfs + system.trim_acl: the created inode reports
            # owner rwx even though O_TMPFILE requested exactly 0600.
            real_fchmod(fd, 0o700)
            return fd

        with mock.patch.object(anonymous.os, "open", side_effect=acl_inheriting_open), \
             mock.patch.object(anonymous.os, "fchmod", wraps=real_fchmod) as normalize:
            handle = self._begin_required_stage()

        self.assertEqual(len(opened_fds), 1)
        normalize.assert_called_once_with(opened_fds[0], 0o600)
        stage = self.store._stages[handle]
        info = os.fstat(stage.fd)
        self.assertTrue(stat.S_ISREG(info.st_mode))
        self.assertEqual(info.st_uid, os.geteuid())
        self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertEqual(info.st_nlink, 0)
        self.assertFalse(os.get_inheritable(stage.fd))
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())

        payload = b"synthetic acl-normalized bytes"
        self.store.append(handle, payload)
        self.store.seal(handle)
        self.assertEqual(self.store.read_for_verification(handle, 0, len(payload)), payload)
        self.store.abort_owned_stage(handle)
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())

    def test_mode_normalization_failure_is_fail_closed_without_publication(self):
        """A failing fchmod must close the descriptor and use a fixed reason."""
        real_open = os.open
        real_close = os.close
        opened_fds: list[int] = []
        closed_fds: list[int] = []

        def record_open(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            opened_fds.append(fd)
            return fd

        def record_close(fd):
            closed_fds.append(fd)
            return real_close(fd)

        with mock.patch.object(anonymous.os, "open", side_effect=record_open), \
             mock.patch.object(anonymous.os, "close", side_effect=record_close), \
             mock.patch.object(
                 anonymous.os,
                 "fchmod",
                 side_effect=OSError(errno.EPERM, "injected fchmod denial"),
             ), \
             mock.patch.object(anonymous.os, "link", side_effect=AssertionError("named fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("name cleanup")):
            with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                self.store.begin_owned_stage("target-1")

        self.assertEqual(caught.exception.reason, "stage_mode_normalization_failed")
        self.assertEqual(str(caught.exception), "stage_mode_normalization_failed")
        self.assertNotIn("injected", str(caught.exception))
        self.assertEqual(len(opened_fds), 1)
        self.assertEqual(closed_fds, opened_fds)
        self.assertEqual(self.store._stages, {})
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())
        with self.assertRaises(OSError):
            os.fstat(opened_fds[0])

    def test_normalization_that_does_not_yield_0600_is_fail_closed(self):
        """Normalization is re-verified strictly; a non-sticky mode is rejected."""
        real_open = os.open
        real_fchmod = os.fchmod
        real_close = os.close
        opened_fds: list[int] = []
        closed_fds: list[int] = []

        def acl_inheriting_open(path, flags, mode=0o777, *, dir_fd=None):
            fd = real_open(path, flags, mode, dir_fd=dir_fd)
            opened_fds.append(fd)
            real_fchmod(fd, 0o700)
            return fd

        def record_close(fd):
            closed_fds.append(fd)
            return real_close(fd)

        def ineffective_fchmod(fd, mode):
            return None

        with mock.patch.object(anonymous.os, "open", side_effect=acl_inheriting_open), \
             mock.patch.object(anonymous.os, "close", side_effect=record_close), \
             mock.patch.object(anonymous.os, "fchmod", side_effect=ineffective_fchmod) as normalize, \
             mock.patch.object(anonymous.os, "link", side_effect=AssertionError("named fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("name cleanup")):
            with self.assertRaisesRegex(RuntimeError, "stage_permissions_or_link_count_invalid") as caught:
                self.store.begin_owned_stage("target-1")

        self.assertEqual(str(caught.exception), "stage_permissions_or_link_count_invalid")
        self.assertNotIsInstance(caught.exception, anonymous.UnsupportedAnonymousStaging)
        self.assertEqual(len(opened_fds), 1)
        normalize.assert_called_once_with(opened_fds[0], 0o600)
        self.assertEqual(closed_fds, opened_fds)
        self.assertEqual(self.store._stages, {})
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())
        with self.assertRaises(OSError):
            os.fstat(opened_fds[0])

    def test_staging_uses_internal_rdwr_fd_but_verification_returns_only_copied_bytes(self):
        real_open = anonymous.os.open
        observed_flags = []

        def capture_open(path, flags, mode=0o777, *, dir_fd=None):
            observed_flags.append(flags)
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(anonymous.os, "open", side_effect=capture_open):
            handle = self._begin_required_stage()
        self.assertEqual(len(observed_flags), 1)
        self.assertEqual(observed_flags[0] & os.O_ACCMODE, os.O_RDWR)

        payload = b"synthetic verifier fixture"
        self.store.append(handle, payload)
        with self.assertRaisesRegex(RuntimeError, "stage_not_readable_for_verification"):
            self.store.read_for_verification(handle, 0, len(payload))
        self.store.seal(handle)
        chunks = [
            self.store.read_for_verification(handle, offset, 7)
            for offset in range(0, len(payload), 7)
        ]
        self.assertEqual(b"".join(chunks), payload)
        self.assertTrue(all(type(chunk) is bytes for chunk in chunks))
        self.assertFalse(any(type(chunk) is int for chunk in chunks))
        self.assertFalse(hasattr(self.store, "verification_fd"))
        self.assertFalse(anonymous.PIPELINE_INTEGRATION_ALLOWED)
        self.store.abort_owned_stage(handle)

    def test_o_tmpfile_unsupported_filesystem_fails_closed_without_named_fallback(self):
        with mock.patch.object(anonymous.os, "open", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")), \
             mock.patch.object(anonymous.os, "link", side_effect=AssertionError("named fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("name cleanup")):
            with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                self.store.begin_owned_stage("target-1")
        self.assertEqual(caught.exception.reason, "o_tmpfile_unsupported")
        self.assertEqual(tuple(self.staging.iterdir()), ())

    def test_o_tmpfile_permission_denial_is_reported_without_fallback(self):
        with mock.patch.object(anonymous.os, "open", side_effect=OSError(errno.EPERM, "denied")), \
             mock.patch.object(anonymous.os, "link", side_effect=AssertionError("named fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("name cleanup")):
            with self.assertRaises(anonymous.UnsupportedAnonymousStaging) as caught:
                self.store.begin_owned_stage("target-1")
        self.assertEqual(caught.exception.reason, "o_tmpfile_permission_denied")
        self.assertEqual(tuple(self.staging.iterdir()), ())

    def test_atomic_conflict_preserves_existing_name_and_allows_only_private_abort(self):
        existing = self.destination / "existing.mp3"
        existing.write_bytes(b"keep existing")
        handle = self._begin_required_stage()
        self.store.append(handle, b"new synthetic bytes")
        self.store.seal(handle)

        with mock.patch.object(
            anonymous,
            "_linkat_empty_path",
            side_effect=FileExistsError(errno.EEXIST, "destination_exists"),
        ) as linkat, mock.patch.object(anonymous.os, "link", side_effect=AssertionError("fallback link")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("name cleanup")):
            result = self.store.publish_noreplace(handle, "existing.mp3")

        self.assertEqual(result, core.StoreResult.CONFLICT)
        self.assertEqual(linkat.call_count, 1)
        self.assertEqual(existing.read_bytes(), b"keep existing")
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.store.abort_owned_stage(handle)
        self.assertEqual(existing.read_bytes(), b"keep existing")

    def test_capability_denial_freezes_store_without_retry_delete_or_fallback(self):
        handle = self._begin_required_stage()
        self.store.append(handle, b"synthetic permission fixture")
        self.store.seal(handle)
        stage = self.store._stages[handle]
        original_fd = stage.fd
        link_error = anonymous.UnsupportedAnonymousStaging("at_empty_path_capability_required")

        with mock.patch.object(anonymous, "_linkat_empty_path", side_effect=link_error) as linkat, \
             mock.patch.object(anonymous.os, "link", side_effect=AssertionError("named fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("name cleanup")):
            with self.assertRaises(anonymous.PublicationUncertain) as caught:
                self.store.publish_noreplace(handle, "never-published.mp3")
            self.assertEqual(caught.exception.reason, "at_empty_path_capability_required")
            with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
                self.store.publish_noreplace(handle, "never-published.mp3")
            with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
                self.store.abort_owned_stage(handle)
            with self.assertRaises(anonymous.PublicationUncertain) as close_error:
                self.store.close()

        self.assertEqual(linkat.call_count, 1)
        self.assertEqual(close_error.exception.reason, "ambiguous_stage_retained_until_process_exit")
        self.assertTrue(stage.publication_attempted)
        self.assertFalse(stage.link_created)
        self.assertFalse(stage.publication_confirmed)
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())
        self.assertTrue(stat.S_ISREG(os.fstat(original_fd).st_mode))
        with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
            self.store.begin_owned_stage("target-1")

    def test_generic_linkat_error_is_unknown_and_cannot_be_retried(self):
        handle = self._begin_required_stage()
        self.store.append(handle, b"synthetic uncertain fixture")
        self.store.seal(handle)
        stage = self.store._stages[handle]

        with mock.patch.object(
            anonymous,
            "_linkat_empty_path",
            side_effect=OSError(errno.EIO, "injected"),
        ) as linkat, mock.patch.object(anonymous.os, "link", side_effect=AssertionError("fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("cleanup")):
            with self.assertRaises(anonymous.PublicationUncertain) as caught:
                self.store.publish_noreplace(handle, "unknown.mp3")
            self.assertEqual(caught.exception.reason, "publication_outcome_unknown")
            with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
                self.store.publish_noreplace(handle, "unknown.mp3")
            with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
                self.store.abort_owned_stage(handle)

        self.assertEqual(linkat.call_count, 1)
        self.assertTrue(stage.publication_attempted)
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())

    def test_destination_symlink_is_a_no_replace_conflict(self):
        sentinel = self.destination / "sentinel.txt"
        sentinel.write_bytes(b"keep symlink target")
        symlink = self.destination / "existing.mp3"
        symlink.symlink_to(sentinel.name)
        handle = self._begin_required_stage()
        self.store.append(handle, b"new synthetic bytes")
        self.store.seal(handle)

        with mock.patch.object(
            anonymous,
            "_linkat_empty_path",
            side_effect=_test_only_publish_fd_via_procfs,
        ) as publish:
            result = self.store.publish_noreplace(handle, symlink.name)

        self.assertEqual(result, core.StoreResult.CONFLICT)
        self.assertEqual(publish.call_count, 1)
        self.assertTrue(symlink.is_symlink())
        self.assertEqual(os.readlink(symlink), sentinel.name)
        self.assertEqual(sentinel.read_bytes(), b"keep symlink target")
        self.assertCountEqual(
            (path.name for path in self.destination.iterdir()),
            ("existing.mp3", "sentinel.txt"),
        )
        self.store.abort_owned_stage(handle)

    def test_replacing_destination_after_link_is_uncertain_and_never_cleaned_up(self):
        sentinel = self.destination / "sentinel.txt"
        sentinel.write_bytes(b"keep replacement target")
        handle = self._begin_required_stage()
        payload = b"published synthetic bytes"
        self.store.append(handle, payload)
        self.store.seal(handle)
        stage = self.store._stages[handle]

        def publish_then_replace(source_fd, destination_fd, basename):
            _test_only_publish_fd_via_procfs(source_fd, destination_fd, basename)
            os.unlink(basename, dir_fd=destination_fd)
            os.symlink(sentinel.name, basename, dir_fd=destination_fd)

        real_unlink = os.unlink
        unlink_calls = []

        def record_unlink(path, *args, **kwargs):
            unlink_calls.append(path)
            return real_unlink(path, *args, **kwargs)

        with mock.patch.object(anonymous, "_linkat_empty_path", side_effect=publish_then_replace), \
             mock.patch.object(anonymous.os, "unlink", side_effect=record_unlink):
            with self.assertRaises(anonymous.PublicationUncertain) as caught:
                self.store.publish_noreplace(handle, "replaced.mp3")

        self.assertEqual(caught.exception.reason, "publication_outcome_unknown")
        self.assertEqual(unlink_calls, ["replaced.mp3"], "only the injected race may unlink this test-owned name")
        replacement = self.destination / "replaced.mp3"
        self.assertTrue(replacement.is_symlink())
        self.assertEqual(os.readlink(replacement), sentinel.name)
        self.assertEqual(sentinel.read_bytes(), b"keep replacement target")
        self.assertTrue(stage.publication_attempted)
        self.assertTrue(stage.link_created)
        self.assertFalse(stage.publication_confirmed)
        self.assertEqual(os.fstat(stage.fd).st_nlink, 0)
        with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
            self.store.publish_noreplace(handle, "replaced.mp3")

    def test_write_error_keeps_output_unpublished_and_abort_is_private(self):
        handle = self._begin_required_stage()
        stage = self.store._stages[handle]
        with mock.patch.object(anonymous.os, "write", side_effect=OSError(errno.EIO, "injected write failure")):
            with self.assertRaisesRegex(RuntimeError, "stage_write_failed"):
                self.store.append(handle, b"synthetic write failure")

        self.assertEqual(os.fstat(stage.fd).st_nlink, 0)
        self.assertEqual(tuple(self.destination.iterdir()), ())
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.store.abort_owned_stage(handle)
        self.assertEqual(tuple(self.destination.iterdir()), ())

    def test_stage_fsync_error_does_not_mark_stage_sealed_or_publishable(self):
        handle = self._begin_required_stage()
        self.store.append(handle, b"synthetic fsync failure")
        stage = self.store._stages[handle]
        with mock.patch.object(anonymous.os, "fsync", side_effect=OSError(errno.EIO, "injected file fsync failure")):
            with self.assertRaisesRegex(RuntimeError, "stage_sync_failed"):
                self.store.seal(handle)

        self.assertFalse(stage.sealed)
        self.assertFalse(stage.publication_attempted)
        self.assertEqual(os.fstat(stage.fd).st_nlink, 0)
        self.assertEqual(tuple(self.destination.iterdir()), ())
        with self.assertRaisesRegex(RuntimeError, "stage_not_publishable"):
            self.store.publish_noreplace(handle, "fsync-failed.mp3")
        self.store.abort_owned_stage(handle)

    def test_destination_directory_fsync_error_retains_published_name_as_uncertain(self):
        payload = b"synthetic directory fsync failure"
        handle = self._begin_required_stage()
        self.store.append(handle, payload)
        self.store.seal(handle)
        stage = self.store._stages[handle]
        real_fsync = os.fsync

        def fail_directory_fsync(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "injected destination directory fsync failure")
            return real_fsync(fd)

        with mock.patch.object(anonymous, "_linkat_empty_path", side_effect=_test_only_publish_fd_via_procfs), \
             mock.patch.object(anonymous.os, "fsync", side_effect=fail_directory_fsync):
            with self.assertRaises(anonymous.PublicationUncertain) as caught:
                self.store.publish_noreplace(handle, "directory-fsync.mp3")

        self.assertEqual(caught.exception.reason, "publication_outcome_unknown")
        published = self.destination / "directory-fsync.mp3"
        self.assertEqual(published.read_bytes(), payload)
        self.assertEqual(published.stat().st_nlink, 1)
        self.assertTrue(stage.publication_attempted)
        self.assertTrue(stage.link_created)
        self.assertFalse(stage.publication_confirmed)
        with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
            self.store.abort_owned_stage(handle)

    def test_multiprocess_same_name_race_has_one_winner_and_preserves_its_bytes(self):
        """Two independent stores race at the kernel's atomic no-replace link."""
        context = multiprocessing.get_context("fork")
        barrier = context.Barrier(2)
        results = context.Queue()
        payloads = (b"worker one synthetic payload", b"worker two synthetic payload")
        candidates = [
            context.Process(
                target=_same_name_race_worker,
                args=(str(self.destination), str(self.staging), payload, barrier, results),
            )
            for payload in payloads
        ]
        workers = []
        start_errors = []
        join_errors = []
        alive_after_cleanup = []
        exit_codes = []
        records = []
        try:
            for worker in candidates:
                try:
                    worker.start()
                    workers.append(worker)
                except Exception as exc:
                    start_errors.append(f"{type(exc).__name__}:{exc}")
                    break
            for worker in workers:
                try:
                    worker.join(timeout=12)
                except Exception as exc:
                    join_errors.append(f"{type(exc).__name__}:{exc}")
        finally:
            try:
                barrier.abort()
            except Exception:
                pass
            for worker in workers:
                try:
                    if worker.is_alive():
                        worker.terminate()
                except Exception as exc:
                    join_errors.append(f"terminate:{type(exc).__name__}:{exc}")
            for worker in workers:
                try:
                    worker.join(timeout=3)
                except Exception as exc:
                    join_errors.append(f"terminate_join:{type(exc).__name__}:{exc}")
            for worker in workers:
                try:
                    if worker.is_alive() and hasattr(worker, "kill"):
                        worker.kill()
                        worker.join(timeout=3)
                    if worker.is_alive():
                        alive_after_cleanup.append(worker.pid)
                    else:
                        exit_codes.append(worker.exitcode)
                except Exception as exc:
                    alive_after_cleanup.append(worker.pid)
                    join_errors.append(f"final_join:{type(exc).__name__}:{exc}")
            for _worker in workers:
                try:
                    records.append(results.get(timeout=1))
                except queue.Empty:
                    records.append(("missing_result", None))
            results.close()
            results.cancel_join_thread()
            for worker in workers:
                if not worker.is_alive():
                    worker.close()

        self.assertEqual(start_errors, [], f"race worker start failed: {start_errors}")
        self.assertEqual(join_errors, [], f"race worker cleanup issue: {join_errors}")
        self.assertEqual(alive_after_cleanup, [], f"race workers remain alive: {alive_after_cleanup}")
        self.assertEqual(exit_codes, [0] * len(workers), f"race worker exit codes: {exit_codes}")
        self.assertEqual(len(workers), len(candidates), "both race workers must start")
        self.assertEqual(len(records), len(payloads), f"race worker result records: {records}")
        outcomes = [outcome for outcome, _error in records]
        self.assertCountEqual(outcomes, [core.StoreResult.PUBLISHED.value, core.StoreResult.CONFLICT.value])
        self.assertTrue(all(error is None for _outcome, error in records), records)
        published = self.destination / "same-name.mp3"
        self.assertIn(published.read_bytes(), payloads)
        self.assertEqual(published.stat().st_nlink, 1)
        self.assertEqual(tuple(path.name for path in self.destination.iterdir()), ("same-name.mp3",))
        self.assertEqual(tuple(self.staging.iterdir()), ())

    def test_one_ambiguous_stage_freezes_every_operation_for_other_handles(self):
        unknown_handle = self._begin_required_stage()
        other_handle = self._begin_required_stage()
        unknown_payload = b"first synthetic stage"
        other_payload = b"second synthetic stage"
        self.store.append(unknown_handle, unknown_payload)
        self.store.seal(unknown_handle)
        self.store.append(other_handle, other_payload)
        self.store.seal(other_handle)
        unknown_stage = self.store._stages[unknown_handle]
        other_stage = self.store._stages[other_handle]

        with mock.patch.object(
            anonymous,
            "_linkat_empty_path",
            side_effect=OSError(errno.EIO, "injected"),
        ) as linkat, mock.patch.object(anonymous.os, "link", side_effect=AssertionError("fallback")), \
             mock.patch.object(anonymous.os, "unlink", side_effect=AssertionError("cleanup")):
            with self.assertRaises(anonymous.PublicationUncertain):
                self.store.publish_noreplace(unknown_handle, "unknown.mp3")
            for operation in (
                lambda: self.store.append(other_handle, b"more bytes"),
                lambda: self.store.seal(other_handle),
                lambda: self.store.read_for_verification(other_handle, 0, 1),
                lambda: self.store.publish_noreplace(other_handle, "other.mp3"),
                lambda: self.store.abort_owned_stage(other_handle),
                lambda: self.store.release_owned_stage(other_handle),
                lambda: self.store.begin_owned_stage("target-1"),
            ):
                with self.assertRaisesRegex(anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"):
                    operation()

        self.assertEqual(linkat.call_count, 1)
        self.assertTrue(stat.S_ISREG(os.fstat(unknown_stage.fd).st_mode))
        self.assertTrue(stat.S_ISREG(os.fstat(other_stage.fd).st_mode))
        self.assertTrue(unknown_stage.publication_attempted)
        self.assertFalse(other_stage.publication_attempted)
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.assertEqual(tuple(self.destination.iterdir()), ())

    def test_real_at_empty_path_publication_when_host_privilege_allows_it(self):
        """Require actual AT_EMPTY_PATH publication; permission denial is a failure."""
        payload = b"synthetic local test bytes"
        handle = self._begin_required_stage()
        self.store.append(handle, payload)
        self.store.seal(handle)
        try:
            result = self.store.publish_noreplace(handle, "published.mp3")
        except anonymous.PublicationUncertain as exc:
            if exc.reason in {
                "at_empty_path_capability_required",
                "at_empty_path_unsupported",
                "linkat_unavailable",
                "staging_and_destination_not_linkable",
            }:
                self._force_close_after_ambiguous_for_test()
                self.fail(f"real linkat(AT_EMPTY_PATH) not validated: {exc.reason}")
            raise

        self.assertEqual(result, core.StoreResult.PUBLISHED)
        published = self.destination / "published.mp3"
        self.assertEqual(published.read_bytes(), payload)
        self.assertEqual(stat.S_IMODE(published.stat().st_mode), 0o600)
        self.assertEqual(published.stat().st_nlink, 1)
        self.assertEqual(tuple(self.staging.iterdir()), ())
        self.store.release_owned_stage(handle)

        conflict_handle = self._begin_required_stage()
        self.store.append(conflict_handle, b"second synthetic payload")
        self.store.seal(conflict_handle)
        conflict = self.store.publish_noreplace(conflict_handle, "published.mp3")
        self.assertEqual(conflict, core.StoreResult.CONFLICT)
        self.assertEqual(published.read_bytes(), payload)
        self.store.abort_owned_stage(conflict_handle)

    def test_real_destination_permission_denied_is_frozen_without_publication(self):
        """Real EACCES from linkat: destination directory mode 0555, non-root UID."""
        if os.geteuid() == 0:
            self.fail(
                "real EACCES requires a non-root effective UID; root bypasses directory "
                "write permission (run the anonymous suite as the dedicated runner owner)"
            )
        os.chmod(self.destination, 0o555)
        self.addCleanup(os.chmod, self.destination, 0o700)
        baseline = _tree_entries(self.root)
        handle = self._begin_required_stage()
        payload = b"synthetic destination permission denial"
        self.store.append(handle, payload)
        self.store.seal(handle)
        stage = self.store._stages[handle]
        audit = WriteAudit()
        audit.active = True
        try:
            with self.assertRaises(anonymous.PublicationUncertain) as caught:
                self.store.publish_noreplace(handle, "permission-denied.mp3")
        finally:
            audit.active = False
        self.assertEqual(caught.exception.reason, "destination_permission_denied")
        self.assertEqual(str(caught.exception), "destination_permission_denied")
        self.assertTrue(stage.publication_attempted)
        self.assertFalse(stage.link_created)
        self.assertFalse(stage.publication_confirmed)
        self.assertIs(stat.S_ISREG(os.fstat(stage.fd).st_mode), True)
        self.assertEqual(
            _tree_entries(self.root), baseline,
            "a denied publication must create no directory entry or temporary file",
        )
        self.assertEqual(audit.violations(), [])
        for operation in (
            lambda: self.store.publish_noreplace(handle, "permission-denied.mp3"),
            lambda: self.store.abort_owned_stage(handle),
            lambda: self.store.begin_owned_stage("target-1"),
        ):
            with self.assertRaisesRegex(
                anonymous.PublicationUncertain, "store_blocked_after_unknown_publication"
            ):
                operation()
        with self.assertRaises(anonymous.PublicationUncertain) as close_error:
            self.store.close()
        self.assertEqual(
            close_error.exception.reason, "ambiguous_stage_retained_until_process_exit"
        )

    def test_real_read_only_destination_is_a_frozen_erofs_publication(self):
        """Real EROFS: read-only bind mount of the destination on the same tmpfs."""
        base = self.root / "namespace-erofs-destination"
        base.mkdir(mode=0o700)
        baseline = _tree_entries(self.root)
        result = self._run_namespace_scenario("erofs-destination", base)
        self.assertIs(result["ok"], True, result)
        self.assertIs(result["devices_equal"], True)
        self.assertIs(result["read_only_view_is_read_only"], True)
        self.assertEqual(result["adapter_reason"], "destination_filesystem_read_only")
        self.assertTrue(result["adapter_publication_attempted"])
        self.assertFalse(result["adapter_link_created"])
        self.assertFalse(result["adapter_publication_confirmed"])
        self.assertEqual(
            result["adapter_close_reason"], "ambiguous_stage_retained_until_process_exit"
        )
        self.assertEqual(result["adapter_audit_write_violations"], [])
        self.assertEqual(result["core_outcome"], "commit_unknown")
        self.assertEqual(result["core_reason"], "commit_unknown")
        self.assertTrue(result["core_cleanup_pending"])
        self.assertEqual(result["core_stage_retained"], 1)
        self.assertEqual(
            result["core_store_close_reason"], "ambiguous_stage_retained_until_process_exit"
        )
        self.assertEqual(result["core_audit_write_violations"], [])
        self.assertEqual(result["staging_entries"], [])
        self.assertEqual(result["destination_entries"], [])
        self.assertEqual(result["read_only_view_entries"], [])
        self.assertEqual(_tree_entries(self.root), baseline)

    def test_real_read_only_staging_rejects_o_tmpfile_without_fallback(self):
        """Real EROFS at O_TMPFILE: read-only staging bind mount on the same tmpfs."""
        base = self.root / "namespace-erofs-staging"
        base.mkdir(mode=0o700)
        baseline = _tree_entries(self.root)
        result = self._run_namespace_scenario("erofs-staging", base)
        self.assertIs(result["ok"], True, result)
        self.assertIs(result["staging_view_is_read_only"], True)
        self.assertIs(result["devices_equal"], True)
        self.assertEqual(result["begin_reason"], "o_tmpfile_unavailable")
        self.assertIs(result["begin_error_text_is_fixed"], True)
        self.assertEqual(result["stage_registered"], 0)
        self.assertEqual(result["store_close_reason"], None)
        self.assertEqual(result["audit_write_violations"], [])
        self.assertEqual(result["staging_entries"], [])
        self.assertEqual(result["destination_entries"], [])
        self.assertEqual(_tree_entries(self.root), baseline)

    def test_real_no_space_write_failure_is_fail_closed_and_abortable(self):
        """Real ENOSPC: 64 KiB tmpfs exhausted by the staging write."""
        base = self.root / "namespace-enospc"
        base.mkdir(mode=0o700)
        baseline = _tree_entries(self.root)
        result = self._run_namespace_scenario("enospc", base)
        self.assertIs(result["ok"], True, result)
        self.assertEqual(result["append_reason"], "stage_write_failed")
        self.assertIs(result["append_reason_is_fixed"], True)
        self.assertFalse(result["stage_sealed_after_failure"])
        self.assertFalse(result["stage_publication_attempted"])
        self.assertEqual(result["stage_link_count_after_failure"], 0)
        self.assertEqual(result["audit_write_violations"], [])
        self.assertIs(result["abort_succeeded"], True)
        self.assertIs(result["store_close_succeeded"], True)
        self.assertEqual(result["core_outcome"], "failed")
        self.assertEqual(result["core_reason"], "staging_failed")
        self.assertFalse(result["core_cleanup_pending"])
        self.assertEqual(result["core_store_close_reason"], None)
        self.assertEqual(result["core_audit_write_violations"], [])
        self.assertEqual(result["staging_entries"], [])
        self.assertEqual(result["destination_entries"], [])
        self.assertEqual(_tree_entries(self.root), baseline)


if __name__ == "__main__":
    unittest.main()
