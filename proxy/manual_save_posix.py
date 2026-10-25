"""Offline Linux-only candidate for the manual-save storage protocol.

This module does not authenticate callers, authorize a destination, acquire or
inspect audio, resolve paths, recover abandoned stages, or enable a save route.
Callers must provide already-open directory descriptors. It is not a production
adapter and is intentionally not imported by application code.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import secrets
import stat
import sys
import threading
import unicodedata
from typing import Final

from proxy import manual_save_core as core


_STAGE_PREFIX: Final = ".fnmusic-offline-stage-"
_STAGE_ATTEMPTS: Final = 16


class UnsupportedPosixStore(RuntimeError):
    """The host lacks the Linux primitives this candidate requires."""


class InvalidPosixStore(RuntimeError):
    """A supplied descriptor or private staging directory is not acceptable."""


@dataclass(slots=True)
class _Stage:
    name: str
    fd: int
    device: int
    inode: int
    sealed: bool = False
    publication_attempted: bool = False
    link_created: bool = False
    publication_confirmed: bool = False


def _require_linux_primitives() -> None:
    if sys.platform != "linux":
        raise UnsupportedPosixStore("linux_required")

    required_flags = ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")
    if any(not hasattr(os, flag) for flag in required_flags):
        raise UnsupportedPosixStore("required_open_flags_unavailable")

    if not all((
        os.open in os.supports_dir_fd,
        os.stat in os.supports_dir_fd,
        os.link in os.supports_dir_fd,
        os.unlink in os.supports_dir_fd,
        os.stat in os.supports_follow_symlinks,
        os.link in os.supports_follow_symlinks,
        callable(getattr(os, "fstat", None)),
        callable(getattr(os, "set_inheritable", None)),
        callable(getattr(os, "fsync", None)),
    )):
        raise UnsupportedPosixStore("required_dirfd_primitives_unavailable")


def _directory_stat(fd: int, label: str) -> os.stat_result:
    try:
        result = os.fstat(fd)
    except OSError:
        raise InvalidPosixStore(f"{label}_fd_invalid") from None
    if not stat.S_ISDIR(result.st_mode):
        raise InvalidPosixStore(f"{label}_not_directory")
    return result


def _safe_basename(value: object) -> str:
    if type(value) is not str or not value or len(value) > core.MAX_BASENAME_CHARS:
        raise ValueError("invalid_basename")
    if value in {".", ".."} or value[-1] in {" ", "."}:
        raise ValueError("invalid_basename")
    if any(character in value for character in ("/", "\\", ":", "\x00")):
        raise ValueError("invalid_basename")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("invalid_basename")
    try:
        encoded = os.fsencode(value)
    except (UnicodeError, ValueError):
        raise ValueError("invalid_basename") from None
    if len(encoded) > 255 or any(unicodedata.category(ch) in {"Cc", "Cs"} for ch in value):
        raise ValueError("invalid_basename")
    return value


class OfflineLinuxStore:
    """Narrow offline candidate bound to two caller-opened Linux directories.

    The destination and staging descriptors are duplicated and made
    non-inheritable. This object never accepts a path string and never opens a
    directory. `target_id` is only a binding label, not an authorization token.
    """

    def __init__(self, target_id: object, destination_dir_fd: object, staging_dir_fd: object):
        _require_linux_primitives()
        if type(target_id) is not str or not target_id or len(target_id) > core.MAX_TARGET_ID_CHARS:
            raise InvalidPosixStore("invalid_target")
        if type(destination_dir_fd) is not int or type(staging_dir_fd) is not int:
            raise InvalidPosixStore("directory_fd_required")

        destination_copy = -1
        staging_copy = -1
        try:
            destination_copy = os.dup(destination_dir_fd)
            os.set_inheritable(destination_copy, False)
            staging_copy = os.dup(staging_dir_fd)
            os.set_inheritable(staging_copy, False)

            destination_info = _directory_stat(destination_copy, "destination")
            staging_info = _directory_stat(staging_copy, "staging")
            if (destination_info.st_dev, destination_info.st_ino) == (
                staging_info.st_dev, staging_info.st_ino,
            ):
                raise InvalidPosixStore("directories_must_be_distinct")
            if destination_info.st_dev != staging_info.st_dev:
                raise InvalidPosixStore("directories_must_share_device")

            effective_uid = os.geteuid()
            staging_mode = stat.S_IMODE(staging_info.st_mode)
            if staging_info.st_uid != effective_uid:
                raise InvalidPosixStore("staging_owner_mismatch")
            if staging_mode & 0o077 or staging_mode & 0o700 != 0o700:
                raise InvalidPosixStore("staging_directory_not_private")
        except Exception:
            if staging_copy >= 0:
                os.close(staging_copy)
            if destination_copy >= 0:
                os.close(destination_copy)
            raise

        self._target_id = target_id
        self._destination_fd = destination_copy
        self._staging_fd = staging_copy
        self._staging_device = staging_info.st_dev
        self._effective_uid = effective_uid
        self._stages: dict[object, _Stage] = {}
        self._lock = threading.RLock()
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("store_closed")

    def _get_stage(self, handle: object) -> _Stage:
        if type(handle) is not object or handle not in self._stages:
            raise RuntimeError("unknown_stage")
        return self._stages[handle]

    def _verify_stage_entry(self, stage: _Stage) -> os.stat_result:
        try:
            descriptor_info = os.fstat(stage.fd)
            entry_info = os.stat(
                stage.name,
                dir_fd=self._staging_fd,
                follow_symlinks=False,
            )
        except OSError:
            raise RuntimeError("stage_identity_unavailable") from None

        expected = (stage.device, stage.inode)
        if (descriptor_info.st_dev, descriptor_info.st_ino) != expected:
            raise RuntimeError("stage_descriptor_changed")
        if (entry_info.st_dev, entry_info.st_ino) != expected:
            raise RuntimeError("stage_entry_changed")
        if not stat.S_ISREG(entry_info.st_mode) or entry_info.st_uid != self._effective_uid:
            raise RuntimeError("stage_entry_not_owned_regular_file")
        return entry_info

    def begin_owned_stage(self, target_id: object) -> object:
        with self._lock:
            self._ensure_open()
            if target_id != self._target_id:
                raise RuntimeError("target_mismatch")

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            for _ in range(_STAGE_ATTEMPTS):
                name = _STAGE_PREFIX + secrets.token_hex(24)
                try:
                    fd = os.open(name, flags, 0o600, dir_fd=self._staging_fd)
                except FileExistsError:
                    continue
                except OSError:
                    raise RuntimeError("stage_unavailable") from None

                info: os.stat_result | None = None
                try:
                    info = os.fstat(fd)
                    os.set_inheritable(fd, False)
                    if not stat.S_ISREG(info.st_mode):
                        raise InvalidPosixStore("stage_not_regular")
                    if info.st_uid != self._effective_uid:
                        raise InvalidPosixStore("stage_owner_mismatch")
                    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1:
                        raise InvalidPosixStore("stage_permissions_invalid")
                except Exception:
                    try:
                        entry = os.stat(name, dir_fd=self._staging_fd, follow_symlinks=False)
                        if info is not None and (entry.st_dev, entry.st_ino) == (info.st_dev, info.st_ino):
                            os.unlink(name, dir_fd=self._staging_fd)
                    except Exception:
                        pass
                    os.close(fd)
                    raise RuntimeError("stage_validation_failed") from None

                handle = object()
                self._stages[handle] = _Stage(name, fd, info.st_dev, info.st_ino)
                return handle
            raise RuntimeError("stage_name_exhausted")

    def append(self, handle: object, data: object) -> None:
        with self._lock:
            self._ensure_open()
            stage = self._get_stage(handle)
            if stage.sealed or stage.publication_attempted:
                raise RuntimeError("stage_not_writable")
            if type(data) is not bytes or not data or len(data) > core.MAX_CHUNK_BYTES:
                raise ValueError("invalid_chunk")
            self._verify_stage_entry(stage)

            remaining = memoryview(data)
            while remaining:
                try:
                    written = os.write(stage.fd, remaining)
                except OSError:
                    raise RuntimeError("stage_write_failed") from None
                if written <= 0:
                    raise RuntimeError("stage_short_write")
                remaining = remaining[written:]

    def seal(self, handle: object) -> None:
        with self._lock:
            self._ensure_open()
            stage = self._get_stage(handle)
            if stage.sealed or stage.publication_attempted:
                raise RuntimeError("stage_already_sealed")
            self._verify_stage_entry(stage)
            try:
                os.fsync(stage.fd)
            except OSError:
                raise RuntimeError("stage_sync_failed") from None
            stage.sealed = True

    def publish_noreplace(self, handle: object, basename: object) -> core.StoreResult:
        safe_name = _safe_basename(basename)
        with self._lock:
            self._ensure_open()
            stage = self._get_stage(handle)
            if not stage.sealed or stage.publication_attempted or stage.link_created:
                raise RuntimeError("stage_not_publishable")
            # Freeze before identity verification. The core treats every
            # publication exception as ambiguous, so even a transient stat
            # failure must not enable later retry or cleanup.
            stage.publication_attempted = True
            self._verify_stage_entry(stage)
            try:
                os.link(
                    stage.name,
                    safe_name,
                    src_dir_fd=self._staging_fd,
                    dst_dir_fd=self._destination_fd,
                    follow_symlinks=False,
                )
            except FileExistsError:
                stage.publication_attempted = False
                return core.StoreResult.CONFLICT
            except OSError:
                raise RuntimeError("publication_unknown") from None

            stage.link_created = True
            try:
                os.fsync(self._destination_fd)
            except OSError:
                # The hard link may already be visible. Keep the owned stage
                # for reconciliation; never retry or roll back the final name.
                raise RuntimeError("publication_unknown") from None
            stage.publication_confirmed = True
            return core.StoreResult.PUBLISHED

    def _remove_owned_stage(self, handle: object, *, published: bool) -> None:
        stage = self._get_stage(handle)
        if published and not stage.publication_confirmed:
            raise RuntimeError("stage_publication_not_confirmed")
        if not published and (
            stage.publication_attempted or stage.link_created or stage.publication_confirmed
        ):
            raise RuntimeError("ambiguous_stage_not_abortable")
        self._verify_stage_entry(stage)
        try:
            os.unlink(stage.name, dir_fd=self._staging_fd)
            os.fsync(self._staging_fd)
        except FileNotFoundError:
            # An earlier cleanup may have unlinked the owned entry before its
            # directory fsync failed. No unrelated entry is removed here.
            pass
        except OSError:
            raise RuntimeError("stage_cleanup_failed") from None

        try:
            os.close(stage.fd)
        finally:
            del self._stages[handle]

    def abort_owned_stage(self, handle: object) -> None:
        with self._lock:
            self._ensure_open()
            self._remove_owned_stage(handle, published=False)

    def release_owned_stage(self, handle: object) -> None:
        with self._lock:
            self._ensure_open()
            self._remove_owned_stage(handle, published=True)

    def close(self) -> None:
        """Close owned descriptors but leave all stage names for inspection."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            first_error: OSError | None = None
            for stage in self._stages.values():
                try:
                    os.close(stage.fd)
                except OSError as exc:
                    first_error = first_error or exc
            for fd in (self._staging_fd, self._destination_fd):
                try:
                    os.close(fd)
                except OSError as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise RuntimeError("descriptor_close_failed") from None

    def __enter__(self) -> "OfflineLinuxStore":
        self._ensure_open()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
