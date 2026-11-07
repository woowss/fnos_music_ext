"""Guarded local tests for the manually run R2 isolation probe."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "r2_isolation_probe.py"
SPEC = importlib.util.spec_from_file_location("r2_isolation_probe_test", SCRIPT)
if SPEC is None or SPEC.loader is None:
    raise AssertionError("cannot load R2 probe module")
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


class R2IsolationProbeGuardTests(unittest.TestCase):
    def test_source_compiles_without_creating_bytecode(self):
        source = SCRIPT.read_text(encoding="utf-8")
        compile(source, str(SCRIPT), "exec")

    def test_document_pins_the_exact_script_and_aborts_on_hash_mismatch(self):
        source_hash = hashlib.sha256(SCRIPT.read_bytes()).hexdigest()
        document = (SCRIPT.parents[1] / "docs" / "R2_ISOLATION_PROBE.md").read_text(encoding="utf-8")
        pinned_hashes = re.findall(r"(?m)^expected_hash=([0-9a-f]{64})$", document)
        self.assertEqual(pinned_hashes, [source_hash])
        self.assertIn("SHA-256 mismatch; probe not run", document)
        self.assertIn("mktemp -d /root/fnmusic-r2-probe.XXXXXX", document)
        self.assertIn("(\nset -eu", document)
        self.assertIn("python3 -I -S \"$probe_file\"", document)
        self.assertIn("python3 -I -S \"$probe_file\" --target-user kurumi\n)", document)
        self.assertIn('[ ! -e "$probe_file" ] && [ ! -L "$probe_file" ]', document)
        self.assertIn('[ "$actual_hash" = "$expected_hash" ] ||', document)
        self.assertIn("trap cleanup 0", document)
        self.assertNotIn("rm -rf", document)

    def test_document_shell_allows_only_matching_hash_and_cleans_own_directory(self):
        bash = shutil.which("bash")
        if not bash and os.name == "nt":
            candidate = Path(r"C:\Program Files\Git\bin\bash.exe")
            bash = str(candidate) if candidate.is_file() else None
        if not bash:
            self.skipTest("BLOCKED: no Bash/sh executable available for real shell validation")

        document = (SCRIPT.parents[1] / "docs" / "R2_ISOLATION_PROBE.md").read_text(encoding="utf-8")
        match = re.search(r"```sh\n(.*?)\n```", document, re.DOTALL)
        self.assertIsNotNone(match)
        shell_block = match.group(1)
        pinned_hash = re.search(r"(?m)^expected_hash=([0-9a-f]{64})$", shell_block).group(1)

        def posix_path(path: Path) -> str:
            if os.name != "nt":
                return str(path)
            drive, tail = os.path.splitdrive(str(path))
            return f"/{drive[0].lower()}{tail.replace(os.sep, '/')}"

        def execute_case(expected_hash: str):
            with tempfile.TemporaryDirectory(prefix="r2-shell-test-") as temp_name:
                base = Path(temp_name)
                fake_root = base / "root"
                fake_root.mkdir()
                fake_tmp = base / "tmp"
                fake_tmp.mkdir()
                source = fake_tmp / "r2_isolation_probe.py"
                source.write_bytes(SCRIPT.read_bytes())
                marker = base / "python-was-invoked"
                root_path = posix_path(fake_root)
                source_path = posix_path(source)
                marker_path = posix_path(marker)

                adapted = shell_block.replace(
                    "source_file=/tmp/r2_isolation_probe.py",
                    f"source_file={shlex.quote(source_path)}",
                )
                adapted = adapted.replace(
                    "expected_hash=" + pinned_hash,
                    "expected_hash=" + expected_hash,
                )
                adapted = adapted.replace(
                    "/root/fnmusic-r2-probe.",
                    root_path + "/fnmusic-r2-probe.",
                )

                shims = f"""
stat() {{
    if [ "$1" = "-c" ] && [ "$2" = "%u:%a:%F" ] && [ -d "$3" ]; then
        printf '%s\\n' '0:700:directory'
    elif [ "$1" = "-c" ] && [ "$2" = "%u" ] && [ -f "$3" ]; then
        printf '%s\\n' '0'
    else
        return 1
    fi
}}
install() {{
    [ "$1" = "-o" ] && [ "$2" = "root" ] && [ "$3" = "-g" ] && [ "$4" = "root" ]
    [ "$5" = "-m" ] && [ "$6" = "0700" ]
    command cp -- "$7" "$8"
    command chmod 700 "$8"
}}
python3() {{
    [ "$1" = "-I" ] && [ "$2" = "-S" ] && [ "$4" = "--target-user" ] && [ "$5" = "kurumi" ]
    [ -f "$3" ] || return 1
    : > {shlex.quote(marker_path)}
}}
"""
                result = subprocess.run(
                    [bash, "--noprofile", "--norc", "-c", shims + "\n" + adapted],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10,
                    check=False,
                )
                return result, marker.exists(), list(fake_root.iterdir())

        ok_result, ok_invoked, ok_leftovers = execute_case(pinned_hash)
        self.assertEqual(ok_result.returncode, 0, ok_result.stderr)
        self.assertTrue(ok_invoked, "matching hash did not reach the probe command")
        self.assertEqual(ok_leftovers, [], "successful run left its private directory behind")

        bad_hash = "0" * 64 if pinned_hash != "0" * 64 else "1" * 64
        bad_result, bad_invoked, bad_leftovers = execute_case(bad_hash)
        self.assertNotEqual(bad_result.returncode, 0)
        self.assertIn("SHA-256 mismatch; probe not run", bad_result.stderr)
        self.assertFalse(bad_invoked, "mismatched hash still invoked Python")
        self.assertEqual(bad_leftovers, [], "mismatch path left its private directory behind")

    def test_privilege_drop_uses_popen_credentials_not_preexec(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("subprocess.Popen(", source)
        self.assertIn("user=target_uid", source)
        self.assertIn("group=target_gid", source)
        self.assertIn("extra_groups=[]", source)
        self.assertIn("communicate(timeout=PROBE_TIMEOUT_SECONDS)", source)
        self.assertNotIn("preexec_fn", source)

    def test_actual_privilege_dropped_argv_uses_isolated_python(self):
        argv = PROBE._different_uid_argv(Path("/tmp/probe.sock"))
        self.assertEqual(argv[1:4], ["-I", "-S", "-c"])
        self.assertEqual(argv[-1], str(Path("/tmp/probe.sock")))

    def test_missing_target_user_fails_closed_before_observation(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--target-user", "__codex_probe_user_missing__"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5,
            check=False,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertNotIn("temporary_dac_experiment", payload)
        self.assertNotIn("container_observation", payload)
        self.assertIn(payload["reason"], {
            "Linux is required",
            "run manually as root; this script does not invoke sudo",
            "target user does not exist",
        })


if __name__ == "__main__":
    unittest.main()
