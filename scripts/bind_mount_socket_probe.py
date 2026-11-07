#!/usr/bin/env python3
"""Compare root-owned Unix-socket bind mounts in private and original mount namespaces.

The probe uses only random, self-created fixtures below /run and /vol1/1000. It
does not inspect or contact application paths. Real execution requires a
separately reviewed root-owned copy on Linux; offline tests do not emulate the
kernel mount operation.
"""

from __future__ import annotations

import ctypes
import errno
import json
import os
import secrets
import selectors
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path


RUN = Path("/run")
BASE = Path("/vol1/1000")
TARGET_USER = "kurumi"
TARGET_UID = 1000
TARGET_GID = 1001
TARGET_GROUPS = (1000,)
ROOT_RESPONSE = b"root-fixture-service\n"
FAKE_RESPONSE = b"fake-fixture-service\n"
SOCKET_TIMEOUT = 2.0
CHILD_TIMEOUT = 8.0
WORKER_TIMEOUT = 90.0
CONTROL_TIMEOUT = 8.0
MODES = {"0755": 0o755, "0777": 0o777, "acl": 0o775}
OPERATIONS = ("rename", "unlink", "replace")
ACL_EXPECTED = (
    {"tag": "user_obj", "id": None, "perm": 7},
    {"tag": "user", "id": 955, "perm": 7},
    {"tag": "group_obj", "id": None, "perm": 5},
    {"tag": "mask", "id": None, "perm": 7},
    {"tag": "other", "id": None, "perm": 5},
)


class ProbeBlocked(RuntimeError):
    def __init__(self, reason: str, details: dict | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or {}


def _status(name: str, **values) -> dict:
    return {"status": name, **values}


def _exception_record(exc: BaseException) -> dict:
    return {"type": exc.__class__.__name__,
            "errno": getattr(exc, "errno", None),
            "reason": exc.reason if isinstance(exc, ProbeBlocked) else str(exc)}


def _dir_flags() -> int:
    directory, nofollow = getattr(os, "O_DIRECTORY", 0), getattr(os, "O_NOFOLLOW", 0)
    if not directory or not nofollow:
        raise ProbeBlocked("directory O_NOFOLLOW support is required")
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _opath_flags() -> int:
    opath, nofollow = getattr(os, "O_PATH", 0), getattr(os, "O_NOFOLLOW", 0)
    if not opath or not nofollow:
        raise ProbeBlocked("O_PATH and O_NOFOLLOW are required for socket mount checks")
    return opath | nofollow | getattr(os, "O_CLOEXEC", 0)


def _kind(mode: int) -> str:
    if stat.S_ISDIR(mode):
        return "directory"
    if stat.S_ISSOCK(mode):
        return "socket"
    return "other"


def _identity(info) -> dict:
    return {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
            "gid": info.st_gid, "type": _kind(info.st_mode),
            "mode": stat.S_IMODE(info.st_mode)}


def _same_object(info, identity: dict, *, include_mode: bool = False) -> bool:
    current = _identity(info)
    keys = ("device", "inode", "uid", "gid", "type", "mode") if include_mode else (
        "device", "inode", "uid", "gid", "type")
    return all(current.get(key) == identity.get(key) for key in keys)


def _same_directory(left, right) -> bool:
    return (stat.S_ISDIR(left.st_mode) and stat.S_ISDIR(right.st_mode)
            and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino))


def _same_recorded_inode(left: dict | None, right: dict | None) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict)
            and left.get("device") == right.get("device")
            and left.get("inode") == right.get("inode")
            and left.get("type") == "directory"
            and right.get("type") == "directory")


def _identity_binding_verified(record: dict) -> bool:
    return (record.get("identity_binding") == "BOUND"
            and _same_recorded_inode(record.get("initial_identity"),
                                     record.get("identity")))


def _fail_identity_binding(record: dict, reason: str) -> None:
    if not record.get("identity_binding_failure"):
        record["identity_binding_failure"] = reason
    record["identity_binding"] = "FAILED"


def _record_bound_directory_identity(record: dict, info, stage: str) -> dict:
    if (not _identity_binding_verified(record)
            or not _same_object(info, record.get("initial_identity"))):
        _fail_identity_binding(record, f"{stage} did not match initial creation identity")
        record["identity_stage"] = f"{stage} identity mismatch"
        raise ProbeBlocked(f"{stage} no longer matches the directory creation identity")
    identity = _identity(info)
    record["identity"] = identity
    record["identity_stage"] = stage
    return identity


def _open_child_dir(parent_fd: int, name: str) -> tuple[int, os.stat_result]:
    if not name or name in (".", "..") or "/" in name:
        raise ProbeBlocked("directory entry name is not a fixed child")
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ProbeBlocked(f"{name} is not a real directory")
    fd = os.open(name, _dir_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_directory(before, opened) or not _same_directory(after, opened):
            raise ProbeBlocked(f"{name} changed while opening")
        return fd, opened
    except BaseException:
        os.close(fd)
        raise


def _open_absolute_dir(path: Path) -> tuple[int, os.stat_result]:
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise ProbeBlocked("directory path is not a fixed absolute path")
    fd = os.open("/", _dir_flags())
    try:
        current = os.fstat(fd)
        for component in path.parts[1:]:
            child, current = _open_child_dir(fd, component)
            os.close(fd)
            fd = child
        return fd, current
    except BaseException:
        os.close(fd)
        raise


def _require_nonreplaceable_directory_chain(chain: list[dict]) -> None:
    previous = None
    if not hasattr(os, "getxattr"):
        raise ProbeBlocked("cannot verify parent-chain ACLs")
    no_acl_errors = {errno.ENODATA, getattr(errno, "ENOATTR", errno.ENODATA)}
    for item in chain:
        path, fd, recorded = item["path"], item["fd"], item["stat"]
        current = os.fstat(fd)
        if not _same_directory(recorded, current):
            raise ProbeBlocked(f"{path} descriptor identity changed during parent-chain admission")
        if previous is not None:
            entry = os.stat(item["name"], dir_fd=previous["fd"],
                            follow_symlinks=False)
            if not _same_directory(entry, current):
                raise ProbeBlocked(f"{path} is no longer bound to its held parent-chain descriptor")
        mode = stat.S_IMODE(current.st_mode)
        if current.st_uid != 0 or current.st_gid != 0:
            raise ProbeBlocked(f"{path} is not root:root; fixture parent replacement is possible")
        if mode & 0o022:
            raise ProbeBlocked(f"{path} grants group/other write; fixture parent replacement is possible")
        for attr in ("system.posix_acl_access", "system.posix_acl_default"):
            try:
                os.getxattr(fd, attr)
            except OSError as exc:
                if exc.errno in no_acl_errors:
                    continue
                raise ProbeBlocked(f"cannot verify {attr} on fixture parent {path} ({exc.errno})") from exc
            raise ProbeBlocked(f"{path} has {attr}; parent-chain write access is not certified")
        previous = item


def _fd_mount_id(text: str) -> dict:
    values = [line.partition(":")[2].strip() for line in text.splitlines()
              if line.partition(":")[0].strip() == "mnt_id"]
    if not values:
        return {"status": "NOT FOUND"}
    if len(values) != 1:
        return {"status": "DUPLICATE", "entry_count": len(values)}
    if not values[0].isdigit():
        return {"status": "ANOMALOUS"}
    return {"status": "read", "mount_id": values[0]}


def _unescape_mount_field(value: str) -> str:
    for escaped, char in (("\\040", " "), ("\\011", "\t"),
                          ("\\012", "\n"), ("\\134", "\\")):
        value = value.replace(escaped, char)
    return value


def _parse_mountinfo(rows: list[str]) -> tuple[list[dict], int]:
    records, malformed = [], 0
    for row in rows:
        fields = row.split()
        try:
            separator = fields.index("-")
            if (separator < 6 or len(fields) < separator + 4
                    or not fields[0].isdigit() or not fields[1].isdigit()
                    or len(fields[2].split(":")) != 2
                    or not all(part.isdigit() for part in fields[2].split(":"))):
                malformed += 1
                continue
            records.append({"mount_id": fields[0], "parent_id": fields[1],
                            "device": fields[2], "root": _unescape_mount_field(fields[3]),
                            "mountpoint": _unescape_mount_field(fields[4]),
                            "options": fields[5], "optional": fields[6:separator],
                            "filesystem_type": fields[separator + 1],
                            "source": _unescape_mount_field(fields[separator + 2]),
                            "super_options": fields[separator + 3]})
        except (ValueError, IndexError):
            malformed += 1
    return records, malformed


def _mount_record_for_id(mount_id: str, rows: list[str]) -> dict:
    records, malformed = _parse_mountinfo(rows)
    if malformed:
        return {"status": "ANOMALOUS", "malformed_entry_count": malformed}
    matches = [item for item in records if item["mount_id"] == str(mount_id)]
    if not matches:
        return {"status": "NOT FOUND", "mount_id": str(mount_id)}
    if len(matches) != 1:
        return {"status": "DUPLICATE", "mount_id": str(mount_id),
                "entry_count": len(matches)}
    return {"status": "read", **matches[0]}


def _mount_for_fd(fd: int) -> dict:
    try:
        info = Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="ascii")
        descriptor = _fd_mount_id(info)
        if descriptor.get("status") != "read":
            return descriptor
        rows = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"status": "NOT FOUND", "errno": exc.errno}
    return _mount_record_for_id(descriptor["mount_id"], rows)


def _mount_for_path(path: str) -> dict:
    fd = os.open(path, _opath_flags())
    try:
        return _mount_for_fd(fd)
    finally:
        os.close(fd)


def _propagation_report(rows: list[str]) -> dict:
    records, malformed = _parse_mountinfo(rows)
    if malformed:
        return {"status": "BLOCKED", "reason": "mountinfo contains malformed rows",
                "malformed_entry_count": malformed}
    shared = [r["mount_id"] for r in records
              if any(x.startswith("shared:") for x in r["optional"])]
    master = [r["mount_id"] for r in records
              if any(x.startswith("master:") for x in r["optional"])]
    if shared or master:
        return {"status": "BLOCKED", "shared_mount_ids": shared,
                "master_mount_ids": master}
    return {"status": "PRIVATE", "shared_mount_ids": [], "master_mount_ids": []}


def _fd_directory_path(directory_fd: int,
                       expected_identity: dict | None = None) -> str:
    if directory_fd < 0:
        raise ProbeBlocked("directory descriptor is invalid")
    try:
        opened = os.fstat(directory_fd)
        link_path = f"/proc/self/fd/{directory_fd}"
        link = os.stat(link_path, follow_symlinks=False)
        resolved = os.stat(link_path, follow_symlinks=True)
    except OSError as exc:
        raise ProbeBlocked(f"proc fd directory path could not be verified ({exc.errno})") from exc
    if not stat.S_ISDIR(opened.st_mode) or not stat.S_ISLNK(link.st_mode):
        raise ProbeBlocked("proc fd path is not a directory magic link")
    if not _same_directory(opened, resolved):
        raise ProbeBlocked("proc fd path does not resolve to the held directory")
    if (expected_identity is not None
            and not _same_object(opened, expected_identity, include_mode=True)):
        raise ProbeBlocked("held directory identity differs from its fixture identity")
    return link_path


def _namespace_changed(parent: dict, child: dict) -> bool:
    try:
        p = (int(parent["device"]), int(parent["inode"]))
        c = (int(child["device"]), int(child["inode"]))
    except (KeyError, TypeError, ValueError):
        return False
    return p != c


def _namespace_identity(path: str = "/proc/self/ns/mnt") -> dict:
    # /proc/.../ns/mnt is a magic symlink; compare the namespace object it
    # references, not the procfs symlink inode.
    info = os.stat(path, follow_symlinks=True)
    return {"device": info.st_dev, "inode": info.st_ino}


def _read_mountinfo(path: str = "/proc/self/mountinfo") -> list[str]:
    return Path(path).read_text(encoding="utf-8").splitlines()


def _mount_matches_bind(record: dict, *, mount_id: str, source: dict,
                        target: dict, target_path: str, source_root: str) -> bool:
    return (record.get("status") == "read" and record.get("mount_id") == str(mount_id)
            and str(mount_id) != str(source.get("mount_id"))
            and record.get("mountpoint") == target_path
            and record.get("root") == source_root
            and record.get("filesystem_type") == "tmpfs"
            and record.get("device") == source.get("device")
            and record.get("device") != target.get("device"))


def _unmount_is_authorized(*, namespace_private: bool, mount_record: dict,
                           expected_mount_id: str, source_mount: dict,
                           target_mount: dict, source_identity: dict, mounted_identity: dict,
                           target_path: str, source_root: str) -> bool:
    return (namespace_private is True
            and _mount_matches_bind(mount_record, mount_id=expected_mount_id,
                                    source=source_mount, target=target_mount,
                                    target_path=target_path, source_root=source_root)
            and mounted_identity == source_identity)


def _acl_wire(entries=ACL_EXPECTED) -> bytes:
    tags = {"user_obj": 1, "user": 2, "group_obj": 4, "group": 8,
            "mask": 16, "other": 32}
    undefined = 0xFFFFFFFF
    payload = bytearray(struct.pack("<I", 2))
    for item in entries:
        identifier = item["id"] if item["tag"] in ("user", "group") else undefined
        payload.extend(struct.pack("<HHI", tags[item["tag"]], item["perm"], identifier))
    return bytes(payload)


def _numeric_acl(data: bytes) -> list[dict]:
    tags = {1: "user_obj", 2: "user", 4: "group_obj", 8: "group",
            16: "mask", 32: "other"}
    undefined = 0xFFFFFFFF
    if len(data) < 4 or (len(data) - 4) % 8 or struct.unpack_from("<I", data)[0] != 2:
        raise ProbeBlocked("POSIX ACL encoding is unsupported")
    result = []
    for offset in range(4, len(data), 8):
        tag, perm, ident = struct.unpack_from("<HHI", data, offset)
        name = tags.get(tag)
        if name is None or perm & ~7:
            raise ProbeBlocked("POSIX ACL contains an unsupported entry")
        named = name in ("user", "group")
        if (named and ident == undefined) or (not named and ident != undefined):
            raise ProbeBlocked("POSIX ACL contains an invalid numeric ID")
        result.append({"tag": name, "id": ident if named else None, "perm": perm})
    return result


def _acl_write_allowed(info, entries: list[dict] | None,
                       uid: int = TARGET_UID, groups: tuple[int, ...] = (TARGET_GID, *TARGET_GROUPS)) -> bool:
    if entries is None:
        if info.st_uid == uid:
            permissions = (stat.S_IMODE(info.st_mode) >> 6) & 7
        elif info.st_gid in groups:
            permissions = (stat.S_IMODE(info.st_mode) >> 3) & 7
        else:
            permissions = stat.S_IMODE(info.st_mode) & 7
        return bool(permissions & 2)
    get = lambda tag, ident=None: next((item["perm"] for item in entries
                                        if item["tag"] == tag and item["id"] == ident), None)
    if info.st_uid == uid:
        perm = get("user_obj")
    else:
        named = get("user", uid)
        mask = get("mask")
        if named is not None:
            return bool((named & (mask if mask is not None else 7)) & 2)
        group_match, group_perm = False, 0
        if info.st_gid in groups:
            group_match = True
            group_perm |= get("group_obj") or 0
        for item in entries:
            if item["tag"] == "group" and item["id"] in groups:
                group_match = True
                group_perm |= item["perm"]
        if group_match:
            return bool((group_perm & (mask if mask is not None else 7)) & 2)
        perm = get("other")
    return bool((perm or 0) & 2)


def _clear_acl(fd: int, diagnostics: dict | None = None) -> None:
    stages = []
    for name in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.removexattr(fd, name)
            stages.append({"xattr": name, "status": "REMOVED", "errno": None})
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                stages.append({"xattr": name, "status": "FAILED", "errno": exc.errno})
                if diagnostics is not None:
                    diagnostics["clear"] = {"status": "FAILED", "xattrs": stages}
                    raise ProbeBlocked(f"cannot clear inherited ACL ({exc.errno})",
                                       diagnostics) from exc
                raise ProbeBlocked(f"cannot clear inherited ACL ({exc.errno})") from exc
            stages.append({"xattr": name, "status": "ABSENT", "errno": exc.errno})
    if diagnostics is not None:
        diagnostics["clear"] = {"status": "COMPLETE", "xattrs": stages}


def _require_acl_profile(fd: int, diagnostics: dict) -> dict:
    diagnostics["get"] = {"status": "RUNNING"}
    try:
        raw_acl = os.getxattr(fd, "system.posix_acl_access")
    except OSError as exc:
        diagnostics["get"] = {"status": "FAILED", "errno": exc.errno}
        raise ProbeBlocked(f"exact ACL fixture getxattr failed ({exc.errno})",
                           diagnostics) from exc
    try:
        entries = _numeric_acl(raw_acl)
    except ProbeBlocked as exc:
        diagnostics["get"] = {"status": "INVALID", "errno": None,
                              "error": exc.reason, "raw_hex": raw_acl.hex()}
        raise ProbeBlocked("numeric ACL fixture could not be decoded", diagnostics) from exc
    try:
        info = os.fstat(fd)
    except OSError as exc:
        diagnostics["get"] = {"status": "FAILED AFTER READ", "errno": exc.errno}
        diagnostics["actual_numeric_entries"] = entries
        raise ProbeBlocked(f"ACL fixture metadata check failed ({exc.errno})",
                           diagnostics) from exc
    actual = {"numeric_entries": entries, "mode": stat.S_IMODE(info.st_mode),
              "uid": info.st_uid, "gid": info.st_gid}
    expected = {"numeric_entries": list(ACL_EXPECTED), "mode": MODES["acl"],
                "uid": 0, "gid": 0}
    write_allowed = _acl_write_allowed(info, entries)
    diagnostics["get"] = {"status": "COMPLETE", "errno": None}
    diagnostics["actual"] = actual
    diagnostics["uid_1000_write"] = write_allowed
    differences = {}
    for key in ("numeric_entries", "mode", "uid", "gid"):
        if actual[key] != expected[key]:
            differences[key] = {"expected": expected[key], "actual": actual[key]}
    if write_allowed:
        differences["uid_1000_write"] = {"expected": False, "actual": True}
    diagnostics["differences"] = differences
    if differences:
        diagnostics["validation"] = "FAILED"
        raise ProbeBlocked("numeric ACL fixture did not match the exact approved entries and metadata",
                           diagnostics)
    diagnostics["validation"] = "VERIFIED"
    return {"status": "VERIFIED", "entries": entries,
            "uid_1000_write": False,
            "mode": f"{stat.S_IMODE(info.st_mode):04o}",
            "uid": info.st_uid, "gid": info.st_gid,
            "diagnostics": diagnostics}


def _local_target_groups() -> tuple[int, ...] | None:
    try:
        passwd = Path("/etc/passwd").read_text(encoding="utf-8").splitlines()
        group_file = Path("/etc/group").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    user = None
    for line in passwd:
        fields = line.split(":")
        if len(fields) >= 4 and fields[0] == TARGET_USER:
            try:
                user = (int(fields[2]), int(fields[3]))
            except ValueError:
                return None
            break
    if user != (TARGET_UID, TARGET_GID):
        return None
    groups = set()
    for line in group_file:
        fields = line.split(":")
        if len(fields) >= 4 and TARGET_USER in fields[3].split(","):
            try:
                groups.add(int(fields[2]))
            except ValueError:
                return None
    groups.discard(TARGET_GID)
    return tuple(sorted(groups)) if tuple(sorted(groups)) == TARGET_GROUPS else None


def _trusted_executable(candidates) -> str | None:
    for candidate in candidates:
        try:
            path = Path(candidate).resolve(strict=True)
            info = path.stat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            continue
        parent, trusted = path.parent, True
        while True:
            try:
                item = parent.stat()
            except OSError:
                trusted = False
                break
            if not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022:
                trusted = False
                break
            if parent.parent == parent:
                break
            parent = parent.parent
        if trusted:
            return str(path)
    return None


def _trusted_python() -> str | None:
    return _trusted_executable(("/usr/bin/python3", "/bin/python3", "/usr/local/bin/python3"))


def _trusted_script() -> str | None:
    # Apply the same ownership and parent-directory checks used for Python.
    # Checking only the file would allow replacement through a writable parent.
    return _trusted_executable((__file__,))


def _require_mount(fd: int, filesystem_type: str) -> dict:
    mount = _mount_for_fd(fd)
    if mount.get("status") != "read" or mount.get("filesystem_type") != filesystem_type:
        raise ProbeBlocked(f"required {filesystem_type} mount could not be verified")
    return mount


def _runtime_preflight() -> dict:
    if not sys.platform.startswith("linux") or not hasattr(os, "geteuid"):
        raise ProbeBlocked("Linux is required")
    if os.geteuid() != 0:
        raise ProbeBlocked("run manually as root; the script does not invoke sudo")
    if _local_target_groups() != TARGET_GROUPS:
        raise ProbeBlocked("local kurumi UID/GID/supplementary groups differ from the approved identity")
    python, script = _trusted_python(), _trusted_script()
    if not python or not script:
        raise ProbeBlocked("trusted root-owned Python and script paths are required")
    root_fd = run_fd = vol_fd = base_fd = None
    try:
        root_fd = os.open("/", _dir_flags())
        root_stat = os.fstat(root_fd)
        if root_stat.st_uid != 0 or root_stat.st_gid != 0 or stat.S_IMODE(root_stat.st_mode) != 0o755:
            raise ProbeBlocked("/ must be root:root mode 0755")
        run_fd, run_stat = _open_child_dir(root_fd, "run")
        vol_fd, vol_stat = _open_child_dir(root_fd, "vol1")
        base_fd, base_stat = _open_child_dir(vol_fd, "1000")
        if run_stat.st_uid != 0 or run_stat.st_gid != 0 or stat.S_IMODE(run_stat.st_mode) != 0o755:
            raise ProbeBlocked("/run must be root:root mode 0755")
        _require_nonreplaceable_directory_chain([
            {"path": "/", "fd": root_fd, "stat": root_stat},
            {"path": "/run", "name": "run", "fd": run_fd, "stat": run_stat},
        ])
        _require_nonreplaceable_directory_chain([
            {"path": "/", "fd": root_fd, "stat": root_stat},
            {"path": "/vol1", "name": "vol1", "fd": vol_fd, "stat": vol_stat},
            {"path": str(BASE), "name": "1000", "fd": base_fd, "stat": base_stat},
        ])
        run_mount = _require_mount(run_fd, "tmpfs")
        base_mount = _require_mount(base_fd, "btrfs")
        return {"root_fd": root_fd, "run_fd": run_fd, "vol_fd": vol_fd,
                "base_fd": base_fd, "root_stat": root_stat, "run_stat": run_stat,
                "vol_stat": vol_stat, "base_stat": base_stat, "run_mount": run_mount,
                "base_mount": base_mount, "python": python, "script": script,
                "parent_namespace": _namespace_identity()}
    except BaseException:
        for fd in (base_fd, vol_fd, run_fd, root_fd):
            if fd is not None:
                os.close(fd)
        raise


def _new_setup_ledger() -> dict:
    return {"phase": "initializing", "roots": {}, "cases": [], "sockets": [],
            "servers": [], "setup_complete": False}


def _plan_lab_root(ledger: dict, role: str, parent_fd: int,
                   parent_path: str, prefix: str) -> dict:
    name = prefix + secrets.token_hex(16)
    record = {"kind": "root", "role": role, "name": name,
              "path": str(Path(parent_path) / name), "parent_fd": parent_fd,
              "dir_fd": None, "created": False, "identity": None,
              "initial_identity": None, "identity_binding": "NOT CREATED",
              "identity_binding_failure": None,
              "identity_stage": "NOT REGISTERED", "cleanup": "NOT RUN"}
    ledger["roots"][role] = record
    return record


def _plan_case(ledger: dict, root_record: dict, name: str) -> dict:
    record = {"kind": "case", "root_role": root_record["role"],
              "name": name, "path": str(Path(root_record["path"]) / name),
              "parent_fd": root_record.get("dir_fd"), "dir_fd": None,
              "created": False, "identity": None,
              "initial_identity": None, "identity_binding": "NOT CREATED",
              "identity_binding_failure": None,
              "identity_stage": "NOT REGISTERED", "allowed_entries": {},
              "acl": {"status": "NOT RUN"}, "cleanup": "NOT RUN"}
    ledger["cases"].append(record)
    return record


def _plan_socket(ledger: dict, root_record: dict, name: str, path: str,
                 directory_fd: int, case_record: dict | None = None) -> dict:
    record = {"kind": "socket", "root_role": root_record["role"],
              "case_name": case_record.get("name") if case_record else None,
              "name": name, "path": path, "dir_fd": directory_fd,
              "created": False, "identity": None,
              "identity_stage": "NOT REGISTERED", "cleanup": "NOT RUN"}
    ledger["sockets"].append(record)
    return record


def _create_lab_root(base_fd: int, record: dict, ledger: dict) -> tuple[str, int, dict]:
    name = record["name"]
    ledger["phase"] = f"root:{record['role']}:mkdir"
    fd = None
    try:
        os.mkdir(name, 0o700, dir_fd=base_fd)
    except BaseException as exc:
        record["failure"] = _exception_record(exc)
        record["status"] = "mkdir failed; path ownership unverified"
        raise
    record["created"] = True
    record["identity_binding"] = "UNBOUND"
    record["identity_stage"] = "created; awaiting initial parent stat"
    try:
        ledger["phase"] = f"root:{record['role']}:stat"
        entry = os.stat(name, dir_fd=base_fd, follow_symlinks=False)
        initial_identity = _identity(entry)
        record["initial_identity"] = initial_identity
        record["identity"] = dict(initial_identity)
        record["identity_stage"] = "initial parent stat"
        if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != 0:
            _fail_identity_binding(record, "initial path stat was not a root-owned directory")
            raise ProbeBlocked("new lab root is not a root-owned directory")
        ledger["phase"] = f"root:{record['role']}:open"
        fd = os.open(name, _dir_flags(), dir_fd=base_fd)
        record["dir_fd"] = fd
        opened = os.fstat(fd)
        if (not _same_directory(entry, opened)
                or not _same_object(opened, initial_identity)):
            _fail_identity_binding(record, "initial path and opened descriptor identities differ")
            raise ProbeBlocked("new lab root path and descriptor identity differ")
        record["identity_binding"] = "BOUND"
        record["identity"] = _identity(opened)
        record["identity_stage"] = "initial stat bound to descriptor"
        ledger["phase"] = f"root:{record['role']}:clear-acl"
        _clear_acl(fd)
        ledger["phase"] = f"root:{record['role']}:chmod"
        os.fchmod(fd, 0o711)
        final = os.fstat(fd)
        _record_bound_directory_identity(record, final, "final descriptor stat")
        if final.st_uid != 0 or stat.S_IMODE(final.st_mode) != 0o711:
            raise ProbeBlocked("new lab root mode or owner did not verify")
        record["status"] = "READY"
        return name, fd, _identity(final)
    except BaseException as exc:
        if record.get("created") and record.get("identity_binding") != "BOUND":
            if record.get("identity_stage") == "created; awaiting initial parent stat":
                record["identity_stage"] = "initial parent stat failed"
            _fail_identity_binding(record, _exception_record(exc)["reason"])
        raise


def _apply_case_profile(fd: int, profile: str, diagnostics: dict | None = None,
                        ledger: dict | None = None, case_name: str | None = None) -> dict:
    if profile not in MODES:
        raise ProbeBlocked("unknown permission profile")
    if profile == "acl":
        diagnostic = diagnostics if diagnostics is not None else {}
        info = os.fstat(fd)
        diagnostic.update({
            "status": "SETTING",
            "fixture": {"identity": _identity(info), "mode": stat.S_IMODE(info.st_mode),
                        "uid": info.st_uid, "gid": info.st_gid},
            "expected": {"numeric_entries": list(ACL_EXPECTED),
                         "mode": MODES["acl"], "uid": 0, "gid": 0},
            "set": {"status": "NOT RUN", "errno": None},
            "get": {"status": "NOT RUN", "errno": None},
        })
        initial_owner = {"uid": info.st_uid, "gid": info.st_gid}
        owner_differences = {
            key: {"expected": 0, "actual": actual}
            for key, actual in initial_owner.items() if actual != 0
        }
        diagnostic["initial_owner_validation"] = {
            "status": "FAILED" if owner_differences else "VERIFIED",
            "expected": {"uid": 0, "gid": 0}, "actual": initial_owner,
            "differences": owner_differences,
        }
        if owner_differences:
            diagnostic["validation"] = "FAILED BEFORE SET"
            raise ProbeBlocked("ACL fixture initial owner was not root:root", diagnostic)
        if ledger is not None:
            ledger["phase"] = f"case:{case_name}:acl-clear-inherited"
        try:
            _clear_acl(fd, diagnostic)
        except ProbeBlocked:
            diagnostic["validation"] = "FAILED BEFORE SET"
            raise
        if ledger is not None:
            ledger["phase"] = f"case:{case_name}:acl-set"
        diagnostic["set"] = {"status": "RUNNING", "errno": None}
        try:
            os.setxattr(fd, "system.posix_acl_access", _acl_wire())
        except OSError as exc:
            diagnostic["set"] = {"status": "FAILED", "errno": exc.errno}
            diagnostic["validation"] = "FAILED"
            raise ProbeBlocked(f"ACL fixture setxattr failed ({exc.errno})",
                               diagnostic) from exc
        diagnostic["set"] = {"status": "COMPLETE", "errno": None}
        if ledger is not None:
            ledger["phase"] = f"case:{case_name}:acl-get-verify"
        return _require_acl_profile(fd, diagnostic)
    _clear_acl(fd)
    if ledger is not None:
        ledger["phase"] = f"case:{case_name}:chmod-profile"
    os.fchmod(fd, MODES[profile])
    info = os.fstat(fd)
    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != MODES[profile]:
        raise ProbeBlocked("fixture directory mode or owner did not verify")
    return {"entries": "absent", "uid_1000_write": profile == "0777"}


def _create_case_dir(lab_fd: int, name: str, profile: str,
                     record: dict, ledger: dict) -> tuple[int, dict]:
    if profile not in MODES:
        raise ProbeBlocked("unknown permission profile")
    ledger["phase"] = f"case:{name}:mkdir"
    try:
        os.mkdir(name, 0o700, dir_fd=lab_fd)
    except BaseException as exc:
        record["failure"] = _exception_record(exc)
        record["status"] = "mkdir failed; path ownership unverified"
        raise
    record["created"] = True
    record["identity_binding"] = "UNBOUND"
    record["identity_stage"] = "created; awaiting initial parent stat"
    try:
        ledger["phase"] = f"case:{name}:stat"
        entry = os.stat(name, dir_fd=lab_fd, follow_symlinks=False)
        initial_identity = _identity(entry)
        record["initial_identity"] = initial_identity
        record["identity"] = dict(initial_identity)
        record["identity_stage"] = "initial parent stat"
        if not stat.S_ISDIR(entry.st_mode) or entry.st_uid != 0:
            _fail_identity_binding(record, "initial path stat was not a root-owned directory")
            raise ProbeBlocked("new fixture case is not a root-owned directory")
        ledger["phase"] = f"case:{name}:open"
        fd = os.open(name, _dir_flags(), dir_fd=lab_fd)
        record["dir_fd"] = fd
        opened = os.fstat(fd)
        if (not _same_directory(entry, opened)
                or not _same_object(opened, initial_identity)):
            _fail_identity_binding(record, "initial path and opened descriptor identities differ")
            raise ProbeBlocked("fixture case path and descriptor identity differ")
        record["identity_binding"] = "BOUND"
        record["identity"] = _identity(opened)
        record["identity_stage"] = "initial stat bound to descriptor"
        ledger["phase"] = f"case:{name}:lock"
        _clear_acl(fd)
        os.fchmod(fd, 0o700)
        locked = os.fstat(fd)
        _record_bound_directory_identity(record, locked, "locked descriptor stat")
        if locked.st_uid != 0 or stat.S_IMODE(locked.st_mode) != 0o700:
            raise ProbeBlocked("fixture case directory could not be locked for setup")
        record["profile"] = profile
        record["status"] = "LOCKED"
        return fd, record
    except BaseException as exc:
        if record.get("created") and record.get("identity_binding") != "BOUND":
            if record.get("identity_stage") == "created; awaiting initial parent stat":
                record["identity_stage"] = "initial parent stat failed"
            _fail_identity_binding(record, _exception_record(exc)["reason"])
        raise


def _bind_root_socket(directory_fd: int, name: str,
                      directory_identity: dict, *, ledger: dict | None = None,
                      socket_record: dict | None = None,
                      case_record: dict | None = None) -> tuple[socket.socket, dict]:
    if socket_record is not None:
        socket_record["identity_stage"] = "validating fixed leaf before bind"
    if ledger is not None:
        ledger["phase"] = f"socket:{name}:validate-before-bind"
    try:
        if not _safe_leaf(name):
            raise ProbeBlocked("socket entry name is not a fixed child")
        bind_path = _fd_child_path(directory_fd, name, directory_identity)
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ProbeBlocked("socket fixture name already exists")
    except BaseException as exc:
        if socket_record is not None:
            socket_record["failure"] = _exception_record(exc)
            socket_record["identity_stage"] = "failed before bind"
        raise
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if socket_record is not None:
            socket_record["bind_attempted"] = True
            socket_record["identity_stage"] = "bind attempted; awaiting socket stat"
        if ledger is not None:
            ledger["phase"] = f"socket:{name}:bind"
        old_umask = os.umask(0o177)
        try:
            server.bind(bind_path)
        finally:
            os.umask(old_umask)
        if socket_record is not None:
            socket_record["created"] = True
            socket_record["identity_stage"] = "bound; awaiting socket stat"
        if ledger is not None:
            ledger["phase"] = f"socket:{name}:stat-after-bind"
        _fd_directory_path(directory_fd, directory_identity)
        bound_info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISSOCK(bound_info.st_mode) or bound_info.st_uid != 0 or bound_info.st_gid != 0:
            raise ProbeBlocked("bound root fixture socket owner or type did not verify")
        bound_identity = _identity(bound_info)
        if socket_record is not None:
            socket_record["identity"] = bound_identity
            socket_record["identity_stage"] = "socket stat after bind"
        if case_record is not None:
            case_record.setdefault("allowed_entries", {})[name] = bound_identity
            case_record["socket_identity"] = bound_identity
        if ledger is not None:
            ledger["phase"] = f"socket:{name}:chmod-and-verify"
        os.chmod(name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
        _fd_directory_path(directory_fd, directory_identity)
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        if (not _same_object(info, bound_identity)
                or not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ProbeBlocked("bound root fixture socket identity or mode changed")
        socket_identity = _identity(info)
        if socket_record is not None:
            socket_record["identity"] = socket_identity
            socket_record["identity_stage"] = "socket mode verified"
            socket_record["status"] = "READY"
        if case_record is not None:
            case_record.setdefault("allowed_entries", {})[name] = socket_identity
            case_record["socket_identity"] = socket_identity
        if ledger is not None:
            ledger["phase"] = f"socket:{name}:listen"
        server.listen(8)
        server.settimeout(0.25)
        return server, socket_identity
    except BaseException as exc:
        if socket_record is not None:
            socket_record["failure"] = _exception_record(exc)
        server.close()
        raise


class RootSocketServer:
    def __init__(self, listener: socket.socket, response: bytes):
        self.listener = listener
        self.response = response
        self.clients: list[dict] = []
        self.errors: list[str] = []
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.started = False

    def start(self) -> None:
        self.thread.start()
        self.started = True

    def _serve(self) -> None:
        deadline = time.monotonic() + 60.0
        while not self.stop_event.is_set() and time.monotonic() < deadline:
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with client:
                client.settimeout(SOCKET_TIMEOUT)
                try:
                    raw = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                    pid, uid, gid = struct.unpack("3i", raw)
                    client.sendall(self.response)
                    self.clients.append({"pid": pid, "uid": uid, "gid": gid,
                                         "response": self.response.decode("ascii")})
                except OSError as exc:
                    self.errors.append(str(exc.errno or exc.__class__.__name__))

    def close(self) -> bool:
        self.stop_event.set()
        try:
            self.listener.close()
        except OSError:
            pass
        if self.started:
            self.thread.join(2.0)
        return not self.thread.is_alive()


def _root_client(path: str, expected: bytes = ROOT_RESPONSE) -> dict:
    if not hasattr(socket, "SO_PEERCRED"):
        return {"result": "BLOCKED", "reason": "SO_PEERCRED is unavailable"}
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(SOCKET_TIMEOUT)
    try:
        client.connect(path)
        raw = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        pid, uid, gid = struct.unpack("3i", raw)
        response = client.recv(128)
        value = {"result": "success", "peer_pid": pid, "peer_uid": uid,
                 "peer_gid": gid, "response": response.decode("ascii", "replace")}
        value["verified"] = uid == 0 and gid == 0 and response == expected
        return value
    except OSError as exc:
        return {"result": "error", "errno": exc.errno or errno.EIO}
    finally:
        client.close()


def _root_client_at(directory_fd: int, name: str, expected: bytes,
                    directory_identity: dict, socket_identity: dict | None = None,
                    *, expect_missing: bool = False) -> dict:
    try:
        path = _fd_child_path(directory_fd, name, directory_identity)
        before = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        if not expect_missing:
            return {"result": "error", "errno": errno.ENOENT}
        try:
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            return {"result": "error", "errno": errno.ENOENT}
        except OSError as exc:
            return {"result": "error", "errno": exc.errno or errno.EIO}
        return {"result": "BLOCKED", "reason": "fixture name appeared during absence check"}
    except (OSError, ProbeBlocked) as exc:
        return {"result": "BLOCKED",
                "reason": exc.reason if isinstance(exc, ProbeBlocked)
                else f"fixture path check failed ({exc.errno or exc.__class__.__name__})"}
    if expect_missing:
        return {"result": "BLOCKED", "reason": "expected fixture name is present"}
    if socket_identity is None or not _same_object(before, socket_identity, include_mode=True):
        return {"result": "BLOCKED", "reason": "fixture socket identity changed before connect"}
    result = _root_client(path, expected)
    try:
        _fd_directory_path(directory_fd, directory_identity)
        after = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError as exc:
        return {"result": "BLOCKED",
                "reason": f"fixture socket disappeared after connect ({exc.errno or errno.EIO})",
                "client_result": result}
    if not _same_object(after, socket_identity, include_mode=True):
        return {"result": "BLOCKED", "reason": "fixture socket identity changed during connect",
                "client_result": result}
    return result


def _root_client_valid(result: dict, *, uid: int = 0, gid: int = 0,
                       response: bytes = ROOT_RESPONSE) -> bool:
    return (isinstance(result, dict) and result.get("result") == "success"
            and result.get("peer_uid") == uid and result.get("peer_gid") == gid
            and result.get("response") == response.decode("ascii")
            and (uid != 0 or result.get("verified") is True))


def _child_code() -> str:
    return r'''import ctypes, errno, json, os, socket, stat, struct, sys
TUID, TGID, TGROUPS = 1000, 1001, [1000]
FAKE = b"fake-fixture-service\n"
def emit(value):
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n"); sys.stdout.flush()
def caps():
    wanted = {"CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd", "NoNewPrivs"}
    result = {}
    with open("/proc/self/status", "r", encoding="ascii") as f:
        for line in f:
            key, sep, value = line.partition(":")
            if sep and key in wanted: result[key] = value.strip()
    return result
def drop_all_caps():
    libc = ctypes.CDLL(None, use_errno=True)
    for cap in range(64):
        ctypes.set_errno(0); read = libc.prctl(23, cap, 0, 0, 0)
        if read == 1:
            ctypes.set_errno(0)
            if libc.prctl(24, cap, 0, 0, 0) != 0: raise OSError(ctypes.get_errno(), "PR_CAPBSET_DROP")
        elif read == -1 and ctypes.get_errno() not in (errno.EINVAL,):
            raise OSError(ctypes.get_errno(), "PR_CAPBSET_READ")
    if libc.prctl(38, 1, 0, 0, 0) != 0: raise OSError(ctypes.get_errno(), "PR_SET_NO_NEW_PRIVS")
def identity():
    u, g = os.getresuid(), os.getresgid()
    c = caps()
    zeros = all(c.get(x) == "0000000000000000" for x in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"))
    return {"verified": u == (TUID,)*3 and g == (TGID,)*3 and sorted(os.getgroups()) == TGROUPS
            and zeros and c.get("NoNewPrivs") == "1", "uid": list(u), "gid": list(g),
            "groups": sorted(os.getgroups()), "capabilities": c, "capabilities_zero": zeros}
def fd_access(verified):
    return {"method":"inherited-directory-fd",
            "directory_fd_identity_verified":bool(verified),
            "production_path_traversal_verified":False}
def kind(mode):
    if stat.S_ISDIR(mode): return "directory"
    if stat.S_ISSOCK(mode): return "socket"
    return "other"
def object_identity(info):
    return {"device":info.st_dev,"inode":info.st_ino,"uid":info.st_uid,"gid":info.st_gid,
            "type":kind(info.st_mode),"mode":stat.S_IMODE(info.st_mode)}
def matches(info, expected):
    actual = object_identity(info)
    return all(actual.get(key) == expected.get(key)
               for key in ("device","inode","uid","gid","type","mode"))
def main():
    op, fd_text, socket_name, moved_name, expected_text = sys.argv[2:7]
    try:
        directory_fd = int(fd_text)
        expected = json.loads(expected_text)
    except (ValueError, TypeError):
        emit({"status":"BLOCKED","reason":"invalid fixture descriptor or identity"}); return 3
    drop_all_caps(); os.setgroups(TGROUPS); os.setgid(TGID); os.setuid(TUID)
    ident = identity()
    if not ident["verified"]:
        emit({"status":"BLOCKED", "identity":ident,
              "fixture_access":fd_access(False)}); return 3
    try:
        if (not socket_name or socket_name in (".","..") or "/" in socket_name or "\\" in socket_name
                or not moved_name or moved_name in (".","..") or "/" in moved_name or "\\" in moved_name
                or socket_name == moved_name):
            raise OSError(errno.EINVAL, "fixture leaf name is invalid")
        directory_info = os.fstat(directory_fd)
        proc_path = "/proc/self/fd/" + str(directory_fd)
        proc_link = os.stat(proc_path, follow_symlinks=False)
        proc_target = os.stat(proc_path, follow_symlinks=True)
        if (not stat.S_ISDIR(directory_info.st_mode) or not stat.S_ISLNK(proc_link.st_mode)
                or (directory_info.st_dev,directory_info.st_ino) != (proc_target.st_dev,proc_target.st_ino)
                or not matches(directory_info, expected["directory"])):
            raise OSError(errno.ESTALE, "held fixture directory identity did not verify")
        fixture = os.stat(socket_name, dir_fd=directory_fd, follow_symlinks=False)
        if not stat.S_ISSOCK(fixture.st_mode) or fixture.st_uid != 0 or fixture.st_gid != 0:
            raise OSError(errno.EPERM, "fixture socket owner or type did not verify")
        fixture_id = object_identity(fixture)
        if not all(fixture_id.get(key) == expected["fixture"].get(key)
                   for key in ("device","inode","uid","gid","type","mode")):
            raise OSError(errno.ESTALE, "fixture socket identity changed")
        try:
            os.stat(moved_name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise OSError(errno.EEXIST, "reserved moved fixture name already exists")
    except OSError as exc:
        emit({"status":"BLOCKED", "identity":ident,
              "fixture_access":fd_access(False),
              "errno":exc.errno or errno.EIO}); return 3
    out = {"status":"COMPLETE", "identity":ident, "operation":op,
           "fixture_access":fd_access(True), "fixture_identity":fixture_id}
    try:
        if op == "rename":
            os.rename(socket_name, moved_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            out["operation_result"] = "success"
        elif op == "unlink":
            os.unlink(socket_name, dir_fd=directory_fd); out["operation_result"] = "success"
        elif op == "replace":
            os.rename(socket_name, moved_name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            out["rename_result"] = "success"
            fake = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            old = os.umask(0o177)
            try: fake.bind(proc_path + "/" + socket_name)
            finally: os.umask(old)
            os.chmod(socket_name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
            fake.listen(1); fake.settimeout(3.0)
            info = os.stat(socket_name, dir_fd=directory_fd, follow_symlinks=False)
            out["replacement"] = object_identity(info)
            emit({"status":"FAKE_READY", "identity":ident, "replacement":out["replacement"]})
            conn, _ = fake.accept()
            with conn:
                conn.settimeout(2.0); peer = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
                pid, uid, gid = struct.unpack("3i", peer); conn.sendall(FAKE)
            out["fake_server"] = {"peer_pid":pid,"peer_uid":uid,"peer_gid":gid,"response":FAKE.decode("ascii")}
            fake.close(); out["operation_result"] = "success"
        else: raise ValueError("unknown operation")
    except OSError as exc:
        out["operation_result"] = "error"; out["errno"] = exc.errno or errno.EIO
        if op == "replace": out.setdefault("rename_result", "error")
    except BaseException as exc:
        out["status"] = "BLOCKED"; out["reason"] = type(exc).__name__
    emit(out)
    return 0 if out["status"] == "COMPLETE" else 2
raise SystemExit(main())
'''

def _target_identity_verified(value: dict) -> bool:
    if not isinstance(value, dict):
        return False
    try:
        uid, gid = tuple(map(int, value.get("uid", ()))), tuple(map(int, value.get("gid", ())))
        groups = tuple(sorted(map(int, value.get("groups", ()))))
    except (TypeError, ValueError):
        return False
    caps = value.get("capabilities", {})
    return (value.get("verified") is True and uid == (TARGET_UID,) * 3
            and gid == (TARGET_GID,) * 3 and groups == TARGET_GROUPS
            and value.get("capabilities_zero") is True
            and isinstance(caps, dict)
            and all(caps.get(key) == "0000000000000000"
                    for key in ("CapEff", "CapPrm", "CapInh", "CapAmb", "CapBnd"))
            and caps.get("NoNewPrivs") == "1")


def _target_observation_valid(value: dict, expected_fixture: dict) -> bool:
    observed = value.get("fixture_identity", {}) if isinstance(value, dict) else {}
    return (_target_identity_verified(value.get("identity", {}))
            and _target_fixture_fd_access_verified(value)
            and _same_identity_dict(observed, expected_fixture))


def _target_fixture_fd_access_verified(value: dict) -> bool:
    access = value.get("fixture_access", {}) if isinstance(value, dict) else {}
    return (isinstance(access, dict)
            and access.get("method") == "inherited-directory-fd"
            and access.get("directory_fd_identity_verified") is True
            and access.get("production_path_traversal_verified") is False)


def _target_socket_identity_verified(value: dict) -> bool:
    if not isinstance(value, dict) or value.get("type") != "socket":
        return False
    try:
        device, inode = int(value["device"]), int(value["inode"])
        uid, gid, mode = int(value["uid"]), int(value["gid"]), int(value["mode"])
    except (KeyError, TypeError, ValueError):
        return False
    return (device >= 0 and inode > 0 and uid == TARGET_UID
            and gid == TARGET_GID and mode == 0o600)


def _same_identity_dict(left: dict, right: dict) -> bool:
    return (isinstance(left, dict) and isinstance(right, dict)
            and all(left.get(key) == right.get(key)
                    for key in ("device", "inode", "uid", "gid", "type", "mode")))


def _readline_timeout(stream, timeout: float) -> bytes | None:
    if timeout <= 0 or stream is None:
        return None
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return None
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    line = bytearray()
    try:
        selector.register(fd, selectors.EVENT_READ)
        while len(line) <= 65536:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                return None
            try:
                chunk = os.read(fd, 1)
            except BlockingIOError:
                continue
            except OSError as exc:
                if exc.errno == errno.EINTR:
                    continue
                return None
            if not chunk:
                # Protocol records must be newline-terminated; partial data is
                # not accepted as a complete event.
                return None
            line.extend(chunk)
            if chunk == b"\n":
                return bytes(line)
        return None
    except (OSError, ValueError):
        return None
    finally:
        selector.close()


def _child_argv(python: str, script: str, operation: str, directory_fd: int,
                socket_name: str, moved_name: str, fixture_identity: dict,
                directory_identity: dict) -> list[str]:
    expected = json.dumps({"fixture": fixture_identity, "directory": directory_identity},
                          separators=(",", ":"), sort_keys=True)
    return [python, "-I", "-S", script, "--target-child", operation,
            str(directory_fd), socket_name, moved_name, expected]


def _terminate_and_reap(proc: subprocess.Popen, *, process_group: bool = False) -> bool:
    try:
        if process_group:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=2.0)
            # A worker can have a target child in the same new session. Do not
            # declare cleanup safe until that whole process group has exited.
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    break
                os.killpg(proc.pid, signal.SIGKILL)
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.05)
            if proc.poll() is None:
                proc.wait(timeout=1.0)
            return proc.poll() is not None
        if proc.poll() is not None:
            proc.wait(timeout=1.0)
            return True
        proc.terminate()
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2.0)
        return proc.poll() is not None
    except (OSError, subprocess.TimeoutExpired):
        return False


def _run_target(python: str, script: str, operation: str, directory_fd: int,
                socket_name: str, moved_name: str, fixture_identity: dict,
                directory_identity: dict, root_client=None) -> dict:
    try:
        _fd_child_path(directory_fd, socket_name, directory_identity)
        _fd_child_path(directory_fd, moved_name, directory_identity)
        argv = _child_argv(python, script, operation, directory_fd, socket_name,
                           moved_name, fixture_identity, directory_identity)
        proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, close_fds=True,
                                pass_fds=(directory_fd,), env={"PATH": "/usr/bin:/bin"},
                                bufsize=0)
    except ProbeBlocked as exc:
        return {"status": "BLOCKED", "reason": exc.reason}
    except OSError as exc:
        return {"status": "BLOCKED", "reason": f"target child could not start ({exc.errno})"}
    deadline = time.monotonic() + CHILD_TIMEOUT
    try:
        first = _readline_timeout(proc.stdout, min(3.5, max(0.0, deadline - time.monotonic())))
        if first is None:
            return {"status": "BLOCKED", "reason": "target child readiness timed out",
                    "reaped": _terminate_and_reap(proc)}
        try:
            event = json.loads(first)
        except (ValueError, TypeError):
            return {"status": "BLOCKED", "reason": "target child returned malformed JSON",
                    "reaped": _terminate_and_reap(proc)}
        if event.get("status") == "FAKE_READY":
            if not _target_identity_verified(event.get("identity", {})):
                return {"status": "BLOCKED", "reason": "fake socket child identity was not verified",
                        "reaped": _terminate_and_reap(proc)}
            client = root_client(socket_name, event.get("replacement", {})) if root_client else {"result": "NOT RUN"}
            remaining = max(0.0, deadline - time.monotonic())
            final_line = _readline_timeout(proc.stdout, remaining)
            if final_line is None:
                return {"status": "BLOCKED", "reason": "fake service child timed out",
                        "root_client": client, "reaped": _terminate_and_reap(proc)}
            try:
                final = json.loads(final_line)
            except (ValueError, TypeError):
                return {"status": "BLOCKED", "reason": "fake service child returned malformed JSON",
                        "root_client": client, "reaped": _terminate_and_reap(proc)}
            final["root_client"] = client
        else:
            final = event
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            final["reaped"] = proc.poll() is not None
        except subprocess.TimeoutExpired:
            final.update(status="BLOCKED", reason="target child exit timed out",
                         reaped=_terminate_and_reap(proc))
        return final
    finally:
        if proc.poll() is None:
            _terminate_and_reap(proc)


def _fake_server_valid(value: dict) -> bool:
    return (isinstance(value, dict) and value.get("peer_uid") == 0
            and value.get("peer_gid") == 0
            and value.get("response") == FAKE_RESPONSE.decode("ascii"))


def _positive_control_confirmed(case: dict, root_client: dict) -> bool:
    target = case.get("target_result", {})
    return (case.get("profile") == "0777" and case.get("operation") == "replace"
            and target.get("operation_result") == "success"
            and target.get("rename_result") == "success"
            and _target_identity_verified(target.get("identity", {}))
            and _target_fixture_fd_access_verified(target)
            and _target_socket_identity_verified(target.get("replacement", {}))
            and _fake_server_valid(target.get("fake_server", {}))
            and _root_client_valid(root_client, uid=TARGET_UID, gid=TARGET_GID,
                                   response=FAKE_RESPONSE))


def _case_status(profile: str, result: dict) -> str:
    if result.get("status") != "COMPLETE" or not _target_identity_verified(result.get("identity", {})) \
            or not _target_fixture_fd_access_verified(result):
        return "BLOCKED"
    success = result.get("operation_result") == "success"
    if profile in ("0755", "acl") and success:
        return "UNSAFE_OBSERVED"
    if profile == "0777" and not success:
        return "PARTIAL"
    return "COMPLETE"


def _post_operation_observation(operation: str, result: dict,
                                directory_fd: int, socket_name: str,
                                moved_name: str, directory_identity: dict,
                                original_identity: dict) -> dict:
    outcome = result.get("operation_result")
    if outcome == "success":
        if operation == "replace":
            replacement = result.get("replacement", {})
            # _run_target connected while the fake server was alive, checked
            # SO_PEERCRED/response and rechecked this replacement inode. The
            # one-shot child server has exited by this post-operation pass.
            client = result.get("root_client", {"result": "NOT RUN"})
            valid = (_target_socket_identity_verified(replacement)
                     and _root_client_valid(client, uid=TARGET_UID, gid=TARGET_GID,
                                        response=FAKE_RESPONSE)
                     and _fake_server_valid(result.get("fake_server", {})))
        elif operation == "rename":
            client = _root_client_at(directory_fd, moved_name, ROOT_RESPONSE,
                                     directory_identity, original_identity)
            valid = _root_client_valid(client, response=ROOT_RESPONSE)
        elif operation == "unlink":
            client = _root_client_at(directory_fd, socket_name, ROOT_RESPONSE,
                                     directory_identity, expect_missing=True)
            valid = client.get("result") == "error" and client.get("errno") == errno.ENOENT
        else:
            return {"status": "PARTIAL", "root_client": {"result": "NOT RUN"}}
        return {"status": "UNSAFE_OBSERVED", "root_client": client,
                "root_client_valid": valid}
    if outcome == "error" and isinstance(result.get("errno"), int):
        client = _root_client_at(directory_fd, socket_name, ROOT_RESPONSE,
                                 directory_identity, original_identity)
        valid = _root_client_valid(client, response=ROOT_RESPONSE)
        return {"status": "COMPLETE" if valid else "PARTIAL",
                "root_client": client, "root_client_valid": valid}
    return {"status": "PARTIAL", "root_client": {"result": "NOT RUN"},
            "root_client_valid": False}


def _overall_status(plain_cases: list[dict], mounted_cases: list[dict],
                    positive: str, cleanup: str, worker_status: str) -> str:
    if cleanup != "COMPLETE" or worker_status == "BLOCKED":
        return "BLOCKED"
    if any(case.get("status") == "UNSAFE_OBSERVED" for case in (*plain_cases, *mounted_cases)):
        return "UNSAFE_OBSERVED"
    if positive != "CONFIRMED" or any(case.get("status") != "COMPLETE"
                                       for case in (*plain_cases, *mounted_cases)):
        return "PARTIAL"
    return "COMPLETE"


def _mount_bind(source_path: str, target_path: str) -> None:
    if not (_safe_procfd_child(source_path) and _safe_procfd_child(target_path)):
        raise ProbeBlocked("bind paths must be fixed children reached through held directory descriptors")
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount
    mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                      ctypes.c_ulong, ctypes.c_void_p)
    mount.restype = ctypes.c_int
    if mount(os.fsencode(source_path), os.fsencode(target_path), None, 4096, None) != 0:
        err = ctypes.get_errno() or errno.EIO
        raise OSError(err, "bind mount failed")


def _unmount_path(path: str) -> None:
    if not _safe_procfd_child(path):
        raise ProbeBlocked("unmount path must use the held target directory descriptor")
    libc = ctypes.CDLL(None, use_errno=True)
    umount2 = libc.umount2
    umount2.argtypes = (ctypes.c_char_p, ctypes.c_int)
    umount2.restype = ctypes.c_int
    if umount2(os.fsencode(path), 0) != 0:
        err = ctypes.get_errno() or errno.EIO
        raise OSError(err, "fixture bind unmount failed")


def _safe_procfd_child(path: str) -> bool:
    if not path.startswith("/proc/self/fd/"):
        return False
    tail = path[len("/proc/self/fd/"):].split("/")
    return (len(tail) == 2 and tail[0].isdigit()
            and _safe_leaf(tail[1]))


def _fd_child_path(directory_fd: int, name: str,
                   expected_directory_identity: dict | None = None) -> str:
    if not _safe_leaf(name):
        raise ProbeBlocked("fd-relative child path is invalid")
    directory_path = _fd_directory_path(directory_fd, expected_directory_identity)
    path = f"{directory_path}/{name}"
    if not _safe_procfd_child(path):
        raise ProbeBlocked("fd-relative child path is not a fixed procfd leaf")
    return path


def _emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
    sys.stdout.flush()


def _wait_command(timeout: float) -> dict | None:
    line = _readline_timeout(sys.stdin.buffer, timeout)
    if not line:
        return None
    try:
        value = json.loads(line)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _call_unshare() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    unshare = libc.unshare
    unshare.argtypes = (ctypes.c_int,)
    unshare.restype = ctypes.c_int
    if unshare(0x00020000) != 0:
        err = ctypes.get_errno() or errno.EIO
        raise OSError(err, "unshare(CLONE_NEWNS) failed")


def _make_mounts_private() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    mount = libc.mount
    mount.argtypes = (ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                      ctypes.c_ulong, ctypes.c_void_p)
    mount.restype = ctypes.c_int
    if mount(None, b"/", None, (1 << 14) | (1 << 18), None) != 0:  # MS_REC | MS_PRIVATE
        err = ctypes.get_errno() or errno.EIO
        raise OSError(err, "recursive private mount propagation setup failed")


def _verify_worker_namespace(pid: int, parent_namespace: dict) -> dict:
    try:
        child_namespace = _namespace_identity(f"/proc/{pid}/ns/mnt")
        propagation = _propagation_report(_read_mountinfo(f"/proc/{pid}/mountinfo"))
    except OSError as exc:
        return {"status": "BLOCKED", "reason": f"worker namespace could not be read ({exc.errno})"}
    if not _namespace_changed(parent_namespace, child_namespace):
        return {"status": "BLOCKED", "reason": "worker mount namespace inode matches parent",
                "namespace": child_namespace, "propagation": propagation}
    if propagation.get("status") != "PRIVATE":
        return {"status": "BLOCKED", "reason": "worker mount propagation is not verified private",
                "namespace": child_namespace, "propagation": propagation}
    return {"status": "PRIVATE", "namespace": child_namespace,
            "propagation": propagation}


def _mountpoint_event_valid(event: dict, case: dict) -> bool:
    return (event.get("event") == "mount_ready" and event.get("name") == case.get("name")
            and isinstance(event.get("mount_id"), str) and event["mount_id"].isdigit()
            and event.get("mount_id") != case.get("original_mount_id")
            and event.get("source_identity") == case.get("source_identity")
            and event.get("mounted_identity") == case.get("source_identity")
            and event.get("target_path") == case.get("target_path")
            and event.get("target_mount_id") == case.get("original_mount_id")
            and _mount_matches_bind(event.get("mountinfo", {}), mount_id=event.get("mount_id"),
                                    source=case.get("source_mount", {}),
                                    target=case.get("target_mount", {}),
                                    target_path=case.get("target_path"),
                                    source_root=case.get("source_root", "")))


def _original_view_matches(path: str, identity: dict, mount_id: str,
                           expected_mount_id: str, *, base_fd: int | None = None,
                           directory_name: str | None = None,
                           directory_identity: dict | None = None) -> dict:
    directory_fd = None
    try:
        if base_fd is not None:
            if not directory_name or not _safe_leaf(directory_name):
                return {"status": "BLOCKED", "reason": "invalid fixture directory name"}
            directory_fd, directory_info = _open_child_dir(base_fd, directory_name)
            if (directory_identity is not None
                    and not _same_object(directory_info, directory_identity, include_mode=True)):
                return {"status": "BLOCKED", "reason": "fixture directory identity changed"}
            fd = os.open(Path(path).name, _opath_flags(), dir_fd=directory_fd)
        else:
            fd = os.open(path, _opath_flags())
        try:
            info = os.fstat(fd)
            mount = _mount_for_fd(fd)
        finally:
            os.close(fd)
    except OSError as exc:
        return {"status": "BLOCKED", "errno": exc.errno}
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
    valid = (_same_object(info, identity) and mount.get("status") == "read"
             and mount.get("mount_id") == expected_mount_id == mount_id)
    return {"status": "VERIFIED" if valid else "BLOCKED",
            "identity": _identity(info), "mount_id": mount.get("mount_id"),
            "expected_mount_id": expected_mount_id}


def _safe_leaf(value: str) -> bool:
    return bool(value) and value not in (".", "..") and "/" not in value and "\\" not in value


def _worker_case(config: dict, parent_namespace: dict) -> int:
    current = None
    try:
        if os.geteuid() != 0:
            raise ProbeBlocked("namespace worker lost root identity")
        _call_unshare()
        current = _namespace_identity()
        if not _namespace_changed(parent_namespace, current):
            raise ProbeBlocked("unshare did not create a distinct mount namespace")
        _make_mounts_private()
        rows = _read_mountinfo()
        propagation = _propagation_report(rows)
        if propagation.get("status") != "PRIVATE":
            raise ProbeBlocked("mountinfo still reports shared/master propagation")

        run_lab_path = config.get("run_lab_path", "")
        base_lab_path = config.get("base_lab_path", "")
        if (not run_lab_path.startswith("/run/bind-sock-run-")
                or not base_lab_path.startswith("/vol1/1000/bind-sock-btrfs-")
                or not _safe_leaf(Path(run_lab_path).name)
                or not _safe_leaf(Path(base_lab_path).name)):
            raise ProbeBlocked("fixture roots escaped the approved /run and /vol1/1000 prefixes")
        run_lab_fd, run_lab_stat = _open_absolute_dir(Path(run_lab_path))
        base_lab_fd = None
        try:
            base_lab_fd, base_lab_stat = _open_absolute_dir(Path(base_lab_path))
            if (not _same_object(run_lab_stat, config["run_lab_identity"], include_mode=True)
                    or not _same_object(base_lab_stat, config["base_lab_identity"], include_mode=True)):
                raise ProbeBlocked("fixture root identity changed before namespace bind")
            _emit({"event": "namespace_ready", "namespace": current,
                   "propagation": propagation, "status": "READY"})
            command = _wait_command(CONTROL_TIMEOUT)
            if not command or command.get("command") != "begin":
                raise ProbeBlocked("parent did not authorize the fixture mount phase")
            for case in config["cases"]:
                if (not all(_safe_leaf(case.get(k, "")) for k in
                             ("source_name", "target_dir", "socket_name", "private_moved_name"))
                        or case.get("operation") not in OPERATIONS):
                    raise ProbeBlocked("worker case contains an unsafe child name")
                target_dir_fd, target_dir_stat = _open_child_dir(base_lab_fd, case["target_dir"])
                source_fd = target_fd = None
                try:
                    if not _same_object(target_dir_stat, case["target_dir_identity"]):
                        raise ProbeBlocked("mount target fixture directory identity changed")
                    source_path = str(Path(config["run_lab_path"]) / case["source_name"])
                    target_path = str(Path(config["base_lab_path"]) / case["target_dir"] / case["socket_name"])
                    if source_path != case["source_path"] or target_path != case["target_path"]:
                        raise ProbeBlocked("mount path did not match the fixed fixture path")
                    source_fd = target_fd = None
                    try:
                        source_fd = os.open(case["source_name"], _opath_flags(), dir_fd=run_lab_fd)
                        target_fd = os.open(case["socket_name"], _opath_flags(), dir_fd=target_dir_fd)
                        source_stat, target_stat = os.fstat(source_fd), os.fstat(target_fd)
                        if (not _same_object(source_stat, case["source_identity"], include_mode=True)
                                or not _same_object(target_stat, case["target_identity"], include_mode=True)):
                            raise ProbeBlocked("source or target socket inode changed before bind")
                        source_before_mount = _mount_for_fd(source_fd)
                        target_before_mount = _mount_for_fd(target_fd)
                        if (not _same_object(source_stat, case["source_identity"])
                                or not _same_object(target_stat, case["target_identity"])
                                or source_before_mount.get("status") != "read"
                                or source_before_mount.get("filesystem_type") != "tmpfs"
                                or source_before_mount.get("mount_id") != case["source_mount"]["mount_id"]
                                or target_before_mount.get("status") != "read"
                                or target_before_mount.get("filesystem_type") != "btrfs"
                                or target_before_mount.get("mount_id") != case["original_mount_id"]):
                            raise ProbeBlocked("source or target socket identity/mount changed before bind")
                        source_mount_arg = _fd_child_path(run_lab_fd, case["source_name"], config["run_lab_identity"])
                        target_mount_arg = _fd_child_path(target_dir_fd, case["socket_name"], case["target_dir_identity"])
                        try:
                            _mount_bind(source_mount_arg, target_mount_arg)
                        except OSError as exc:
                            _emit({"event": "case_blocked", "name": case["name"],
                                   "reason": "bind mount failed", "errno": exc.errno or errno.EIO,
                                   "fallback": "NONE"})
                            raise ProbeBlocked("bind mount failed; no fallback is permitted") from exc
                    finally:
                        for descriptor in (target_fd, source_fd):
                            if descriptor is not None:
                                os.close(descriptor)

                    mounted_fd = os.open(case["socket_name"], _opath_flags(), dir_fd=target_dir_fd)
                    try:
                        mounted_stat = os.fstat(mounted_fd)
                        mount = _mount_for_fd(mounted_fd)
                        mount_id = mount.get("mount_id")
                        source_root = f"/{Path(config['run_lab_path']).name}/{case['source_name']}"
                        verified = (mount.get("status") == "read"
                                    and mount.get("mount_id") != case["original_mount_id"]
                                    and _same_object(mounted_stat, case["source_identity"], include_mode=True)
                                    and _mount_matches_bind(mount, mount_id=mount_id,
                                                            source=source_before_mount,
                                                            target=target_before_mount,
                                                            target_path=target_path,
                                                            source_root=source_root))
                        if not verified:
                            raise ProbeBlocked("bind mount ID/source/target did not verify")
                    finally:
                        os.close(mounted_fd)

                    _emit({"event": "mount_ready", "name": case["name"],
                           "mount_id": mount_id, "target_mount_id": case["original_mount_id"],
                           "target_path": target_path, "source_identity": case["source_identity"],
                           "mounted_identity": _identity(mounted_stat), "mountinfo": mount})
                    command = _wait_command(CONTROL_TIMEOUT)
                    if not command or command.get("command") != "parent_verified":
                        raise ProbeBlocked("original-namespace fixture view was not verified by parent")
                    underlay_state = command.get("underlay_state")
                    original_result = command.get("original_target_result")
                    if (not isinstance(original_result, dict)
                            or original_result.get("status") != "COMPLETE"
                            or not original_result.get("reaped")
                            or not _target_observation_valid(original_result, case["target_identity"])):
                        raise ProbeBlocked("original-namespace target result identity was not verified")
                    expected_underlay = _expected_fixture_state(
                        case["operation"], original_result, case["target_identity"])
                    if (not _fixture_states_match(underlay_state, expected_underlay)
                            or underlay_state.get("entries", {}).get("private_moved", {}).get("status")
                            != "missing"):
                        raise ProbeBlocked("original-namespace fixture state was not verified")

                    private_moved_name = case["private_moved_name"]
                    child = _run_target(
                        config["python"], config["script"], case["operation"],
                        target_dir_fd, case["socket_name"], private_moved_name,
                        case["source_identity"], case["target_dir_identity"],
                        root_client=lambda name, replacement: _root_client_at(
                            target_dir_fd, name, FAKE_RESPONSE,
                            case["target_dir_identity"], replacement))
                    if (child.get("status") == "BLOCKED" or not child.get("reaped")
                            or not _target_observation_valid(child, case["source_identity"])):
                        raise ProbeBlocked("target child failed or was not reaped in private namespace")
                    observation = _post_operation_observation(
                        case["operation"], child, target_dir_fd, case["socket_name"],
                        private_moved_name, case["target_dir_identity"],
                        case["source_identity"])
                    private_status = observation["status"]

                    check_fd = os.open(case["socket_name"], _opath_flags(), dir_fd=target_dir_fd)
                    try:
                        current_mount = _mount_for_fd(check_fd)
                        current_info = os.fstat(check_fd)
                        can_unmount = _unmount_is_authorized(
                            namespace_private=True, mount_record=current_mount,
                            expected_mount_id=str(mount_id), source_mount=source_before_mount,
                            target_mount=target_before_mount,
                            source_identity=case["source_identity"],
                            mounted_identity=_identity(current_info), target_path=target_path,
                            source_root=source_root)
                    finally:
                        os.close(check_fd)
                    if not can_unmount:
                        raise ProbeBlocked("verified mount identity no longer authorizes unmount")
                    _unmount_path(_fd_child_path(
                        target_dir_fd, case["socket_name"], case["target_dir_identity"]))
                    restored_state = _read_fixture_state(
                        target_dir_fd, case["socket_name"],
                        case["socket_name"] + ".moved", private_moved_name,
                        case["target_dir_identity"])
                    if not _fixture_states_match(restored_state, underlay_state):
                        raise ProbeBlocked("Btrfs fixture state changed during private-view operation")
                    restored_mounts = _verify_fixture_mount_ids(
                        target_dir_fd, restored_state, case["socket_name"],
                        case["socket_name"] + ".moved", private_moved_name,
                        case["target_dir_identity"], case["original_mount_id"])
                    if restored_mounts.get("status") != "VERIFIED":
                        raise ProbeBlocked("Btrfs fixture mount identity did not restore after unmount")
                    _emit({"event": "mount_case_done", "name": case["name"],
                           "status": private_status, "target_result": child,
                           "root_client": observation["root_client"],
                           "root_client_valid": observation.get("root_client_valid", False),
                           "underlay_expected": underlay_state,
                           "underlay_after_unmount": restored_state,
                           "restored_mounts": restored_mounts,
                           "cleanup": "COMPLETE", "mount_id": str(mount_id)})
                    command = _wait_command(CONTROL_TIMEOUT)
                    if not command or command.get("command") != "case_checked":
                        raise ProbeBlocked("parent did not verify original namespace after unmount")
                finally:
                    for descriptor in (target_fd, source_fd):
                        if descriptor is not None:
                            try:
                                os.close(descriptor)
                            except OSError:
                                pass
                    os.close(target_dir_fd)
            _emit({"event": "final", "status": "COMPLETE"})
            return 0
        finally:
            if base_lab_fd is not None:
                os.close(base_lab_fd)
            os.close(run_lab_fd)
    except (OSError, ProbeBlocked) as exc:
        _emit({"event": "final", "status": "BLOCKED",
               "reason": exc.reason if isinstance(exc, ProbeBlocked)
               else f"namespace fixture failed ({exc.errno or exc.__class__.__name__})"})
        return 2


def _remove_known(directory_fd: int, name: str, identity: dict) -> bool:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if not _same_object(info, identity, include_mode="mode" in identity):
        return False
    try:
        os.unlink(name, dir_fd=directory_fd)
        return True
    except OSError:
        return False


def _fixture_entry_state(directory_fd: int, name: str) -> dict:
    if not _safe_leaf(name):
        raise ProbeBlocked("fixture state name is not a fixed child")
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return {"status": "missing"}
    except OSError as exc:
        raise ProbeBlocked(f"fixture state could not be read ({exc.errno})") from exc
    return {"status": "present", "identity": _identity(info)}


def _read_fixture_state(directory_fd: int, socket_name: str, moved_name: str,
                        private_moved_name: str,
                        directory_identity: dict) -> dict:
    _fd_directory_path(directory_fd, directory_identity)
    names = (socket_name, moved_name, private_moved_name)
    if any(not _safe_leaf(name) for name in names) or len(set(names)) != len(names):
        raise ProbeBlocked("fixture state names are invalid or duplicated")
    return {"status": "read", "entries": {
        "target": _fixture_entry_state(directory_fd, socket_name),
        "original_moved": _fixture_entry_state(directory_fd, moved_name),
        "private_moved": _fixture_entry_state(directory_fd, private_moved_name),
    }}


def _fixture_states_match(actual: dict, expected: dict) -> bool:
    if (not isinstance(actual, dict) or not isinstance(expected, dict)
            or actual.get("status") != "read" or expected.get("status") != "read"):
        return False
    actual_entries, expected_entries = actual.get("entries"), expected.get("entries")
    if (not isinstance(actual_entries, dict) or not isinstance(expected_entries, dict)
            or set(actual_entries) != {"target", "original_moved", "private_moved"}
            or set(expected_entries) != {"target", "original_moved", "private_moved"}):
        return False
    for key in actual_entries:
        left, right = actual_entries[key], expected_entries[key]
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        if left.get("status") != right.get("status"):
            return False
        if left.get("status") == "present":
            if not _same_identity_dict(left.get("identity", {}),
                                       right.get("identity", {})):
                return False
        elif left.get("status") != "missing":
            return False
    return True


def _verify_fixture_mount_ids(directory_fd: int, state: dict,
                               socket_name: str, moved_name: str,
                               private_moved_name: str,
                               directory_identity: dict,
                               expected_mount_id: str) -> dict:
    try:
        _fd_directory_path(directory_fd, directory_identity)
        directory_mount = _mount_for_fd(directory_fd)
    except ProbeBlocked as exc:
        return {"status": "BLOCKED", "reason": exc.reason}
    except OSError as exc:
        return {"status": "BLOCKED", "reason": f"fixture directory mount check failed ({exc.errno})"}
    if (directory_mount.get("status") != "read"
            or directory_mount.get("filesystem_type") != "btrfs"
            or directory_mount.get("mount_id") != str(expected_mount_id)):
        return {"status": "BLOCKED", "reason": "fixture directory mount identity changed",
                "directory_mount": directory_mount}
    entries = state.get("entries", {}) if isinstance(state, dict) else {}
    names = {"target": socket_name, "original_moved": moved_name,
             "private_moved": private_moved_name}
    if (state.get("status") != "read" or set(entries) != set(names)):
        return {"status": "BLOCKED", "reason": "fixture state structure is invalid"}
    for key, name in names.items():
        item = entries.get(key, {})
        if item.get("status") == "missing":
            continue
        if item.get("status") != "present":
            return {"status": "BLOCKED", "reason": "fixture entry state is unknown"}
        fd = None
        try:
            fd = os.open(name, _opath_flags(), dir_fd=directory_fd)
            info = os.fstat(fd)
            mount = _mount_for_fd(fd)
        except OSError as exc:
            return {"status": "BLOCKED", "reason": f"fixture mount check failed ({exc.errno})"}
        finally:
            if fd is not None:
                os.close(fd)
        if (not _same_object(info, item.get("identity", {}), include_mode=True)
                or mount.get("status") != "read"
                or mount.get("mount_id") != expected_mount_id):
            return {"status": "BLOCKED", "reason": "fixture entry identity or mount ID changed",
                    "entry": key, "mount": mount}
    return {"status": "VERIFIED", "mount_id": str(expected_mount_id),
            "directory_mount": directory_mount}


def _verify_original_endpoint_for_state(directory_fd: int, case: dict,
                                        state: dict) -> dict:
    if not isinstance(state, dict) or state.get("status") != "read":
        return {"status": "BLOCKED", "reason": "fixture state snapshot is missing"}
    entries = state.get("entries", {})
    target = entries.get("target", {})
    moved = entries.get("original_moved", {})
    directory_identity = case["identity"]
    if moved.get("status") == "present":
        identity = moved.get("identity", {})
        client = _root_client_at(directory_fd, case["socket_name"] + ".moved",
                                 ROOT_RESPONSE, directory_identity, identity)
        valid = _root_client_valid(client, response=ROOT_RESPONSE)
        target_valid = False
        if target.get("status") == "present":
            replacement = target.get("identity", {})
            try:
                target_info = os.stat(case["socket_name"], dir_fd=directory_fd,
                                      follow_symlinks=False)
                target_valid = (_same_object(target_info, replacement, include_mode=True)
                                and _target_socket_identity_verified(replacement))
            except OSError:
                target_valid = False
        elif target.get("status") == "missing":
            target_valid = _root_client_at(
                directory_fd, case["socket_name"], ROOT_RESPONSE,
                directory_identity, expect_missing=True).get("errno") == errno.ENOENT
        return {"status": "VERIFIED" if valid and target_valid else "BLOCKED",
                "endpoint": "original-moved",
                "client": client, "target_identity_valid": target_valid}
    if target.get("status") == "present":
        identity = target.get("identity", {})
        if identity.get("uid") == TARGET_UID and identity.get("gid") == TARGET_GID:
            # A replacement without the moved original endpoint cannot be
            # reconciled with any permitted fixture operation.
            return {"status": "BLOCKED", "endpoint": "replacement-without-original",
                    "reason": "replacement socket exists without the recorded original socket"}
        client = _root_client_at(directory_fd, case["socket_name"], ROOT_RESPONSE,
                                 directory_identity, identity)
        valid = _root_client_valid(client, response=ROOT_RESPONSE)
        return {"status": "VERIFIED" if valid else "BLOCKED",
                "endpoint": "target", "client": client}
    if target.get("status") == "missing" and moved.get("status") == "missing":
        client = _root_client_at(directory_fd, case["socket_name"], ROOT_RESPONSE,
                                 directory_identity, expect_missing=True)
        valid = client.get("result") == "error" and client.get("errno") == errno.ENOENT
        return {"status": "VERIFIED" if valid else "BLOCKED",
                "endpoint": "both-names-missing", "client": client}
    return {"status": "BLOCKED", "reason": "fixture endpoint state is ambiguous"}


def _cleanup_case(lab_fd: int, case: dict) -> str:
    if not _identity_binding_verified(case):
        return "BLOCKED"
    try:
        fd, current = _open_child_dir(lab_fd, case["name"])
    except (OSError, ProbeBlocked):
        return "BLOCKED"
    try:
        if (not _same_object(current, case["identity"])
                or not _same_object(current, case["initial_identity"])):
            return "BLOCKED"
        os.fchmod(fd, 0o700)
        allowed = case.get("allowed_entries", {})
        entries = os.listdir(fd)
        if any(name not in allowed for name in entries):
            return "BLOCKED"
        for name in entries:
            if not _remove_known(fd, name, allowed[name]):
                return "BLOCKED"
        if os.listdir(fd):
            return "BLOCKED"
        final = os.fstat(fd)
        if not _same_object(final, case["identity"]):
            return "BLOCKED"
        os.close(fd)
        fd = None
        path_info = os.stat(case["name"], dir_fd=lab_fd, follow_symlinks=False)
        if not _same_object(path_info, case["identity"]):
            return "BLOCKED"
        os.rmdir(case["name"], dir_fd=lab_fd)
        return "COMPLETE"
    except OSError:
        return "BLOCKED"
    finally:
        if fd is not None:
            os.close(fd)


def _cleanup_lab(base_fd: int, name: str, fd: int, identity: dict,
                 known_sockets: dict, cases: list[dict], lab_path: str) -> dict:
    try:
        entry = os.stat(name, dir_fd=base_fd, follow_symlinks=False)
        opened = os.fstat(fd)
        if not _same_object(entry, identity) or not _same_object(opened, identity):
            return {"status": "BLOCKED", "root_retained": True,
                    "reason": "lab root path or descriptor identity changed"}
        os.fchmod(fd, 0o700)
        entries = os.listdir(fd)
        case_names = {case["name"] for case in cases}
        root_socket_names = set(known_sockets)
        if any(item not in case_names | root_socket_names for item in entries):
            return {"status": "BLOCKED", "root_retained": True,
                    "reason": "unknown fixture entry retained"}
        for case in cases:
            if case["name"] in entries and _cleanup_case(fd, case) != "COMPLETE":
                return {"status": "BLOCKED", "root_retained": True,
                        "reason": "known case cleanup identity failed"}
        for socket_name, socket_identity in known_sockets.items():
            if socket_name in os.listdir(fd) and not _remove_known(fd, socket_name, socket_identity):
                return {"status": "BLOCKED", "root_retained": True,
                        "reason": "known root socket identity failed"}
        residual = os.listdir(fd)
        if residual:
            return {"status": "BLOCKED", "root_retained": True,
                    "residual_entries": residual}
        final = os.fstat(fd)
        if not _same_object(final, identity) or stat.S_IMODE(final.st_mode) != 0o700:
            return {"status": "BLOCKED", "root_retained": True,
                    "reason": "lab root did not remain safely locked"}
        after_path = os.stat(name, dir_fd=base_fd, follow_symlinks=False)
        if not _same_object(after_path, identity):
            return {"status": "BLOCKED", "root_retained": True,
                    "reason": "lab root entry changed during cleanup"}
        return {"status": "COMPLETE", "root_retained": True,
                "path": lab_path,
                "identity": identity, "mode": "0700", "residual_entries": []}
    except OSError as exc:
        return {"status": "BLOCKED", "root_retained": True,
                "reason": f"cleanup check failed ({exc.errno or exc.__class__.__name__})"}


def _ledger_snapshot(ledger: dict) -> dict:
    def clean(record: dict, fields: tuple[str, ...]) -> dict:
        return {key: record.get(key) for key in fields}
    return {
        "phase": ledger.get("phase"),
        "setup_complete": ledger.get("setup_complete", False),
        "roots": {role: clean(record, (
            "role", "name", "path", "created", "identity", "initial_identity",
            "identity_binding", "identity_binding_failure", "identity_stage",
            "status", "cleanup", "failure", "dirfd_release"))
            for role, record in ledger.get("roots", {}).items()},
        "cases": [clean(record, (
            "root_role", "name", "path", "created", "identity", "initial_identity",
            "identity_binding", "identity_binding_failure", "identity_stage",
            "profile", "acl", "allowed_entries", "cleanup", "failure",
            "dirfd_release"))
            for record in ledger.get("cases", [])],
        "sockets": [clean(record, (
            "root_role", "case_name", "name", "path", "bind_attempted", "created",
            "identity", "identity_stage", "cleanup", "failure"))
            for record in ledger.get("sockets", [])],
        "servers": [{
            "path": record.get("path"), "socket_name": record.get("socket_name"),
            "socket_identity": record.get("socket_identity"),
            "case_name": record.get("case_name"), "started": record.get("started"),
            "start_error": record.get("start_error"),
            "close_status": record.get("close_status"),
            "thread_alive_after_close": record.get("thread_alive_after_close"),
            "close_error": record.get("close_error"),
        } for record in ledger.get("servers", [])],
    }


def _mark_cleanup_blocked(ledger: dict, reason: str) -> dict:
    for record in ledger.get("roots", {}).values():
        if record.get("created"):
            record["cleanup"] = "BLOCKED"
        else:
            record["cleanup"] = "NOT CREATED"
    for record in (*ledger.get("cases", []), *ledger.get("sockets", [])):
        record["cleanup"] = "BLOCKED" if record.get("created") else "NOT CREATED"
    return {"status": "BLOCKED", "reason": reason,
            "roots": {role: record.get("cleanup")
                      for role, record in ledger.get("roots", {}).items()},
            "items": [{"kind": record.get("kind"), "name": record.get("name"),
                       "path": record.get("path"), "status": record.get("cleanup")}
                      for record in (*ledger.get("cases", []), *ledger.get("sockets", []))]}


def _preflight_ledger_root(root_record: dict, cases: list[dict],
                           sockets: list[dict]) -> dict:
    if not root_record.get("created"):
        root_record["cleanup"] = "NOT CREATED"
        return {"status": "NOT CREATED", "items": []}
    root_fd, identity = root_record.get("dir_fd"), root_record.get("identity")
    initial_identity = root_record.get("initial_identity")
    if not _identity_binding_verified(root_record):
        return {"status": "BLOCKED", "reason": "root initial stat is not bound to its held descriptor"}
    if root_fd is None or not isinstance(identity, dict):
        return {"status": "BLOCKED", "reason": "root identity or held dirfd is unknown"}
    try:
        path_info = os.stat(root_record["name"], dir_fd=root_record["parent_fd"],
                            follow_symlinks=False)
        fd_info = os.fstat(root_fd)
        if (not _same_object(path_info, initial_identity)
                or not _same_object(fd_info, initial_identity)
                or not _same_object(path_info, identity)
                or not _same_object(fd_info, identity)):
            return {"status": "BLOCKED", "reason": "root path or dirfd identity changed"}
        entries = set(os.listdir(root_fd))
    except (OSError, KeyError, TypeError) as exc:
        return {"status": "BLOCKED",
                "reason": f"root ledger preflight failed ({getattr(exc, 'errno', None)})"}
    active_cases = [case for case in cases if case.get("created")]
    active_sockets = [item for item in sockets
                      if item.get("created") and item.get("case_name") is None]
    expected_names = {case.get("name") for case in active_cases}
    expected_names.update(item.get("name") for item in active_sockets)
    if entries != expected_names:
        return {"status": "BLOCKED", "reason": "root entries differ from the creation ledger",
                "unknown_entries": sorted(entries - expected_names),
                "missing_entries": sorted(expected_names - entries)}
    for case in active_cases:
        if not _identity_binding_verified(case):
            return {"status": "BLOCKED",
                    "reason": f"case initial stat is not bound to its held descriptor: {case.get('name')}"}
        if not isinstance(case.get("identity"), dict) or case.get("dir_fd") is None:
            return {"status": "BLOCKED", "reason": f"case identity or dirfd unknown: {case.get('name')}"}
        try:
            case_info = os.stat(case["name"], dir_fd=root_fd, follow_symlinks=False)
            opened = os.fstat(case["dir_fd"])
            if (not _same_object(case_info, case["initial_identity"])
                    or not _same_object(opened, case["initial_identity"])
                    or not _same_object(case_info, case["identity"], include_mode=True)
                    or not _same_object(opened, case["identity"], include_mode=True)):
                return {"status": "BLOCKED", "reason": f"case identity changed: {case['name']}"}
            child_names = set(os.listdir(case["dir_fd"]))
            allowed = case.get("allowed_entries", {})
            if child_names != set(allowed):
                return {"status": "BLOCKED", "reason": f"case entries differ from ledger: {case['name']}",
                        "case_entries": sorted(child_names),
                        "recorded_entries": sorted(allowed)}
            for child_name, child_identity in allowed.items():
                child_info = os.stat(child_name, dir_fd=case["dir_fd"],
                                     follow_symlinks=False)
                if not _same_object(child_info, child_identity, include_mode=True):
                    return {"status": "BLOCKED",
                            "reason": f"case entry identity changed: {case['name']}/{child_name}"}
        except (OSError, KeyError, TypeError) as exc:
            return {"status": "BLOCKED",
                    "reason": f"case preflight failed: {case.get('name')} ({getattr(exc, 'errno', None)})"}
    for item in active_sockets:
        if not isinstance(item.get("identity"), dict):
            return {"status": "BLOCKED", "reason": f"root socket identity unknown: {item.get('name')}"}
        try:
            info = os.stat(item["name"], dir_fd=root_fd, follow_symlinks=False)
            if not _same_object(info, item["identity"], include_mode=True):
                return {"status": "BLOCKED", "reason": f"root socket identity changed: {item['name']}"}
        except OSError as exc:
            return {"status": "BLOCKED",
                    "reason": f"root socket preflight failed: {item.get('name')} ({exc.errno})"}
    for item in sockets:
        if item.get("root_role") != root_record.get("role") or item.get("case_name") is not None:
            continue
        if item.get("bind_attempted") and not item.get("created"):
            try:
                os.stat(item["name"], dir_fd=root_fd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as exc:
                return {"status": "BLOCKED", "reason": f"unregistered bind result unknown ({exc.errno})"}
            return {"status": "BLOCKED", "reason": f"unregistered socket object present: {item['name']}"}
        if item.get("created") and not isinstance(item.get("identity"), dict):
            return {"status": "BLOCKED", "reason": f"created socket identity unknown: {item['name']}"}
    return {"status": "VERIFIED", "items": []}


def _cleanup_ledger(ledger: dict, *, threads_quiescent: bool,
                    worker_reaped: bool) -> dict:
    if not threads_quiescent:
        return _mark_cleanup_blocked(
            ledger, "server thread exit was not confirmed; all fixtures retained")
    if not worker_reaped:
        return _mark_cleanup_blocked(
            ledger, "namespace worker exit was not confirmed; all fixtures retained")
    preflight = {}
    for role, root in ledger.get("roots", {}).items():
        preflight[role] = _preflight_ledger_root(
            root, [item for item in ledger.get("cases", []) if item.get("root_role") == role],
            [item for item in ledger.get("sockets", []) if item.get("root_role") == role])
    failed = {role: result for role, result in preflight.items()
              if result.get("status") not in ("VERIFIED", "NOT CREATED")}
    if failed:
        cleanup = _mark_cleanup_blocked(
            ledger, "ledger preflight failed; no fixture objects were removed")
        cleanup["preflight"] = failed
        return cleanup

    failures = []
    for root in ledger.get("roots", {}).values():
        if not root.get("created"):
            continue
        try:
            os.fchmod(root["dir_fd"], 0o700)
            info = os.fstat(root["dir_fd"])
            if (not _same_object(info, root["identity"])
                    or stat.S_IMODE(info.st_mode) != 0o700):
                raise ProbeBlocked("verified root could not be locked for cleanup")
        except (OSError, ProbeBlocked) as exc:
            failures.append({"stage": "lock-root", "root": root.get("role"),
                             "error": _exception_record(exc)})
            break
    if failures:
        cleanup = _mark_cleanup_blocked(ledger, "root lock failed before fixture cleanup")
        cleanup["failures"] = failures
        return cleanup

    for role, root in ledger.get("roots", {}).items():
        if not root.get("created"):
            root["cleanup"] = "NOT CREATED"
            continue
        root_fd = root["dir_fd"]
        for item in ledger.get("sockets", []):
            if (item.get("root_role") == role and item.get("case_name") is None
                    and item.get("created")):
                if not _remove_known(root_fd, item["name"], item["identity"]):
                    item["cleanup"] = "BLOCKED"
                    failures.append({"stage": "remove-root-socket", "path": item.get("path")})
                    break
                item["cleanup"] = "COMPLETE"
        if failures:
            break
        for case in ledger.get("cases", []):
            if case.get("root_role") != role:
                continue
            if not case.get("created"):
                case["cleanup"] = "NOT CREATED"
                continue
            if _cleanup_case(root_fd, case) != "COMPLETE":
                case["cleanup"] = "BLOCKED"
                failures.append({"stage": "remove-case", "path": case.get("path")})
                break
            case["cleanup"] = "COMPLETE"
            for socket_item in ledger.get("sockets", []):
                if (socket_item.get("root_role") == role
                        and socket_item.get("case_name") == case.get("name")):
                    socket_item["cleanup"] = "COMPLETE"
        if failures:
            break
        try:
            residual = os.listdir(root_fd)
            if residual:
                raise ProbeBlocked("known fixture cleanup left residual entries")
            final = os.fstat(root_fd)
            path_info = os.stat(root["name"], dir_fd=root["parent_fd"],
                                follow_symlinks=False)
            if (not _same_object(final, root["identity"])
                    or stat.S_IMODE(final.st_mode) != 0o700
                    or not _same_object(path_info, root["identity"])):
                raise ProbeBlocked("outer root identity or locked mode changed")
            root["cleanup"] = "COMPLETE; EMPTY ROOT RETAINED 0700"
        except (OSError, ProbeBlocked) as exc:
            root["cleanup"] = "BLOCKED"
            failures.append({"stage": "verify-root-retained", "root": role,
                             "error": _exception_record(exc)})
            break
    if failures:
        for root in ledger.get("roots", {}).values():
            if root.get("created") and root.get("cleanup", "").startswith("NOT"):
                root["cleanup"] = "BLOCKED"
        for record in (*ledger.get("cases", []), *ledger.get("sockets", [])):
            if record.get("created") and record.get("cleanup") == "NOT RUN":
                record["cleanup"] = "BLOCKED; NOT RUN AFTER PRIOR FAILURE"
        return {"status": "BLOCKED", "reason": "one or more identity-checked cleanup steps failed",
                "failures": failures,
                "roots": {role: item.get("cleanup") for role, item in ledger["roots"].items()},
                "items": [{"kind": record.get("kind"), "name": record.get("name"),
                           "path": record.get("path"), "status": record.get("cleanup")}
                          for record in (*ledger.get("cases", []), *ledger.get("sockets", []))]}
    return {"status": "COMPLETE",
            "roots": {role: item.get("cleanup") for role, item in ledger["roots"].items()},
            "items": [{"kind": record.get("kind"), "name": record.get("name"),
                       "path": record.get("path"), "status": record.get("cleanup")}
                      for record in (*ledger.get("cases", []), *ledger.get("sockets", []))]}


def _close_ledger_descriptors(ledger: dict) -> list[dict]:
    failures = []
    seen = set()
    for record in [*ledger.get("cases", []), *ledger.get("roots", {}).values()]:
        fd = record.get("dir_fd")
        if fd is None or fd in seen:
            continue
        seen.add(fd)
        try:
            os.close(fd)
            record["dirfd_release"] = "CLOSED"
        except OSError as exc:
            record["dirfd_release"] = f"FAILED ({exc.errno})"
            failures.append({"kind": record.get("kind"), "path": record.get("path"),
                             "errno": exc.errno})
        record["dir_fd"] = None
    return failures


def _register_server(ledger: dict, server: RootSocketServer,
                     socket_record: dict, case_record: dict | None = None) -> dict:
    record = {"path": socket_record["path"], "socket_name": socket_record["name"],
              "socket_identity": socket_record.get("identity"), "server": server,
              "case_name": case_record.get("name") if case_record else None,
              "started": False, "start_error": None, "close_status": "NOT RUN"}
    ledger["servers"].append(record)
    if case_record is not None:
        case_record["server"] = server
    return record


def _start_registered_server(ledger: dict, server_record: dict) -> None:
    ledger["phase"] = f"server:{server_record['socket_name']}:start"
    try:
        server_record["server"].start()
        server_record["started"] = True
    except BaseException as exc:
        server_record["started"] = server_record["server"].started
        server_record["start_error"] = _exception_record(exc)
        raise


def _create_fixture_case(lab_fd: int, lab_path: str, name: str,
                         profile: str, socket_name: str,
                         root_record: dict, ledger: dict) -> dict:
    case_record = _plan_case(ledger, root_record, name)
    case_fd, case_info = _create_case_dir(lab_fd, name, profile, case_record, ledger)
    path = str(Path(lab_path) / name / socket_name)
    case_info.update({"socket_name": socket_name, "socket_path": path,
                      "profile": profile})
    socket_record = _plan_socket(ledger, root_record, socket_name, path, case_fd,
                                 case_info)
    listener = None
    server = None
    try:
        listener, socket_identity = _bind_root_socket(
            case_fd, socket_name, case_info["identity"], ledger=ledger,
            socket_record=socket_record, case_record=case_info)
        case_info["socket_identity"] = socket_identity
        server = RootSocketServer(listener, ROOT_RESPONSE)
        server_record = _register_server(ledger, server, socket_record, case_info)
        acl_diagnostics = {"status": "PENDING", "fixture": case_info["identity"]}
        case_info["acl"] = acl_diagnostics
        ledger["phase"] = f"case:{name}:profile"
        try:
            profile_result = _apply_case_profile(
                case_fd, profile, acl_diagnostics, ledger, name)
        except BaseException:
            try:
                _record_bound_directory_identity(
                    case_info, os.fstat(case_fd), "failed profile descriptor stat")
            except ProbeBlocked:
                pass
            except OSError:
                case_info["identity_stage"] = "descriptor identity unavailable after profile failure"
            raise
        case_info["acl"] = profile_result
        _record_bound_directory_identity(
            case_info, os.fstat(case_fd), "profile descriptor stat")
        _start_registered_server(ledger, server_record)
        case_info["status"] = "READY"
        return case_info
    except BaseException:
        # The ledger owns the case dirfd and any bound socket. The central
        # recovery path closes registered servers before considering cleanup.
        if listener is not None and server is None:
            try:
                listener.close()
            except OSError:
                pass
        raise


def _setup_fixtures(context: dict, run_name: str, run_fd: int, run_identity: dict,
                    base_name: str, base_fd: int, base_identity: dict,
                    ledger: dict) -> dict:
    run_path, base_path = str(RUN / run_name), str(BASE / base_name)
    cases, source_servers, plain_cases, mounted_cases = [], [], [], []
    run_root = ledger["roots"]["run"]
    base_root = ledger["roots"]["btrfs"]
    for profile in MODES:
        for operation in OPERATIONS:
            plain_name = f"plain-{profile}-{operation}"
            ledger["phase"] = f"fixture:{plain_name}:create"
            plain = _create_fixture_case(
                base_fd, base_path, plain_name, profile, "service.sock",
                base_root, ledger)
            plain.update(operation=operation, view="original")
            cases.append(plain); plain_cases.append(plain)

            mounted_name = f"mnt-{profile}-{operation}"
            ledger["phase"] = f"fixture:{mounted_name}:create"
            mounted = _create_fixture_case(
                base_fd, base_path, mounted_name, profile, "target.sock",
                base_root, ledger)
            mounted.update(operation=operation, view="private")
            cases.append(mounted); mounted_cases.append(mounted)

            source_name = f"source-{profile}-{operation}.sock"
            source_path = str(Path(run_path) / source_name)
            source_record = _plan_socket(ledger, run_root, source_name, source_path, run_fd)
            ledger["phase"] = f"socket:{source_name}:create"
            source_listener, source_identity = _bind_root_socket(
                run_fd, source_name, run_identity, ledger=ledger,
                socket_record=source_record)
            source_server = RootSocketServer(source_listener, ROOT_RESPONSE)
            source_server_record = _register_server(ledger, source_server, source_record)
            _start_registered_server(ledger, source_server_record)
            source_servers.append(source_server)
            mounted.update(source_name=source_name, source_path=source_path,
                           source_identity=source_identity, source_server=source_server)
    ledger["setup_complete"] = True
    ledger["phase"] = "fixture-setup-complete"
    return {"cases": cases, "plain_cases": plain_cases, "mounted_cases": mounted_cases,
            "source_servers": source_servers, "run_path": run_path,
            "base_path": base_path, "run_name": run_name, "base_name": base_name,
            "run_fd": run_fd, "base_fd": base_fd,
            "run_identity": run_identity, "base_identity": base_identity}


def _expected_fixture_state(operation: str, result: dict,
                            original_identity: dict) -> dict:
    missing = {"status": "missing"}
    original = {"status": "present", "identity": original_identity}
    outcome = result.get("operation_result")
    renamed = operation == "rename" and outcome == "success"
    unlinked = operation == "unlink" and outcome == "success"
    replaced = (operation == "replace" and outcome == "success"
                and _target_socket_identity_verified(result.get("replacement", {})))
    partial_replace = (operation == "replace" and outcome != "success"
                       and result.get("rename_result") == "success")
    if renamed:
        target, moved = missing, original
    elif unlinked:
        target, moved = missing, missing
    elif replaced:
        target = {"status": "present", "identity": result.get("replacement", {})}
        moved = original
    elif partial_replace:
        target, moved = missing, original
    else:
        target, moved = original, missing
    return {"status": "read", "entries": {
        "target": target, "original_moved": moved, "private_moved": missing,
    }}


def _update_case_cleanup_identities(case: dict, expected_state: dict,
                                    moved_name: str, private_moved_name: str) -> None:
    entries = expected_state.get("entries", {})
    allowed = case.setdefault("allowed_entries", {})
    for key, name in (("target", case["socket_name"]),
                      ("original_moved", moved_name),
                      ("private_moved", private_moved_name)):
        entry = entries.get(key, {})
        if entry.get("status") == "present":
            allowed[name] = entry.get("identity", {})
        else:
            allowed.pop(name, None)


def _run_fixture_operation(case: dict, directory_fd: int, python: str, script: str,
                           moved_name: str, private_moved_name: str,
                           *, require_writable_control: bool) -> dict:
    directory_identity = case["identity"]
    original_identity = case["socket_identity"]
    socket_name = case["socket_name"]
    try:
        _fd_directory_path(directory_fd, directory_identity)
        before_state = _read_fixture_state(
            directory_fd, socket_name, moved_name, private_moved_name, directory_identity)
    except (OSError, ProbeBlocked) as exc:
        return {"name": case["name"], "profile": case["profile"],
                "operation": case["operation"], "status": "BLOCKED",
                "reason": exc.reason if isinstance(exc, ProbeBlocked)
                else f"fixture pre-state could not be checked ({exc.errno})"}
    initial = {"status": "read", "entries": {
        "target": {"status": "present", "identity": original_identity},
        "original_moved": {"status": "missing"},
        "private_moved": {"status": "missing"},
    }}
    if not _fixture_states_match(before_state, initial):
        return {"name": case["name"], "profile": case["profile"],
                "operation": case["operation"], "status": "BLOCKED",
                "reason": "fixture entries did not match their creation identities",
                "state_before": before_state}
    baseline = _root_client_at(directory_fd, socket_name, ROOT_RESPONSE,
                               directory_identity, original_identity)
    if not _root_client_valid(baseline):
        return {"name": case["name"], "profile": case["profile"],
                "operation": case["operation"], "status": "BLOCKED",
                "root_baseline": baseline, "state_before": before_state}

    def fake_client(name: str, replacement_identity: dict) -> dict:
        if name != socket_name:
            return {"result": "BLOCKED", "reason": "unexpected replacement leaf"}
        return _root_client_at(directory_fd, name, FAKE_RESPONSE,
                               directory_identity, replacement_identity)

    result = _run_target(
        python, script, case["operation"], directory_fd, socket_name, moved_name,
        original_identity, directory_identity, root_client=fake_client)
    case["target_result"] = result
    if (result.get("status") != "COMPLETE" or not result.get("reaped")
            or not _target_observation_valid(result, original_identity)):
        return {"name": case["name"], "profile": case["profile"],
                "operation": case["operation"], "status": "BLOCKED",
                "target_result": result, "root_baseline": baseline,
                "state_before": before_state, "state_after": "NOT VERIFIED"}

    observation = _post_operation_observation(
        case["operation"], result, directory_fd, socket_name, moved_name,
        directory_identity, original_identity)
    expected_state = _expected_fixture_state(case["operation"], result, original_identity)
    try:
        after_state = _read_fixture_state(
            directory_fd, socket_name, moved_name, private_moved_name, directory_identity)
    except (OSError, ProbeBlocked) as exc:
        return {"name": case["name"], "profile": case["profile"],
                "operation": case["operation"], "status": "BLOCKED",
                "target_result": result, "root_baseline": baseline,
                "root_client": observation["root_client"], "state_before": before_state,
                "reason": exc.reason if isinstance(exc, ProbeBlocked)
                else f"fixture post-state could not be checked ({exc.errno})"}
    state_valid = _fixture_states_match(after_state, expected_state)
    if state_valid:
        _update_case_cleanup_identities(case, expected_state, moved_name, private_moved_name)

    status = _case_status(case["profile"], result)
    if (case["operation"] == "replace"
            and result.get("rename_result") == "success"
            and result.get("operation_result") != "success"):
        status = "PARTIAL"
    if (not require_writable_control and case["profile"] == "0777"
            and result.get("operation_result") == "error"
            and observation.get("root_client_valid")):
        status = "COMPLETE"
    if status != "UNSAFE_OBSERVED" and not observation.get("root_client_valid"):
        status = "PARTIAL"
    if not state_valid:
        status = "BLOCKED"
    if require_writable_control and case["profile"] == "0777":
        # A denied operation on the unmounted 0777 control is not a valid
        # control result, even when the root service remains reachable.
        if result.get("operation_result") != "success":
            status = "PARTIAL"

    case["root_baseline"] = baseline
    case["root_client"] = observation["root_client"]
    case["fixture_state_before"] = before_state
    case["fixture_state_after"] = after_state
    return {"name": case["name"], "profile": case["profile"],
            "operation": case["operation"], "status": status,
            "target_result": result, "root_baseline": baseline,
            "root_client": observation["root_client"],
            "root_client_valid": observation.get("root_client_valid", False),
            "state_before": before_state, "state_after": after_state,
            "acl": case.get("acl")}


def _original_operation(case: dict, python: str, script: str,
                        base_fd: int) -> dict:
    try:
        case_fd, case_info = _open_child_dir(base_fd, case["name"])
    except (OSError, ProbeBlocked) as exc:
        return {"name": case["name"], "profile": case["profile"],
                "operation": case["operation"], "status": "BLOCKED",
                "reason": exc.reason if isinstance(exc, ProbeBlocked)
                else f"fixture directory could not be opened ({exc.errno})"}
    try:
        if not _same_object(case_info, case["identity"], include_mode=True):
            return {"name": case["name"], "profile": case["profile"],
                    "operation": case["operation"], "status": "BLOCKED",
                    "reason": "fixture directory identity changed"}
        return _run_fixture_operation(
            case, case_fd, python, script,
            case["socket_name"] + ".moved",
            case["socket_name"] + ".private-moved",
            require_writable_control=True)
    finally:
        os.close(case_fd)


def _worker_config(fixtures: dict, context: dict) -> dict:
    cases = []
    for case in fixtures["mounted_cases"]:
        target_path = case["socket_path"]
        case_fd, case_dir_info = _open_child_dir(fixtures["base_fd"], case["name"])
        try:
            target_fd = os.open(case["socket_name"], _opath_flags(), dir_fd=case_fd)
            try:
                target_info = os.fstat(target_fd)
                target_mount = _mount_for_fd(target_fd)
            finally:
                os.close(target_fd)
        finally:
            os.close(case_fd)
        if (not _same_object(target_info, case["socket_identity"], include_mode=True)
                or target_mount.get("status") != "read"
                or target_mount.get("filesystem_type") != "btrfs"
                or target_mount.get("mount_id") != context["base_mount"].get("mount_id")
                or not _same_object(case_dir_info, case["identity"], include_mode=True)):
            raise ProbeBlocked("Btrfs target socket mount ID could not be resolved")
        source_fd = os.open(case["source_name"], _opath_flags(), dir_fd=fixtures["run_fd"])
        try:
            source_info = os.fstat(source_fd)
            source_mount = _mount_for_fd(source_fd)
        finally:
            os.close(source_fd)
        if (not _same_object(source_info, case["source_identity"], include_mode=True)
                or source_mount.get("status") != "read"
                or source_mount.get("filesystem_type") != "tmpfs"
                or source_mount.get("mount_id") != context["run_mount"].get("mount_id")):
            raise ProbeBlocked("source socket tmpfs mount ID could not be resolved")
        source_root = f"/{Path(fixtures['run_path']).name}/{case['source_name']}"
        cases.append({"name": case["name"], "profile": case["profile"],
                      "operation": case["operation"], "source_name": case["source_name"],
                      "source_path": case["source_path"], "source_identity": case["source_identity"],
                      "target_dir": case["name"], "target_dir_identity": case["identity"],
                      "socket_name": case["socket_name"],
                      "private_moved_name": case["socket_name"] + ".private-moved",
                      "target_path": target_path,
                      "target_identity": case["socket_identity"],
                      "original_mount_id": target_mount["mount_id"],
                      "source_mount": source_mount, "target_mount": target_mount,
                      "source_root": source_root})
    return {"run_lab_path": fixtures["run_path"], "base_lab_path": fixtures["base_path"],
            "run_lab_identity": fixtures["run_identity"], "base_lab_identity": fixtures["base_identity"],
            "run_fd": fixtures["run_fd"], "base_fd": fixtures["base_fd"],
            "python": context["python"], "script": context["script"], "cases": cases}


def _send_command(proc: subprocess.Popen, command: str, **values) -> bool:
    try:
        payload = {"command": command, **values}
        proc.stdin.write((json.dumps(payload) + "\n").encode("ascii"))
        proc.stdin.flush()
        return True
    except (OSError, ValueError):
        return False


def _read_worker_event(proc: subprocess.Popen, timeout: float) -> dict | None:
    line = _readline_timeout(proc.stdout, timeout)
    if not line:
        return None
    try:
        value = json.loads(line)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _start_worker(context: dict, config: dict) -> subprocess.Popen:
    argv = [context["python"], "-I", "-S", context["script"], "--namespace-worker",
            json.dumps(config, separators=(",", ":")), json.dumps(context["parent_namespace"])]
    return subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, close_fds=True,
                            env={"PATH": "/usr/bin:/bin"}, bufsize=0,
                            start_new_session=True)


def _run_mounted_phase(context: dict, fixtures: dict) -> tuple[list[dict], str, bool]:
    try:
        config = _worker_config(fixtures, context)
        proc = _start_worker(context, config)
    except (OSError, ProbeBlocked):
        return [], "BLOCKED", True
    output, status, worker_reaped = [], "BLOCKED", False
    deadline = time.monotonic() + WORKER_TIMEOUT
    read_event = lambda: _read_worker_event(
        proc, min(CONTROL_TIMEOUT, max(0.0, deadline - time.monotonic())))
    try:
        ready = read_event()
        if not ready or ready.get("event") != "namespace_ready" or ready.get("status") != "READY":
            worker_reaped = _terminate_and_reap(proc, process_group=True)
            return [], "BLOCKED", worker_reaped
        verified = _verify_worker_namespace(proc.pid, context["parent_namespace"])
        if verified.get("status") != "PRIVATE" or ready.get("propagation", {}).get("status") != "PRIVATE":
            worker_reaped = _terminate_and_reap(proc, process_group=True)
            return [], "BLOCKED", worker_reaped
        status = "COMPLETE"
        if not _send_command(proc, "begin"):
            worker_reaped = _terminate_and_reap(proc, process_group=True)
            return [], "BLOCKED", worker_reaped
        for case in config["cases"]:
            event = read_event()
            if not event or event.get("event") == "final":
                status = "BLOCKED"
                break
            if not _mountpoint_event_valid(event, case):
                status = "BLOCKED"
                _send_command(proc, "abort")
                break
            verified_ns = _verify_worker_namespace(proc.pid, context["parent_namespace"])
            if verified_ns.get("status") != "PRIVATE":
                status = "BLOCKED"
                _send_command(proc, "abort")
                break
            worker_mount = _mount_record_for_id(
                event["mount_id"], _read_mountinfo(f"/proc/{proc.pid}/mountinfo"))
            if (worker_mount.get("status") != "read"
                    or not _mount_matches_bind(worker_mount, mount_id=event["mount_id"],
                                                source=case["source_mount"],
                                                target=case["target_mount"],
                                                target_path=case["target_path"],
                                                source_root=case["source_root"])):
                status = "BLOCKED"
                _send_command(proc, "abort")
                break

            target = next(item for item in fixtures["mounted_cases"]
                          if item["name"] == case["name"])
            case_fd = None
            try:
                case_fd, case_dir_info = _open_child_dir(
                    fixtures["base_fd"], case["target_dir"])
                if not _same_object(case_dir_info, case["target_dir_identity"],
                                    include_mode=True):
                    raise ProbeBlocked("original-view fixture directory identity changed")
                original_before = _original_view_matches(
                    case["target_path"], case["target_identity"],
                    case["original_mount_id"], case["original_mount_id"],
                    base_fd=fixtures["base_fd"], directory_name=case["target_dir"],
                    directory_identity=case["target_dir_identity"])
                if original_before.get("status") != "VERIFIED":
                    raise ProbeBlocked("original namespace did not retain its Btrfs socket view")

                original_result = _run_fixture_operation(
                    target, case_fd, context["python"], context["script"],
                    case["socket_name"] + ".moved", case["private_moved_name"],
                    require_writable_control=False)
                if original_result.get("status") not in ("COMPLETE", "UNSAFE_OBSERVED"):
                    raise ProbeBlocked("original namespace operation or identity check was incomplete")
                underlay_state = original_result.get("state_after")
                original_target_result = original_result.get("target_result")
                if not isinstance(original_target_result, dict):
                    raise ProbeBlocked("original namespace child result is missing")
                original_mounts = _verify_fixture_mount_ids(
                    case_fd, underlay_state, case["socket_name"],
                    case["socket_name"] + ".moved", case["private_moved_name"],
                    case["target_dir_identity"], case["original_mount_id"])
                original_endpoint = _verify_original_endpoint_for_state(
                    case_fd, target, underlay_state)
                if (original_mounts.get("status") != "VERIFIED"
                        or original_endpoint.get("status") != "VERIFIED"):
                    raise ProbeBlocked("original namespace fixture state did not verify")
                original_view = {
                    "operation": original_result,
                    "mounts": original_mounts,
                    "endpoint": original_endpoint,
                    "mount_view_before_operation": original_before,
                }
                record = {"name": case["name"], "profile": case["profile"],
                          "operation": case["operation"], "status": "BLOCKED",
                          "same_fixture": {
                              "directory_identity": case["target_dir_identity"],
                              "underlay_mount_id": case["original_mount_id"],
                              "underlay_socket_identity_before_bind": case["target_identity"],
                              "bind_source_identity": case["source_identity"],
                              "private_bind_mount_id": event["mount_id"],
                              "original_namespace_operation_started_while_bind_active": True,
                          },
                          "original_namespace_during_private_mount": original_view}
                output.append(record)
                if not _send_command(
                        proc, "parent_verified", underlay_state=underlay_state,
                        original_target_result=original_target_result):
                    raise ProbeBlocked("parent could not send verified fixture state to worker")

                done = read_event()
                if (not done or done.get("event") != "mount_case_done"
                        or done.get("name") != case["name"]
                        or done.get("cleanup") != "COMPLETE"):
                    raise ProbeBlocked("private namespace operation or unmount did not complete")
                if (not _fixture_states_match(done.get("underlay_expected", {}),
                                              underlay_state)
                        or not _fixture_states_match(done.get("underlay_after_unmount", {}),
                                                     underlay_state)
                        or done.get("restored_mounts", {}).get("status") != "VERIFIED"):
                    raise ProbeBlocked("worker did not restore the original fixture underlay")

                parent_after = _read_fixture_state(
                    case_fd, case["socket_name"], case["socket_name"] + ".moved",
                    case["private_moved_name"], case["target_dir_identity"])
                parent_mounts_after = _verify_fixture_mount_ids(
                    case_fd, parent_after, case["socket_name"],
                    case["socket_name"] + ".moved", case["private_moved_name"],
                    case["target_dir_identity"], case["original_mount_id"])
                parent_endpoint_after = _verify_original_endpoint_for_state(
                    case_fd, target, parent_after)
                if (not _fixture_states_match(parent_after, underlay_state)
                        or parent_mounts_after.get("status") != "VERIFIED"
                        or parent_endpoint_after.get("status") != "VERIFIED"):
                    raise ProbeBlocked("parent original namespace changed after private unmount")

                record["original_namespace_after_private_unmount"] = {
                    "state": parent_after, "mounts": parent_mounts_after,
                    "endpoint": parent_endpoint_after,
                }
                record["private_namespace_during_private_mount"] = {
                    "mount_id": done["mount_id"],
                    "source_identity": case["source_identity"],
                    "target_result": done["target_result"],
                    "root_client": done["root_client"],
                    "root_client_valid": done["root_client_valid"],
                    "status": done["status"],
                }
                statuses = (original_result.get("status"), done.get("status"))
                if "UNSAFE_OBSERVED" in statuses:
                    record["status"] = "UNSAFE_OBSERVED"
                    status = "UNSAFE_OBSERVED"
                elif all(value == "COMPLETE" for value in statuses):
                    record["status"] = "COMPLETE"
                else:
                    record["status"] = "PARTIAL"
                    if status == "COMPLETE":
                        status = "PARTIAL"
                target["mounted_result"] = record
                target["original_during_mount_result"] = original_result
                if not _send_command(proc, "case_checked"):
                    status = "BLOCKED"
                    record["status"] = "BLOCKED"
                    break
            except (OSError, ProbeBlocked) as exc:
                status = "BLOCKED"
                if output and output[-1].get("name") == case["name"]:
                    output[-1]["status"] = "BLOCKED"
                    output[-1]["reason"] = (
                        exc.reason if isinstance(exc, ProbeBlocked)
                        else f"original-view fixture operation failed ({exc.errno})")
                _send_command(proc, "abort")
                break
            finally:
                if case_fd is not None:
                    os.close(case_fd)

        final = read_event()
        if not final or final.get("event") != "final" or final.get("status") != "COMPLETE":
            status = "BLOCKED"
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            if proc.returncode != 0:
                status = "BLOCKED"
        except subprocess.TimeoutExpired:
            status = "BLOCKED"
    except (OSError, StopIteration, subprocess.TimeoutExpired):
        status = "BLOCKED"
    finally:
        if proc.poll() is None and proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        worker_reaped = _terminate_and_reap(proc, process_group=True)
        if not worker_reaped:
            status = "BLOCKED"
        if worker_reaped and proc.returncode == 0 and status != "BLOCKED":
            records = {item.get("name"): item for item in output}
            for case in config["cases"]:
                record = records.get(case["name"])
                expected_state = (record or {}).get(
                    "original_namespace_during_private_mount", {}).get(
                        "operation", {}).get("state_after")
                if expected_state is None:
                    status = "BLOCKED"
                    break
                case_fd = None
                try:
                    case_fd, info = _open_child_dir(
                        fixtures["base_fd"], case["target_dir"])
                    if not _same_object(info, case["target_dir_identity"],
                                        include_mode=True):
                        status = "BLOCKED"
                        break
                    final_state = _read_fixture_state(
                        case_fd, case["socket_name"], case["socket_name"] + ".moved",
                        case["private_moved_name"], case["target_dir_identity"])
                    mounts = _verify_fixture_mount_ids(
                        case_fd, final_state, case["socket_name"],
                        case["socket_name"] + ".moved", case["private_moved_name"],
                        case["target_dir_identity"], case["original_mount_id"])
                    target_fixture = next(
                        item for item in fixtures["mounted_cases"]
                        if item["name"] == case["name"])
                    endpoint = _verify_original_endpoint_for_state(
                        case_fd, target_fixture, final_state)
                    if (not _fixture_states_match(final_state, expected_state)
                            or mounts.get("status") != "VERIFIED"
                            or endpoint.get("status") != "VERIFIED"):
                        status = "BLOCKED"
                        break
                except (OSError, ProbeBlocked):
                    status = "BLOCKED"
                    break
                finally:
                    if case_fd is not None:
                        os.close(case_fd)
        for stream in (proc.stdin, proc.stdout):
            if stream:
                try:
                    stream.close()
                except OSError:
                    pass
    return output, status, worker_reaped


def _add_child_cleanup_identities(fixtures: dict, plain_results: list[dict]) -> None:
    by_name = {case["name"]: case for case in fixtures["plain_cases"]}
    for result in plain_results:
        case = by_name.get(result.get("name"))
        if not case:
            continue
        target_result = result.get("target_result", {})
        if target_result.get("rename_result") == "success":
            case["allowed_entries"][case["socket_name"] + ".moved"] = case["socket_identity"]
            case["allowed_entries"].pop(case["socket_name"], None)
        if target_result.get("operation_result") == "success" and result.get("operation") == "unlink":
            case["allowed_entries"].pop(case["socket_name"], None)
        replacement = target_result.get("replacement")
        if replacement:
            case["allowed_entries"][case["socket_name"]] = replacement


def _close_servers(ledger: dict) -> dict:
    items = []
    all_stopped = True
    for record in ledger.get("servers", []):
        server = record["server"]
        try:
            stopped = server.close()
            alive = server.thread.is_alive()
            record["thread_alive_after_close"] = alive
            record["close_status"] = "COMPLETE" if stopped and not alive else "BLOCKED"
        except BaseException as exc:
            stopped, alive = False, True
            record["thread_alive_after_close"] = alive
            record["close_status"] = "BLOCKED"
            record["close_error"] = _exception_record(exc)
        if record["close_status"] != "COMPLETE":
            all_stopped = False
        items.append({"path": record.get("path"),
                      "socket_identity": record.get("socket_identity"),
                      "started": record.get("started"),
                      "status": record.get("close_status"),
                      "thread_alive": record.get("thread_alive_after_close"),
                      "error": record.get("close_error")})
    return {"status": "COMPLETE" if all_stopped else "BLOCKED", "items": items}


def _run_lab(context: dict) -> dict:
    ledger = _new_setup_ledger()
    fixtures = None
    plain_results, mounted_results = [], []
    worker_status = "BLOCKED"
    worker_reaped = True
    worker_phase_attempted = False
    failure = None
    report = {"status": "BLOCKED", "reason": "lab did not complete"}
    try:
        run_root = _plan_lab_root(ledger, "run", context["run_fd"],
                                  str(RUN), "bind-sock-run-")
        run_name, run_fd, run_identity = _create_lab_root(
            context["run_fd"], run_root, ledger)
        base_root = _plan_lab_root(ledger, "btrfs", context["base_fd"],
                                   str(BASE), "bind-sock-btrfs-")
        base_name, base_fd, base_identity = _create_lab_root(
            context["base_fd"], base_root, ledger)
        fixtures = _setup_fixtures(context, run_name, run_fd, run_identity,
                                   base_name, base_fd, base_identity, ledger)
        for case in fixtures["mounted_cases"]:
            ledger["phase"] = f"baseline:{case['name']}"
            case_fd, case_dir_info = _open_child_dir(fixtures["base_fd"], case["name"])
            try:
                if not _same_object(case_dir_info, case["identity"], include_mode=True):
                    raise ProbeBlocked("fixture directory identity changed before root baseline")
                case["baseline"] = _root_client_at(
                    case_fd, case["socket_name"], ROOT_RESPONSE,
                    case["identity"], case["socket_identity"])
            finally:
                os.close(case_fd)
            case["source_baseline"] = _root_client_at(
                fixtures["run_fd"], case["source_name"], ROOT_RESPONSE,
                fixtures["run_identity"], case["source_identity"])
            if (not _root_client_valid(case["baseline"])
                    or not _root_client_valid(case["source_baseline"])):
                raise ProbeBlocked("root fixture service baseline failed before namespace mount phase")
        # The no-mount cases run only against self-created Btrfs fixture sockets.
        for case in fixtures["plain_cases"]:
            ledger["phase"] = f"original-operation:{case['name']}"
            plain_results.append(_original_operation(case, context["python"], context["script"], base_fd))
        _add_child_cleanup_identities(fixtures, plain_results)
        ledger["phase"] = "private-namespace-phase"
        worker_phase_attempted = True
        mounted_results, worker_status, worker_reaped = _run_mounted_phase(context, fixtures)
        positive_case = next((item for item in plain_results
                              if item.get("profile") == "0777" and item.get("operation") == "replace"), {})
        positive = "CONFIRMED" if _positive_control_confirmed(
            positive_case, positive_case.get("root_client", {})) else "NOT CONFIRMED"
        report = {"status": "BLOCKED", "platform": "Linux", "target_user": TARGET_USER,
                  "target_uid": TARGET_UID, "target_gid": TARGET_GID,
                  "target_groups": list(TARGET_GROUPS),
                  "namespace_parent": context["parent_namespace"],
                  "base_mount": context["base_mount"], "run_mount": context["run_mount"],
                  "experiment_roots": {"run": {"path": str(RUN / run_name), "identity": run_identity},
                                       "btrfs": {"path": str(BASE / base_name), "identity": base_identity}},
                  "original_namespace_cases": plain_results,
                  "private_namespace_cases": mounted_results,
                  "no_mount_0777_positive_control": positive,
                  "worker_status": worker_status,
                  "target_access_scope": {
                      "method": "inherited-directory-fd",
                      "proves": "UID/GID 1000/1001 with the recorded groups and capabilities can operate through the already-open fixture directory descriptor under the tested permissions",
                      "does_not_prove": "that the target user can reach the fixture or any production socket through ordinary pathname traversal",
                  },
                  "interpretation": "Independent fixture results do not establish production socket safety, ordinary path traversal, or gateway compatibility."}
    except BaseException as exc:
        if worker_phase_attempted:
            worker_reaped = False
        failure = {"stage": ledger.get("phase"), "error": _exception_record(exc),
                   "details": exc.details if isinstance(exc, ProbeBlocked) else {}}
        report = _status("BLOCKED", reason=failure["error"]["reason"],
                         failure_stage=failure["stage"], failure_details=failure["details"],
                         partial_original_namespace_cases=plain_results,
                         partial_private_namespace_cases=mounted_results,
                         worker_status=worker_status)
    finally:
        ledger["phase_before_cleanup"] = failure["stage"] if failure else ledger.get("phase")
        ledger["phase"] = "cleanup"
        try:
            server_shutdown = _close_servers(ledger)
        except BaseException as exc:
            server_shutdown = {"status": "BLOCKED", "reason": _exception_record(exc),
                               "items": []}
        try:
            cleanup = _cleanup_ledger(
                ledger, threads_quiescent=server_shutdown.get("status") == "COMPLETE",
                worker_reaped=worker_reaped)
        except BaseException as exc:
            cleanup = _mark_cleanup_blocked(
                ledger, f"cleanup raised {_exception_record(exc)}")
        close_failures = _close_ledger_descriptors(ledger)
        if close_failures:
            cleanup["status"] = "BLOCKED"
            cleanup["dirfd_close_failures"] = close_failures
            report["status"] = "BLOCKED"
        report["cleanup"] = cleanup
        report["server_threads"] = server_shutdown
        report["experiment_roots"] = {
            role: {"path": item.get("path"), "identity": item.get("identity"),
                   "initial_identity": item.get("initial_identity"),
                   "identity_binding": item.get("identity_binding"),
                   "identity_binding_failure": item.get("identity_binding_failure"),
                   "created": item.get("created"), "cleanup": item.get("cleanup")}
            for role, item in ledger["roots"].items()}
        report["setup_ledger"] = _ledger_snapshot(ledger)
        if failure:
            report["failure_stage"] = failure["stage"]
            report["failure_details"] = failure["details"]
            report["reason"] = failure["error"]["reason"]
        elif cleanup.get("status") != "COMPLETE" or worker_status == "BLOCKED":
            report["status"] = "BLOCKED"
            report.setdefault("failure_stage", "cleanup")
            report.setdefault("reason", cleanup.get("reason", "cleanup or worker shutdown was not verified"))
        else:
            report["status"] = _overall_status(
                plain_results, mounted_results,
                report.get("no_mount_0777_positive_control", "NOT CONFIRMED"),
                cleanup["status"], worker_status)
    return report


def run_probe() -> dict:
    try:
        context = _runtime_preflight()
    except (OSError, ProbeBlocked) as exc:
        return _status("BLOCKED", reason=exc.reason if isinstance(exc, ProbeBlocked)
                       else f"preflight failed ({exc.errno or exc.__class__.__name__})",
                       failure_stage="preflight",
                       experiment_roots={"run_parent": {"path": str(RUN), "identity": None},
                                         "btrfs_parent": {"path": str(BASE), "identity": None}},
                       cleanup={"status": "NOT RUN", "items": []})
    try:
        return _run_lab(context)
    finally:
        for key in ("base_fd", "vol_fd", "run_fd", "root_fd"):
            fd = context.get(key)
            if fd is not None:
                os.close(fd)


def main() -> int:
    if len(sys.argv) == 7 and sys.argv[1] == "--target-child":
        exec(compile(_child_code(), "<target-child>", "exec"), {"__name__": "__main__"})
        return 0
    if len(sys.argv) == 4 and sys.argv[1] == "--namespace-worker":
        try:
            config = json.loads(sys.argv[2])
            parent_ns = json.loads(sys.argv[3])
            return _worker_case(config, parent_ns)
        except (ValueError, TypeError, ProbeBlocked, OSError) as exc:
            _emit({"event": "final", "status": "BLOCKED",
                   "reason": exc.reason if isinstance(exc, ProbeBlocked)
                   else f"worker startup failed ({getattr(exc, 'errno', None) or exc.__class__.__name__})"})
            return 2
    result = run_probe()
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result.get("status") == "COMPLETE" else (2 if result.get("status") == "BLOCKED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
