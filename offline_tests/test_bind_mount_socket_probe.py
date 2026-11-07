import errno
import importlib.util
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "bind_mount_socket_probe.py"
SPEC = importlib.util.spec_from_file_location("bind_mount_socket_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class BindMountSocketProbeTests(unittest.TestCase):
    def test_namespace_identity_follows_magic_link_object(self):
        parent_stat = SimpleNamespace(st_dev=7, st_ino=100)
        child_stat = SimpleNamespace(st_dev=7, st_ino=101)
        with mock.patch.object(probe.os, "stat", side_effect=[parent_stat, child_stat]) as stat_call:
            parent = probe._namespace_identity("/proc/self/ns/mnt")
            child = probe._namespace_identity("/proc/123/ns/mnt")
        self.assertEqual(parent, {"device": 7, "inode": 100})
        self.assertEqual(child, {"device": 7, "inode": 101})
        self.assertTrue(probe._namespace_changed(parent, child))
        self.assertEqual(stat_call.call_args_list, [
            mock.call("/proc/self/ns/mnt", follow_symlinks=True),
            mock.call("/proc/123/ns/mnt", follow_symlinks=True),
        ])

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/proc/self/ns/mnt").exists(),
                         "requires Linux procfs mount namespace handles")
    def test_real_child_inherits_same_mount_namespace_identity(self):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            parent_identity = probe._namespace_identity()
            child_identity = probe._namespace_identity(f"/proc/{child.pid}/ns/mnt")
            self.assertEqual(parent_identity, child_identity)
        finally:
            child.terminate()
            child.wait(timeout=3)

    @unittest.skipUnless(sys.platform.startswith("linux") and hasattr(os, "O_PATH")
                         and Path("/proc/self/fd").exists(),
                         "requires Linux O_PATH and procfs fd magic links")
    def test_procfd_directory_path_uses_held_directory_after_rename(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            held_path = root / "held"
            replacement_path = root / "case"
            held_path.mkdir()
            fd = os.open(held_path, probe._dir_flags())
            try:
                identity = probe._identity(os.fstat(fd))
                held_path.rename(replacement_path)
                replacement_path.mkdir()
                resolved = probe._fd_directory_path(fd, identity)
                self.assertEqual(resolved, f"/proc/self/fd/{fd}")
                self.assertEqual(os.stat(resolved), os.fstat(fd))
                with self.assertRaises(probe.ProbeBlocked):
                    probe._fd_directory_path(fd, {**identity, "inode": identity["inode"] + 1})
            finally:
                os.close(fd)

    @unittest.skipUnless(sys.platform.startswith("linux") and getattr(os, "geteuid", lambda: -1)() == 0
                         and hasattr(os, "O_PATH") and Path("/proc/self/fd").exists(),
                         "requires root, Linux Unix sockets, and procfs fd magic links")
    def test_root_socket_bind_stays_in_held_directory_after_ancestor_replacement(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case_path = root / "case"
            moved_path = root / "moved-case"
            case_path.mkdir()
            directory_fd = os.open(case_path, probe._dir_flags())
            listener = server = None
            try:
                directory_identity = probe._identity(os.fstat(directory_fd))
                case_path.rename(moved_path)
                case_path.mkdir()
                listener, socket_identity = probe._bind_root_socket(
                    directory_fd, "probe.sock", directory_identity)
                self.assertTrue((moved_path / "probe.sock").exists())
                self.assertFalse((case_path / "probe.sock").exists())
                server = probe.RootSocketServer(listener, probe.ROOT_RESPONSE)
                server.start()
                result = probe._root_client_at(
                    directory_fd, "probe.sock", probe.ROOT_RESPONSE,
                    directory_identity, socket_identity)
                self.assertTrue(probe._root_client_valid(result))
            finally:
                if server is not None:
                    server.close()
                elif listener is not None:
                    listener.close()
                try:
                    info = os.stat("probe.sock", dir_fd=directory_fd, follow_symlinks=False)
                    if probe._same_object(info, socket_identity):
                        os.unlink("probe.sock", dir_fd=directory_fd)
                except (OSError, UnboundLocalError):
                    pass
                os.close(directory_fd)

    def test_namespace_must_be_distinct_and_recursively_private(self):
        parent = {"device": 1, "inode": 10}
        child = {"device": 1, "inode": 11}
        private = ["40 25 0:1 / / rw - ext4 /dev/root rw"]
        shared = ["40 25 0:1 / / rw shared:1 - ext4 /dev/root rw"]
        with (
            mock.patch.object(probe, "_namespace_identity", return_value=child),
            mock.patch.object(probe, "_read_mountinfo", return_value=private),
        ):
            self.assertEqual(probe._verify_worker_namespace(123, parent)["status"], "PRIVATE")
        with (
            mock.patch.object(probe, "_namespace_identity", return_value=parent),
            mock.patch.object(probe, "_read_mountinfo", return_value=private),
        ):
            self.assertEqual(probe._verify_worker_namespace(123, parent)["status"], "BLOCKED")
        with (
            mock.patch.object(probe, "_namespace_identity", return_value=child),
            mock.patch.object(probe, "_read_mountinfo", return_value=shared),
        ):
            self.assertEqual(probe._verify_worker_namespace(123, parent)["status"], "BLOCKED")
        self.assertEqual(probe._propagation_report(["malformed"])["status"], "BLOCKED")

    def test_worker_precondition_failures_stop_before_any_bind(self):
        parent = {"device": 1, "inode": 10}
        with (
            mock.patch.object(probe.os, "geteuid", return_value=0, create=True),
            mock.patch.object(
                probe, "_call_unshare",
                side_effect=OSError(errno.EPERM, "unshare denied"),
            ),
            mock.patch.object(probe, "_make_mounts_private") as make_private,
            mock.patch.object(probe, "_mount_bind") as bind,
            mock.patch.object(probe, "_emit"),
        ):
            self.assertEqual(probe._worker_case({}, parent), 2)
        make_private.assert_not_called()
        bind.assert_not_called()

        child = {"device": 1, "inode": 11}
        shared = ["40 25 0:1 / / rw master:1 - ext4 /dev/root rw"]
        with (
            mock.patch.object(probe.os, "geteuid", return_value=0, create=True),
            mock.patch.object(probe, "_call_unshare"),
            mock.patch.object(probe, "_namespace_identity", return_value=child),
            mock.patch.object(probe, "_make_mounts_private"),
            mock.patch.object(probe, "_read_mountinfo", return_value=shared),
            mock.patch.object(probe, "_mount_bind") as bind,
            mock.patch.object(probe, "_emit"),
        ):
            self.assertEqual(probe._worker_case({}, parent), 2)
        bind.assert_not_called()

    def test_bind_failure_is_reported_without_fallback(self):
        libc = SimpleNamespace(mount=mock.Mock(return_value=-1))
        with (
            mock.patch.object(probe.ctypes, "CDLL", return_value=libc),
            mock.patch.object(probe.ctypes, "get_errno", return_value=errno.EPERM),
        ):
            with self.assertRaises(OSError) as caught:
                probe._mount_bind(
                    "/proc/self/fd/5/source.sock",
                    "/proc/self/fd/6/target.sock",
                )
        self.assertEqual(caught.exception.errno, errno.EPERM)
        libc.mount.assert_called_once_with(
            b"/proc/self/fd/5/source.sock",
            b"/proc/self/fd/6/target.sock",
            None, 4096, None,
        )
        with mock.patch.object(probe.ctypes, "CDLL") as cdll:
            with self.assertRaises(probe.ProbeBlocked):
                probe._mount_bind(
                    "/tmp/not-a-dirfd-path",
                    "/proc/self/fd/6/target.sock",
                )
        cdll.assert_not_called()

    def test_target_identity_requires_exact_uid_gid_groups_and_zero_caps(self):
        caps = {
            key: "0000000000000000"
            for key in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")
        }
        caps["NoNewPrivs"] = "1"
        valid = {
            "verified": True,
            "uid": [1000, 1000, 1000],
            "gid": [1001, 1001, 1001],
            "groups": [1000],
            "capabilities_zero": True,
            "capabilities": caps,
        }
        self.assertTrue(probe._target_identity_verified(valid))
        for changed in (
            {**valid, "uid": [1000, 0, 1000]},
            {**valid, "gid": [1001, 0, 1001]},
            {**valid, "groups": [1000, 1001]},
            {**valid, "capabilities_zero": False},
            {
                **valid,
                "capabilities": {**caps, "CapEff": "0000000000000001"},
            },
            {**valid, "verified": False},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(probe._target_identity_verified(changed))

        fixture_identity = {
            "device": 1, "inode": 2, "uid": 0, "gid": 0,
            "type": "socket", "mode": 0o600,
        }
        access_result = {
            "identity": valid,
            "fixture_identity": fixture_identity,
            "fixture_access": {
                "method": "inherited-directory-fd",
                "directory_fd_identity_verified": True,
                "production_path_traversal_verified": False,
            },
        }
        self.assertTrue(probe._target_observation_valid(access_result, fixture_identity))
        for changed_access in (
            {**access_result, "fixture_access": {**access_result["fixture_access"],
                                                   "directory_fd_identity_verified": False}},
            {**access_result, "fixture_access": {**access_result["fixture_access"],
                                                   "production_path_traversal_verified": True}},
            {**access_result, "fixture_access": {**access_result["fixture_access"],
                                                   "method": "ordinary-path"}},
        ):
            self.assertFalse(probe._target_observation_valid(changed_access, fixture_identity))

    def test_user_owned_btrfs_parent_blocks_before_any_fixture_or_mount_mutation(self):
        root_info = SimpleNamespace(st_dev=1, st_ino=1, st_uid=0, st_gid=0,
                                    st_mode=probe.stat.S_IFDIR | 0o755)
        run_info = SimpleNamespace(st_dev=2, st_ino=2, st_uid=0, st_gid=0,
                                   st_mode=probe.stat.S_IFDIR | 0o755)
        vol_info = SimpleNamespace(st_dev=3, st_ino=3, st_uid=0, st_gid=0,
                                   st_mode=probe.stat.S_IFDIR | 0o755)
        base_info = SimpleNamespace(st_dev=4, st_ino=4, st_uid=probe.TARGET_UID,
                                    st_gid=probe.TARGET_GID,
                                    st_mode=probe.stat.S_IFDIR | 0o770)
        fd_info = {10: root_info, 11: run_info, 12: vol_info, 13: base_info}
        opened_children = {
            "run": (11, run_info), "vol1": (12, vol_info), "1000": (13, base_info),
        }

        def stat_child(name, *, dir_fd, follow_symlinks):
            self.assertFalse(follow_symlinks)
            if (dir_fd, name) == (10, "run"):
                return run_info
            if (dir_fd, name) == (10, "vol1"):
                return vol_info
            if (dir_fd, name) == (12, "1000"):
                return base_info
            raise AssertionError(f"unexpected path lookup: {dir_fd}/{name}")

        def no_parent_acl(_fd, _attr):
            raise OSError(errno.ENODATA, "no ACL xattr")

        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(probe.sys, "platform", "linux"))
            stack.enter_context(mock.patch.object(probe.os, "geteuid", return_value=0, create=True))
            stack.enter_context(mock.patch.object(probe, "_local_target_groups",
                                                  return_value=probe.TARGET_GROUPS))
            stack.enter_context(mock.patch.object(probe, "_trusted_python", return_value="/usr/bin/python3"))
            stack.enter_context(mock.patch.object(probe, "_trusted_script", return_value="/root/probe.py"))
            stack.enter_context(mock.patch.object(probe.os, "open", return_value=10))
            stack.enter_context(mock.patch.object(probe, "_dir_flags", return_value=0))
            stack.enter_context(mock.patch.object(probe.os, "fstat", side_effect=lambda fd: fd_info[fd]))
            stack.enter_context(mock.patch.object(
                probe, "_open_child_dir",
                side_effect=lambda _fd, name: opened_children[name]))
            stack.enter_context(mock.patch.object(probe.os, "stat", side_effect=stat_child))
            stack.enter_context(mock.patch.object(probe.os, "getxattr",
                                                  side_effect=no_parent_acl, create=True))
            stack.enter_context(mock.patch.object(probe.os, "close"))
            require_mount = stack.enter_context(mock.patch.object(probe, "_require_mount"))
            worker = stack.enter_context(mock.patch.object(probe, "_run_lab"))
            namespace_worker = stack.enter_context(mock.patch.object(probe, "_run_mounted_phase"))
            mount_bind = stack.enter_context(mock.patch.object(probe, "_mount_bind"))
            mkdir = stack.enter_context(mock.patch.object(probe.os, "mkdir"))
            chmod = stack.enter_context(mock.patch.object(probe.os, "fchmod"))
            setxattr = stack.enter_context(mock.patch.object(probe.os, "setxattr", create=True))
            removexattr = stack.enter_context(mock.patch.object(probe.os, "removexattr", create=True))
            unlink = stack.enter_context(mock.patch.object(probe.os, "unlink"))
            rmdir = stack.enter_context(mock.patch.object(probe.os, "rmdir"))
            clear_acl = stack.enter_context(mock.patch.object(probe, "_clear_acl"))
            socket_create = stack.enter_context(mock.patch.object(probe.socket, "socket"))
            report = probe.run_probe()

        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["failure_stage"], "preflight")
        self.assertIn("1000 is not root:root", report["reason"])
        self.assertEqual(report["cleanup"]["status"], "NOT RUN")
        require_mount.assert_not_called()
        worker.assert_not_called()
        namespace_worker.assert_not_called()
        mount_bind.assert_not_called()
        mkdir.assert_not_called()
        chmod.assert_not_called()
        setxattr.assert_not_called()
        removexattr.assert_not_called()
        unlink.assert_not_called()
        rmdir.assert_not_called()
        clear_acl.assert_not_called()
        socket_create.assert_not_called()

    def test_original_and_private_namespace_results_are_independent(self):
        original = [{"name": "original", "status": "COMPLETE"}]
        private = [{"name": "private", "status": "COMPLETE"}]
        self.assertEqual(
            probe._overall_status(
                original, private, "CONFIRMED", "COMPLETE", "COMPLETE"
            ),
            "COMPLETE",
        )
        self.assertEqual(
            probe._overall_status(
                original, [{"name": "private", "status": "PARTIAL"}],
                "CONFIRMED", "COMPLETE", "COMPLETE",
            ),
            "PARTIAL",
        )
        self.assertEqual(
            probe._overall_status(
                original, [{"name": "private", "status": "UNSAFE_OBSERVED"}],
                "CONFIRMED", "COMPLETE", "COMPLETE",
            ),
            "UNSAFE_OBSERVED",
        )

    def test_private_view_rechecks_root_service_after_every_operation(self):
        directory_identity = {
            "device": 1, "inode": 10, "uid": 0, "gid": 0,
            "type": "directory", "mode": 0o755,
        }
        original_identity = {
            "device": 1, "inode": 11, "uid": 0, "gid": 0,
            "type": "socket", "mode": 0o600,
        }
        root_client = {
            "result": "success",
            "peer_uid": 0,
            "peer_gid": 0,
            "response": "root-fixture-service\n",
            "verified": True,
        }
        fake_client = {
            "result": "success",
            "peer_uid": 1000,
            "peer_gid": 1001,
            "response": "fake-fixture-service\n",
        }
        fake_server = {
            "peer_uid": 0,
            "peer_gid": 0,
            "response": "fake-fixture-service\n",
        }
        failed = {"operation_result": "error", "errno": errno.EBUSY}
        with mock.patch.object(
            probe, "_root_client_at", return_value=root_client
        ) as client:
            observation = probe._post_operation_observation(
                "replace", failed,
                4, "target.sock", "target.sock.moved",
                directory_identity, original_identity,
            )
        self.assertEqual(observation["status"], "COMPLETE")
        client.assert_called_once_with(
            4, "target.sock", probe.ROOT_RESPONSE,
            directory_identity, original_identity,
        )

        renamed = {"operation_result": "success"}
        with mock.patch.object(
            probe, "_root_client_at", return_value=root_client
        ) as client:
            observation = probe._post_operation_observation(
                "rename", renamed,
                4, "target.sock", "target.sock.moved",
                directory_identity, original_identity,
            )
        self.assertEqual(observation["status"], "UNSAFE_OBSERVED")
        self.assertTrue(observation["root_client_valid"])
        client.assert_called_once_with(
            4, "target.sock.moved", probe.ROOT_RESPONSE,
            directory_identity, original_identity,
        )

        with mock.patch.object(
            probe, "_root_client_at",
            return_value={"result": "error", "errno": errno.ENOENT},
        ) as client:
            observation = probe._post_operation_observation(
                "unlink", {"operation_result": "success"},
                4, "target.sock", "target.sock.moved",
                directory_identity, original_identity,
            )
        self.assertEqual(observation["status"], "UNSAFE_OBSERVED")
        self.assertTrue(observation["root_client_valid"])
        client.assert_called_once_with(
            4, "target.sock", probe.ROOT_RESPONSE, directory_identity,
            expect_missing=True,
        )

        replaced = {
            "operation_result": "success",
            "replacement": {"device": 1, "inode": 12, "uid": 1000, "gid": 1001,
                            "type": "socket", "mode": 0o600},
            "root_client": fake_client,
            "fake_server": fake_server,
        }
        with mock.patch.object(
            probe, "_root_client_at",
            side_effect=AssertionError("post-operation check must reuse first client"),
        ) as reconnect:
            observation = probe._post_operation_observation(
                "replace", replaced,
                4, "target.sock", "target.sock.moved",
                directory_identity, original_identity,
            )
        self.assertEqual(observation["status"], "UNSAFE_OBSERVED")
        self.assertTrue(observation["root_client_valid"])
        reconnect.assert_not_called()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED")
        and getattr(os, "geteuid", lambda: -1)() == 0
        and all(hasattr(os, name) for name in ("setgroups", "setgid", "setuid"))
        and Path("/proc/self/fd").exists(),
        "requires Linux AF_UNIX/SO_PEERCRED, procfs, and root to run the fake service as UID 1000",
    )
    def test_one_shot_unix_socket_service_is_not_reconnected_after_first_client(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = str(Path(temporary) / "one-shot.sock")
            os.chmod(temporary, 0o777)
            directory_fd = os.open(temporary, probe._dir_flags())
            directory_identity = probe._identity(os.fstat(directory_fd))
            server_code = (
                "import json,os,socket,struct,sys\n"
                "p=sys.argv[1]; s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
                "old=os.umask(0o177)\n"
                "try: s.bind(p)\n"
                "finally: os.umask(old)\n"
                "os.chmod(p,0o600); s.listen(1); print('READY',flush=True)\n"
                "c,_=s.accept(); raw=c.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12)\n"
                "pid,uid,gid=struct.unpack('3i',raw); c.sendall(b'fake-fixture-service\\n'); c.close()\n"
                "print(json.dumps({'peer_pid':pid,'peer_uid':uid,'peer_gid':gid,"
                "'response':'fake-fixture-service\\n'}),flush=True); s.close()\n"
            )

            def become_target():
                os.setgroups([probe.TARGET_GROUPS[0]])
                os.setgid(probe.TARGET_GID)
                os.setuid(probe.TARGET_UID)

            server = subprocess.Popen(
                [sys.executable, "-I", "-S", "-c", server_code, path],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, close_fds=True, preexec_fn=become_target,
            )
            try:
                ready, _, _ = select.select([server.stdout], [], [], 3.0)
                self.assertTrue(ready, "fake service did not report readiness")
                self.assertEqual(server.stdout.readline(), b"READY\n")
                replacement_info = os.stat("one-shot.sock", dir_fd=directory_fd,
                                           follow_symlinks=False)
                replacement_identity = probe._identity(replacement_info)
                first_client = probe._root_client_at(
                    directory_fd, "one-shot.sock", probe.FAKE_RESPONSE,
                    directory_identity, replacement_identity)
                self.assertTrue(probe._root_client_valid(
                    first_client, uid=probe.TARGET_UID, gid=probe.TARGET_GID,
                    response=probe.FAKE_RESPONSE))
                remainder, _ = server.communicate(timeout=3.0)
                self.assertEqual(server.returncode, 0)
                fake_server = json.loads(remainder.splitlines()[-1])
                result = {
                    "operation_result": "success",
                    "replacement": replacement_identity,
                    "fake_server": fake_server,
                    "root_client": first_client,
                }
                with mock.patch.object(
                    probe, "_root_client_at",
                    wraps=probe._root_client_at,
                ) as reconnect:
                    observation = probe._post_operation_observation(
                        "replace", result, directory_fd, "one-shot.sock",
                        "one-shot.sock.moved", directory_identity,
                        replacement_identity,
                    )
                reconnect.assert_not_called()
                self.assertEqual(observation["status"], "UNSAFE_OBSERVED")
                self.assertTrue(observation["root_client_valid"])
            finally:
                if server.poll() is None:
                    server.kill()
                    server.wait(timeout=2.0)
                os.close(directory_fd)

    def test_original_view_endpoint_requires_a_reconciled_fixture_state(self):
        directory_identity = {
            "device": 1, "inode": 10, "uid": 0, "gid": 0,
            "type": "directory", "mode": 0o755,
        }
        original_identity = {
            "device": 1, "inode": 11, "uid": 0, "gid": 0,
            "type": "socket", "mode": 0o600,
        }
        replacement_identity = {
            "device": 1, "inode": 12, "uid": 1000, "gid": 1001,
            "type": "socket", "mode": 0o600,
        }
        case = {"identity": directory_identity, "socket_name": "target.sock"}
        state = {"status": "read", "entries": {
            "target": {"status": "present", "identity": replacement_identity},
            "original_moved": {"status": "present", "identity": original_identity},
        }}
        target_stat = SimpleNamespace(
            st_dev=1, st_ino=12, st_uid=1000, st_gid=1001,
            st_mode=probe.stat.S_IFSOCK | 0o600,
        )
        root_client = {
            "result": "success", "peer_uid": 0, "peer_gid": 0,
            "response": probe.ROOT_RESPONSE.decode(), "verified": True,
        }
        with (
            mock.patch.object(probe, "_root_client_at", return_value=root_client) as client,
            mock.patch.object(probe.os, "stat", return_value=target_stat),
        ):
            observation = probe._verify_original_endpoint_for_state(4, case, state)
        self.assertEqual(observation["status"], "VERIFIED")
        self.assertTrue(observation["target_identity_valid"])
        client.assert_called_once_with(
            4, "target.sock.moved", probe.ROOT_RESPONSE,
            directory_identity, original_identity,
        )

        unreconciled = {"status": "read", "entries": {
            "target": {"status": "present", "identity": replacement_identity},
            "original_moved": {"status": "missing"},
        }}
        self.assertEqual(
            probe._verify_original_endpoint_for_state(4, case, unreconciled)["status"],
            "BLOCKED",
        )

    def test_same_fixture_original_and_private_views_are_recorded_during_bind(self):
        directory_identity = {
            "device": 1, "inode": 10, "uid": 0, "gid": 0,
            "type": "directory", "mode": 0o755,
        }
        btrfs_socket_identity = {
            "device": 2, "inode": 20, "uid": 0, "gid": 0,
            "type": "socket", "mode": 0o600,
        }
        source_identity = {
            "device": 3, "inode": 30, "uid": 0, "gid": 0,
            "type": "socket", "mode": 0o600,
        }
        case = {
            "name": "mnt-0755-rename", "profile": "0755", "operation": "rename",
            "target_dir": "mnt-0755-rename", "target_dir_identity": directory_identity,
            "socket_name": "target.sock", "private_moved_name": "target.sock.private-moved",
            "target_path": "/vol1/1000/bind-sock-btrfs-fixture/mnt-0755-rename/target.sock",
            "target_identity": btrfs_socket_identity, "source_identity": source_identity,
            "original_mount_id": "100", "source_mount": {"mount_id": "50"},
            "target_mount": {"mount_id": "100"}, "source_root": "/fixture/source.sock",
        }
        state = {"status": "read", "entries": {
            "target": {"status": "missing"},
            "original_moved": {"status": "present", "identity": btrfs_socket_identity},
            "private_moved": {"status": "missing"},
        }}
        original_result = {
            "status": "COMPLETE", "state_after": state,
            "target_result": {"status": "COMPLETE", "identity": {"verified": True}},
        }
        events = [
            {"event": "namespace_ready", "status": "READY",
             "propagation": {"status": "PRIVATE"}},
            {"event": "mount_ready", "name": case["name"], "mount_id": "200",
             "target_path": case["target_path"], "target_mount_id": "100",
             "source_identity": source_identity, "mounted_identity": source_identity,
             "mountinfo": {}},
            {"event": "mount_case_done", "name": case["name"], "status": "COMPLETE",
             "cleanup": "COMPLETE", "mount_id": "200", "underlay_expected": state,
             "underlay_after_unmount": state, "restored_mounts": {"status": "VERIFIED"},
             "target_result": {"status": "COMPLETE", "fixture_identity": source_identity},
             "root_client": {"result": "success"}, "root_client_valid": True},
            {"event": "final", "status": "COMPLETE"},
        ]
        trace = []
        def read_event(*_args):
            event = events.pop(0)
            trace.append(event["event"])
            return event
        def original_operation(*_args, **_kwargs):
            trace.append("original_namespace_operation")
            return original_result
        sent = []
        def send_command(_proc, command, **values):
            sent.append((command, values))
            return True

        process = SimpleNamespace(
            pid=77, stdin=mock.Mock(), stdout=mock.Mock(), returncode=0,
            poll=lambda: 0, wait=lambda timeout: None,
        )
        fixtures = {"mounted_cases": [{**case, "identity": directory_identity,
                                      "socket_identity": btrfs_socket_identity}],
                    "base_fd": 8}
        context = {"python": "/usr/bin/python3", "script": "/fixture/probe.py",
                   "parent_namespace": {"device": 1, "inode": 1}}
        directory_stat = SimpleNamespace(
            st_dev=1, st_ino=10, st_uid=0, st_gid=0,
            st_mode=probe.stat.S_IFDIR | 0o755,
        )
        with (
            mock.patch.object(probe, "_worker_config", return_value={"cases": [case]}),
            mock.patch.object(probe, "_start_worker", return_value=process),
            mock.patch.object(probe, "_read_worker_event", side_effect=read_event),
            mock.patch.object(probe, "_verify_worker_namespace", return_value={"status": "PRIVATE"}),
            mock.patch.object(probe, "_send_command", side_effect=send_command),
            mock.patch.object(probe, "_mountpoint_event_valid", return_value=True),
            mock.patch.object(probe, "_mount_record_for_id", return_value={"status": "read"}),
            mock.patch.object(probe, "_mount_matches_bind", return_value=True),
            mock.patch.object(probe, "_read_mountinfo", return_value=[]),
            mock.patch.object(probe, "_open_child_dir", return_value=(44, directory_stat)),
            mock.patch.object(probe, "_same_object", return_value=True),
            mock.patch.object(probe, "_original_view_matches", return_value={"status": "VERIFIED"}),
            mock.patch.object(probe, "_run_fixture_operation", side_effect=original_operation),
            mock.patch.object(probe, "_verify_fixture_mount_ids", return_value={"status": "VERIFIED"}),
            mock.patch.object(probe, "_verify_original_endpoint_for_state", return_value={"status": "VERIFIED"}),
            mock.patch.object(probe, "_fixture_states_match", return_value=True),
            mock.patch.object(probe, "_read_fixture_state", return_value=state),
            mock.patch.object(probe, "_terminate_and_reap", return_value=True),
            mock.patch.object(probe.os, "close"),
        ):
            results, status, reaped = probe._run_mounted_phase(context, fixtures)
        self.assertEqual(status, "COMPLETE")
        self.assertTrue(reaped)
        self.assertLess(trace.index("mount_ready"), trace.index("original_namespace_operation"))
        self.assertLess(trace.index("original_namespace_operation"), trace.index("mount_case_done"))
        self.assertTrue(results[0]["same_fixture"][
            "original_namespace_operation_started_while_bind_active"])
        self.assertEqual(results[0]["same_fixture"]["underlay_socket_identity_before_bind"],
                         btrfs_socket_identity)
        self.assertIn("original_namespace_during_private_mount", results[0])
        self.assertIn("private_namespace_during_private_mount", results[0])
        parent_verified = next(values for command, values in sent if command == "parent_verified")
        self.assertEqual(parent_verified["underlay_state"], state)

    def test_positive_control_failure_cannot_report_complete(self):
        caps = {
            key: "0000000000000000"
            for key in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd")
        }
        caps["NoNewPrivs"] = "1"
        target = {
            "operation_result": "success",
            "rename_result": "success",
            "fixture_access": {
                "method": "inherited-directory-fd",
                "directory_fd_identity_verified": True,
                "production_path_traversal_verified": False,
            },
            "identity": {
                "verified": True,
                "uid": [1000] * 3,
                "gid": [1001] * 3,
                "groups": [1000],
                "capabilities_zero": True,
                "capabilities": caps,
            },
            "replacement": {"device": 1, "inode": 45, "uid": 1000, "gid": 1001,
                            "type": "socket", "mode": 0o600},
            "fake_server": {
                "peer_uid": 0,
                "peer_gid": 0,
                "response": "fake-fixture-service\n",
            },
        }
        client = {
            "result": "success",
            "peer_uid": 1000,
            "peer_gid": 1001,
            "response": "fake-fixture-service\n",
        }
        case = {
            "profile": "0777",
            "operation": "replace",
            "target_result": target,
        }
        self.assertTrue(probe._positive_control_confirmed(case, client))
        self.assertFalse(
            probe._positive_control_confirmed(
                case, {**client, "response": "wrong"}
            )
        )
        self.assertFalse(
            probe._positive_control_confirmed(
                {"profile": "0777", "operation": "replace",
                 "target_result": {}},
                client,
            )
        )
        self.assertEqual(
            probe._overall_status(
                [{"status": "COMPLETE"}], [{"status": "COMPLETE"}],
                "NOT CONFIRMED", "COMPLETE", "COMPLETE",
            ),
            "PARTIAL",
        )

    def test_timeout_reader_rejects_partial_line_without_blocking_readline(self):
        class Stream:
            def fileno(self):
                return 9

            def readline(self):
                raise AssertionError("blocking readline must not be used")

        class Selector:
            def __init__(self):
                self.results = [[(9, 1)], []]
                self.timeouts = []

            def register(self, fd, events):
                self.registered = (fd, events)

            def select(self, timeout):
                self.timeouts.append(timeout)
                return self.results.pop(0)

            def close(self):
                pass

        selector = Selector()
        with (
            mock.patch.object(
                probe.selectors, "DefaultSelector", return_value=selector
            ),
            mock.patch.object(
                probe.time, "monotonic",
                side_effect=[10.0, 10.1, 10.2],
            ),
            mock.patch.object(probe.os, "read", return_value=b"{") as read,
        ):
            self.assertIsNone(probe._readline_timeout(Stream(), 1.0))
        read.assert_called_once_with(9, 1)
        self.assertGreater(selector.timeouts[0], 0)
        self.assertGreater(selector.timeouts[1], 0)

    def test_unreaped_worker_timeout_preserves_fixture_roots(self):
        process = SimpleNamespace(
            pid=77,
            stdin=mock.Mock(),
            stdout=mock.Mock(),
            poll=mock.Mock(return_value=None),
            returncode=None,
        )
        with (
            mock.patch.object(probe, "_worker_config", return_value={}),
            mock.patch.object(probe, "_start_worker", return_value=process),
            mock.patch.object(probe, "_read_worker_event", return_value=None),
            mock.patch.object(probe, "_terminate_and_reap", return_value=False),
        ):
            results, status, reaped = probe._run_mounted_phase({}, {})
        self.assertEqual(results, [])
        self.assertEqual(status, "BLOCKED")
        self.assertFalse(reaped)

        identity = {
            "device": 1, "inode": 10, "uid": 0, "gid": 0,
            "type": "directory", "mode": 0o711,
        }
        fixtures = {
            "mounted_cases": [],
            "plain_cases": [],
            "cases": [],
            "source_servers": [],
            "run_fd": 21,
            "base_fd": 22,
            "run_path": "/run/bind-sock-run-x",
            "base_path": "/vol1/1000/bind-sock-btrfs-x",
        }
        context = {
            "run_fd": 11,
            "base_fd": 12,
            "python": "/usr/bin/python3",
            "script": "/root/bind_mount_socket_probe.py",
            "parent_namespace": {"device": 1, "inode": 2},
            "run_mount": {},
            "base_mount": {},
        }
        roots = [
            ("run-name", 21, identity),
            ("base-name", 22, identity),
        ]
        with (
            mock.patch.object(probe, "_create_lab_root", side_effect=roots),
            mock.patch.object(probe, "_setup_fixtures", return_value=fixtures),
            mock.patch.object(
                probe, "_run_mounted_phase",
                return_value=([], "BLOCKED", False),
            ),
            mock.patch.object(probe, "_cleanup_lab") as cleanup,
            mock.patch.object(probe.os, "close"),
        ):
            report = probe._run_lab(context)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["cleanup"]["status"], "BLOCKED")
        cleanup.assert_not_called()

    def test_first_and_second_root_setup_failures_keep_paths_and_skip_worker(self):
        root_identity = {
            "device": 1, "inode": 10, "uid": 0, "gid": 0,
            "type": "directory", "mode": 0o711,
        }

        for failed_role in ("run", "btrfs"):
            with self.subTest(failed_role=failed_role):
                def create_root(_parent_fd, record, ledger):
                    role = record["role"]
                    ledger["phase"] = f"root:{role}:injected"
                    if role == failed_role:
                        if role == "btrfs":
                            record.update(created=True, identity=None,
                                          initial_identity=None,
                                          identity_binding="FAILED",
                                          identity_binding_failure="injected after mkdir",
                                          identity_stage="injected after mkdir")
                        raise OSError(errno.EIO, "injected root setup failure")
                    record.update(created=True, identity=root_identity,
                                  initial_identity=root_identity,
                                  identity_binding="BOUND", dir_fd=71,
                                  identity_stage="test fstat")
                    return record["name"], 71, root_identity

                context = {"run_fd": 51, "base_fd": 52}
                with (
                    mock.patch.object(probe, "_create_lab_root", side_effect=create_root),
                    mock.patch.object(probe, "_setup_fixtures") as setup,
                    mock.patch.object(probe, "_run_mounted_phase") as worker,
                    mock.patch.object(probe.os, "close"),
                    mock.patch.object(probe.os, "stat", side_effect=OSError(errno.EBADF, "fake fd")),
                ):
                    report = probe._run_lab(context)
                setup.assert_not_called()
                worker.assert_not_called()
                self.assertEqual(report["status"], "BLOCKED")
                self.assertIn("failure_stage", report)
                self.assertIn("run", report["experiment_roots"])
                self.assertTrue(report["experiment_roots"]["run"]["path"].startswith(str(probe.RUN)))
                if failed_role == "btrfs":
                    self.assertIn("btrfs", report["experiment_roots"])
                    self.assertIsNone(report["experiment_roots"]["btrfs"]["identity"])
                    self.assertTrue(report["setup_ledger"]["roots"]["run"]["identity"])

    def test_acl_setup_failure_reports_ledger_and_never_starts_worker(self):
        root_identity = {
            "device": 1, "inode": 10, "uid": 0, "gid": 0,
            "type": "directory", "mode": 0o711,
        }
        acl_details = {
            "fixture": {"mode": 0o700, "uid": 0, "gid": 0},
            "set": {"status": "COMPLETE", "errno": None},
            "get": {"status": "COMPLETE", "errno": None},
            "actual": {"numeric_entries": list(probe.ACL_EXPECTED),
                       "mode": 0o700, "uid": 0, "gid": 0},
            "differences": {"mode": {"expected": 0o775, "actual": 0o700}},
            "validation": "FAILED",
        }
        def create_root(_parent_fd, record, _ledger):
            record.update(created=True, identity=root_identity,
                          initial_identity=root_identity, identity_binding="BOUND",
                          dir_fd=81,
                          identity_stage="injected known identity")
            return record["name"], 81, root_identity
        def fail_setup(_context, _run_name, _run_fd, _run_identity,
                       _base_name, _base_fd, _base_identity, ledger):
            ledger["phase"] = "case:plain-acl-rename:acl-get-verify"
            ledger["cases"].append({
                "kind": "case", "root_role": "btrfs", "name": "plain-acl-rename",
                "path": "/fixture/btrfs/plain-acl-rename", "created": True,
                "identity": None, "initial_identity": None,
                "identity_binding": "FAILED",
                "identity_binding_failure": "identity unavailable",
                "identity_stage": "identity unavailable",
                "acl": acl_details, "allowed_entries": {}, "cleanup": "NOT RUN",
            })
            raise probe.ProbeBlocked("injected ACL profile mismatch", acl_details)

        with (
            mock.patch.object(probe, "_create_lab_root", side_effect=create_root),
            mock.patch.object(probe, "_setup_fixtures", side_effect=fail_setup),
            mock.patch.object(probe, "_run_mounted_phase") as worker,
            mock.patch.object(probe.os, "stat", side_effect=OSError(errno.EIO, "unverified test fd")),
            mock.patch.object(probe.os, "close"),
        ):
            report = probe._run_lab({"run_fd": 51, "base_fd": 52})
        worker.assert_not_called()
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["failure_stage"], "case:plain-acl-rename:acl-get-verify")
        self.assertEqual(report["failure_details"]["differences"]["mode"]["actual"], 0o700)
        self.assertIn("run", report["experiment_roots"])
        self.assertIn("btrfs", report["experiment_roots"])
        self.assertEqual(report["setup_ledger"]["cases"][0]["acl"], acl_details)
        self.assertEqual(report["cleanup"]["status"], "BLOCKED")
        self.assertEqual(report["cleanup"]["items"][0]["status"], "BLOCKED")

    def test_case_mkdir_then_stat_failure_is_unowned_and_never_removed(self):
        ledger = {"phase": "", "roots": {}, "cases": [], "sockets": [], "servers": []}
        root = {"role": "btrfs", "path": "/fixture/root", "dir_fd": 70}
        case = probe._plan_case(ledger, root, "case-one")
        with (
            mock.patch.object(probe.os, "mkdir") as mkdir,
            mock.patch.object(probe.os, "stat", side_effect=OSError(errno.EIO, "injected after mkdir")),
        ):
            with self.assertRaises(OSError):
                probe._create_case_dir(70, "case-one", "0755", case, ledger)
        mkdir.assert_called_once()
        self.assertTrue(case["created"])
        self.assertIsNone(case["identity"])
        self.assertIsNone(case["initial_identity"])
        self.assertEqual(case["identity_binding"], "FAILED")
        self.assertEqual(case["identity_stage"], "initial parent stat failed")

        root_info = SimpleNamespace(st_dev=1, st_ino=10, st_uid=0, st_gid=0,
                                    st_mode=probe.stat.S_IFDIR | 0o711)
        ledger["roots"]["btrfs"] = {
            "role": "btrfs", "name": "root", "path": "/fixture/root",
            "parent_fd": 69, "dir_fd": 70, "created": True,
            "identity": probe._identity(root_info),
            "initial_identity": probe._identity(root_info),
            "identity_binding": "BOUND", "cleanup": "NOT RUN",
        }
        with (
            mock.patch.object(probe.os, "stat", return_value=root_info),
            mock.patch.object(probe.os, "fstat", return_value=root_info),
            mock.patch.object(probe.os, "listdir", return_value=["case-one"]),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe, "_cleanup_case") as cleanup_case,
        ):
            result = probe._cleanup_ledger(ledger, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(result["status"], "BLOCKED")
        chmod.assert_not_called()
        cleanup_case.assert_not_called()

    def test_created_root_initial_inode_mismatch_permanently_blocks_cleanup(self):
        initial = SimpleNamespace(st_dev=1, st_ino=10, st_uid=0, st_gid=0,
                                  st_mode=probe.stat.S_IFDIR | 0o700)
        opened = SimpleNamespace(st_dev=1, st_ino=20, st_uid=0, st_gid=0,
                                 st_mode=probe.stat.S_IFDIR | 0o700)
        ledger = probe._new_setup_ledger()
        root = probe._plan_lab_root(ledger, "run", 12, "/run", "fixture-")
        with (
            mock.patch.object(probe.os, "mkdir"),
            mock.patch.object(probe.os, "stat", return_value=initial),
            mock.patch.object(probe.os, "open", return_value=91),
            mock.patch.object(probe.os, "fstat", return_value=opened),
            mock.patch.object(probe, "_dir_flags", return_value=0),
        ):
            with self.assertRaises(probe.ProbeBlocked):
                probe._create_lab_root(12, root, ledger)

        self.assertEqual(root["initial_identity"]["inode"], 10)
        self.assertEqual(root["identity"]["inode"], 10)
        self.assertEqual(root["identity_binding"], "FAILED")
        self.assertIn("identities differ", root["identity_binding_failure"])

        with (
            mock.patch.object(probe.os, "stat", return_value=opened),
            mock.patch.object(probe.os, "fstat", return_value=opened),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe.os, "unlink") as unlink,
            mock.patch.object(probe.os, "rmdir") as rmdir,
            mock.patch.object(probe, "_remove_known") as remove,
        ):
            preflight = probe._preflight_ledger_root(root, [], [])
            cleanup = probe._cleanup_ledger(
                ledger, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(preflight["status"], "BLOCKED")
        self.assertEqual(cleanup["status"], "BLOCKED")
        chmod.assert_not_called()
        unlink.assert_not_called()
        rmdir.assert_not_called()
        remove.assert_not_called()

    def test_created_root_initial_stat_failure_stays_unbound(self):
        ledger = probe._new_setup_ledger()
        root = probe._plan_lab_root(ledger, "run", 12, "/run", "fixture-")
        with (
            mock.patch.object(probe.os, "mkdir"),
            mock.patch.object(probe.os, "stat", side_effect=OSError(errno.EIO, "initial stat fault")),
        ):
            with self.assertRaises(OSError):
                probe._create_lab_root(12, root, ledger)
        self.assertTrue(root["created"])
        self.assertIsNone(root["initial_identity"])
        self.assertIsNone(root["identity"])
        self.assertEqual(root["identity_binding"], "FAILED")

        with (
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe.os, "unlink") as unlink,
            mock.patch.object(probe.os, "rmdir") as rmdir,
            mock.patch.object(probe, "_remove_known") as remove,
        ):
            cleanup = probe._cleanup_ledger(
                ledger, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(cleanup["status"], "BLOCKED")
        chmod.assert_not_called()
        unlink.assert_not_called()
        rmdir.assert_not_called()
        remove.assert_not_called()

    def test_created_case_initial_inode_mismatch_blocks_root_cleanup(self):
        initial = SimpleNamespace(st_dev=2, st_ino=10, st_uid=0, st_gid=0,
                                  st_mode=probe.stat.S_IFDIR | 0o700)
        opened = SimpleNamespace(st_dev=2, st_ino=20, st_uid=0, st_gid=0,
                                 st_mode=probe.stat.S_IFDIR | 0o700)
        ledger = probe._new_setup_ledger()
        root_info = SimpleNamespace(st_dev=1, st_ino=1, st_uid=0, st_gid=0,
                                    st_mode=probe.stat.S_IFDIR | 0o711)
        root = {"kind": "root", "role": "btrfs", "name": "root",
                "path": "/fixture/root", "parent_fd": 69, "dir_fd": 70,
                "created": True, "identity": probe._identity(root_info),
                "initial_identity": probe._identity(root_info),
                "identity_binding": "BOUND", "cleanup": "NOT RUN"}
        ledger["roots"]["btrfs"] = root
        case = probe._plan_case(ledger, root, "case-one")
        with (
            mock.patch.object(probe.os, "mkdir"),
            mock.patch.object(probe.os, "stat", return_value=initial),
            mock.patch.object(probe.os, "open", return_value=91),
            mock.patch.object(probe.os, "fstat", return_value=opened),
            mock.patch.object(probe, "_dir_flags", return_value=0),
        ):
            with self.assertRaises(probe.ProbeBlocked):
                probe._create_case_dir(70, "case-one", "0755", case, ledger)

        self.assertEqual(case["initial_identity"]["inode"], 10)
        self.assertEqual(case["identity"]["inode"], 10)
        self.assertEqual(case["identity_binding"], "FAILED")

        with (
            mock.patch.object(probe.os, "stat", side_effect=[root_info, opened]),
            mock.patch.object(probe.os, "fstat", side_effect=[root_info, opened]),
            mock.patch.object(probe.os, "listdir", return_value=["case-one"]),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe.os, "unlink") as unlink,
            mock.patch.object(probe.os, "rmdir") as rmdir,
            mock.patch.object(probe, "_remove_known") as remove,
            mock.patch.object(probe, "_cleanup_case") as cleanup_case,
        ):
            cleanup = probe._cleanup_ledger(
                ledger, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(cleanup["status"], "BLOCKED")
        self.assertIn("case initial stat is not bound", cleanup["preflight"]["btrfs"]["reason"])
        chmod.assert_not_called()
        unlink.assert_not_called()
        rmdir.assert_not_called()
        remove.assert_not_called()
        cleanup_case.assert_not_called()

    def test_bind_success_without_socket_identity_is_preserved(self):
        class FakeListener:
            def __init__(self):
                self.bound = False
                self.closed = False

            def bind(self, _path):
                self.bound = True

            def close(self):
                self.closed = True

        ledger = {"phase": "", "roots": {}, "cases": [], "sockets": [], "servers": []}
        root = {"role": "run", "path": "/fixture/run", "dir_fd": 70}
        socket_record = probe._plan_socket(ledger, root, "source.sock",
                                           "/fixture/run/source.sock", 70)
        listener = FakeListener()
        with (
            mock.patch.object(probe, "_fd_child_path", return_value="/proc/self/fd/70/source.sock"),
            mock.patch.object(probe, "_fd_directory_path", return_value="/proc/self/fd/70"),
            mock.patch.object(probe.socket, "socket", return_value=listener),
            mock.patch.object(probe.socket, "AF_UNIX", 1, create=True),
            mock.patch.object(probe.socket, "SOCK_STREAM", 1, create=True),
            mock.patch.object(probe.os, "stat", side_effect=[
                FileNotFoundError(errno.ENOENT, "missing"), OSError(errno.EIO, "stat fault")]),
        ):
            with self.assertRaises(OSError):
                probe._bind_root_socket(
                    70, "source.sock", {"device": 1, "inode": 10, "uid": 0,
                                         "gid": 0, "type": "directory", "mode": 0o711},
                    ledger=ledger, socket_record=socket_record)
        self.assertTrue(listener.bound)
        self.assertTrue(listener.closed)
        self.assertTrue(socket_record["created"])
        self.assertIsNone(socket_record["identity"])

        root_info = SimpleNamespace(st_dev=1, st_ino=10, st_uid=0, st_gid=0,
                                    st_mode=probe.stat.S_IFDIR | 0o711)
        ledger["roots"]["run"] = {
            "role": "run", "name": "root", "path": "/fixture/run",
            "parent_fd": 69, "dir_fd": 70, "created": True,
            "identity": probe._identity(root_info),
            "initial_identity": probe._identity(root_info),
            "identity_binding": "BOUND", "cleanup": "NOT RUN",
        }
        with (
            mock.patch.object(probe.os, "stat", return_value=root_info),
            mock.patch.object(probe.os, "fstat", return_value=root_info),
            mock.patch.object(probe.os, "listdir", return_value=["source.sock"]),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe, "_remove_known") as remove,
        ):
            result = probe._cleanup_ledger(ledger, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(result["status"], "BLOCKED")
        chmod.assert_not_called()
        remove.assert_not_called()

    def test_acl_failure_diagnostics_preserve_locked_mode_and_exact_difference(self):
        locked = SimpleNamespace(st_dev=1, st_ino=22, st_uid=0, st_gid=0,
                                 st_mode=probe.stat.S_IFDIR | 0o700)
        def absent_xattr(_fd, _name):
            raise OSError(errno.ENODATA, "no inherited ACL")

        diagnostics = {}
        ledger = {"phase": ""}
        with (
            mock.patch.object(probe.os, "fstat", return_value=locked),
            mock.patch.object(probe.os, "removexattr", side_effect=absent_xattr, create=True),
            mock.patch.object(probe.os, "setxattr", side_effect=OSError(errno.EOPNOTSUPP, "set failed"), create=True),
        ):
            with self.assertRaises(probe.ProbeBlocked) as caught:
                probe._apply_case_profile(9, "acl", diagnostics, ledger, "acl-case")
        self.assertEqual(caught.exception.details["fixture"]["mode"], 0o700)
        self.assertEqual(caught.exception.details["set"],
                         {"status": "FAILED", "errno": errno.EOPNOTSUPP})
        self.assertEqual(caught.exception.details["get"]["status"], "NOT RUN")
        self.assertEqual(ledger["phase"], "case:acl-case:acl-set")

        diagnostics = {}
        with (
            mock.patch.object(probe.os, "fstat", return_value=locked),
            mock.patch.object(probe.os, "removexattr", side_effect=absent_xattr, create=True),
            mock.patch.object(probe.os, "setxattr", create=True),
            mock.patch.object(probe.os, "getxattr", side_effect=OSError(errno.EIO, "get failed"), create=True),
        ):
            with self.assertRaises(probe.ProbeBlocked) as caught:
                probe._apply_case_profile(9, "acl", diagnostics, ledger, "acl-case")
        self.assertEqual(caught.exception.details["set"]["status"], "COMPLETE")
        self.assertEqual(caught.exception.details["get"],
                         {"status": "FAILED", "errno": errno.EIO})

        diagnostics = {}
        with (
            mock.patch.object(probe.os, "fstat", return_value=locked),
            mock.patch.object(probe.os, "removexattr", side_effect=absent_xattr, create=True),
            mock.patch.object(probe.os, "setxattr", create=True) as set_acl,
            mock.patch.object(probe.os, "getxattr", return_value=probe._acl_wire(), create=True),
            mock.patch.object(probe.os, "fchmod") as chmod,
        ):
            with self.assertRaises(probe.ProbeBlocked) as caught:
                probe._apply_case_profile(9, "acl", diagnostics, ledger, "acl-case")
        details = caught.exception.details
        self.assertEqual(details["actual"]["numeric_entries"], list(probe.ACL_EXPECTED))
        self.assertEqual(details["actual"]["mode"], 0o700)
        self.assertEqual(details["differences"]["mode"],
                         {"expected": 0o775, "actual": 0o700})
        self.assertEqual(details["set"]["status"], "COMPLETE")
        self.assertEqual(details["get"]["status"], "COMPLETE")
        set_acl.assert_called_once()
        chmod.assert_not_called()

    def test_acl_initial_owner_must_be_root_root_before_set(self):
        wrong_owner = SimpleNamespace(st_dev=1, st_ino=25, st_uid=0, st_gid=1234,
                                      st_mode=probe.stat.S_IFDIR | 0o700)
        diagnostics = {}
        with (
            mock.patch.object(probe.os, "fstat", return_value=wrong_owner),
            mock.patch.object(probe.os, "setxattr", create=True) as set_acl,
            mock.patch.object(probe.os, "removexattr", create=True) as clear_acl,
        ):
            with self.assertRaises(probe.ProbeBlocked) as caught:
                probe._apply_case_profile(9, "acl", diagnostics)
        self.assertEqual(caught.exception.details["expected"]["uid"], 0)
        self.assertEqual(caught.exception.details["expected"]["gid"], 0)
        self.assertEqual(caught.exception.details["initial_owner_validation"], {
            "status": "FAILED", "expected": {"uid": 0, "gid": 0},
            "actual": {"uid": 0, "gid": 1234},
            "differences": {"gid": {"expected": 0, "actual": 1234}},
        })
        self.assertEqual(caught.exception.details["set"]["status"], "NOT RUN")
        set_acl.assert_not_called()
        clear_acl.assert_not_called()

    def test_acl_readback_uses_fixed_root_root_owner_expectation(self):
        initial = SimpleNamespace(st_dev=1, st_ino=26, st_uid=0, st_gid=0,
                                  st_mode=probe.stat.S_IFDIR | 0o700)
        wrong_readback = SimpleNamespace(st_dev=1, st_ino=26, st_uid=0, st_gid=1234,
                                         st_mode=probe.stat.S_IFDIR | 0o775)

        def absent_xattr(_fd, _name):
            raise OSError(errno.ENODATA, "no inherited ACL")

        diagnostics = {}
        with (
            mock.patch.object(probe.os, "fstat", side_effect=[initial, wrong_readback]),
            mock.patch.object(probe.os, "removexattr", side_effect=absent_xattr, create=True),
            mock.patch.object(probe.os, "setxattr", create=True),
            mock.patch.object(probe.os, "getxattr", return_value=probe._acl_wire(), create=True),
        ):
            with self.assertRaises(probe.ProbeBlocked) as caught:
                probe._apply_case_profile(9, "acl", diagnostics)
        details = caught.exception.details
        self.assertEqual(details["expected"]["uid"], 0)
        self.assertEqual(details["expected"]["gid"], 0)
        self.assertEqual(details["actual"], {
            "numeric_entries": list(probe.ACL_EXPECTED),
            "mode": 0o775, "uid": 0, "gid": 1234,
        })
        self.assertEqual(details["differences"], {
            "gid": {"expected": 0, "actual": 1234},
        })

    def test_server_start_failure_is_registered_and_closed(self):
        listener = mock.Mock()
        server = probe.RootSocketServer(listener, probe.ROOT_RESPONSE)
        ledger = {"phase": "", "roots": {}, "cases": [], "sockets": [], "servers": []}
        socket_record = {"name": "service.sock", "path": "/fixture/service.sock",
                         "identity": {"device": 1, "inode": 2}}
        record = probe._register_server(ledger, server, socket_record)
        with mock.patch.object(server.thread, "start", side_effect=RuntimeError("thread start fault")):
            with self.assertRaises(RuntimeError):
                probe._start_registered_server(ledger, record)
        shutdown = probe._close_servers(ledger)
        self.assertEqual(shutdown["status"], "COMPLETE")
        self.assertEqual(record["start_error"]["type"], "RuntimeError")
        listener.close.assert_called_once()

    def test_server_close_timeout_blocks_all_fixture_cleanup(self):
        server = SimpleNamespace(
            close=mock.Mock(return_value=False),
            thread=SimpleNamespace(is_alive=mock.Mock(return_value=True)),
        )
        ledger = {"phase": "", "roots": {}, "cases": [], "sockets": [],
                  "servers": [{"server": server, "path": "/fixture/service.sock",
                               "socket_name": "service.sock", "started": True}]}
        shutdown = probe._close_servers(ledger)
        with (
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe, "_remove_known") as remove,
            mock.patch.object(probe, "_cleanup_case") as cleanup_case,
        ):
            cleanup = probe._cleanup_ledger(
                ledger, threads_quiescent=shutdown["status"] == "COMPLETE",
                worker_reaped=True)
        self.assertEqual(shutdown["status"], "BLOCKED")
        self.assertEqual(cleanup["status"], "BLOCKED")
        chmod.assert_not_called()
        remove.assert_not_called()
        cleanup_case.assert_not_called()

    def test_unknown_case_entry_and_changed_root_identity_block_all_mutations(self):
        root_info = SimpleNamespace(st_dev=1, st_ino=10, st_uid=0, st_gid=0,
                                    st_mode=probe.stat.S_IFDIR | 0o711)
        case_info = SimpleNamespace(st_dev=1, st_ino=11, st_uid=0, st_gid=0,
                                    st_mode=probe.stat.S_IFDIR | 0o755)
        known_socket = {"device": 2, "inode": 22, "uid": 0, "gid": 0,
                        "type": "socket", "mode": 0o600}
        case = {"kind": "case", "root_role": "btrfs", "name": "case-one",
                "path": "/fixture/root/case-one", "parent_fd": 7, "dir_fd": 8,
                "created": True, "identity": probe._identity(case_info),
                "initial_identity": probe._identity(case_info),
                "identity_binding": "BOUND",
                "allowed_entries": {"known.sock": known_socket}, "cleanup": "NOT RUN"}
        root = {"kind": "root", "role": "btrfs", "name": "root", "path": "/fixture/root",
                "parent_fd": 6, "dir_fd": 7, "created": True,
                "identity": probe._identity(root_info),
                "initial_identity": probe._identity(root_info),
                "identity_binding": "BOUND", "cleanup": "NOT RUN"}
        ledger = {"phase": "cleanup", "roots": {"btrfs": root}, "cases": [case],
                  "sockets": [], "servers": []}
        with (
            mock.patch.object(probe.os, "stat", side_effect=[root_info, case_info]),
            mock.patch.object(probe.os, "fstat", side_effect=[root_info, case_info]),
            mock.patch.object(probe.os, "listdir", side_effect=[["case-one"],
                                                                  ["known.sock", "unknown.sock"]]),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe, "_remove_known") as remove,
            mock.patch.object(probe, "_cleanup_case") as cleanup_case,
        ):
            cleanup = probe._cleanup_ledger(ledger, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(cleanup["status"], "BLOCKED")
        self.assertIn("case entries differ", cleanup["preflight"]["btrfs"]["reason"])
        chmod.assert_not_called()
        remove.assert_not_called()
        cleanup_case.assert_not_called()

        wrong_root = SimpleNamespace(st_dev=1, st_ino=99, st_uid=0, st_gid=0,
                                     st_mode=probe.stat.S_IFDIR | 0o711)
        root_only = {"phase": "cleanup", "roots": {"btrfs": root},
                     "cases": [], "sockets": [], "servers": []}
        with (
            mock.patch.object(probe.os, "stat", return_value=wrong_root),
            mock.patch.object(probe.os, "fstat", return_value=root_info),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe, "_remove_known") as remove,
        ):
            cleanup = probe._cleanup_ledger(root_only, threads_quiescent=True, worker_reaped=True)
        self.assertEqual(cleanup["status"], "BLOCKED")
        self.assertIn("root path or dirfd identity changed", cleanup["preflight"]["btrfs"]["reason"])
        chmod.assert_not_called()
        remove.assert_not_called()

    def test_cleanup_refuses_changed_identities_without_unlinking(self):
        replacement = SimpleNamespace(
            st_dev=1, st_ino=99, st_uid=0, st_gid=0, st_mode=0o140600
        )
        expected = {
            "device": 1, "inode": 42, "uid": 0, "gid": 0, "type": "socket"
        }
        with (
            mock.patch.object(probe.os, "stat", return_value=replacement),
            mock.patch.object(probe.os, "unlink") as unlink,
        ):
            self.assertFalse(probe._remove_known(7, "known.sock", expected))
        unlink.assert_not_called()

        case_identity = {
            "device": 1, "inode": 11, "uid": 0, "gid": 0,
            "type": "directory",
        }
        with (
            mock.patch.object(
                probe, "_open_child_dir", return_value=(8, replacement)
            ),
            mock.patch.object(probe, "_same_object", return_value=False),
            mock.patch.object(probe.os, "fchmod") as chmod,
            mock.patch.object(probe.os, "rmdir") as rmdir,
            mock.patch.object(probe.os, "close"),
        ):
            self.assertEqual(
                probe._cleanup_case(
                    7, {"name": "case", "identity": case_identity}
                ),
                "BLOCKED",
            )
        chmod.assert_not_called()
        rmdir.assert_not_called()

    def test_script_trust_checks_use_parent_directory_validation(self):
        with mock.patch.object(
            probe, "_trusted_executable", return_value=None
        ) as trusted:
            self.assertIsNone(probe._trusted_script())
        trusted.assert_called_once_with((probe.__file__,))


if __name__ == "__main__":
    unittest.main()
