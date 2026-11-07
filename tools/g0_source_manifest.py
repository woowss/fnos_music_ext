#!/usr/bin/env python3
"""Emit a bounded, read-only manifest of an explicitly selected install tree.

This utility hashes only the fixed source-file allowlist below. It does not
inspect configuration, logs, credentials, processes, sockets, the effective
systemd unit, or music files. Its output is source evidence only and always
fails closed for G0 and production readiness.
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import sys
from typing import Final


MAX_FILE_BYTES: Final = 8 * 1024 * 1024
MAX_OUTPUT_BYTES: Final = 32 * 1024
MAX_ROOT_CHARS: Final = 2048
READ_CHUNK_BYTES: Final = 64 * 1024

SCOPE: Final = "installed_repo_source_only"
_VERSION_RE: Final = re.compile(rb"([0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,4})(?:\r?\n)?\Z")
_FILES: Final = (
    ("VERSION", "version"),
    ("proxy/app.py", "source"),
    ("proxy/webui_gateway.py", "source"),
    ("proxy/takeover.py", "source"),
    ("proxy/gateway_identity.py", "source"),
    ("proxy/gateway_observation.py", "source"),
    ("webui-service/app.py", "source"),
    ("fnmusic-ext.service", "unit_template"),
)


class _ManifestError(Exception):
    """An internal fixed-code error; exception details are never serialized."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _metadata(info: os.stat_result) -> dict[str, int | str]:
    return {
        "size": int(info.st_size),
        "uid": int(info.st_uid),
        "gid": int(info.st_gid),
        "mode": format(stat.S_IMODE(info.st_mode), "04o"),
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
    }


def _file_signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
        info.st_mtime_ns,
        info.st_ctime_ns,
        info.st_nlink,
    )


def _directory_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_uid,
        info.st_gid,
        stat.S_IMODE(info.st_mode),
    )


def _require_linux_capabilities() -> None:
    if sys.platform != "linux":
        raise _ManifestError("linux_required")
    if any(not hasattr(os, name) for name in (
        "O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC", "O_NONBLOCK", "O_NOCTTY",
    )):
        raise _ManifestError("required_open_flags_unavailable")
    if not all((
        os.open in os.supports_dir_fd,
        os.stat in os.supports_dir_fd,
        os.stat in os.supports_follow_symlinks,
        callable(getattr(os, "fstat", None)),
        callable(getattr(os, "read", None)),
        callable(getattr(os, "close", None)),
    )):
        raise _ManifestError("required_dirfd_primitives_unavailable")


def _root_components(value: object) -> list[str]:
    if type(value) is not str or not value or len(value) > MAX_ROOT_CHARS:
        raise _ManifestError("invalid_root")
    if not value.startswith("/") or "\x00" in value:
        raise _ManifestError("invalid_root")
    if value == "/":
        return []
    pieces = value[1:].split("/")
    if any(not piece or piece in {".", ".."} for piece in pieces):
        raise _ManifestError("invalid_root")
    return pieces


class _Root:
    """An explicitly selected absolute directory pinned through no-follow fds."""

    def __init__(self, path: str, components: list[str]):
        self.path = path
        self.fds: list[int] = []
        self.chain: list[tuple[int, str, int, tuple[int, ...]]] = []
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        try:
            current = os.open("/", flags)
            self.fds.append(current)
            for component in components:
                child = os.open(component, flags, dir_fd=current)
                self.fds.append(child)
                opened = os.fstat(child)
                entry = os.stat(component, dir_fd=current, follow_symlinks=False)
                if not stat.S_ISDIR(opened.st_mode) or _directory_identity(opened) != _directory_identity(entry):
                    raise _ManifestError("unsafe_root")
                self.chain.append((current, component, child, _directory_identity(opened)))
                current = child
        except _ManifestError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            if isinstance(exc, FileNotFoundError):
                raise _ManifestError("root_missing") from None
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise _ManifestError("unsafe_root") from None
            raise _ManifestError("root_unavailable") from None

    @property
    def fd(self) -> int:
        return self.fds[-1]

    def verify(self) -> bool:
        try:
            for parent, name, child, expected in self.chain:
                opened = os.fstat(child)
                entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISDIR(entry.st_mode):
                    return False
                if _directory_identity(opened) != expected or _directory_identity(entry) != expected:
                    return False
            return True
        except OSError:
            return False

    def close(self) -> None:
        while self.fds:
            fd = self.fds.pop()
            try:
                os.close(fd)
            except OSError:
                pass


def _base_manifest(root: str | None) -> dict[str, object]:
    return {
        "schema_version": 1,
        "scope": SCOPE,
        "repository_root": root,
        "g0_pass": False,
        "production_ready": False,
        "effective_unit_verified": False,
        "entries": [],
    }


def _encode(report: dict[str, object]) -> str:
    encoded = json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) + 1 > MAX_OUTPUT_BYTES:
        fallback = _base_manifest(None)
        fallback["error"] = "output_limit_exceeded"
        return json.dumps(fallback, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return encoded


def _error_entry(path: str, role: str, status: str, *, exists: bool | None = None) -> dict[str, object]:
    return {"path": path, "role": role, "exists": exists, "status": status, "stable": False}


def _entry_dirs(root: _Root, directories: list[str]) -> tuple[list[int], list[tuple[int, str, int, tuple[int, ...]]]]:
    fds = [os.dup(root.fd)]
    chain: list[tuple[int, str, int, tuple[int, ...]]] = []
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        for component in directories:
            parent = fds[-1]
            child = os.open(component, flags, dir_fd=parent)
            fds.append(child)
            opened = os.fstat(child)
            entry = os.stat(component, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or _directory_identity(opened) != _directory_identity(entry):
                raise _ManifestError("unsafe_path")
            chain.append((parent, component, child, _directory_identity(opened)))
        return fds, chain
    except _ManifestError:
        for fd in reversed(fds):
            os.close(fd)
        raise
    except OSError as exc:
        for fd in reversed(fds):
            os.close(fd)
        if isinstance(exc, FileNotFoundError):
            raise _ManifestError("missing") from None
        if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
            raise _ManifestError("unsafe_path") from None
        raise _ManifestError("read_error") from None


def _verify_dirs(root: _Root, chain: list[tuple[int, str, int, tuple[int, ...]]]) -> bool:
    if not root.verify():
        return False
    try:
        for parent, name, child, expected in chain:
            opened = os.fstat(child)
            entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode):
                return False
            if _directory_identity(opened) != expected or _directory_identity(entry) != expected:
                return False
        return True
    except OSError:
        return False


def _capture_file(root: _Root, path: str, role: str) -> dict[str, object]:
    pieces = path.split("/")
    parent_fds: list[int] = []
    directory_chain: list[tuple[int, str, int, tuple[int, ...]]] = []
    fd = -1
    try:
        parent_fds, directory_chain = _entry_dirs(root, pieces[:-1])
        parent = parent_fds[-1]
        name = pieces[-1]
        try:
            before_entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return _error_entry(path, role, "missing", exists=False)
        except OSError:
            return _error_entry(path, role, "read_error", exists=None)

        exists = True
        if not stat.S_ISREG(before_entry.st_mode):
            item = _error_entry(path, role, "non_regular", exists=exists)
            item.update(_metadata(before_entry))
            return item

        open_flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY
        try:
            fd = os.open(name, open_flags, dir_fd=parent)
        except FileNotFoundError:
            return _error_entry(path, role, "unstable", exists=True)
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                return _error_entry(path, role, "unstable", exists=True)
            return _error_entry(path, role, "read_error", exists=True)

        before_fd = os.fstat(fd)
        if not stat.S_ISREG(before_fd.st_mode) or _file_signature(before_fd) != _file_signature(before_entry):
            item = _error_entry(path, role, "unstable", exists=True)
            item.update(_metadata(before_fd))
            return item

        if before_fd.st_size < 0 or before_fd.st_size > MAX_FILE_BYTES:
            stable = _verify_dirs(root, directory_chain)
            try:
                after_fd = os.fstat(fd)
                after_entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
                stable = stable and _file_signature(before_fd) == _file_signature(after_fd) == _file_signature(after_entry)
            except OSError:
                stable = False
            item = _error_entry(path, role, "too_large" if stable else "unstable", exists=True)
            item.update(_metadata(before_fd))
            item["stable"] = stable
            return item

        digest = hashlib.sha256()
        collected = 0
        content = bytearray() if role == "version" else None
        while True:
            chunk = os.read(fd, min(READ_CHUNK_BYTES, MAX_FILE_BYTES + 1 - collected))
            if not chunk:
                break
            collected += len(chunk)
            if collected > MAX_FILE_BYTES:
                return _error_entry(path, role, "unstable", exists=True)
            digest.update(chunk)
            if content is not None:
                content.extend(chunk)

        try:
            after_fd = os.fstat(fd)
            after_entry = os.stat(name, dir_fd=parent, follow_symlinks=False)
            stable = (
                _verify_dirs(root, directory_chain)
                and _file_signature(before_fd) == _file_signature(after_fd) == _file_signature(after_entry)
                and collected == before_fd.st_size
            )
        except OSError:
            stable = False

        if not stable:
            item = _error_entry(path, role, "unstable", exists=True)
            item.update(_metadata(before_fd))
            return item

        item: dict[str, object] = {
            "path": path,
            "role": role,
            "exists": True,
            "status": "hashed",
            "stable": True,
            **_metadata(before_fd),
            "sha256": digest.hexdigest(),
        }
        if role == "version":
            match = _VERSION_RE.fullmatch(bytes(content or b""))
            if match is None:
                item["status"] = "invalid_version"
            else:
                item["version"] = match.group(1).decode("ascii")
        return item
    except _ManifestError as exc:
        return _error_entry(path, role, exc.code, exists=False if exc.code == "missing" else None)
    except OSError:
        return _error_entry(path, role, "read_error", exists=None)
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        for parent_fd in reversed(parent_fds):
            try:
                os.close(parent_fd)
            except OSError:
                pass


def collect_manifest(repository_root: object) -> dict[str, object]:
    """Read the fixed source allowlist beneath one absolute Linux directory."""
    root_path = repository_root if type(repository_root) is str else None
    report = _base_manifest(root_path)
    try:
        _require_linux_capabilities()
        components = _root_components(repository_root)
        root = _Root(repository_root, components)
    except _ManifestError as exc:
        report["repository_root"] = root_path if root_path and len(root_path) <= MAX_ROOT_CHARS else None
        report["error"] = exc.code
        return report

    try:
        report["entries"] = [_capture_file(root, path, role) for path, role in _FILES]
    finally:
        root.close()
    return report


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        report = _base_manifest(None)
        report["error"] = "usage"
        sys.stdout.write(_encode(report) + "\n")
        return 2

    report = collect_manifest(args[0])
    sys.stdout.write(_encode(report) + "\n")
    if "error" in report:
        return 2
    entries = report.get("entries", [])
    if any(item.get("status") not in {"hashed", "missing"} for item in entries):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
