import importlib.util
import inspect
import os
import stat
import struct
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_socket_path_probe.py"
SPEC = importlib.util.spec_from_file_location("run_socket_path_probe", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class RunSocketPathProbeTests(unittest.TestCase):
    def test_run_mount_must_have_one_fdinfo_mount_id_and_tmpfs_record(self):
        row = "34 22 0:28 / /run rw,nosuid - tmpfs tmpfs rw,size=1g"
        self.assertEqual(probe._fd_mount_id("pos:\t0\n")["status"], "NOT FOUND")
        self.assertEqual(probe._fd_mount_id("mnt_id:\t34\nmnt_id:\t35\n")["status"], "DUPLICATE")
        self.assertEqual(probe._fd_mount_id("mnt_id:\tbad\n")["status"], "ANOMALOUS")
        self.assertEqual(probe._mount_record_for_id("34", [row])["filesystem_type"], "tmpfs")
        self.assertEqual(probe._mount_record_for_id("34", [row, row])["status"], "DUPLICATE")
        with mock.patch.object(probe, "_mount_for_fd", return_value={"status": "NOT FOUND"}):
            with self.assertRaises(probe.ProbeBlocked):
                probe._require_tmpfs(9)
        with mock.patch.object(probe, "_mount_for_fd", return_value={
                "status": "read", "mount_id": "34", "filesystem_type": "ext4"}):
            with self.assertRaises(probe.ProbeBlocked):
                probe._require_tmpfs(9)

    def test_acl_write_check_applies_named_user_mask_and_group_membership(self):
        info = SimpleNamespace(st_uid=0, st_gid=0, st_mode=stat.S_IFDIR | 0o755)
        self.assertFalse(probe._acl_write_allowed(info, None))
        entries = [
            {"tag": "user_obj", "id": None, "perm": 7},
            {"tag": "user", "id": 1000, "perm": 7},
            {"tag": "group_obj", "id": None, "perm": 5},
            {"tag": "mask", "id": None, "perm": 5},
            {"tag": "other", "id": None, "perm": 5},
        ]
        self.assertFalse(probe._acl_write_allowed(info, entries))
        entries[3]["perm"] = 7
        self.assertTrue(probe._acl_write_allowed(info, entries))
        entries = [
            {"tag": "user_obj", "id": None, "perm": 7},
            {"tag": "group_obj", "id": None, "perm": 5},
            {"tag": "group", "id": 1000, "perm": 7},
            {"tag": "mask", "id": None, "perm": 7},
            {"tag": "other", "id": None, "perm": 5},
        ]
        self.assertTrue(probe._acl_write_allowed(info, entries))

    def test_target_identity_requires_uid_gid_supplementary_group_and_zero_caps(self):
        valid = {"verified": True, "uid": [1000, 1000, 1000], "gid": [1001, 1001, 1001],
                 "groups": [1000], "capabilities_zero": True}
        self.assertTrue(probe._target_identity_verified(valid))
        for changed in (
                {**valid, "uid": [1000, 0, 1000]},
                {**valid, "gid": [1001, 1000, 1001]},
                {**valid, "groups": [1000, 1001]},
                {**valid, "capabilities_zero": False},
                {**valid, "verified": False}):
            with self.subTest(changed=changed):
                self.assertFalse(probe._target_identity_verified(changed))

    def test_child_uses_absolute_path_isolation_and_no_directory_fd(self):
        argv = probe._child_argv("/usr/bin/python3", "/run/lab/case", True)
        self.assertEqual(argv[1:3], ["-I", "-S"])
        self.assertEqual(argv[-2:], ["/run/lab/case", "positive"])
        source = inspect.getsource(probe._run_target)
        self.assertIn("close_fds=True", source)
        self.assertIn("user=TARGET_UID", source)
        self.assertIn("group=TARGET_GID", source)
        self.assertIn("extra_groups=[1000]", source)
        self.assertNotIn("pass_fds", source)
        self.assertNotIn("preexec_fn", source)
        child = probe._child_code()
        for token in ("os.getresuid()", "os.getresgid()", "os.getgroups()",
                      '"CapEff"', '"CapPrm"', '"CapInh"', '"CapAmb"', "os.rename(service,moved)"):
            self.assertIn(token, child)
        compile(child, "<socket-path-child>", "exec")

    def test_preflight_failure_returns_blocked_without_creating_experiment(self):
        with mock.patch.object(probe, "_runtime_preflight", side_effect=probe.ProbeBlocked("unsafe ACL")), \
             mock.patch.object(probe.os, "mkdir") as mkdir, \
             mock.patch.object(probe, "_run_lab") as run_lab:
            result = probe.run_probe()
        self.assertEqual(result["status"], "BLOCKED")
        mkdir.assert_not_called()
        run_lab.assert_not_called()

    def test_positive_control_failure_cannot_report_complete(self):
        cases = [{"name": "parent-0755", "status": "COMPLETE"},
                 {"name": "parent-0777", "status": "PARTIAL"}]
        self.assertEqual(probe._overall_status(cases, "NOT CONFIRMED", "COMPLETE"), "PARTIAL")
        self.assertEqual(probe._overall_status(cases, "CONFIRMED", "BLOCKED"), "BLOCKED")
        self.assertEqual(probe._overall_status(
            [{"name": "parent-0755", "status": "UNSAFE_OBSERVED"},
             {"name": "parent-0777", "status": "COMPLETE"}], "CONFIRMED", "COMPLETE"),
            "UNSAFE_OBSERVED")

    def test_positive_confirmation_requires_root_client_peer_and_fixed_response(self):
        result = {"identity": {"verified": True, "uid": [1000] * 3, "gid": [1001] * 3,
                               "groups": [1000], "capabilities_zero": True},
                  "fixture_reachable": True,
                  "connect": {"result": "error", "errno": 13},
                  "rename": {"result": "success"},
                  "replacement": {"result": "success"}, "fake_server": "served"}
        client = {"result": "success", "peer_uid": 1000, "peer_gid": 1001,
                  "response": "fake-service\n"}
        root_service = {"clients": [{"peer_uid": 0, "peer_gid": 0,
                                     "response": "root-service\n"}],
                        "second_client": "NOT RECEIVED WITHIN TIMEOUT"}
        second_client = {"clients": [root_service["clients"][0],
                                     {"peer_uid": 1000, "peer_gid": 1001,
                                      "response": "root-service\n"}],
                         "second_client": "received"}
        root_service_client = {"result": "success", "peer_uid": 0, "peer_gid": 0,
                               "response": "root-service\n"}
        self.assertTrue(probe._positive_confirmed(result, client, root_service, root_service_client))
        connected = {**result, "connect": {"result": "success", "response": "root-service\n"}}
        self.assertTrue(probe._root_service_target_valid(connected, second_client))
        wrong_peer = {"clients": [second_client["clients"][0],
                                  {"peer_uid": 0, "peer_gid": 0, "response": "root-service\n"}],
                      "second_client": "received"}
        self.assertFalse(probe._root_service_target_valid(connected, wrong_peer))
        pending_service = {"clients": root_service["clients"], "second_client": "pending"}
        self.assertFalse(probe._root_service_target_valid(result, pending_service))
        self.assertFalse(probe._root_service_baseline_valid(
            {**root_service_client, "peer_uid": 1000}, root_service))
        self.assertFalse(probe._positive_confirmed(
            result, {**client, "peer_uid": 0}, root_service, root_service_client))
        self.assertFalse(probe._positive_confirmed(
            result, {**client, "response": "wrong"}, root_service, root_service_client))

    def test_run_lab_simulated_success_covers_root_baseline_then_positive_control(self):
        root_info = SimpleNamespace(st_dev=1, st_ino=100, st_uid=0, st_gid=0,
                                    st_mode=stat.S_IFDIR | 0o711)
        case_info = SimpleNamespace(st_dev=1, st_ino=201, st_uid=0, st_gid=0,
                                    st_mode=stat.S_IFDIR | 0o777)
        fake_info = SimpleNamespace(st_dev=1, st_ino=301, st_uid=1000, st_gid=1001,
                                    st_mode=stat.S_IFSOCK | 0o600)
        case_identity = probe._object_identity(case_info)
        lab_identity = probe._object_identity(root_info)
        target_identity = {"verified": True, "uid": [1000] * 3, "gid": [1001] * 3,
                           "groups": [1000], "capabilities_zero": True}
        baseline_client = {"result": "success", "peer_uid": 0, "peer_gid": 0,
                           "response": "root-service\n"}
        service_for_case = {"clients": [{"peer_uid": 0, "peer_gid": 0,
                                          "response": "root-service\n"}],
                            "second_client": "NOT RECEIVED WITHIN TIMEOUT"}
        positive_client = {"result": "success", "peer_uid": 1000, "peer_gid": 1001,
                           "response": "fake-service\n"}
        child_denied = {"identity": target_identity, "fixture_reachable": True,
                        "connect": {"result": "error", "errno": 13},
                        "rename": {"result": "error", "errno": 13}}
        child_positive = {"identity": target_identity, "fixture_reachable": True,
                          "connect": {"result": "error", "errno": 13},
                          "rename": {"result": "success"},
                          "replacement": {"result": "success", "device": 1, "inode": 301},
                          "fake_server": "served"}
        server_a, server_b = mock.Mock(), mock.Mock()
        thread_a, thread_b = mock.Mock(), mock.Mock()
        thread_a.is_alive.return_value = False
        thread_b.is_alive.return_value = False
        services = [service_for_case.copy(), service_for_case.copy()]
        make_server = [
            (server_a, {"device": 1, "inode": 201, "uid": 0, "gid": 0, "type": "socket"},
             services[0], thread_a),
            (server_b, {"device": 1, "inode": 202, "uid": 0, "gid": 0, "type": "socket"},
             services[1], thread_b),
        ]
        target_runs = [
            (child_denied, 0, {"reaped": True, "root_client": {"result": "NOT RUN"}}),
            (child_positive, 0, {"reaped": True, "root_client": positive_client}),
        ]
        events = []
        context = {"run_fd": 10, "root_fd": 11, "run_stat": SimpleNamespace(st_dev=1),
                   "python": "/usr/bin/python3", "root_acl": {}, "run_acl": {},
                   "mount": {"status": "read", "mount_id": "34", "filesystem_type": "tmpfs"}}

        def stat_fake(path, **kwargs):
            if path == "service.sock":
                return fake_info
            if str(path).startswith("sock-path-probe-"):
                return root_info
            raise AssertionError(f"unexpected simulated stat path: {path}")

        def root_client_call(path):
            events.append("root-client")
            return baseline_client

        def target_call(*args):
            events.append("target")
            return target_runs.pop(0)

        with mock.patch.object(probe, "_verify_runtime_paths"), \
             mock.patch.object(probe.secrets, "token_hex", return_value="fixed-random-root"), \
             mock.patch.object(probe, "_create_lab_root", return_value=(20, lab_identity)), \
             mock.patch.object(probe, "_create_case_dir", side_effect=[(21, case_identity), (22, case_identity)]), \
             mock.patch.object(probe, "_make_root_server", side_effect=make_server), \
             mock.patch.object(probe, "_root_client", side_effect=root_client_call) as root_clients, \
             mock.patch.object(probe, "_run_target", side_effect=target_call) as run_target, \
             mock.patch.object(probe, "_cleanup_case", return_value="COMPLETE"), \
             mock.patch.object(probe.os, "stat", side_effect=stat_fake), \
             mock.patch.object(probe.os, "fstat", return_value=root_info), \
             mock.patch.object(probe.os, "listdir", return_value=[]), \
             mock.patch.object(probe.os, "close"):
            result = probe._run_lab(context)

        self.assertEqual(result["status"], "COMPLETE")
        self.assertEqual(result["cases"][0]["status"], "COMPLETE")
        self.assertEqual(result["cases"][1]["status"], "COMPLETE")
        self.assertEqual(result["cases"][1]["positive_control"], "CONFIRMED")
        self.assertEqual(events, ["root-client", "target", "root-client", "target"])
        self.assertEqual(root_clients.call_count, 2)
        self.assertEqual(run_target.call_count, 2)
        self.assertIn("for index in range(2)", inspect.getsource(probe._make_root_server))

    def test_cleanup_unknown_entry_or_unlink_failure_is_blocked_without_rmdir(self):
        case_id = {"device": 1, "inode": 2, "uid": 0, "gid": 0, "type": "directory"}
        socket_id = {"device": 1, "inode": 3, "uid": 0, "gid": 0, "type": "socket"}
        with mock.patch.object(probe, "_lock_case_dir", return_value=True), \
             mock.patch.object(probe.os, "listdir", return_value=["unknown"]), \
             mock.patch.object(probe, "_remove_known") as remove, \
             mock.patch.object(probe.os, "rmdir") as rmdir:
            self.assertEqual(probe._cleanup_case(10, "case", 11, case_id, {}), "BLOCKED")
            remove.assert_not_called()
            rmdir.assert_not_called()

        socket_stat = SimpleNamespace(st_dev=1, st_ino=3, st_uid=0, st_gid=0,
                                      st_mode=stat.S_IFSOCK | 0o600)
        with mock.patch.object(probe, "_lock_case_dir", return_value=True), \
             mock.patch.object(probe.os, "listdir", return_value=["service.sock"]), \
             mock.patch.object(probe.os, "stat", return_value=socket_stat), \
             mock.patch.object(probe, "_remove_known", return_value=False), \
             mock.patch.object(probe.os, "rmdir") as rmdir:
            self.assertEqual(probe._cleanup_case(10, "case", 11, case_id,
                                                 {"service.sock": socket_id}), "BLOCKED")
            rmdir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
