import ast
import importlib.util
import inspect
import json
import os
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "btrfs_socket_path_probe.py"
SPEC = importlib.util.spec_from_file_location("btrfs_socket_path_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BtrfsSocketPathProbeTests(unittest.TestCase):
    def test_non_linux_is_blocked_before_identity_or_filesystem_access(self):
        with mock.patch.object(probe.sys, "platform", "win32"), \
             mock.patch.object(probe, "_passwd_and_groups", side_effect=AssertionError), \
             mock.patch.object(probe.Path, "lstat", side_effect=AssertionError):
            result = probe.run_probe()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("Linux is required", result["reason"])

    def test_non_root_is_blocked_before_local_account_read(self):
        with mock.patch.object(probe.sys, "platform", "linux"), \
             mock.patch.object(probe.os, "geteuid", create=True, return_value=1000), \
             mock.patch.object(probe, "_passwd_and_groups", side_effect=AssertionError):
            result = probe.run_probe()
        self.assertEqual(result["status"], "BLOCKED")
        self.assertIn("as root", result["reason"])

    def test_acl_encoding_matches_the_expected_numeric_entries(self):
        entries = [struct.unpack_from("<HHI", probe.ACL_BYTES, offset)
                   for offset in range(4, len(probe.ACL_BYTES), 8)]
        self.assertEqual(struct.unpack_from("<I", probe.ACL_BYTES)[0], 2)
        self.assertEqual(entries, list(probe.ACL_ENTRIES))

    def test_target_groups_require_primary_users_and_administrators(self):
        parsed = probe._passwd_and_groups
        self.assertEqual(probe.EXPECTED_UID, 1000)
        self.assertEqual(probe.EXPECTED_GID, 1001)
        self.assertEqual(probe.EXPECTED_GROUPS, (1000, 1001))
        self.assertEqual(probe.EXPECTED_EXTRA_GROUPS, (1000,))
        self.assertTrue(callable(parsed))

    def test_child_programs_check_identity_capabilities_and_use_dirfds(self):
        connect = probe._try_connect_child.__code__.co_consts
        rename = probe._rename_child_code()
        plain = probe._run_plain_rename.__code__.co_consts
        connect_source = next(item for item in connect if isinstance(item, str) and "SO_PEERCRED" not in item)
        plain_source = next(item for item in plain if isinstance(item, str) and "getresuid" in item)
        self.assertIn("os.getresuid()", connect_source)
        self.assertIn("CapAmb", connect_source)
        self.assertIn("src_dir_fd=dfd", rename)
        self.assertIn("src_dir_fd=fd", plain_source)
        self.assertIn("os.getresgid()", plain_source)
        self.assertIn('b"fake-service\\n"', rename)
        self.assertIn("SO_PEERCRED", inspect.getsource(probe._create_server))

    def test_rename_child_executes_the_rename_at_module_scope(self):
        source = probe._rename_child_code()
        tree = ast.parse(source)
        compile(tree, "<btrfs-probe-rename-child>", "exec")
        self.assertTrue(any(
            isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "first" for target in node.targets)
            for node in tree.body
        ))

    def test_each_embedded_child_program_compiles(self):
        plain = next(item for item in probe._run_plain_rename.__code__.co_consts
                     if isinstance(item, str) and "getresuid" in item)
        connect = next(item for item in probe._try_connect_child.__code__.co_consts
                       if isinstance(item, str) and "getresuid" in item)
        for name, source in (("rename", probe._rename_child_code()),
                             ("plain-rename", plain), ("connect", connect)):
            with self.subTest(program=name):
                compile(source, f"<btrfs-probe-{name}>", "exec")

    def test_child_launch_is_isolated_and_outer_directory_is_not_removed_by_name(self):
        launch_source = (inspect.getsource(probe._child_argv) + inspect.getsource(probe._run_child)
                         + inspect.getsource(probe._spawn_child))
        self.assertIn('"-I", "-S"', launch_source)
        self.assertIn("pass_fds", launch_source)
        self.assertIn("extra_groups", launch_source)
        self.assertIn("start_new_session=True", launch_source)
        self.assertNotIn("preexec_fn", launch_source)
        run_source = inspect.getsource(probe.run_probe)
        self.assertNotIn("os.rmdir(root_name", run_source)
        self.assertNotIn("_remove_known(base_fd", run_source)

    def test_child_argv_positions_match_a_real_python_c_process(self):
        code = "import json,sys; print(json.dumps(sys.argv))"
        completed = subprocess.run(
            probe._child_argv(code, 17, sys.executable, (1000,)),
            check=True, capture_output=True, text=True, timeout=5,
        )
        actual_argv = json.loads(completed.stdout)
        self.assertEqual(actual_argv[0], "-c")
        self.assertEqual(actual_argv[1:], ["17", "1000", "1001", "1000"])

    def test_failed_0777_positive_control_prevents_top_level_complete(self):
        connect = {"connect": "error", "errno": 13, "identity_verified": True, "fixture_reachable": True}
        rename = {
            "fixture_reachable": True,
            "socket_rename": {"result": "success"},
            "replacement": {"result": "success"},
            "parent_reached_fake_service": "fake-service\n",
        }
        cases = [
            {"name": "parent-0755", "status": "COMPLETE"},
            {"name": "parent-0777", "status": "COMPLETE"},
            {"name": "parent-acl", "status": "NOT RUN"},
        ]
        protected = {"status": "COMPLETE"}
        cases[1]["positive_control"] = (
            "CONFIRMED" if probe._positive_control_confirmed(connect, rename) else "NOT CONFIRMED")
        self.assertEqual(cases[1]["positive_control"], "CONFIRMED", "EACCES is an observed connect result")
        self.assertEqual(probe._overall_status(cases, protected), "COMPLETE")

        for connect_result in ({}, {"connect": "error", "errno": 13},
                               {"connect": "error", "errno": 13, "identity_verified": True,
                                "fixture_reachable": False}):
            cases[1]["positive_control"] = (
                "CONFIRMED" if probe._positive_control_confirmed(connect_result, rename) else "NOT CONFIRMED")
            self.assertEqual(probe._overall_status(cases, protected), "PARTIAL", "connect not completed")

        for failed_test, failed_value in (
                ("socket_rename", {"result": "error"}),
                ("replacement", {"result": "error"}), ("parent_reached_fake_service", "wrong-service\n")):
            connect_result = dict(connect)
            rename_result = dict(rename)
            rename_result[failed_test] = failed_value
            cases[1]["positive_control"] = (
                "CONFIRMED" if probe._positive_control_confirmed(connect_result, rename_result)
                else "NOT CONFIRMED")
            self.assertEqual(probe._overall_status(cases, protected), "PARTIAL", failed_test)

    def test_mountinfo_resolves_a_unique_descriptor_mount_id(self):
        row = "41 25 0:42 / /vol1 rw,relatime - btrfs /dev/mapper/vol1 rw,space_cache=v2"
        result = probe._mount_record_for_id("41", [row])
        self.assertEqual(result["filesystem_type"], "btrfs")
        self.assertEqual(result["device"], "0:42")
        self.assertEqual(result["source"], "/dev/mapper/vol1")
        self.assertEqual(result["mount_id"], "41")

    def test_mount_for_fd_uses_fdinfo_mount_id(self):
        row = "41 25 0:54 / /vol1 rw,relatime - btrfs /dev/mapper/vol1 rw,space_cache=v2"
        with mock.patch.object(probe.Path, "read_text", side_effect=["mnt_id:\t41\n", row]):
            result = probe._mount_for_fd(123)
        self.assertEqual(result["mount_id"], "41")
        self.assertEqual(result["device"], "0:54")
        self.assertEqual(result["filesystem_type"], "btrfs")

    def test_fdinfo_missing_duplicate_and_anomalous_mount_ids_are_reported(self):
        self.assertEqual(probe._parse_fdinfo_mount_id("pos:\t0\n")["status"], "NOT FOUND")
        self.assertEqual(probe._parse_fdinfo_mount_id(
            "mnt_id:\t41\nmnt_id:\t42\n")["status"], "DUPLICATE")
        self.assertEqual(probe._parse_fdinfo_mount_id("mnt_id:\tbad\n")["status"], "ANOMALOUS")

    def test_mountinfo_missing_duplicate_and_malformed_ids_are_not_accepted(self):
        row = "41 25 0:42 / /vol1 rw,relatime - btrfs /dev/mapper/vol1 rw,space_cache=v2"
        self.assertEqual(probe._mount_record_for_id("42", [row])["status"], "NOT FOUND")
        self.assertEqual(probe._mount_record_for_id("41", [row, row])["status"], "DUPLICATE")
        self.assertEqual(probe._mount_record_for_id("41", [row, "malformed"])["status"], "ANOMALOUS")

    def test_mount_admission_uses_btrfs_mount_id_not_st_dev_major_minor(self):
        mount = {"status": "read", "mount_id": "41", "filesystem_type": "btrfs",
                 "device": "0:54"}
        with mock.patch.object(probe, "_mount_for_fd", return_value=mount):
            self.assertEqual(probe._require_btrfs_mount(123), mount)
        base_stat = SimpleNamespace(st_dev=60)
        production = {"device": 55, "identity_verified": True,
                      "filesystem": {"status": "read", "mount_id": "41"}}
        comparison = probe._production_comparison(base_stat, mount, production)
        self.assertEqual(comparison["mount_id"], "same")
        self.assertEqual(comparison["st_dev"], "different")
        self.assertTrue(comparison["same_mount_does_not_prove_same_subvolume"])

    def test_missing_duplicate_or_non_btrfs_mount_blocks_admission(self):
        for mount in (
                {"status": "NOT FOUND"}, {"status": "DUPLICATE"},
                {"status": "ANOMALOUS"},
                {"status": "read", "mount_id": "41", "filesystem_type": "ext4"}):
            with self.subTest(mount=mount), mock.patch.object(probe, "_mount_for_fd", return_value=mount):
                with self.assertRaises(probe.ProbeBlocked):
                    probe._require_btrfs_mount(123)

    def test_production_comparison_uses_unknown_for_missing_metadata(self):
        base_stat = SimpleNamespace(st_dev=60)
        base_mount = {"status": "read", "mount_id": "41"}
        missing = {"status": "NOT FOUND", "filesystem": {"status": "NOT FOUND"}}
        comparison = probe._production_comparison(base_stat, base_mount, missing)
        self.assertEqual(comparison["mount_id"], "unknown")
        self.assertEqual(comparison["st_dev"], "unknown")
        unknown_mount = {"device": 55, "identity_verified": True,
                         "filesystem": {"status": "NOT FOUND"}}
        comparison = probe._production_comparison(base_stat, base_mount, unknown_mount)
        self.assertEqual(comparison["mount_id"], "unknown")
        self.assertEqual(comparison["st_dev"], "different")

    def test_production_st_dev_requires_verified_lstat_fd_identity(self):
        path = Path("/production/path")
        lstat_observation = self._fake_stat(dev=55, ino=90)
        base_stat = SimpleNamespace(st_dev=55)
        base_mount = {"status": "read", "mount_id": "41"}

        with mock.patch.object(probe.Path, "lstat", return_value=lstat_observation), \
             mock.patch.object(probe, "_open_directory_path", side_effect=OSError(2, "missing")):
            failed_open = probe._path_metadata(path)
        self.assertEqual(failed_open["lstat_device_observation"], 55)
        self.assertFalse(failed_open["identity_verified"])
        self.assertIsNone(failed_open["device"])
        self.assertEqual(probe._production_comparison(base_stat, base_mount, failed_open)["st_dev"],
                         "unknown")

        mismatched_open = self._fake_stat(dev=55, ino=91)
        with mock.patch.object(probe.Path, "lstat", return_value=lstat_observation), \
             mock.patch.object(probe, "_open_directory_path", return_value=20), \
             mock.patch.object(probe.os, "fstat", return_value=mismatched_open), \
             mock.patch.object(probe.os, "close") as close_fd, \
             mock.patch.object(probe, "_mount_for_fd") as mount_for_fd:
            mismatched = probe._path_metadata(path)
        self.assertEqual(mismatched["lstat_device_observation"], 55)
        self.assertFalse(mismatched["identity_verified"])
        self.assertIsNone(mismatched["device"])
        self.assertEqual(probe._production_comparison(base_stat, base_mount, mismatched)["st_dev"],
                         "unknown")
        close_fd.assert_called_once_with(20)
        mount_for_fd.assert_not_called()

        matching_open = self._fake_stat(dev=55, ino=90)
        with mock.patch.object(probe.Path, "lstat", return_value=lstat_observation), \
             mock.patch.object(probe, "_open_directory_path", return_value=21), \
             mock.patch.object(probe.os, "fstat", return_value=matching_open), \
             mock.patch.object(probe, "_mount_for_fd", return_value={"status": "NOT FOUND"}), \
             mock.patch.object(probe.os, "close"):
            verified = probe._path_metadata(path)
        self.assertTrue(verified["identity_verified"])
        self.assertEqual(verified["device"], 55)
        self.assertEqual(probe._production_comparison(base_stat, base_mount, verified)["st_dev"],
                         "same")

    @staticmethod
    def _fake_stat(*, dev=7, ino=11, uid=0, gid=0, mode=None):
        return SimpleNamespace(st_dev=dev, st_ino=ino, st_uid=uid, st_gid=gid,
                               st_mode=(stat.S_IFDIR | 0o700) if mode is None else mode)

    def test_open_child_directory_rejects_symlink_and_inode_race(self):
        link = self._fake_stat(mode=stat.S_IFLNK | 0o777)
        with mock.patch.object(probe, "_directory_open_flags", return_value=0), \
             mock.patch.object(probe.os, "stat", return_value=link), \
             mock.patch.object(probe.os, "open") as open_child:
            with self.assertRaises(probe.ProbeBlocked):
                probe._open_child_directory(10, "1000")
            open_child.assert_not_called()

        before = self._fake_stat(dev=7, ino=11)
        opened = self._fake_stat(dev=7, ino=12)
        with mock.patch.object(probe, "_directory_open_flags", return_value=0), \
             mock.patch.object(probe.os, "stat", side_effect=[before, before]), \
             mock.patch.object(probe.os, "open", return_value=20), \
             mock.patch.object(probe.os, "fstat", return_value=opened), \
             mock.patch.object(probe.os, "close") as close_fd:
            with self.assertRaises(probe.ProbeBlocked):
                probe._open_child_directory(10, "1000")
            close_fd.assert_called_once_with(20)

    def test_path_identity_change_blocks_before_mkdir_in_simulated_descriptors(self):
        for changed_component in ("vol1", "1000"):
            with self.subTest(changed_component=changed_component):
                held_vol1 = self._fake_stat(dev=5, ino=10)
                held_base = self._fake_stat(dev=6, ino=20)
                replacement_vol1 = self._fake_stat(dev=5, ino=11)
                replacement_base = self._fake_stat(dev=6, ino=21)
                opened = ([(30, replacement_vol1)] if changed_component == "vol1" else
                          [(30, held_vol1), (31, replacement_base)])
                with mock.patch.object(probe, "_directory_open_flags", return_value=0), \
                     mock.patch.object(probe.os, "fstat", side_effect=[held_vol1, held_base]), \
                     mock.patch.object(probe, "_open_child_directory", side_effect=opened), \
                     mock.patch.object(probe.os, "close"), \
                     mock.patch.object(probe.os, "mkdir") as mkdir:
                    with self.assertRaises(probe.ProbeBlocked):
                        probe._create_experiment_root(1, 2, 3, "probe-root", held_vol1, held_base, "41")
                    mkdir.assert_not_called()

    def test_mount_id_change_blocks_before_mkdir_in_simulated_descriptors(self):
        vol1 = self._fake_stat(dev=5, ino=10)
        base = self._fake_stat(dev=6, ino=20)
        mounts = [
            {"status": "read", "mount_id": "41", "filesystem_type": "btrfs"},
            {"status": "read", "mount_id": "42", "filesystem_type": "btrfs"},
        ]
        with mock.patch.object(probe, "_directory_open_flags", return_value=0), \
             mock.patch.object(probe.os, "fstat", side_effect=[vol1, base]), \
             mock.patch.object(probe, "_open_child_directory",
                               side_effect=[(30, vol1), (31, base)]), \
             mock.patch.object(probe, "_require_btrfs_mount", side_effect=mounts), \
             mock.patch.object(probe.os, "close"), \
             mock.patch.object(probe.os, "mkdir") as mkdir:
            with self.assertRaises(probe.ProbeBlocked):
                probe._create_experiment_root(1, 2, 3, "probe-root", vol1, base, "41")
            mkdir.assert_not_called()

    def test_unverifiable_or_non_btrfs_mount_blocks_before_mkdir(self):
        vol1 = self._fake_stat(dev=5, ino=10)
        base = self._fake_stat(dev=6, ino=20)
        for mount in ({"status": "NOT FOUND"}, {"status": "DUPLICATE"},
                      {"status": "ANOMALOUS"},
                      {"status": "read", "mount_id": "41", "filesystem_type": "ext4"}):
            with self.subTest(mount=mount):
                error = probe.ProbeBlocked("mount validation failed", evidence=mount)
                with mock.patch.object(probe, "_directory_open_flags", return_value=0), \
                     mock.patch.object(probe.os, "fstat", side_effect=[vol1, base]), \
                     mock.patch.object(probe, "_open_child_directory",
                                       side_effect=[(30, vol1), (31, base)]), \
                     mock.patch.object(probe, "_require_btrfs_mount", side_effect=error), \
                     mock.patch.object(probe.os, "close"), \
                     mock.patch.object(probe.os, "mkdir") as mkdir:
                    with self.assertRaises(probe.ProbeBlocked):
                        probe._create_experiment_root(1, 2, 3, "probe-root", vol1, base, "41")
                    mkdir.assert_not_called()

    def test_new_root_identity_mismatch_is_blocked_without_deleting_entry(self):
        base_stat = self._fake_stat(dev=6, ino=20)
        entry_stat = self._fake_stat(dev=6, ino=30)
        opened_stat = self._fake_stat(dev=7, ino=30)
        with mock.patch.object(probe, "_directory_open_flags", return_value=0), \
             mock.patch.object(probe, "_verify_experiment_base"), \
             mock.patch.object(probe.os, "mkdir") as mkdir, \
             mock.patch.object(probe.os, "stat", return_value=entry_stat), \
             mock.patch.object(probe.os, "open", return_value=40), \
             mock.patch.object(probe.os, "fstat", return_value=opened_stat), \
             mock.patch.object(probe.os, "close") as close_fd, \
             mock.patch.object(probe.os, "unlink") as unlink, \
             mock.patch.object(probe.os, "rmdir") as rmdir:
            with self.assertRaises(probe.ProbeBlocked) as raised:
                probe._create_experiment_root(1, 2, 3, "probe-root", base_stat, base_stat, "41")
            mkdir.assert_called_once_with("probe-root", 0o700, dir_fd=3)
            self.assertTrue(raised.exception.created)
            close_fd.assert_called_once_with(40)
            unlink.assert_not_called()
            rmdir.assert_not_called()

    @staticmethod
    def _open_fixture_base(temp_path):
        vol1_path = temp_path / "vol1"
        base_path = vol1_path / "1000"
        base_path.mkdir(parents=True)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(temp_path, flags)
        vol1_fd, vol1_stat = probe._open_child_directory(root_fd, "vol1")
        base_fd, base_stat = probe._open_child_directory(vol1_fd, "1000")
        return root_fd, vol1_fd, base_fd, vol1_stat, base_stat, vol1_path, base_path

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_DIRECTORY")
                         and hasattr(os, "O_NOFOLLOW"), "requires POSIX no-follow dir_fd support")
    def test_path_inode_mismatch_and_symlink_block_before_mkdir(self):
        for use_symlink in (False, True):
            with self.subTest(use_symlink=use_symlink), tempfile.TemporaryDirectory() as temp:
                root_fd, vol1_fd, base_fd, vol1_stat, base_stat, vol1_path, base_path = (
                    self._open_fixture_base(Path(temp)))
                try:
                    moved = vol1_path / "old-1000"
                    os.rename(base_path, moved)
                    if use_symlink:
                        os.symlink(moved, base_path, target_is_directory=True)
                    else:
                        base_path.mkdir()
                    real_mkdir = os.mkdir
                    with mock.patch.object(probe.os, "mkdir", wraps=real_mkdir) as mkdir:
                        with self.assertRaises(probe.ProbeBlocked):
                            probe._create_experiment_root(root_fd, vol1_fd, base_fd, "probe-root",
                                                          vol1_stat, base_stat, "41")
                        mkdir.assert_not_called()
                finally:
                    for fd in (base_fd, vol1_fd, root_fd):
                        os.close(fd)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_DIRECTORY")
                         and hasattr(os, "O_NOFOLLOW"), "requires POSIX no-follow dir_fd support")
    def test_changed_mount_id_blocks_before_mkdir(self):
        with tempfile.TemporaryDirectory() as temp:
            root_fd, vol1_fd, base_fd, vol1_stat, base_stat, _, _ = self._open_fixture_base(Path(temp))
            try:
                mounts = [
                    {"status": "read", "mount_id": "41", "filesystem_type": "btrfs"},
                    {"status": "read", "mount_id": "42", "filesystem_type": "btrfs"},
                ]
                real_mkdir = os.mkdir
                with mock.patch.object(probe, "_mount_for_fd", side_effect=mounts), \
                     mock.patch.object(probe.os, "mkdir", wraps=real_mkdir) as mkdir:
                    with self.assertRaises(probe.ProbeBlocked):
                        probe._create_experiment_root(root_fd, vol1_fd, base_fd, "probe-root",
                                                      vol1_stat, base_stat, "41")
                    mkdir.assert_not_called()
            finally:
                for fd in (base_fd, vol1_fd, root_fd):
                    os.close(fd)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_DIRECTORY")
                         and hasattr(os, "O_NOFOLLOW"), "requires POSIX no-follow dir_fd support")
    def test_new_root_identity_mismatch_is_retained_and_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            root_fd, vol1_fd, base_fd, vol1_stat, base_stat, _, base_path = self._open_fixture_base(Path(temp))
            real_fstat = os.fstat

            def wrong_device(fd):
                info = real_fstat(fd)
                return SimpleNamespace(st_mode=info.st_mode, st_dev=info.st_dev + 1,
                                       st_ino=info.st_ino, st_uid=info.st_uid, st_gid=info.st_gid)

            try:
                with mock.patch.object(probe, "_verify_experiment_base"), \
                     mock.patch.object(probe.os, "fstat", side_effect=wrong_device):
                    with self.assertRaises(probe.ProbeBlocked) as raised:
                        probe._create_experiment_root(root_fd, vol1_fd, base_fd, "probe-root",
                                                      vol1_stat, base_stat, "41")
                self.assertTrue(raised.exception.created)
                self.assertTrue((base_path / "probe-root").is_dir())
            finally:
                for fd in (base_fd, vol1_fd, root_fd):
                    os.close(fd)

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_DIRECTORY"), "requires POSIX dir_fd support")
    def test_cleanup_refuses_a_different_inode(self):
        import tempfile

        with tempfile.TemporaryDirectory() as temp:
            root_fd = os.open(temp, os.O_RDONLY | os.O_DIRECTORY)
            try:
                path = Path(temp) / "entry"
                path.write_text("owned test fixture", encoding="utf-8")
                identity = {"ino": path.stat().st_ino + 1, "uid": os.getuid(),
                            "type": "socket", "dev": path.stat().st_dev}
                self.assertFalse(probe._remove_known(root_fd, "entry", identity))
                self.assertTrue(path.exists())
            finally:
                os.close(root_fd)


if __name__ == "__main__":
    unittest.main()
