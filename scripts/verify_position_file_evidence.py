#!/usr/bin/env python3
"""Read-only content reconciliation for an explicit read-only evidence view.

This local CLI is not an HTTP endpoint and never writes to the tree. It compares
position-level hashes with regular files below the supplied root. On Linux
x86_64 it requires an unprivileged process and one Btrfs read-only subvolume
snapshot (or a subtree directory inside one) for the complete tree. It uses
openat2 for every descendant lookup and refuses symlinks, subvolume/mount
crossings and paths outside the root. Unsupported or writable trees fail closed.

Two explicit evidence profiles exist (``--evidence-profile``):

``btrfs-ro-snapshot`` (default)
    Original contract: the root must be a Btrfs read-only subvolume or a subtree
    inside one, every observed inode must belong to the same subvolume identity,
    and the entry/generation bracket must stay stable. Read opens request
    ``O_NOATIME`` so the scanner's own reads cannot advance atime (which was
    observed to advance a Btrfs root generation and create a false bracket
    positive); an ``O_NOATIME`` refusal is fail-closed, never a silent fallback.
    A filesystem-level sync barrier (``syncfs`` or ``sync``) is taken before and
    after the scan and the generation is recorded around both barriers.
    Complete results also require the existing hard gate
    ``_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED`` (still ``False``) and the
    separate ``_POINT_IN_TIME_SCAN_VALIDATED`` gate (still ``False``).

``independent-copy``
    For an M4 plain copy or an M2 received tree. It does not pretend to have
    Btrfs read-only semantics; it still performs unprivileged-process, identity,
    O_NOATIME hash and sync-barrier checks, records that RO semantics are
    unavailable, and always reports ``complete: false`` (all positions
    ``unknown``) until a separately reviewed independent validation gate exists.

This candidate is not wired into the product and its hard gates stay ``False``;
it is capability work only, not acceptance evidence.
"""
from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import sys
from typing import Any


MAX_POSITIONS = 10_000
MAX_ENTRIES = 100_000
MAX_BYTES = 512 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_OPENAT2_SYSCALL_X86_64 = 437
_RESOLVE_NO_XDEV = 0x01
_RESOLVE_NO_MAGICLINKS = 0x02
_RESOLVE_NO_SYMLINKS = 0x04
_RESOLVE_BENEATH = 0x08
# No native Linux/Btrfs evidence currently proves that the GET_SUBVOL_INFO
# root generation always changes after a transient RO -> RW -> write -> RO.
# Keep complete-result reporting disabled until that property is validated and
# this source gate is deliberately revised in a separately reviewed change.
_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED = False
# Separate gates for the point-in-time capability paths implemented by this
# candidate. They stay False: this change adds code paths and synthetic tests,
# not the native evidence required by the redesign (G1-G3/G8). Flipping either
# gate requires a separate, independently reviewed code change.
_POINT_IN_TIME_SCAN_VALIDATED = False
_INDEPENDENT_COPY_VIEW_VALIDATED = False
_BTRFS_IOCTL_MAGIC = 0x94
_BTRFS_IOCTL_GET_SUBVOL_INFO_NR = 60
_BTRFS_IOCTL_GET_SUBVOL_INFO_SIZE = 504
# BTRFS_IOC_GET_SUBVOL_INFO returns root_item.flags, whose readonly bit is
# BTRFS_ROOT_SUBVOL_RDONLY (bit 0). Do not use BTRFS_SUBVOL_RDONLY (bit 1),
# which belongs to the GETFLAGS/SETFLAGS and create ioctl arguments.
_BTRFS_ROOT_SUBVOL_RDONLY = 1 << 0
_BTRFS_SUBVOL_TREEID_OFFSET = 0
_BTRFS_SUBVOL_GENERATION_OFFSET = 280
_BTRFS_SUBVOL_FLAGS_OFFSET = 288
_BTRFS_SUBVOL_UUID_OFFSET = 296
_BTRFS_SUBVOL_PARENT_UUID_OFFSET = 312
_BTRFS_IOC_GET_SUBVOL_INFO = (
    (2 << 30) | (_BTRFS_IOCTL_GET_SUBVOL_INFO_SIZE << 16)
    | (_BTRFS_IOCTL_MAGIC << 8) | _BTRFS_IOCTL_GET_SUBVOL_INFO_NR
)
# Btrfs UAPI: the root directory of a subvolume (and of a snapshot) always has
# inode objectid BTRFS_FIRST_FREE_OBJECTID (256). This is used only to label the
# supplied root as a subvolume root or a subtree inside one; it is not a
# security gate and does not change any traversal decision.
_BTRFS_FIRST_FREE_OBJECTID = 256

_EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT = "btrfs-ro-snapshot"
_EVIDENCE_PROFILE_INDEPENDENT_COPY = "independent-copy"
_EVIDENCE_PROFILES = (_EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT, _EVIDENCE_PROFILE_INDEPENDENT_COPY)

# O_NOATIME keeps the scanner's own reads from advancing atime. The native
# generation probe observed that a read can advance a Btrfs root generation
# (atime update) and therefore create a false bracket positive. It requires
# ownership of the inode or CAP_FOWNER; callers must not silently re-open
# without it, because that fallback reintroduces the false positive.
_O_NOATIME = getattr(os, "O_NOATIME", 0)

_UNKNOWN_REASON_BY_ERRNO = {
    errno.ENOTSUP: "platform_unsupported",
    errno.ESTALE: "view_mutated",
    errno.EROFS: "ro_semantics_unavailable",
    errno.E2BIG: "limits_exceeded",
    errno.EPERM: "permission_unverifiable",
    errno.EACCES: "permission_unverifiable",
    errno.ELOOP: "symlink_in_tree",
    errno.EINVAL: "invalid_tree_entry",
    errno.EXDEV: "cross_device",
}
_FALLBACK_UNKNOWN_REASON = "scan_failed"


class EvidenceInputError(ValueError):
    pass


class UnsafeTreeError(OSError):
    pass


class NoAtimeDeniedError(UnsafeTreeError):
    """O_NOATIME needs ownership or CAP_FOWNER; a refusal is fail-closed."""


class NoAtimeUnavailableError(UnsafeTreeError):
    """The platform does not expose O_NOATIME, so reads would be atime-unsafe."""


class SyncBarrierError(UnsafeTreeError):
    """The filesystem-level commit barrier failed; the bracket is unusable."""


class _OpenHow(ctypes.Structure):
    _fields_ = [("flags", ctypes.c_uint64), ("mode", ctypes.c_uint64), ("resolve", ctypes.c_uint64)]


def _openat2(dir_fd: int, path: str, flags: int) -> int:
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise UnsafeTreeError(errno.ENOTSUP, "unsupported platform")
    if not path or path.startswith("/") or "\x00" in path:
        raise UnsafeTreeError(errno.EINVAL, "invalid relative path")
    libc = ctypes.CDLL(None, use_errno=True)
    how = _OpenHow(
        flags | getattr(os, "O_CLOEXEC", 0),
        0,
        _RESOLVE_BENEATH | _RESOLVE_NO_SYMLINKS | _RESOLVE_NO_MAGICLINKS | _RESOLVE_NO_XDEV,
    )
    result = libc.syscall(
        ctypes.c_long(_OPENAT2_SYSCALL_X86_64), ctypes.c_int(dir_fd),
        ctypes.c_char_p(os.fsencode(path)), ctypes.byref(how), ctypes.sizeof(how),
    )
    if result < 0:
        error = ctypes.get_errno()
        raise UnsafeTreeError(error, "openat2 refused path")
    return int(result)


def _noatime_flag() -> int:
    if _O_NOATIME == 0:
        raise NoAtimeUnavailableError(errno.ENOTSUP, "O_NOATIME unavailable")
    return _O_NOATIME


def _directory_open_flags() -> int:
    # O_NOATIME is added by _openat2_noatime for every read/traversal open.
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _openat2_noatime(dir_fd: int, path: str, flags: int) -> int:
    """openat2 with O_NOATIME; a permission refusal is fail-closed.

    The kernel does not tell EPERM/EACCES caused by O_NOATIME apart from other
    permission refusals, so both are reported as ``noatime_permission_denied``
    and the whole scan becomes ``unknown`` instead of silently re-reading with
    atime updates enabled.
    """
    try:
        return _openat2(dir_fd, path, flags | _noatime_flag())
    except UnsafeTreeError as exc:
        if exc.errno in (errno.EPERM, errno.EACCES):
            raise NoAtimeDeniedError(errno.EPERM, "O_NOATIME refused by the kernel") from None
        raise


def _validate_positions(value: object) -> list[dict[str, Any]]:
    if type(value) is not dict or set(value) != {"positions"}:
        raise EvidenceInputError("invalid input")
    positions = value["positions"]
    if type(positions) is not list or not positions or len(positions) > MAX_POSITIONS:
        raise EvidenceInputError("invalid positions")
    output: list[dict[str, Any]] = []
    seen: set[int] = set()
    for row in positions:
        if type(row) is not dict or set(row) != {"position", "published_sha256", "published_size"}:
            raise EvidenceInputError("invalid position row")
        position, digest, size = row["position"], row["published_sha256"], row["published_size"]
        if (type(position) is not int or isinstance(position, bool) or not 1 <= position <= MAX_POSITIONS
                or position in seen or type(digest) is not str or _SHA256_RE.fullmatch(digest) is None
                or type(size) is not int or isinstance(size, bool) or not 0 < size <= MAX_BYTES):
            raise EvidenceInputError("invalid position evidence")
        seen.add(position)
        output.append({"position": position, "published_sha256": digest, "published_size": size})
    return sorted(output, key=lambda row: row["position"])


def _validate_evidence_profile(value: object) -> str:
    if type(value) is not str or value not in _EVIDENCE_PROFILES:
        raise EvidenceInputError("invalid evidence profile")
    return value


def _validate_max_entries(value: object) -> int:
    if type(value) is not int or isinstance(value, bool) or value < 1:
        raise EvidenceInputError("invalid entry limit")
    return value


def _classify_root_scope(root_stat: os.stat_result) -> str:
    """Label the root as a subvolume root or a subtree inside one (Btrfs UAPI)."""
    if root_stat.st_ino == _BTRFS_FIRST_FREE_OBJECTID:
        return "subvolume_root"
    return "subtree"


def _open_root(path: str) -> int:
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise UnsafeTreeError(errno.ENOTSUP, "unsupported platform")
    if not os.path.isabs(path):
        raise UnsafeTreeError(errno.EINVAL, "root must be absolute")
    if ".." in path.split(os.sep):
        raise UnsafeTreeError(errno.EINVAL, "root path traversal")
    absolute = os.path.normpath(path)
    parts = [item for item in absolute.split(os.sep) if item]
    if any(item in {".", ".."} for item in parts):
        raise UnsafeTreeError(errno.EINVAL, "root path traversal")
    fd = os.open(os.sep, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for index, item in enumerate(parts):
            final = index == len(parts) - 1
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
            if final:
                # Only the final directory is read (scandir), so only its atime
                # matters; intermediate traversal opens must not require
                # ownership that the scan root itself does not have.
                flags |= _noatime_flag()
            try:
                next_fd = os.open(item, flags, dir_fd=fd)
            except OSError as exc:
                if final and exc.errno in (errno.EPERM, errno.EACCES):
                    raise NoAtimeDeniedError(errno.EPERM, "O_NOATIME refused for root") from None
                raise
            os.close(fd)
            fd = next_fd
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise UnsafeTreeError(errno.ENOTDIR, "root is not directory")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _require_unprivileged_process() -> None:
    """Do not scan from a process that can remount or thaw the snapshot."""
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise UnsafeTreeError(errno.ENOTSUP, "unsupported platform")
    if os.geteuid() == 0:
        raise UnsafeTreeError(errno.EPERM, "privileged scanner process")
    try:
        with open("/proc/self/status", "r", encoding="ascii") as stream:
            status = stream.read()
        effective, permitted = _parse_process_capabilities(status)
    except (OSError, ValueError, EvidenceInputError):
        raise UnsafeTreeError(errno.EPERM, "process capabilities unavailable") from None
    if effective != 0 or permitted != 0:
        raise UnsafeTreeError(errno.EPERM, "scanner process has capabilities")


def _parse_process_capabilities(status: str) -> tuple[int, int]:
    values: dict[str, int] = {}
    for line in status.splitlines():
        name, separator, value = line.partition(":")
        if separator and name in {"CapEff", "CapPrm"}:
            try:
                values[name] = int(value.strip(), 16)
            except ValueError:
                raise EvidenceInputError("capability value invalid") from None
    if set(values) != {"CapEff", "CapPrm"}:
        raise EvidenceInputError("capability fields unavailable")
    return values["CapEff"], values["CapPrm"]


def _read_btrfs_subvol_info(fd: int) -> tuple[int, int, int, bytes, bytes]:
    """Read the containing subvolume identity and freeze flags for one inode."""
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise UnsafeTreeError(errno.ENOTSUP, "unsupported platform")
    try:
        import fcntl
        info = bytearray(_BTRFS_IOCTL_GET_SUBVOL_INFO_SIZE)
        fcntl.ioctl(fd, _BTRFS_IOC_GET_SUBVOL_INFO, info, True)
    except (ImportError, OSError):
        raise UnsafeTreeError(errno.ENOTSUP, "Btrfs subvolume state unavailable") from None

    def u64(offset: int) -> int:
        return int.from_bytes(info[offset:offset + 8], byteorder=sys.byteorder, signed=False)

    return (
        u64(_BTRFS_SUBVOL_TREEID_OFFSET),
        u64(_BTRFS_SUBVOL_GENERATION_OFFSET),
        u64(_BTRFS_SUBVOL_FLAGS_OFFSET),
        bytes(info[_BTRFS_SUBVOL_UUID_OFFSET:_BTRFS_SUBVOL_UUID_OFFSET + 16]),
        bytes(info[_BTRFS_SUBVOL_PARENT_UUID_OFFSET:_BTRFS_SUBVOL_PARENT_UUID_OFFSET + 16]),
    )


def _require_readonly_snapshot(fd: int) -> tuple[int, int, int, bytes, bytes]:
    """Require a real Btrfs snapshot with its subvolume read-only bit set.

    The ioctl reports the subvolume that contains the supplied inode, so the
    same check works for a subvolume root and for any subtree directory inside
    a read-only snapshot.
    """
    info = _read_btrfs_subvol_info(fd)
    treeid, _generation, flags, uuid, parent_uuid = info
    if treeid <= 0 or not any(uuid) or not any(parent_uuid) or flags & _BTRFS_ROOT_SUBVOL_RDONLY == 0:
        raise UnsafeTreeError(errno.EROFS, "not a read-only Btrfs snapshot")
    return info


def _require_same_snapshot(
    fd: int, expected: tuple[int, int, int, bytes, bytes],
) -> tuple[int, int, int, bytes, bytes]:
    current = _require_readonly_snapshot(fd)
    if current != expected:
        raise UnsafeTreeError(errno.ESTALE, "snapshot identity or generation changed")
    return current


def _require_same_view_identity(
    current: tuple[int, int, int, bytes, bytes],
    expected: tuple[int, int, int, bytes, bytes],
) -> tuple[int, int, int, bytes, bytes]:
    """Require the same subvolume identity, allowing the generation to advance.

    The pre-scan sync barrier commits writes that were already issued before
    the evidence window; a generation advance across that barrier is expected.
    Only the identity fields (tree id, flags, uuid, parent uuid) must match.
    """
    treeid, _generation, flags, uuid, parent_uuid = current
    expected_treeid, _expected_generation, expected_flags, expected_uuid, expected_parent_uuid = expected
    if (treeid != expected_treeid or flags != expected_flags
            or uuid != expected_uuid or parent_uuid != expected_parent_uuid):
        raise UnsafeTreeError(errno.ESTALE, "snapshot identity changed")
    return current


def _sync_filesystem(root_fd: int) -> str:
    """Commit the tree's filesystem state as a bracket barrier.

    Uses ``syncfs(2)`` scoped to the tree's filesystem; falls back to ``sync(2)``
    when the libc does not expose the wrapper. Both are callable by
    unprivileged processes. Returns the mechanism used for the record.
    """
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        raise UnsafeTreeError(errno.ENOTSUP, "unsupported platform")
    libc = ctypes.CDLL(None, use_errno=True)
    syncfs = getattr(libc, "syncfs", None)
    if syncfs is not None:
        result = syncfs(ctypes.c_int(root_fd))
        if result != 0:
            raise UnsafeTreeError(ctypes.get_errno() or errno.EIO, "syncfs failed")
        return "syncfs"
    libc.sync()
    return "sync"


def _barrier(evidence: dict[str, Any], root_fd: int, phase: str) -> None:
    try:
        mechanism = _sync_filesystem(root_fd)
    except OSError:
        evidence["sync_barrier"][phase] = "failed"
        raise SyncBarrierError(errno.EIO, "filesystem sync barrier failed") from None
    evidence["sync_barrier"]["mechanism"] = mechanism
    evidence["sync_barrier"][phase] = "done"


def _stat_unchanged(before: os.stat_result, after: os.stat_result) -> bool:
    return (before.st_dev, before.st_ino, before.st_mode, before.st_size,
            before.st_mtime_ns, before.st_ctime_ns) == (
            after.st_dev, after.st_ino, after.st_mode, after.st_size,
            after.st_mtime_ns, after.st_ctime_ns)


def _hash_candidate(
    root_fd: int,
    relative_path: str,
    expected_stat: os.stat_result,
    *,
    view_guard: Any = None,
    read_content: bool = True,
) -> str:
    fd = _openat2_noatime(root_fd, relative_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_dev != expected_stat.st_dev
                or before.st_ino != expected_stat.st_ino or before.st_size != expected_stat.st_size):
            raise UnsafeTreeError(errno.ESTALE, "file changed before open")
        if view_guard is not None:
            view_guard(fd)
        digest = hashlib.sha256() if read_content else None
        if digest is not None:
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        after = os.fstat(fd)
        if view_guard is not None:
            view_guard(fd)
        if not _stat_unchanged(before, after):
            raise UnsafeTreeError(errno.ESTALE, "file changed while reading")
        return digest.hexdigest() if digest is not None else ""
    finally:
        os.close(fd)


def _new_evidence(evidence_profile: str, max_entries: int) -> dict[str, Any]:
    independent_copy = evidence_profile == _EVIDENCE_PROFILE_INDEPENDENT_COPY
    return {
        "evidence_profile": evidence_profile,
        "read_mode": "o_noatime",
        "root_scope": "unknown",
        "sync_barrier": {
            "pre": "not_attempted",
            "post": "not_attempted",
            "mechanism": None,
            "generation_before_pre_sync": None,
            "generation_start": None,
            "generation_before_post_sync": None,
            "generation_end": None,
        },
        "limits": {
            "max_entries": max_entries,
            "entries_seen": 0,
            "limit_exceeded": False,
        },
        "checks": {
            "ro_semantics": "unavailable" if independent_copy else "unknown",
            "generation_bracket": "not_applicable" if independent_copy else "unknown",
            "scanner_unprivileged": "unknown",
            "identity": "unknown",
            "content_hash": "not_attempted",
        },
    }


def _legacy_unknown(position_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Original fail-closed report shape, kept for the default gate-blocked path."""
    return {"scope": "read_only_test_tree", "complete": False, "positions": position_rows}


def _evidence_report(
    position_rows: list[dict[str, Any]],
    evidence: dict[str, Any],
    *,
    complete: bool,
    unknown_reason: str | None,
) -> dict[str, Any]:
    return {
        "scope": "read_only_test_tree",
        "evidence_profile": evidence["evidence_profile"],
        "complete": complete,
        "unknown_reason": unknown_reason,
        "read_mode": evidence["read_mode"],
        "root_scope": evidence["root_scope"],
        "sync_barrier": evidence["sync_barrier"],
        "limits": evidence["limits"],
        "checks": evidence["checks"],
        "positions": position_rows,
    }


def _unknown_reason_for(exc: OSError) -> str:
    if isinstance(exc, NoAtimeDeniedError):
        return "noatime_permission_denied"
    if isinstance(exc, NoAtimeUnavailableError):
        return "noatime_unavailable"
    error = exc.errno if exc.errno is not None else 0
    return _UNKNOWN_REASON_BY_ERRNO.get(error, _FALLBACK_UNKNOWN_REASON)


def scan_tree(
    root: str,
    positions: list[dict[str, Any]],
    *,
    evidence_profile: str = _EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT,
    max_entries: int = MAX_ENTRIES,
) -> dict[str, Any]:
    """Return counts only; anything other than one stable view is unknown.

    ``max_entries`` overrides the entry budget (default ``MAX_ENTRIES``); the
    effective value and whether it was reached are always recorded, and hitting
    the budget stays ``unknown`` (never a pass).
    """
    raw_positions = positions if type(positions) is list else []

    def unknown_rows() -> list[dict[str, Any]]:
        # Never echo arbitrary input into the report: a malformed "position"
        # could otherwise smuggle a local path, URL, or other sensitive text.
        result = []
        for row in raw_positions:
            if type(row) is not dict:
                continue
            position = row.get("position")
            if type(position) is not int or isinstance(position, bool) or not 1 <= position <= MAX_POSITIONS:
                continue
            result.append({"position": position, "status": "unknown"})
        return result

    try:
        evidence_profile = _validate_evidence_profile(evidence_profile)
        max_entries = _validate_max_entries(max_entries)
        positions = _validate_positions({"positions": positions})
    except (EvidenceInputError, OSError):
        return _legacy_unknown(unknown_rows())

    def unknown_positions() -> list[dict[str, Any]]:
        return [{"position": row["position"], "status": "unknown"} for row in positions]

    if evidence_profile == _EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT:
        if not _BTRFS_GENERATION_RELOCK_GUARD_VALIDATED or not _POINT_IN_TIME_SCAN_VALIDATED:
            return _legacy_unknown(unknown_positions())

    evidence = _new_evidence(evidence_profile, max_entries)
    unknown = unknown_positions()

    def unknown_report(reason: str) -> dict[str, Any]:
        return _evidence_report(unknown, evidence, complete=False, unknown_reason=reason)

    try:
        _require_unprivileged_process()
    except OSError as exc:
        return unknown_report(_unknown_reason_for(exc))
    evidence["checks"]["scanner_unprivileged"] = "ok"

    root_fd = None
    try:
        root_fd = _open_root(root)
        root_stat = os.fstat(root_fd)
        baseline: tuple[int, int, int, bytes, bytes] | None = None
        initial: tuple[int, int, int, bytes, bytes] | None = None
        if evidence_profile == _EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT:
            initial = _require_readonly_snapshot(root_fd)
            evidence["checks"]["ro_semantics"] = "metadata_ro_bit_only"
            evidence["root_scope"] = _classify_root_scope(root_stat)
            evidence["sync_barrier"]["generation_before_pre_sync"] = initial[1]
        _barrier(evidence, root_fd, "pre")
        if initial is not None:
            baseline = _require_readonly_snapshot(root_fd)
            _require_same_view_identity(baseline, initial)
            evidence["sync_barrier"]["generation_start"] = baseline[1]
            view_guard: Any = lambda fd: _require_same_snapshot(fd, baseline)  # noqa: E731
        else:
            view_guard = None

        wanted_sizes = {row["published_size"] for row in positions}
        matched: dict[tuple[int, str], int] = {
            (row["published_size"], row["published_sha256"]): 0 for row in positions
        }
        entries_seen = 0

        def walk(directory_fd: int, relative: str = "") -> None:
            nonlocal entries_seen
            if view_guard is not None:
                view_guard(directory_fd)
            directory_before = os.fstat(directory_fd)
            with os.scandir(directory_fd) as iterator:
                for entry in iterator:
                    entries_seen += 1
                    evidence["limits"]["entries_seen"] = entries_seen
                    if entries_seen > max_entries:
                        evidence["limits"]["limit_exceeded"] = True
                        raise UnsafeTreeError(errno.E2BIG, "entry limit")
                    name = entry.name
                    info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise UnsafeTreeError(errno.ELOOP, "symlink in tree")
                    child_relative = name if not relative else relative + "/" + name
                    if stat.S_ISDIR(info.st_mode):
                        child_fd = _openat2_noatime(root_fd, child_relative, _directory_open_flags())
                        try:
                            opened = os.fstat(child_fd)
                            if (opened.st_dev != info.st_dev or opened.st_ino != info.st_ino
                                    or opened.st_dev != root_stat.st_dev):
                                raise UnsafeTreeError(errno.ESTALE, "directory changed or crossed device")
                            if view_guard is not None:
                                view_guard(child_fd)
                            walk(child_fd, child_relative)
                        finally:
                            os.close(child_fd)
                    elif stat.S_ISREG(info.st_mode):
                        read_content = info.st_size in wanted_sizes
                        if read_content:
                            evidence["checks"]["content_hash"] = "attempted"
                        digest = _hash_candidate(
                            root_fd, child_relative, info,
                            view_guard=view_guard,
                            read_content=read_content,
                        )
                        if read_content:
                            evidence["checks"]["content_hash"] = "performed"
                        key = (info.st_size, digest)
                        if read_content and key in matched:
                            matched[key] += 1
                    else:
                        raise UnsafeTreeError(errno.EINVAL, "unsupported inode in test tree")
            directory_after = os.fstat(directory_fd)
            if view_guard is not None:
                view_guard(directory_fd)
            if not _stat_unchanged(directory_before, directory_after):
                raise UnsafeTreeError(errno.ESTALE, "directory changed while scanning")

        walk(root_fd)

        if baseline is not None:
            before_post = _require_readonly_snapshot(root_fd)
            evidence["sync_barrier"]["generation_before_post_sync"] = before_post[1]
            _require_same_view_identity(before_post, baseline)
            if before_post != baseline:
                raise UnsafeTreeError(errno.ESTALE, "snapshot changed before post barrier")
        _barrier(evidence, root_fd, "post")
        if baseline is not None:
            final = _require_readonly_snapshot(root_fd)
            evidence["sync_barrier"]["generation_end"] = final[1]
            if final != baseline:
                raise UnsafeTreeError(errno.ESTALE, "snapshot changed after post barrier")
            evidence["checks"]["generation_bracket"] = "stable"
        evidence["checks"]["identity"] = "ok"
    except OSError as exc:
        if isinstance(exc, NoAtimeDeniedError):
            evidence["read_mode"] = "o_noatime_denied"
        elif isinstance(exc, NoAtimeUnavailableError):
            evidence["read_mode"] = "unavailable"
        if (evidence_profile == _EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT
                and exc.errno == errno.ESTALE):
            evidence["checks"]["generation_bracket"] = "changed"
        if isinstance(exc, SyncBarrierError):
            return unknown_report("sync_barrier_failed")
        return unknown_report(_unknown_reason_for(exc))
    finally:
        if root_fd is not None:
            os.close(root_fd)

    if evidence_profile == _EVIDENCE_PROFILE_INDEPENDENT_COPY:
        # RO semantics are unavailable by definition for M4 plain copies and M2
        # received trees, and this candidate contains no independent
        # verification implementation. Never fabricate a complete result.
        if _INDEPENDENT_COPY_VIEW_VALIDATED:
            return unknown_report("independent_validation_unimplemented")
        return unknown_report("ro_semantics_unavailable")

    results = []
    for row in positions:
        match_count = matched[(row["published_size"], row["published_sha256"])]
        if match_count == 0:
            results.append({"position": row["position"], "status": "no_match", "match_count": 0})
        elif match_count == 1:
            results.append({
                "position": row["position"], "status": "unique_match",
                "match_count": 1,
            })
        else:
            results.append({"position": row["position"], "status": "ambiguous", "match_count": match_count})
    return _evidence_report(results, evidence, complete=True, unknown_reason=None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="absolute authorized test-library root")
    parser.add_argument(
        "--evidence-profile", choices=_EVIDENCE_PROFILES,
        default=_EVIDENCE_PROFILE_BTRFS_RO_SNAPSHOT,
        help="btrfs-ro-snapshot (default; Btrfs RO snapshot/subtree) or "
             "independent-copy (M4 plain copy / M2 received tree; never complete)",
    )
    parser.add_argument(
        "--max-entries", type=int, default=MAX_ENTRIES,
        help=f"override the entry budget (default {MAX_ENTRIES}); hitting it is unknown",
    )
    args = parser.parse_args(argv)
    try:
        payload = json.load(sys.stdin)
        positions = _validate_positions(payload)
        _validate_max_entries(args.max_entries)
        report = scan_tree(
            args.root, positions,
            evidence_profile=args.evidence_profile,
            max_entries=args.max_entries,
        )
    except (EvidenceInputError, OSError, json.JSONDecodeError, UnicodeError):
        safe = {"scope": "read_only_test_tree", "complete": False, "positions": []}
        sys.stdout.write(json.dumps(safe, separators=(",", ":")) + "\n")
        return 2
    sys.stdout.write(json.dumps(report, ensure_ascii=False, separators=(",", ":")) + "\n")
    return 0 if report.get("complete") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
