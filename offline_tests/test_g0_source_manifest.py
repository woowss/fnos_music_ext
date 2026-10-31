"""Safety contract for the explicit, read-only G0 source manifest utility."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock


TOOL_PATH = Path(__file__).resolve().parents[1] / "tools" / "g0_source_manifest.py"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("g0_source_manifest_test_target", TOOL_PATH)
assert SPEC is not None and SPEC.loader is not None
manifest = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = manifest
SPEC.loader.exec_module(manifest)


def _write_tree(root: Path, *, include_identity: bool = True, include_observer: bool = True) -> dict[str, bytes]:
    contents = {
        "VERSION": b"2.8.2\n",
        "proxy/app.py": b"app-source\n",
        "proxy/webui_gateway.py": b"gateway-source\n",
        "proxy/takeover.py": b"takeover-source\n",
        "proxy/gateway_identity.py": b"identity-source\n",
        "proxy/gateway_observation.py": b"observer-source\n",
        "webui-service/app.py": b"webui-source\n",
        "fnmusic-ext.service": b"[Unit]\n# repository template only\n",
    }
    if not include_identity:
        contents.pop("proxy/gateway_identity.py")
    if not include_observer:
        contents.pop("proxy/gateway_observation.py")
    for relative, content in contents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return contents


@contextlib.contextmanager
def _patched_dirfd_open(wrapper):
    """Keep the Linux capability probe valid while replacing os.open in a test."""
    supported = set(os.supports_dir_fd)
    with mock.patch.object(manifest.os, "open", side_effect=wrapper) as patched_open:
        with mock.patch.object(manifest.os, "supports_dir_fd", supported | {patched_open}):
            yield patched_open


@unittest.skipUnless(sys.platform == "linux", "requires Linux dir_fd and O_NOFOLLOW semantics")
class LinuxManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fnmusic-g0-manifest-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.contents = _write_tree(self.root)

    def _entries(self, report):
        return {item["path"]: item for item in report["entries"]}

    def test_hashes_only_allowlisted_regular_files_and_keeps_g0_false(self):
        report = manifest.collect_manifest(str(self.root))
        self.assertNotIn("error", report)
        self.assertEqual(report["scope"], "installed_repo_source_only")
        self.assertFalse(report["g0_pass"])
        self.assertFalse(report["production_ready"])
        self.assertFalse(report["effective_unit_verified"])
        self.assertEqual(report["repository_root"], str(self.root))

        entries = self._entries(report)
        self.assertEqual(set(entries), {path for path, _role in manifest._FILES})
        for relative, content in self.contents.items():
            entry = entries[relative]
            self.assertTrue(entry["exists"])
            self.assertTrue(entry["stable"])
            self.assertEqual(entry["status"], "hashed")
            self.assertEqual(entry["size"], len(content))
            self.assertEqual(entry["sha256"], hashlib.sha256(content).hexdigest())
            for key in ("uid", "gid", "mode", "device", "inode"):
                self.assertIn(key, entry)
        self.assertEqual(entries["VERSION"]["version"], "2.8.2")
        self.assertEqual(entries["fnmusic-ext.service"]["role"], "unit_template")
        self.assertLessEqual(len(manifest._encode(report).encode("utf-8")) + 1, manifest.MAX_OUTPUT_BYTES)

    def test_missing_identity_and_observer_are_reported_independently(self):
        (self.root / "proxy/gateway_identity.py").unlink()
        (self.root / "proxy/gateway_observation.py").unlink()
        entries = self._entries(manifest.collect_manifest(str(self.root)))
        for relative in ("proxy/gateway_identity.py", "proxy/gateway_observation.py"):
            self.assertEqual(entries[relative]["status"], "missing")
            self.assertFalse(entries[relative]["exists"])
            self.assertNotIn("sha256", entries[relative])

    def test_invalid_version_is_hashed_but_never_reported_as_a_version(self):
        (self.root / "VERSION").write_bytes(b"2.8.2\nCOOKIE_CANARY\n")
        entry = self._entries(manifest.collect_manifest(str(self.root)))["VERSION"]
        self.assertEqual(entry["status"], "invalid_version")
        self.assertTrue(entry["stable"])
        self.assertNotIn("version", entry)
        self.assertNotIn("COOKIE_CANARY", json.dumps(entry))

    def test_symlink_root_and_intermediate_directory_fail_closed(self):
        alias = Path(self.temp.name) / "repo-alias"
        alias.symlink_to(self.root, target_is_directory=True)
        report = manifest.collect_manifest(str(alias))
        self.assertEqual(report["error"], "unsafe_root")

        proxy = self.root / "proxy"
        moved = self.root / "proxy-real"
        proxy.rename(moved)
        proxy.symlink_to(moved, target_is_directory=True)
        entries = self._entries(manifest.collect_manifest(str(self.root)))
        self.assertEqual(entries["proxy/app.py"]["status"], "unsafe_path")
        self.assertNotIn("sha256", entries["proxy/app.py"])

    def test_symlink_leaf_and_fifo_are_never_hashed_or_opened_for_content(self):
        outside = Path(self.temp.name) / "outside-canary"
        outside.write_bytes(b"DO_NOT_READ_CANARY")
        target = self.root / "proxy/app.py"
        target.unlink()
        target.symlink_to(outside)
        takeover = self.root / "proxy/takeover.py"
        takeover.unlink()
        os.mkfifo(takeover)

        report = manifest.collect_manifest(str(self.root))
        entries = self._entries(report)
        self.assertEqual(entries["proxy/app.py"]["status"], "non_regular")
        self.assertEqual(entries["proxy/takeover.py"]["status"], "non_regular")
        serialized = json.dumps(report)
        self.assertNotIn("DO_NOT_READ_CANARY", serialized)
        self.assertNotIn(hashlib.sha256(b"DO_NOT_READ_CANARY").hexdigest(), serialized)

    def test_relative_and_parent_traversal_roots_are_rejected_before_io(self):
        for value in ("relative/repo", str(self.root) + "/../repo", "/tmp//repo", "/tmp/./repo"):
            with self.subTest(value=value):
                with self.assertRaises(manifest._ManifestError) as caught:
                    manifest._root_components(value)
                self.assertEqual(caught.exception.code, "invalid_root")

    def test_nonregular_leaf_and_oversized_file_are_not_hashed(self):
        oversized = self.root / "webui-service/app.py"
        with oversized.open("wb") as stream:
            stream.truncate(manifest.MAX_FILE_BYTES + 1)
        entry = self._entries(manifest.collect_manifest(str(self.root)))["webui-service/app.py"]
        self.assertEqual(entry["status"], "too_large")
        self.assertTrue(entry["stable"])
        self.assertEqual(entry["size"], manifest.MAX_FILE_BYTES + 1)
        self.assertNotIn("sha256", entry)

    def test_changed_file_during_hash_is_unstable_and_digest_is_withheld(self):
        target = self.root / "proxy/app.py"
        target.write_bytes(b"A" * (manifest.READ_CHUNK_BYTES * 2))
        original_read = os.read
        original_open = os.open
        changed = [False]
        target_fd = [None]

        def track_target_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == "app.py" and target_fd[0] is None:
                target_fd[0] = fd
            return fd

        def read_then_modify(fd, count):
            chunk = original_read(fd, count)
            if fd == target_fd[0] and chunk and not changed[0]:
                changed[0] = True
                write_fd = original_open(target, os.O_WRONLY)
                try:
                    os.pwrite(write_fd, b"Z", 0)
                finally:
                    os.close(write_fd)
            return chunk

        with _patched_dirfd_open(track_target_open), \
             mock.patch.object(manifest.os, "read", side_effect=read_then_modify):
            entry = self._entries(manifest.collect_manifest(str(self.root)))["proxy/app.py"]
        self.assertIsNotNone(target_fd[0])
        self.assertTrue(changed[0])
        self.assertEqual(entry["status"], "unstable")
        self.assertFalse(entry["stable"])
        self.assertNotIn("sha256", entry)

    def test_replaced_leaf_during_hash_is_unstable_and_digest_is_withheld(self):
        target = self.root / "proxy/app.py"
        target.write_bytes(b"original-content" * 8192)
        moved = target.with_name("app.py.old")
        original_read = os.read
        original_open = os.open
        original_replace = os.replace
        replaced = [False]
        target_fd = [None]

        def track_target_open(path, flags, *args, **kwargs):
            fd = original_open(path, flags, *args, **kwargs)
            if path == "app.py" and target_fd[0] is None:
                target_fd[0] = fd
            return fd

        def read_then_replace(fd, count):
            chunk = original_read(fd, count)
            if fd == target_fd[0] and chunk and not replaced[0]:
                replaced[0] = True
                original_replace(target, moved)
                write_fd = original_open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    os.write(write_fd, b"replacement")
                finally:
                    os.close(write_fd)
            return chunk

        with _patched_dirfd_open(track_target_open), \
             mock.patch.object(manifest.os, "read", side_effect=read_then_replace):
            entry = self._entries(manifest.collect_manifest(str(self.root)))["proxy/app.py"]
        self.assertIsNotNone(target_fd[0])
        self.assertTrue(replaced[0])
        self.assertEqual(entry["status"], "unstable")
        self.assertFalse(entry["stable"])
        self.assertNotIn("sha256", entry)

    def test_regular_file_swapped_for_fifo_before_open_is_never_read(self):
        target = self.root / "proxy/app.py"
        original_open = os.open
        original_unlink = os.unlink
        original_mkfifo = os.mkfifo
        original_read = os.read
        swapped = [False]

        target_fd = [None]

        def swap_before_leaf_open(path, flags, *args, **kwargs):
            if path == "app.py" and not swapped[0]:
                swapped[0] = True
                original_unlink(target)
                original_mkfifo(target)
            fd = original_open(path, flags, *args, **kwargs)
            if path == "app.py":
                target_fd[0] = fd
            return fd

        def unexpected_read(fd, *args, **kwargs):
            if fd == target_fd[0]:
                raise AssertionError("swapped special file was read")
            return original_read(fd, *args, **kwargs)

        with mock.patch.object(manifest, "_FILES", (("proxy/app.py", "source"),)), \
             _patched_dirfd_open(swap_before_leaf_open), \
             mock.patch.object(manifest.os, "read", side_effect=unexpected_read):
            report = manifest.collect_manifest(str(self.root))
        self.assertNotIn("error", report)
        entry = self._entries(report)["proxy/app.py"]
        self.assertTrue(swapped[0])
        self.assertIsNotNone(target_fd[0])
        self.assertEqual(entry["status"], "unstable")
        self.assertFalse(entry["stable"])
        self.assertNotIn("sha256", entry)

    def test_canary_files_are_not_opened_or_disclosed_and_runtime_is_read_only(self):
        canary_content = "COOKIE=COOKIE_CANARY_DO_NOT_DISCLOSE"
        (self.root / ".env").write_text(canary_content, encoding="utf-8")
        (self.root / "logs").mkdir()
        (self.root / "logs/proxy.log").write_text("LOG_CANARY_DO_NOT_DISCLOSE", encoding="utf-8")
        (self.root / "cookies.json").write_text("TOKEN_CANARY_DO_NOT_DISCLOSE", encoding="utf-8")

        opened = []
        original_open = os.open

        def track_open(path, flags, *args, **kwargs):
            opened.append(os.fspath(path))
            return original_open(path, flags, *args, **kwargs)

        def forbidden(*_args, **_kwargs):
            raise AssertionError("forbidden side effect attempted")

        with _patched_dirfd_open(track_open), \
             mock.patch.object(manifest.os, "write", side_effect=forbidden), \
             mock.patch.object(manifest.os, "mkdir", side_effect=forbidden), \
             mock.patch.object(manifest.os, "unlink", side_effect=forbidden), \
             mock.patch.object(manifest.os, "rename", side_effect=forbidden), \
             mock.patch.object(manifest.os, "replace", side_effect=forbidden), \
             mock.patch.object(manifest.os, "chmod", side_effect=forbidden), \
             mock.patch.object(manifest.os, "chown", side_effect=forbidden, create=True), \
             mock.patch.object(socket, "socket", side_effect=forbidden), \
             mock.patch.object(subprocess, "run", side_effect=forbidden), \
             mock.patch.object(subprocess, "Popen", side_effect=forbidden), \
             mock.patch.object(threading.Thread, "start", side_effect=forbidden):
            report = manifest.collect_manifest(str(self.root))

        self.assertNotIn("error", report)
        self.assertEqual(len(report["entries"]), len(manifest._FILES))
        self.assertTrue(any(value == "VERSION" for value in opened))
        serialized = json.dumps(report)
        for canary in ("COOKIE_CANARY_DO_NOT_DISCLOSE", "LOG_CANARY_DO_NOT_DISCLOSE", "TOKEN_CANARY_DO_NOT_DISCLOSE"):
            self.assertNotIn(canary, serialized)
        self.assertFalse(any(value in {".env", "logs", "proxy.log", "cookies.json"} for value in opened))
        self.assertLessEqual(len(serialized.encode("utf-8")) + 1, manifest.MAX_OUTPUT_BYTES)


class PortableManifestTests(unittest.TestCase):
    def test_import_has_no_filesystem_network_process_thread_or_write_side_effects(self):
        source = TOOL_PATH.read_bytes()
        code = compile(source, str(TOOL_PATH), "exec")

        def forbidden(*_args, **_kwargs):
            raise AssertionError("module import attempted a side effect")

        namespace = {"__name__": "g0_manifest_import_probe", "__file__": str(TOOL_PATH)}
        with mock.patch.object(manifest.os, "open", side_effect=forbidden), \
             mock.patch.object(manifest.os, "write", side_effect=forbidden), \
             mock.patch.object(manifest.os, "mkdir", side_effect=forbidden), \
             mock.patch.object(manifest.os, "unlink", side_effect=forbidden), \
             mock.patch.object(manifest.os, "rename", side_effect=forbidden), \
             mock.patch.object(manifest.os, "replace", side_effect=forbidden), \
             mock.patch.object(manifest.os, "chmod", side_effect=forbidden), \
             mock.patch.object(manifest.os, "chown", side_effect=forbidden, create=True), \
             mock.patch.object(socket, "socket", side_effect=forbidden), \
             mock.patch.object(subprocess, "run", side_effect=forbidden), \
             mock.patch.object(subprocess, "Popen", side_effect=forbidden), \
             mock.patch.object(threading.Thread, "start", side_effect=forbidden):
            exec(code, namespace)

    def test_usage_response_has_fixed_readiness_fields_and_no_argument_echo(self):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = manifest.main(["TOKEN_CANARY_DO_NOT_DISCLOSE", "unexpected-extra-argument"])
        report = json.loads(stdout.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(report["error"], "usage")
        self.assertFalse(report["g0_pass"])
        self.assertFalse(report["production_ready"])
        self.assertFalse(report["effective_unit_verified"])
        self.assertNotIn("TOKEN_CANARY_DO_NOT_DISCLOSE", stdout.getvalue())

    def test_root_parser_rejects_relative_and_normalization_paths(self):
        for value in ("relative", "/tmp/../repo", "/tmp//repo", "/tmp/./repo", "/tmp/"):
            with self.subTest(value=value):
                with self.assertRaises(manifest._ManifestError) as caught:
                    manifest._root_components(value)
                self.assertEqual(caught.exception.code, "invalid_root")


if __name__ == "__main__":
    unittest.main()
