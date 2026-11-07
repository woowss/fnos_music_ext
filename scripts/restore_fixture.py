#!/usr/bin/env python3
"""Fail-closed recovery-fixture candidate; archive decoding is not enabled.

This is not an installer or service rollback. Until archive decoding runs inside
verified hard resource isolation, the NAS entry point refuses to scan or restore
an archive. No archive ownership, permissions, links, or files are restored.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import tarfile
from dataclasses import dataclass
from typing import BinaryIO, Iterable


MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
MAX_MEMBERS = 5_000
MAX_TOTAL_BYTES = 128 * 1024 * 1024
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_PATH_BYTES = 4_096
MAX_DEPTH = 32
CHUNK_BYTES = 1024 * 1024
FIXTURE_PREFIX = "fnmusic-restore-"
SAFE_PARENT = "/run"
PROBE_UIDS = (1000, 65534)
RUN_SERVICE_RESERVE_BYTES = 1024 * 1024 * 1024
RUN_SERVICE_RESERVE_INODES = 256
SPACE_METADATA_BLOCKS_PER_PATH = 1
REGULAR_TYPES = {tarfile.REGTYPE, tarfile.AREGTYPE}
POSIX_ACL_XATTRS = {"system.posix_acl_access", "system.posix_acl_default"}


class FixtureError(Exception):
    """Expected failure carrying a non-sensitive fixed result code."""

    def __init__(self, code: str, *, details: dict[str, object] | None = None):
        super().__init__(code)
        self.code = code
        # Optional output is restricted to internally assembled, redacted
        # counters. Never place archive member names or raw exception text here.
        self.details = details or {}


@dataclass(frozen=True)
class Manifest:
    files: dict[str, tuple[int, str]]
    directories: tuple[str, ...]
    member_count: int
    link_count: int
    total_bytes: int
    setuid_entries: int
    setgid_entries: int


@dataclass(frozen=True)
class ProbeIdentity:
    uid: int
    username: str
    primary_gid: int
    groups: tuple[int, ...]


def _tmpfs_space_state(fd: int) -> tuple[int, int, int]:
    """Return available bytes, available inodes, and block size or fail closed."""
    try:
        info = os.fstatvfs(fd)
        block_size = int(info.f_frsize or info.f_bsize)
        available_blocks = int(info.f_bavail)
        available_inodes = int(info.f_favail)
        total_inodes = int(info.f_files)
    except (AttributeError, OSError, TypeError, ValueError):
        raise FixtureError("tmpfs_space_unavailable") from None
    if (block_size <= 0 or available_blocks < 0 or available_inodes < 0
            or total_inodes <= 0):
        raise FixtureError("tmpfs_space_unavailable")
    return available_blocks * block_size, available_inodes, block_size


def _rounded_blocks(size: int, block_size: int) -> int:
    if size < 0:
        raise FixtureError("tmpfs_space_unavailable")
    if size == 0:
        return 0
    return ((size + block_size - 1) // block_size) * block_size


def _check_tmpfs_capacity(fd: int, file_sizes: Iterable[int], *,
                          directory_count: int = 0, fixture_count: int = 0) -> None:
    """Keep file/inode budget and a fixed service reserve available on /run."""
    available_bytes, available_inodes, block_size = _tmpfs_space_state(fd)
    if (RUN_SERVICE_RESERVE_BYTES <= 0 or RUN_SERVICE_RESERVE_INODES <= 0
            or SPACE_METADATA_BLOCKS_PER_PATH <= 0):
        raise FixtureError("tmpfs_space_unavailable")
    try:
        sizes = tuple(int(size) for size in file_sizes)
    except (TypeError, ValueError):
        raise FixtureError("tmpfs_space_unavailable") from None
    if directory_count < 0 or fixture_count < 0:
        raise FixtureError("tmpfs_space_unavailable")
    metadata_nodes = len(sizes) + directory_count + fixture_count
    data_bytes = sum(_rounded_blocks(size, block_size) for size in sizes)
    metadata_bytes = metadata_nodes * block_size * SPACE_METADATA_BLOCKS_PER_PATH
    required_bytes = data_bytes + metadata_bytes + RUN_SERVICE_RESERVE_BYTES
    required_inodes = metadata_nodes + RUN_SERVICE_RESERVE_INODES
    if available_bytes < required_bytes:
        raise FixtureError("tmpfs_service_reserve_bytes")
    if available_inodes < required_inodes:
        raise FixtureError("tmpfs_service_reserve_inodes")


def _archive_directory_paths(manifest: Manifest) -> tuple[str, ...]:
    """Include explicit and implicit directories that extraction will create."""
    directories = set(manifest.directories)
    for path in manifest.files:
        parts = path.split("/")
        directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    return tuple(sorted(directories))


def _fixed_path(name: str, is_directory: bool) -> str:
    """Accept only canonical relative POSIX paths, without path normalization."""
    if not isinstance(name, str) or not name or name.startswith("/") or "\\" in name:
        raise FixtureError("unsafe_member_path")
    if is_directory and name.endswith("/"):
        name = name[:-1]
    if not name or len(name.encode("utf-8", "strict")) > MAX_PATH_BYTES:
        raise FixtureError("unsafe_member_path")
    parts = name.split("/")
    if len(parts) > MAX_DEPTH or any(part in ("", ".", "..") for part in parts):
        raise FixtureError("unsafe_member_path")
    if any(any(ord(ch) < 32 or ord(ch) == 127 for ch in part) for part in parts):
        raise FixtureError("unsafe_member_path")
    return "/".join(parts)


def _member_stream(tf: tarfile.TarFile, member: tarfile.TarInfo) -> BinaryIO:
    stream = tf.extractfile(member)
    if stream is None:
        raise FixtureError("archive_read_failed")
    return stream


def scan_archive_fd(fd: int) -> Manifest:
    """Refuse archive decoding until a hard resource-isolated scanner exists.

    ``tarfile`` parses PAX extended headers before returning a ``TarInfo`` to
    this caller, so checking ``pax_headers`` after ``tarfile.open`` is too late
    to bound allocations. The supported NAS entrypoint therefore rejects
    scanning before reading or decompressing any archive byte. ``fd`` is
    intentionally unused; no read wrapper can constrain allocations already
    made by a TAR decoder.
    """
    del fd
    raise FixtureError("archive_scan_isolation_unproven")


def _hash_fd(fd: int, *, max_bytes: int | None = None) -> str:
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    read_size = 0
    while True:
        request_size = CHUNK_BYTES
        if max_bytes is not None:
            if max_bytes < 0:
                raise FixtureError("archive_size_limit")
            request_size = min(request_size, max_bytes - read_size + 1)
        block = os.read(fd, request_size)
        if not block:
            break
        read_size += len(block)
        if max_bytes is not None and read_size > max_bytes:
            raise FixtureError("archive_size_limit")
        digest.update(block)
    os.lseek(fd, 0, os.SEEK_SET)
    return digest.hexdigest()


def _write_stream(source: BinaryIO, fd: int, expected_size: int,
                  *, before_block=None) -> tuple[int, str]:
    """Write one archive stream, retaining the input digest for comparison."""
    digest = hashlib.sha256()
    written = 0
    while True:
        block = source.read(CHUNK_BYTES)
        if not block:
            break
        written += len(block)
        if written > expected_size:
            raise FixtureError("member_size_mismatch")
        if before_block is not None:
            before_block(expected_size - (written - len(block)))
        digest.update(block)
        view = memoryview(block)
        while view:
            try:
                amount = os.write(fd, view)
            except OSError:
                raise FixtureError("fixture_write_failed") from None
            if amount <= 0:
                raise FixtureError("fixture_write_failed")
            view = view[amount:]
    return written, digest.hexdigest()


def _verify_file_at(parent_fd: int, name: str, written_info: os.stat_result,
                    expected: tuple[int, str]) -> None:
    """Reopen the published directory entry safely and hash bytes on disk."""
    try:
        verify_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                            dir_fd=parent_fd)
    except OSError:
        raise FixtureError("fixture_readback_failed") from None
    try:
        opened = os.fstat(verify_fd)
        identity_fields = ("st_dev", "st_ino", "st_uid", "st_gid")
        if (not stat.S_ISREG(opened.st_mode)
                or any(getattr(opened, field) != getattr(written_info, field)
                       for field in identity_fields)
                or opened.st_uid != 0 or stat.S_IMODE(opened.st_mode) != 0o600
                or opened.st_size != expected[0]):
            raise FixtureError("fixture_readback_mismatch")
        _reject_posix_acls(verify_fd, "fixture_readback_acl_unverified")
        digest = _hash_fd(verify_fd)
        after = os.fstat(verify_fd)
        stable_fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if (any(getattr(after, field) != getattr(opened, field) for field in stable_fields)
                or digest != expected[1]):
            raise FixtureError("fixture_readback_mismatch")
    finally:
        os.close(verify_fd)


def _secure_open_path(path: str, *, directory: bool) -> int:
    """Open an absolute path component-by-component, rejecting symlinks."""
    # File opening is disabled until archive scanning has a hard resource
    # boundary. In particular, O_RDONLY on a FIFO can block forever and opening
    # device nodes can have side effects before a later fstat rejects them.
    # Keep only the directory-opening use needed by the unreachable /run
    # destination checks; the public restore entrypoint refuses before calling
    # this helper for either kind of path.
    if not directory:
        raise FixtureError("archive_scan_isolation_unproven")
    if not path.startswith("/") or "\x00" in path:
        raise FixtureError("path_not_absolute")
    if path == "/":
        if directory:
            return os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        raise FixtureError("unsafe_path")
    components = path.split("/")[1:]
    if any(part in ("", ".", "..") for part in components):
        raise FixtureError("unsafe_path")

    current = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, component in enumerate(components):
            final = index == len(components) - 1
            flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
            if not final or directory:
                flags |= os.O_DIRECTORY
            elif hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            new_fd = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = new_fd
            result = os.fstat(current)
            if not final:
                if not stat.S_ISDIR(result.st_mode):
                    raise FixtureError("not_directory")
                if result.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                    raise FixtureError("writable_by_other")
                if result.st_uid != 0 and result.st_mode & stat.S_IWUSR:
                    raise FixtureError("writable_by_nonroot_owner")
        if directory and not stat.S_ISDIR(result.st_mode):
            raise FixtureError("not_directory")
        if not directory and not stat.S_ISREG(result.st_mode):
            raise FixtureError("not_regular_file")
        owned = os.geteuid() == result.st_uid == 0
        if not owned:
            raise FixtureError("not_root_owned")
        if result.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise FixtureError("writable_by_other")
        return current
    except Exception:
        os.close(current)
        raise


def _reject_posix_acls(fd: int, error_code: str) -> None:
    """Fail closed when ACL state is present or cannot be inspected."""
    try:
        names = os.listxattr(fd)
    except (AttributeError, NotImplementedError, OSError, TypeError):
        raise FixtureError(error_code) from None
    if POSIX_ACL_XATTRS.intersection(names):
        raise FixtureError(error_code)


def _decode_mountinfo_path(value: str) -> str:
    """Decode the three-digit octal escapes used by proc mountinfo."""
    return re.sub(r"\\([0-7]{3})", lambda match: chr(int(match.group(1), 8)), value)


def _parse_mountinfo_line(line: str) -> dict[str, object] | None:
    """Parse one Linux mountinfo row, returning only fields needed here."""
    try:
        before, after = line.rstrip("\n").split(" - ", 1)
        left = before.split()
        right = after.split()
        if len(left) < 6 or len(right) < 3:
            return None
        return {
            "mount_id": int(left[0]),
            "root": _decode_mountinfo_path(left[3]),
            "mountpoint": _decode_mountinfo_path(left[4]),
            "mount_options": set(left[5].split(",")),
            "filesystem": right[0],
            "super_options": set(right[2].split(",")),
        }
    except (ValueError, IndexError):
        return None


def _mount_id_for_fd(fd: int) -> int:
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as stream:
            matches = [line.split(":", 1)[1].strip() for line in stream
                       if line.startswith("mnt_id:")]
        if len(matches) != 1:
            raise ValueError
        return int(matches[0])
    except (OSError, UnicodeError, ValueError):
        raise FixtureError("mount_identity_unavailable") from None


def _mountinfo_for_fd(fd: int) -> dict[str, object]:
    mount_id = _mount_id_for_fd(fd)
    try:
        with open("/proc/self/mountinfo", encoding="ascii") as stream:
            matches = [parsed for line in stream
                       if (parsed := _parse_mountinfo_line(line)) is not None
                       and parsed["mount_id"] == mount_id]
        if len(matches) != 1:
            raise ValueError
        return matches[0]
    except (OSError, UnicodeError, ValueError):
        raise FixtureError("mount_identity_unavailable") from None


def _verify_run_mount(mount: dict[str, object]) -> None:
    """Require /run itself to be a writable, private tmpfs mount."""
    if (mount.get("mountpoint") != SAFE_PARENT or mount.get("root") != "/"
            or mount.get("filesystem") != "tmpfs"):
        raise FixtureError("parent_not_private_tmpfs")
    options = set(mount.get("mount_options", set()))
    if not {"rw", "nosuid", "nodev"}.issubset(options):
        raise FixtureError("parent_mount_options_unsafe")


def _verify_destination_parent(parent_path: str) -> int:
    """Open and prove the exact /run tmpfs destination before creating data."""
    if parent_path != SAFE_PARENT:
        raise FixtureError("destination_must_be_run")
    root_fd = -1
    parent_fd = -1
    try:
        root_fd = _secure_open_path("/", directory=True)
        root_info = os.fstat(root_fd)
        if root_info.st_uid != 0 or stat.S_IMODE(root_info.st_mode) != 0o755:
            raise FixtureError("root_directory_unsafe")
        _reject_posix_acls(root_fd, "root_acl_unverified")
        parent_fd = _secure_open_path(SAFE_PARENT, directory=True)
        parent_info = os.fstat(parent_fd)
        if (parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) != 0o755
                or parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise FixtureError("parent_not_private")
        _reject_posix_acls(parent_fd, "parent_acl_unverified")
        _verify_run_mount(_mountinfo_for_fd(parent_fd))
        return parent_fd
    except FixtureError:
        if parent_fd >= 0:
            os.close(parent_fd)
        raise
    except OSError:
        if parent_fd >= 0:
            os.close(parent_fd)
        raise FixtureError("destination_check_failed") from None
    finally:
        if root_fd >= 0:
            os.close(root_fd)


def _resolve_probe_identity(uid: int) -> ProbeIdentity:
    """Resolve a UID and complete NSS group list, rejecting unstable account data."""
    try:
        import pwd

        account = pwd.getpwuid(uid)
        if account.pw_uid != uid or not account.pw_name or account.pw_gid < 0:
            raise ValueError
        named_account = pwd.getpwnam(account.pw_name)
        current_account = pwd.getpwuid(uid)
        if ((named_account.pw_uid, named_account.pw_name, named_account.pw_gid)
                != (account.pw_uid, account.pw_name, account.pw_gid)
                or (current_account.pw_uid, current_account.pw_name, current_account.pw_gid)
                != (account.pw_uid, account.pw_name, account.pw_gid)):
            raise ValueError
        group_lookup = getattr(os, "getgrouplist")
        raw_groups = tuple(group_lookup(account.pw_name, account.pw_gid))
        if not raw_groups or any(not isinstance(group, int) or group < 0 for group in raw_groups):
            raise ValueError
        groups = tuple(sorted(set(raw_groups)))
        if account.pw_gid not in groups:
            raise ValueError
        return ProbeIdentity(uid, account.pw_name, account.pw_gid, groups)
    except FixtureError:
        raise
    except (AttributeError, ImportError, KeyError, OSError, TypeError, ValueError):
        raise FixtureError("probe_account_unavailable") from None


def _probe_status_matches(identity: ProbeIdentity) -> bool:
    """Require /proc to report the exact dropped IDs/groups and no capabilities."""
    try:
        with open("/proc/self/status", encoding="ascii") as stream:
            fields: dict[str, tuple[int, ...]] = {}
            for line in stream:
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                if key in {"Uid", "Gid", "Groups"}:
                    fields[key] = tuple(int(part) for part in value.split())
                elif key in {"CapEff", "CapPrm", "CapInh", "CapAmb"}:
                    fields[key] = (int(value.strip(), 16),)
                elif key == "NoNewPrivs":
                    fields[key] = (int(value.strip()),)
        return (
            fields.get("Uid") == (identity.uid,) * 4
            and fields.get("Gid") == (identity.primary_gid,) * 4
            and fields.get("Groups") == identity.groups
            and all(fields.get(key) == (0,) for key in ("CapEff", "CapPrm", "CapInh", "CapAmb"))
            and fields.get("NoNewPrivs") == (1,)
        )
    except (OSError, UnicodeError, ValueError):
        return False


def _expected_eacces(operation) -> bool:
    try:
        operation()
    except OSError as exc:
        return exc.errno == errno.EACCES
    return False


def _probe_child(parent_fd: int, fixture_fd: int, fixture_name: str,
                 marker_name: str, parent_probe_name: str, identity: ProbeIdentity,
                 status_fd: int, inherited_fds: tuple[int, ...]) -> None:
    """Drop privilege then try to cross/write/delete the disposable fixture."""
    try:
        for fd in inherited_fds:
            if fd not in (parent_fd, fixture_fd, status_fd):
                try:
                    os.close(fd)
                except OSError:
                    pass

        libc = ctypes.CDLL(None, use_errno=True)
        # PR_SET_NO_NEW_PRIVS keeps a later exec from regaining privilege.
        if libc.prctl(ctypes.c_int(38), ctypes.c_ulong(1), 0, 0, 0) != 0:
            raise OSError
        os.setgroups(list(identity.groups))
        os.setgid(identity.primary_gid)
        os.setuid(identity.uid)
        if (os.getresuid() != (identity.uid, identity.uid, identity.uid)
                or os.getresgid() != (identity.primary_gid,) * 3
                or tuple(sorted(os.getgroups())) != identity.groups
                or not _probe_status_matches(identity)):
            raise OSError

        def create_in_fixture():
            fd = os.open(".probe-create", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=fixture_fd)
            os.close(fd)
            try:
                os.unlink(".probe-create", dir_fd=fixture_fd)
            except OSError:
                pass

        def create_in_parent():
            os.mkdir(parent_probe_name, 0o700, dir_fd=parent_fd)
            try:
                os.rmdir(parent_probe_name, dir_fd=parent_fd)
            except OSError:
                pass

        tests = (
            lambda: os.open(fixture_name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=parent_fd),
            create_in_fixture,
            lambda: os.open(marker_name, os.O_WRONLY | os.O_TRUNC, dir_fd=fixture_fd),
            lambda: os.unlink(marker_name, dir_fd=fixture_fd),
            create_in_parent,
        )
        if not all(_expected_eacces(operation) for operation in tests):
            raise OSError
        os.write(status_fd, b"PASS")
        os._exit(0)
    except BaseException:
        try:
            os.write(status_fd, b"FAIL")
        except OSError:
            pass
        os._exit(1)


def _probe_identity(parent_fd: int, fixture_fd: int, fixture_name: str,
                    identity: ProbeIdentity) -> None:
    marker_name = ".permission-probe-" + secrets.token_hex(8)
    parent_probe_name = FIXTURE_PREFIX + "probe-" + secrets.token_hex(8)
    marker_fd = -1
    marker_info = None
    probe_succeeded = False
    try:
        marker_fd = os.open(marker_name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                            0o600, dir_fd=fixture_fd)
        os.write(marker_fd, b"probe\n")
        os.fsync(marker_fd)
        marker_info = os.fstat(marker_fd)
        os.close(marker_fd)
        marker_fd = -1

        status_read, status_write = os.pipe()
        try:
            inherited_fds = tuple(int(name) for name in os.listdir("/proc/self/fd")
                                  if name.isdecimal()) + (status_read, status_write)
            pid = os.fork()
            if pid == 0:
                os.close(status_read)
                _probe_child(parent_fd, fixture_fd, fixture_name, marker_name,
                             parent_probe_name, identity, status_write, inherited_fds)
            os.close(status_write)
            status_write = -1
            result = os.read(status_read, 8)
            _, wait_status = os.waitpid(pid, 0)
            if result != b"PASS" or not os.WIFEXITED(wait_status) or os.WEXITSTATUS(wait_status) != 0:
                raise FixtureError("permission_probe_failed")
            probe_succeeded = True
        finally:
            os.close(status_read)
            if status_write >= 0:
                os.close(status_write)
    except FixtureError:
        raise
    except (AttributeError, OSError, TypeError, ValueError):
        raise FixtureError("permission_probe_unavailable") from None
    finally:
        if marker_fd >= 0:
            os.close(marker_fd)
        # If the probe fails, preserve the partial fixture for inspection
        # rather than deleting an entry after uncertain access.
        if probe_succeeded and marker_info is not None:
            try:
                current = os.stat(marker_name, dir_fd=fixture_fd, follow_symlinks=False)
                if (current.st_dev == marker_info.st_dev and current.st_ino == marker_info.st_ino
                        and current.st_uid == 0 and current.st_gid == marker_info.st_gid
                        and stat.S_IMODE(current.st_mode) == 0o600):
                    os.unlink(marker_name, dir_fd=fixture_fd)
                else:
                    raise FixtureError("permission_probe_marker_changed")
            except OSError:
                raise FixtureError("permission_probe_cleanup_failed") from None


def _verify_low_privilege_boundary(parent_fd: int, fixture_fd: int,
                                   fixture_name: str) -> tuple[ProbeIdentity, ...]:
    if sys.platform != "linux" or not hasattr(os, "fork") or os.geteuid() != 0:
        raise FixtureError("permission_probe_unavailable")
    identities = []
    for uid in PROBE_UIDS:
        identity = _resolve_probe_identity(uid)
        _probe_identity(parent_fd, fixture_fd, fixture_name, identity)
        if _resolve_probe_identity(uid) != identity:
            raise FixtureError("probe_account_changed")
        identities.append(identity)
    return tuple(identities)


def _verify_archive_identity(fd: int, initial: os.stat_result, expected_sha256: str) -> None:
    current = os.fstat(fd)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(current, field) != getattr(initial, field) for field in fields):
        raise FixtureError("archive_changed")
    if _hash_fd(fd, max_bytes=MAX_ARCHIVE_BYTES) != expected_sha256:
        raise FixtureError("archive_digest_mismatch")


def _open_or_create_dir(parent_fd: int, name: str) -> int:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=parent_fd)
    try:
        if created:
            os.fchmod(fd, 0o700)
        info = os.fstat(fd)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or stat.S_IMODE(info.st_mode) != 0o700):
            raise FixtureError("fixture_directory_unsafe")
        _reject_posix_acls(fd, "fixture_directory_acl_unverified")
        return fd
    except Exception:
        os.close(fd)
        raise


def _create_directories(root_fd: int, paths: tuple[str, ...]) -> None:
    for path in sorted(paths, key=lambda item: (item.count("/"), item)):
        current, name = _parent_fd(root_fd, path)
        try:
            child = _open_or_create_dir(current, name)
            os.close(child)
            os.fsync(current)
        finally:
            os.close(current)


def _parent_fd(root_fd: int, path: str) -> tuple[int, str]:
    parts = path.split("/")
    current = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            next_fd = _open_or_create_dir(current, component)
            os.close(current)
            current = next_fd
        return current, parts[-1]
    except Exception:
        os.close(current)
        raise


def _extract_file(archive: tarfile.TarFile, member: tarfile.TarInfo,
                  root_fd: int, expected: tuple[int, str], capacity_fd: int,
                  future_file_sizes: tuple[int, ...]) -> None:
    parent_fd, name = _parent_fd(root_fd, _fixed_path(member.name, False))
    out_fd = -1
    try:
        _check_tmpfs_capacity(capacity_fd, (expected[0], *future_file_sizes))
        out_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent_fd)
        os.fchmod(out_fd, 0o600)
        info = os.fstat(out_fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600:
            raise FixtureError("fixture_file_unsafe")
        _reject_posix_acls(out_fd, "fixture_file_acl_unverified")

        def recheck_remaining_space(current_remaining: int) -> None:
            _check_tmpfs_capacity(capacity_fd, (current_remaining, *future_file_sizes))

        with _member_stream(archive, member) as source:
            written, input_digest = _write_stream(source, out_fd, expected[0],
                                                  before_block=recheck_remaining_space)
        if written != expected[0] or input_digest != expected[1]:
            raise FixtureError("extracted_digest_mismatch")
        os.fsync(out_fd)
        written_info = os.fstat(out_fd)
        if written_info.st_size != expected[0]:
            raise FixtureError("extracted_digest_mismatch")
        os.close(out_fd)
        out_fd = -1
        _verify_file_at(parent_fd, name, written_info, expected)
        os.fsync(parent_fd)
    finally:
        if out_fd >= 0:
            os.close(out_fd)
        os.close(parent_fd)


def restore_fixture(archive_path: str, parent_path: str, expected_sha256: str) -> dict[str, object]:
    """Refuse before touching any caller-supplied path or archive descriptor."""
    # ``tarfile`` can allocate PAX metadata before returning control to a
    # caller. Until a separately resource-isolated scanner is reviewed, do not
    # open/stat/read/hash either the archive or destination and do not inspect
    # caller-provided values that could make the entrypoint appear partially
    # operational. The fixed block is platform-independent.
    del archive_path, parent_path, expected_sha256
    raise FixtureError("archive_scan_isolation_unproven", details={
        "archive_scan": "not_run",
        "fixture_created": False,
    })


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--parent", required=True,
                        help="must be the existing root-owned /run tmpfs mount")
    parser.add_argument("--sha256", required=True, help="expected archive SHA-256")
    args = parser.parse_args()
    try:
        result = restore_fixture(args.archive, args.parent, args.sha256)
    except FixtureError as exc:
        payload = {"status": "BLOCKED", "reason": exc.code}
        payload.update(exc.details)
        print(json.dumps(payload, sort_keys=True))
        return 1
    except OSError:
        print(json.dumps({"status": "BLOCKED", "reason": "io_error"}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
