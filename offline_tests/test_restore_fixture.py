"""Synthetic archive checks for scripts/restore_fixture.py (no NAS access)."""

from __future__ import annotations

import hashlib
import gzip
import importlib.util
import io
import json
import os
import stat
import sys
import tarfile
import tempfile
import tracemalloc
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restore_fixture.py"
SPEC = importlib.util.spec_from_file_location("restore_fixture", SCRIPT)
restore = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = restore
SPEC.loader.exec_module(restore)


def archive_bytes(entries, *, pax=False):
    output = io.BytesIO()
    fmt = tarfile.PAX_FORMAT if pax else tarfile.USTAR_FORMAT
    with tarfile.open(fileobj=output, mode="w:gz", format=fmt) as tf:
        for name, kind, data, metadata in entries:
            member = tarfile.TarInfo(name)
            member.type = kind
            if metadata:
                for key, value in metadata.items():
                    setattr(member, key, value)
            if kind in restore.REGULAR_TYPES:
                member.size = len(data)
                tf.addfile(member, io.BytesIO(data))
            else:
                tf.addfile(member)
    return output.getvalue()


def scan(data):
    fd, name = tempfile.mkstemp(prefix="fnmusic-restore-fixture-")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        with open(name, "rb") as stream:
            return restore.scan_archive_fd(stream.fileno())
    finally:
        os.unlink(name)


class RestoreFixtureArchiveTests(unittest.TestCase):
    def _assert_scan_refused_without_decode(self, data):
        fd, name = tempfile.mkstemp(prefix="fnmusic-restore-fixture-")
        try:
            os.write(fd, data)
            start = os.lseek(fd, 0, os.SEEK_CUR)
            tracemalloc.start()
            try:
                with mock.patch.object(restore.tarfile, "open",
                                       side_effect=AssertionError("tar decoder invoked")) as opened:
                    with self.assertRaises(restore.FixtureError) as caught:
                        restore.scan_archive_fd(fd)
                    _current, peak = tracemalloc.get_traced_memory()
                opened.assert_not_called()
            finally:
                tracemalloc.stop()
            self.assertEqual(caught.exception.code, "archive_scan_isolation_unproven")
            self.assertEqual(os.lseek(fd, 0, os.SEEK_CUR), start,
                             "refusal must consume zero compressed bytes")
            self.assertLess(peak, 512 * 1024,
                            "refusal must not allocate based on expanded archive headers")
        finally:
            os.close(fd)
            os.unlink(name)

    def test_compressed_gzip_bomb_is_refused_before_decompression(self):
        data = gzip.compress(b"x" * (8 * 1024 * 1024), compresslevel=9, mtime=0)
        self.assertLess(len(data), 64 * 1024)
        self._assert_scan_refused_without_decode(data)

    def test_eight_mib_pax_extended_header_is_refused_before_tarfile_allocation(self):
        data = archive_bytes([
            ("entry", tarfile.REGTYPE, b"x", {
                "pax_headers": {"comment": "x" * (8 * 1024 * 1024)},
            }),
        ], pax=True)
        self.assertLess(len(data), 64 * 1024,
                        "fixture must be a small compressed input with a large PAX body")
        self._assert_scan_refused_without_decode(data)

    def test_normal_archive_scan_is_also_refused_until_isolation_is_proven(self):
        self._assert_scan_refused_without_decode(
            archive_bytes([("settings.json", tarfile.REGTYPE, b"{}", {})])
        )

    def test_archive_inode_or_metadata_change_is_rejected(self):
        data = archive_bytes([("entry", tarfile.REGTYPE, b"x", {})])
        fd, name = tempfile.mkstemp(prefix="fnmusic-restore-fixture-")
        try:
            os.write(fd, data)
            initial = os.fstat(fd)
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, b"changed")
            with self.assertRaises(restore.FixtureError):
                restore._verify_archive_identity(fd, initial, hashlib.sha256(data).hexdigest())
        finally:
            os.close(fd)
            os.unlink(name)


class RestoreFixtureBoundaryTests(unittest.TestCase):
    def test_restore_refuses_before_any_archive_or_destination_io(self):
        # The guard is deliberately platform-independent: even malformed or
        # special-file paths must be refused before any filesystem operation.
        with mock.patch.object(restore.os, "open") as open_path, \
                mock.patch.object(restore.os, "fstat") as fstat, \
                mock.patch.object(restore.os, "read") as read, \
                mock.patch.object(restore.os, "lseek") as lseek, \
                mock.patch.object(restore, "_verify_destination_parent") as verify_parent, \
                mock.patch.object(restore, "_secure_open_path") as secure_open, \
                mock.patch.object(restore, "_hash_fd") as hash_fd, \
                mock.patch.object(restore, "scan_archive_fd") as scan_archive, \
                mock.patch.object(restore, "_verify_archive_identity"), \
                mock.patch.object(restore.os, "close"), \
                mock.patch.object(restore.os, "mkdir") as mkdir, \
                mock.patch.object(restore, "_check_tmpfs_capacity") as capacity, \
                mock.patch.object(restore, "_verify_low_privilege_boundary") as probe:
            with self.assertRaises(restore.FixtureError) as caught:
                restore.restore_fixture("/private/archive.fifo", "\x00/run", "not-a-digest")

        self.assertEqual(caught.exception.code, "archive_scan_isolation_unproven")
        self.assertEqual(caught.exception.details, {
            "archive_scan": "not_run",
            "fixture_created": False,
        })
        open_path.assert_not_called()
        fstat.assert_not_called()
        read.assert_not_called()
        lseek.assert_not_called()
        verify_parent.assert_not_called()
        secure_open.assert_not_called()
        hash_fd.assert_not_called()
        scan_archive.assert_not_called()
        mkdir.assert_not_called()
        capacity.assert_not_called()
        probe.assert_not_called()

    def test_file_path_helper_is_disabled_before_open_on_all_platforms(self):
        # Regular files, FIFOs, and device paths all use the same disabled
        # entry. This synthetic test is safe to run on Windows and proves the
        # helper cannot reach a blocking O_RDONLY open in this snapshot.
        for path in ("/private/archive.tar.gz", "/private/archive.fifo", "/dev/null"):
            with self.subTest(path=path), mock.patch.object(restore.os, "open") as open_path:
                with self.assertRaises(restore.FixtureError) as caught:
                    restore._secure_open_path(path, directory=False)
                self.assertEqual(caught.exception.code, "archive_scan_isolation_unproven")
                open_path.assert_not_called()

    def test_destination_is_exact_run_mount_only(self):
        with self.assertRaises(restore.FixtureError) as caught:
            restore._verify_destination_parent("/vol1/1000/fnmusic-test")
        self.assertEqual(caught.exception.code, "destination_must_be_run")

    def test_mountinfo_parser_decodes_mountpoint_escapes(self):
        row = "44 25 0:36 / /run/test\\040fixture rw,nosuid,nodev - tmpfs tmpfs rw,size=1m\n"
        parsed = restore._parse_mountinfo_line(row)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["mount_id"], 44)
        self.assertEqual(parsed["root"], "/")
        self.assertEqual(parsed["mountpoint"], "/run/test fixture")
        self.assertEqual(parsed["filesystem"], "tmpfs")

    def test_run_mount_requires_root_tmpfs_and_safe_mount_options(self):
        good = {"mountpoint": "/run", "root": "/", "filesystem": "tmpfs",
                "mount_options": {"rw", "nosuid", "nodev"}, "super_options": set()}
        restore._verify_run_mount(good)
        for field, value, code in (
            ("filesystem", "btrfs", "parent_not_private_tmpfs"),
            ("mountpoint", "/run/child", "parent_not_private_tmpfs"),
            ("root", "/subtree", "parent_not_private_tmpfs"),
            ("mount_options", {"ro", "nosuid", "nodev"}, "parent_mount_options_unsafe"),
            ("mount_options", {"rw", "nodev"}, "parent_mount_options_unsafe"),
        ):
            with self.subTest(field=field, value=value):
                unsafe = dict(good)
                unsafe[field] = value
                with self.assertRaises(restore.FixtureError) as caught:
                    restore._verify_run_mount(unsafe)
                self.assertEqual(caught.exception.code, code)
        ro_mount = dict(good)
        ro_mount["mount_options"] = {"ro", "nosuid", "nodev"}
        ro_mount["super_options"] = {"rw"}
        with self.assertRaises(restore.FixtureError) as caught:
            restore._verify_run_mount(ro_mount)
        self.assertEqual(caught.exception.code, "parent_mount_options_unsafe")

    def test_tmpfs_budget_preserves_bytes_metadata_and_inode_reserve(self):
        values = SimpleNamespace(f_frsize=4096, f_bsize=4096, f_bavail=8,
                                 f_favail=6, f_files=1000)
        with mock.patch.object(restore, "RUN_SERVICE_RESERVE_BYTES", 8192), \
                mock.patch.object(restore, "RUN_SERVICE_RESERVE_INODES", 2), \
                mock.patch.object(restore.os, "fstatvfs", return_value=values, create=True):
            restore._check_tmpfs_capacity(42, [4097], directory_count=2, fixture_count=1)

        for field, value, expected_code in (
            ("f_bavail", 7, "tmpfs_service_reserve_bytes"),
            ("f_favail", 5, "tmpfs_service_reserve_inodes"),
        ):
            with self.subTest(field=field):
                insufficient = SimpleNamespace(**vars(values))
                setattr(insufficient, field, value)
                with mock.patch.object(restore, "RUN_SERVICE_RESERVE_BYTES", 8192), \
                        mock.patch.object(restore, "RUN_SERVICE_RESERVE_INODES", 2), \
                        mock.patch.object(restore.os, "fstatvfs", return_value=insufficient,
                                          create=True):
                    with self.assertRaises(restore.FixtureError) as caught:
                        restore._check_tmpfs_capacity(42, [4097], directory_count=2,
                                                      fixture_count=1)
                self.assertEqual(caught.exception.code, expected_code)

    def test_tmpfs_budget_fails_closed_when_statvfs_is_unavailable(self):
        with mock.patch.object(restore.os, "fstatvfs", create=True,
                               side_effect=OSError("unavailable")):
            with self.assertRaises(restore.FixtureError) as caught:
                restore._check_tmpfs_capacity(42, [1])
        self.assertEqual(caught.exception.code, "tmpfs_space_unavailable")

    def test_runtime_space_guard_runs_before_each_write_block(self):
        remaining = []

        def block_guard(amount):
            remaining.append(amount)
            raise restore.FixtureError("tmpfs_service_reserve_bytes")

        with mock.patch.object(restore.os, "write") as write:
            with self.assertRaises(restore.FixtureError) as caught:
                restore._write_stream(io.BytesIO(b"next block"), 42, 10,
                                      before_block=block_guard)
        self.assertEqual(caught.exception.code, "tmpfs_service_reserve_bytes")
        self.assertEqual(remaining, [10])
        write.assert_not_called()

    def test_probe_identity_uses_nss_primary_and_full_supplementary_groups(self):
        account = SimpleNamespace(pw_uid=1000, pw_name="kurumi", pw_gid=1001)
        fake_pwd = types.ModuleType("pwd")
        fake_pwd.getpwuid = lambda uid: account
        fake_pwd.getpwnam = lambda username: account
        with mock.patch.dict(sys.modules, {"pwd": fake_pwd}), \
                mock.patch.object(restore.os, "getgrouplist", return_value=[1000, 1001],
                                  create=True):
            identity = restore._resolve_probe_identity(1000)
        self.assertEqual(identity.uid, 1000)
        self.assertEqual(identity.primary_gid, 1001)
        self.assertEqual(identity.groups, (1000, 1001))

    def test_probe_identity_fails_closed_when_account_or_groups_are_incomplete(self):
        account = SimpleNamespace(pw_uid=1000, pw_name="kurumi", pw_gid=1001)
        changed = SimpleNamespace(pw_uid=1000, pw_name="kurumi", pw_gid=1002)
        fake_pwd = types.ModuleType("pwd")
        calls = iter((account, changed))
        fake_pwd.getpwuid = lambda uid: next(calls)
        fake_pwd.getpwnam = lambda username: account
        with mock.patch.dict(sys.modules, {"pwd": fake_pwd}):
            with mock.patch.object(restore.os, "getgrouplist", return_value=[1000, 1001],
                                  create=True):
                with self.assertRaises(restore.FixtureError) as caught:
                    restore._resolve_probe_identity(1000)
        self.assertEqual(caught.exception.code, "probe_account_unavailable")

        fake_pwd.getpwuid = lambda uid: account
        with mock.patch.dict(sys.modules, {"pwd": fake_pwd}):
            with mock.patch.object(restore.os, "getgrouplist", return_value=[1000],
                                  create=True):
                with self.assertRaises(restore.FixtureError) as caught:
                    restore._resolve_probe_identity(1000)
        self.assertEqual(caught.exception.code, "probe_account_unavailable")

    def test_proc_probe_requires_exact_real_uid_gid_groups_and_no_capabilities(self):
        identity = restore.ProbeIdentity(1000, "kurumi", 1001, (1000, 1001))
        status = """Uid:\t1000\t1000\t1000\t1000
Gid:\t1001\t1001\t1001\t1001
Groups:\t1000 1001
CapEff:\t0000000000000000
CapPrm:\t0000000000000000
CapInh:\t0000000000000000
CapAmb:\t0000000000000000
NoNewPrivs:\t1
"""
        with mock.patch("builtins.open", mock.mock_open(read_data=status)):
            self.assertTrue(restore._probe_status_matches(identity))
        with mock.patch("builtins.open", mock.mock_open(
                read_data=status.replace("Groups:\t1000 1001", "Groups:\t1001"))):
            self.assertFalse(restore._probe_status_matches(identity))

    def test_acl_check_rejects_acl_and_fails_closed_when_unreadable(self):
        with mock.patch.object(restore.os, "listxattr", create=True,
                               return_value=["system.posix_acl_default"]):
            with self.assertRaises(restore.FixtureError) as caught:
                restore._reject_posix_acls(42, "acl_unverified")
        self.assertEqual(caught.exception.code, "acl_unverified")

        with mock.patch.object(restore.os, "listxattr", create=True,
                               side_effect=OSError("unavailable")):
            with self.assertRaises(restore.FixtureError) as caught:
                restore._reject_posix_acls(42, "acl_unverified")
        self.assertEqual(caught.exception.code, "acl_unverified")

    def test_reopened_destination_hash_mismatch_blocks_success(self):
        info = SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_dev=1, st_ino=2,
                               st_uid=0, st_gid=0, st_size=3,
                               st_mtime_ns=4, st_ctime_ns=5)
        after = SimpleNamespace(**vars(info))
        with mock.patch.object(restore.os, "open", return_value=42) as open_file, \
                mock.patch.object(restore.os, "O_NOFOLLOW", 0, create=True), \
                mock.patch.object(restore.os, "O_CLOEXEC", 0, create=True), \
                mock.patch.object(restore.os, "fstat", side_effect=[info, after]), \
                mock.patch.object(restore.os, "close"), \
                mock.patch.object(restore, "_reject_posix_acls"), \
                mock.patch.object(restore, "_hash_fd", return_value="0" * 64):
            with self.assertRaises(restore.FixtureError) as caught:
                restore._verify_file_at(7, "settings.json", info,
                                        (3, "1" * 64))
        self.assertEqual(caught.exception.code, "fixture_readback_mismatch")
        self.assertEqual(open_file.call_args.kwargs["dir_fd"], 7)
        self.assertEqual(open_file.call_args.args[1],
                         os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                         | getattr(os, "O_CLOEXEC", 0))

    def test_actual_readback_detects_changed_bytes_on_linux_as_root(self):
        if (sys.platform != "linux" or os.geteuid() != 0
                or os.open not in getattr(os, "supports_dir_fd", set())):
            self.skipTest("requires Linux root and openat dir_fd support")
        expected_bytes = b"verified fixture bytes"
        with tempfile.TemporaryDirectory(prefix="fnmusic-readback-") as directory:
            parent_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                fd = os.open("settings.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                             0o600, dir_fd=parent_fd)
                os.write(fd, expected_bytes)
                os.fsync(fd)
                written_info = os.fstat(fd)
                os.close(fd)
                digest = hashlib.sha256(expected_bytes).hexdigest()
                restore._verify_file_at(parent_fd, "settings.json", written_info,
                                        (len(expected_bytes), digest))

                fd = os.open("settings.json", os.O_WRONLY | os.O_TRUNC, dir_fd=parent_fd)
                os.write(fd, b"altered fixture bytes")
                os.fsync(fd)
                os.close(fd)
                with self.assertRaises(restore.FixtureError) as caught:
                    restore._verify_file_at(parent_fd, "settings.json", written_info,
                                            (len(expected_bytes), digest))
                self.assertEqual(caught.exception.code, "fixture_readback_mismatch")
            finally:
                os.close(parent_fd)

    def test_low_privilege_probe_fails_closed_off_linux(self):
        with mock.patch.object(restore.sys, "platform", "win32"):
            with self.assertRaises(restore.FixtureError) as caught:
                restore._verify_low_privilege_boundary(1, 2, "fixture")
        self.assertEqual(caught.exception.code, "permission_probe_unavailable")

    def test_io_errors_return_redacted_blocked_result(self):
        output = io.StringIO()
        with mock.patch.object(restore.sys, "argv", [
                "restore_fixture.py", "--archive", "private-backup.tar.gz",
                "--parent", "/run", "--sha256", "a" * 64]), \
                mock.patch.object(restore, "restore_fixture", side_effect=OSError("private path")), \
                mock.patch("sys.stdout", output):
            self.assertEqual(restore.main(), 1)
        self.assertEqual(json.loads(output.getvalue()),
                         {"reason": "io_error", "status": "BLOCKED"})
        self.assertNotIn("private", output.getvalue())

    def test_main_reports_only_fixed_archive_scan_block(self):
        output = io.StringIO()
        with mock.patch.object(restore.sys, "argv", [
                "restore_fixture.py", "--archive", "/private/secret.tar.gz",
                "--parent", "/run", "--sha256", "a" * 64]), \
                mock.patch.object(restore.os, "open") as open_path, \
                mock.patch.object(restore.os, "fstat") as fstat, \
                mock.patch.object(restore.os, "read") as read, \
                mock.patch.object(restore, "_hash_fd") as hash_fd, \
                mock.patch("sys.stdout", output):
            self.assertEqual(restore.main(), 1)
        result = json.loads(output.getvalue())
        self.assertEqual(result, {
            "status": "BLOCKED", "reason": "archive_scan_isolation_unproven",
            "archive_scan": "not_run", "fixture_created": False,
        })
        self.assertNotIn("secret", output.getvalue())
        open_path.assert_not_called()
        fstat.assert_not_called()
        read.assert_not_called()
        hash_fd.assert_not_called()


if __name__ == "__main__":
    unittest.main()
