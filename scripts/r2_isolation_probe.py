#!/usr/bin/env python3
"""Read-only fnmusic-ext isolation probe; the only writes are in a private /tmp dir.

This is a diagnostic, not a production security fix. It must be run manually on
the NAS by an administrator after reviewing docs/R2_ISOLATION_PROBE.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

PRODUCTION_SOCKET = Path("/vol1/@appcenter/fnmusic-ext/fnmusic-ext.sock")
PRODUCTION_DIR = PRODUCTION_SOCKET.parent
CONTAINER_NAME = "fnmusic-sources"
COMMAND_TIMEOUT_SECONDS = 4
PROBE_TIMEOUT_SECONDS = 8
SAFE_BIN_DIRS = (Path("/usr/sbin"), Path("/usr/bin"), Path("/sbin"), Path("/bin"), Path("/usr/local/bin"))
SAFE_PATH = "/usr/sbin:/usr/bin:/sbin:/bin:/usr/local/bin"
SAFE_SUBPROCESS_ENV = {"PATH": SAFE_PATH, "LC_ALL": "C", "PYTHONNOUSERSITE": "1"}


def _blocked(reason: str) -> dict:
    return {"status": "BLOCKED", "reason": reason}


def _run_readonly(argv: list[str], timeout: int = COMMAND_TIMEOUT_SECONDS):
    """Run a fixed, read-only command and discard stderr and all raw errors."""
    try:
        return subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout,
            check=False,
            env=SAFE_SUBPROCESS_ENV,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _trusted_executable(name: str) -> str | None:
    """Find a root-owned binary under non-writable system directories only."""
    for directory in SAFE_BIN_DIRS:
        candidate = directory / name
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            continue
        parent = resolved.parent
        safe = True
        while True:
            try:
                parent_info = parent.stat()
            except OSError:
                safe = False
                break
            if (not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != 0
                    or parent_info.st_mode & 0o022):
                safe = False
                break
            if parent.parent == parent:
                break
            parent = parent.parent
        if safe:
            return str(resolved)
    return None


def _local_passwd_entry(username: str):
    """Read only /etc/passwd so account lookup cannot invoke network NSS."""
    try:
        entries = Path("/etc/passwd").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in entries:
        fields = line.split(":")
        if len(fields) < 7 or fields[0] != username:
            continue
        try:
            return int(fields[2]), int(fields[3])
        except ValueError:
            return None
    return None


def _path_observation(path: Path) -> dict:
    result = {"path": str(path), "status": "NOT FOUND"}
    try:
        info = path.lstat()
    except OSError:
        return result

    result.update(
        status="PRESENT",
        type=("symlink" if stat.S_ISLNK(info.st_mode) else
              "socket" if stat.S_ISSOCK(info.st_mode) else
              "directory" if stat.S_ISDIR(info.st_mode) else "other"),
        mode=f"{stat.S_IMODE(info.st_mode):04o}",
        uid=info.st_uid,
        gid=info.st_gid,
    )

    if not stat.S_ISLNK(info.st_mode):
        getfacl = _trusted_executable("getfacl")
        if getfacl:
            acl = _run_readonly([getfacl, "-n", "-cp", "--", str(path)])
            if acl and acl.returncode == 0:
                lines = [line for line in acl.stdout.splitlines() if line and not line.startswith("#")]
                result["acl"] = {
                    "status": "read",
                    "entry_count": len(lines),
                    "has_named_entries": any(
                        line.partition(":")[0] in {"user", "group"}
                        and len(line.split(":", 2)) == 3
                        and bool(line.split(":", 2)[1])
                        for line in lines
                    ),
                    "has_mask": any(line.startswith("mask::") for line in lines),
                }
            else:
                result["acl"] = {"status": "unavailable"}
        else:
            result["acl"] = {"status": "getfacl unavailable"}

        findmnt = _trusted_executable("findmnt")
        if findmnt:
            mount = _run_readonly([findmnt, "--noheadings", "--output", "FSTYPE", "--target", str(path)])
            if mount and mount.returncode == 0:
                result["filesystem_type"] = mount.stdout.strip()[:80]
            else:
                result["filesystem_type"] = "unavailable"
        else:
            result["filesystem_type"] = "findmnt unavailable"
    else:
        result["acl"] = {"status": "not queried for symlink"}
        result["filesystem_type"] = "not queried for symlink"
    return result


def _read_proc_identity(pid: int) -> dict:
    root = Path("/proc") / str(pid)
    result = {"status": "NOT RUN"}
    try:
        uid_map = (root / "uid_map").read_text(encoding="ascii").strip()
        gid_map = (root / "gid_map").read_text(encoding="ascii").strip()
        status = (root / "status").read_text(encoding="ascii")
    except OSError:
        return result

    cap_eff = None
    for line in status.splitlines():
        if line.startswith("CapEff:"):
            cap_eff = line.split(":", 1)[1].strip()
            break
    result = {
        "status": "read",
        "uid_map": uid_map[:160],
        "gid_map": gid_map[:160],
        "effective_capabilities_hex": cap_eff,
    }
    return result


def _container_observation() -> dict:
    docker = _trusted_executable("docker")
    if not docker:
        return {"status": "NOT RUN", "reason": "docker command unavailable"}
    docker_socket = Path("/var/run/docker.sock")
    try:
        if not stat.S_ISSOCK(docker_socket.stat().st_mode):
            return {"status": "NOT RUN", "reason": "local Docker socket unavailable"}
    except OSError:
        return {"status": "NOT RUN", "reason": "local Docker socket unavailable"}

    template = (
        "{{.State.Status}}|{{.State.Pid}}|{{.Config.User}}|"
        "{{.HostConfig.UsernsMode}}|{{.HostConfig.NetworkMode}}|{{.Image}}|"
        "{{range .Mounts}}{{.Type}}:{{.RW}};{{end}}"
    )
    inspected = _run_readonly(
        [docker, "--host", "unix:///var/run/docker.sock", "inspect", "--format", template, CONTAINER_NAME],
        timeout=COMMAND_TIMEOUT_SECONDS,
    )
    if not inspected or inspected.returncode != 0:
        return {"status": "NOT RUN", "reason": "docker inspect unavailable or denied"}

    fields = inspected.stdout.rstrip("\n").split("|", 6)
    if len(fields) != 7:
        return {"status": "BLOCKED", "reason": "unexpected docker inspect response"}
    state, pid_raw, configured_user, userns_mode, network_mode, image_id, mount_raw = fields
    try:
        pid = int(pid_raw)
    except ValueError:
        return {"status": "BLOCKED", "reason": "container process id unavailable"}

    image_user = ""
    if image_id.startswith("sha256:") and all(c in "0123456789abcdef" for c in image_id[7:]) and len(image_id) == 71:
        image_inspect = _run_readonly(
            [docker, "--host", "unix:///var/run/docker.sock", "image", "inspect", "--format", "{{.Config.User}}", image_id],
            timeout=COMMAND_TIMEOUT_SECONDS,
        )
        if image_inspect and image_inspect.returncode == 0:
            image_user = image_inspect.stdout.strip()[:80]

    mounts = []
    for item in mount_raw.split(";")[:64]:
        if not item:
            continue
        kind, _, writable = item.partition(":")
        mounts.append({"type": kind[:32], "writable": writable.lower() == "true"})

    return {
        "status": "read",
        "container": CONTAINER_NAME,
        "state": state[:40],
        "pid": pid,
        "configured_user": configured_user[:80] or "image default",
        "image_user": image_user or "unavailable",
        "userns_mode": userns_mode[:80] or "daemon default",
        "network_mode": network_mode[:80],
        "mounts": mounts,
        "process_identity": _read_proc_identity(pid),
    }


def _different_uid_client_code() -> str:
    return r"""
import json, os, socket, sys
path = sys.argv[1]
capabilities = {}
try:
    for line in open('/proc/self/status', encoding='ascii'):
        key, sep, value = line.partition(':')
        if sep and key in ('CapEff', 'CapPrm', 'CapInh', 'CapAmb'):
            capabilities[key] = value.strip()
except OSError:
    pass
result = {
    'uid': os.getuid(), 'euid': os.geteuid(), 'gid': os.getgid(), 'egid': os.getegid(),
    'groups': os.getgroups(), 'capabilities': capabilities,
}
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(2)
try:
    s.connect(path)
    result.update(connect='allowed')
except OSError as exc:
    result.update(connect='denied', errno=exc.errno)
finally:
    s.close()
print(json.dumps(result, sort_keys=True))
"""


def _different_uid_argv(socket_path: Path) -> list[str]:
    return [sys.executable, "-I", "-S", "-c", _different_uid_client_code(), str(socket_path)]


def _dac_experiment(target_name: str, target_uid: int, target_gid: int) -> dict:
    if target_uid == 0 or target_uid == os.geteuid():
        return {"status": "BLOCKED", "reason": "target user must be a distinct non-root uid"}

    temp_root = None
    probe_dir = None
    server = None
    server_thread = None
    server_result = {"accepted_expected_peer": False, "error": None}
    try:
        temp_root = Path("/tmp").resolve(strict=True)
        temp_info = temp_root.stat()
        if (not stat.S_ISDIR(temp_info.st_mode) or temp_info.st_uid != 0
                or temp_info.st_mode & 0o1002 != 0o1002):
            return {"status": "BLOCKED", "reason": "/tmp is not a root-owned sticky world-writable directory"}
        probe_dir = Path(tempfile.mkdtemp(prefix="r2p-", dir=str(temp_root)))
        os.chmod(probe_dir, 0o711)
        sock_path = probe_dir / "s"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        os.chmod(sock_path, 0o600)
        server.listen(4)
        server.settimeout(PROBE_TIMEOUT_SECONDS)

        def serve_expected_peer():
            deadline = time.monotonic() + PROBE_TIMEOUT_SECONDS
            while time.monotonic() < deadline:
                try:
                    conn, _ = server.accept()
                except socket.timeout:
                    server_result["error"] = "accept timeout"
                    return
                with conn:
                    try:
                        peer_uid = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                        _, peer_uid, _ = struct.unpack("3i", peer_uid)
                    except (OSError, AttributeError, struct.error):
                        server_result["error"] = "SO_PEERCRED unavailable"
                        return
                    if peer_uid == os.geteuid():
                        conn.sendall(b"probe-ok\n")
                        server_result["accepted_expected_peer"] = True
                        return

        server_thread = threading.Thread(target=serve_expected_peer, daemon=True)
        server_thread.start()

        argv = _different_uid_argv(sock_path)
        try:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                env=SAFE_SUBPROCESS_ENV,
                user=target_uid,
                group=target_gid,
                extra_groups=[],
            )
        except TypeError:
            return {"status": "BLOCKED", "reason": "Python Popen lacks safe uid/gid options"}
        except (OSError, subprocess.SubprocessError):
            return {"status": "BLOCKED", "reason": "could not start privilege-dropped child"}
        try:
            child_stdout, _ = process.communicate(timeout=PROBE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
                process.communicate(timeout=2)
            except (OSError, subprocess.SubprocessError, subprocess.TimeoutExpired):
                pass
            return {"status": "BLOCKED", "reason": "different-uid child timed out"}

        if process.returncode != 0:
            return {"status": "BLOCKED", "reason": "different-uid child failed"}
        try:
            child_result = json.loads(child_stdout)
        except (json.JSONDecodeError, TypeError):
            return {"status": "BLOCKED", "reason": "different-uid child returned invalid result"}
        if (child_result.get("uid") != target_uid or child_result.get("euid") != target_uid
                or child_result.get("gid") != target_gid or child_result.get("egid") != target_gid
                or child_result.get("groups") != []):
            return {"status": "BLOCKED", "reason": "child identity did not match requested uid/gid"}
        capabilities = child_result.get("capabilities")
        try:
            all_capabilities_zero = all(
                int(capabilities[key], 16) == 0 for key in ("CapEff", "CapPrm", "CapInh", "CapAmb")
            )
        except (KeyError, TypeError, ValueError):
            all_capabilities_zero = False
        if not all_capabilities_zero:
            return {"status": "BLOCKED", "reason": "could not verify all child capability sets are zero"}

        if child_result.get("connect") == "allowed":
            different_uid_status = "FAIL"
        elif child_result.get("connect") == "denied" and child_result.get("errno") == 13:
            different_uid_status = "PASS"
        else:
            different_uid_status = "BLOCKED"

        same_uid = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        same_uid.settimeout(2)
        try:
            same_uid.connect(str(sock_path))
            data = same_uid.recv(32)
        except OSError:
            data = b""
        finally:
            same_uid.close()
        server_thread.join(timeout=PROBE_TIMEOUT_SECONDS + 1)
        same_uid_ok = data == b"probe-ok\n" and server_result["accepted_expected_peer"]

        same_uid_status = "PASS" if same_uid_ok else "BLOCKED"
        if different_uid_status == "FAIL":
            result_status = "FAIL"
        elif different_uid_status == "PASS" and same_uid_ok:
            result_status = "PASS"
        else:
            result_status = "BLOCKED"
        return {
            "status": result_status,
            "filesystem": "/tmp",
            "target_user": target_name,
            "different_uid_connect": different_uid_status,
            "same_uid_connect": same_uid_status,
            "all_capability_sets_zero": all_capabilities_zero,
            "peer_credentials_checked": same_uid_status == "PASS",
            "server_diagnostic": server_result["error"],
            "scope": "temporary /tmp DAC behavior only",
        }
    except OSError:
        return {"status": "BLOCKED", "reason": "temporary DAC experiment could not complete"}
    finally:
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if server_thread is not None:
            server_thread.join(timeout=PROBE_TIMEOUT_SECONDS + 1)
        if probe_dir is not None:
            try:
                resolved = probe_dir.resolve(strict=True)
                info = resolved.lstat()
                if (temp_root is not None and resolved.parent == temp_root and resolved.name.startswith("r2p-")
                        and stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
                        and info.st_uid == os.geteuid()):
                    shutil.rmtree(resolved)
            except OSError:
                pass


def run_probe(target_user: str) -> dict:
    if not sys.platform.startswith("linux"):
        return _blocked("Linux is required")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        return _blocked("run manually as root; this script does not invoke sudo")
    if not target_user or any(c in target_user for c in ":\r\n\x00"):
        return _blocked("invalid target user name")
    try:
        target = _local_passwd_entry(target_user)
    except OSError:
        target = None
    if target is None:
        return _blocked("target user does not exist")
    target_uid, target_gid = target
    if target_uid == 0:
        return _blocked("target user must not be root")

    dac = _dac_experiment(target_user, target_uid, target_gid)
    return {
        "status": "COMPLETE" if dac["status"] == "PASS" else dac["status"],
        "platform": platform.system(),
        "euid": os.geteuid(),
        "temporary_dac_experiment": dac,
        "production_paths_read_only": [
            _path_observation(PRODUCTION_DIR),
            _path_observation(PRODUCTION_SOCKET),
        ],
        "container_observation": _container_observation(),
        "interpretation": (
            "The /tmp test does not prove access behavior on the production filesystem, "
            "container isolation, or that R2 is fixed."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-user", default="kurumi", help="existing non-root user for the DAC test")
    args = parser.parse_args(argv)
    result = run_probe(args.target_user)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result["status"] == "COMPLETE" else (2 if result["status"] == "BLOCKED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
