"""Offline Linux-only anonymous staging candidate for manual-save bytes.

This module is deliberately not imported by the application or any save route.
It accepts caller-opened directory descriptors and bytes only. It does not
authenticate or authorize a user, obtain or inspect audio, resolve a library
path, or establish production readiness.

Staging uses O_TMPFILE. Publication uses exactly linkat(fd, "", dirfd, name,
AT_EMPTY_PATH), which may require CAP_DAC_READ_SEARCH. There is no named-file,
procfs, rename, or other fallback. An uncertain link result freezes the store
and retains its descriptors until process exit; the candidate never retries
publication or removes a possibly published name.

On ACL-inheriting filesystems (for example fnOS btrfs with system.trim_acl),
an O_TMPFILE inode requested with mode 0600 can be reported with owner rwx
(mode 0700). begin_owned_stage therefore normalizes the freshly created
inode to 0600 with fchmod before its strict verification. It fails closed if
normalization fails or does not yield exactly 0600; there is no fallback and
no exception text is exposed.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import errno
import os
import stat
import sys
import threading
import unicodedata
from typing import Final

from proxy import manual_save_core as core


_AT_EMPTY_PATH: Final = 0x1000  # Linux <fcntl.h>
PIPELINE_INTEGRATION_ALLOWED: Final = False


class UnsupportedAnonymousStaging(RuntimeError):
    """Anonymous staging or the required Linux publication primitive is absent."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class PublicationUncertain(RuntimeError):
    """Publication was attempted; this handle can no longer be changed or retried."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


@dataclass(slots=True)
class _Stage:
    fd: int
    device: int
    inode: int
    sealed: bool = False
    publication_attempted: bool = False
    link_created: bool = False
    publication_confirmed: bool = False
    fd_close_attempted: bool = False


def _require_linux_primitives() -> None:
    if sys.platform != "linux":
        raise UnsupportedAnonymousStaging("linux_required")
    if not hasattr(os, "O_TMPFILE"):
        raise UnsupportedAnonymousStaging("o_tmpfile_flag_unavailable")
    if not hasattr(os, "O_CLOEXEC") or not hasattr(os, "O_DIRECTORY"):
        raise UnsupportedAnonymousStaging("required_open_flags_unavailable")
    if not all((
        os.open in os.supports_dir_fd,
        callable(getattr(os, "dup", None)),
        callable(getattr(os, "fchmod", None)),
        callable(getattr(os, "fstat", None)),
        callable(getattr(os, "set_inheritable", None)),
        callable(getattr(os, "write", None)),
        callable(getattr(os, "pread", None)),
        callable(getattr(os, "fsync", None)),
        callable(getattr(os, "close", None)),
        callable(getattr(os, "geteuid", None)),
    )):
        raise UnsupportedAnonymousStaging("required_descriptor_primitives_unavailable")
    try:
        getattr(ctypes.CDLL(None, use_errno=True), "linkat")
    except (AttributeError, OSError):
        raise UnsupportedAnonymousStaging("linkat_unavailable") from None


def _directory_stat(fd: int, label: str) -> os.stat_result:
    try:
        result = os.fstat(fd)
    except OSError:
        raise ValueError(f"{label}_fd_invalid") from None
    if not stat.S_ISDIR(result.st_mode):
        raise ValueError(f"{label}_not_directory")
    return result


def _normalize_anonymous_mode(fd: int) -> None:
    """Force the freshly created anonymous inode to exactly mode 0600.

    ACL-inheriting filesystems can report owner rwx for an O_TMPFILE inode
    that was requested with 0600 (observed on fnOS btrfs + system.trim_acl).
    The normalization is mandatory and has no fallback; the caller re-verifies
    the inode afterwards and fails closed if the mode did not take. Callers
    never see the underlying OSError text.
    """
    try:
        os.fchmod(fd, 0o600)
    except OSError:
        raise UnsupportedAnonymousStaging("stage_mode_normalization_failed") from None


def _safe_basename(value: object) -> str:
    if type(value) is not str or not value or len(value) > core.MAX_BASENAME_CHARS:
        raise ValueError("invalid_basename")
    if value in {".", ".."} or value[-1] in {" ", "."}:
        raise ValueError("invalid_basename")
    if any(character in value for character in ("/", "\\", ":", "\x00")):
        raise ValueError("invalid_basename")
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        raise ValueError("invalid_basename")
    try:
        encoded = os.fsencode(value)
    except (UnicodeError, ValueError):
        raise ValueError("invalid_basename") from None
    if len(encoded) > 255:
        raise ValueError("invalid_basename")
    return value


def _linkat_empty_path(source_fd: int, destination_dir_fd: int, basename: str) -> None:
    """Make one no-replace hard link from an anonymous inode, with no fallback."""
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        linkat = libc.linkat
    except (AttributeError, OSError):
        raise UnsupportedAnonymousStaging("linkat_unavailable") from None

    linkat.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
    )
    linkat.restype = ctypes.c_int
    result = linkat(
        source_fd,
        b"",
        destination_dir_fd,
        os.fsencode(basename),
        _AT_EMPTY_PATH,
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, "destination_exists")
    if error_number == errno.EPERM:
        # Linux documents CAP_DAC_READ_SEARCH for AT_EMPTY_PATH. Do not retry
        # through /proc/self/fd or a name: the caller must treat this attempt
        # as uncertain and stop.
        raise UnsupportedAnonymousStaging("at_empty_path_capability_required")
    if error_number == errno.EACCES:
        raise UnsupportedAnonymousStaging("destination_permission_denied")
    if error_number == errno.EROFS:
        raise UnsupportedAnonymousStaging("destination_filesystem_read_only")
    if error_number == errno.EMLINK:
        raise UnsupportedAnonymousStaging("filesystem_link_limit")
    if error_number in {errno.EINVAL, errno.EOPNOTSUPP, errno.ENOSYS}:
        raise UnsupportedAnonymousStaging("at_empty_path_unsupported")
    if error_number == errno.EXDEV:
        raise UnsupportedAnonymousStaging("staging_and_destination_not_linkable")
    raise OSError(error_number, "linkat_failed")


class AnonymousLinuxStore:
    """Descriptor-bound offline candidate; intentionally not pipeline-integrable.

    Both directories must be opened by the caller and reside on the same
    device. The staging directory must be owned by the effective UID and have
    mode 0700. This class never opens a path or creates a directory. Every
    staged inode is normalized to 0600 and re-verified strictly; an
    ACL-inheriting filesystem that overrides the requested mode is rejected
    unless the normalization produces exactly 0600.
    """

    def __init__(self, target_id: object, destination_dir_fd: object, staging_dir_fd: object):
        _require_linux_primitives()
        if (
            type(target_id) is not str
            or not target_id
            or len(target_id) > core.MAX_TARGET_ID_CHARS
            or core._TARGET_RE.fullmatch(target_id) is None
        ):
            raise ValueError("invalid_target")
        if type(destination_dir_fd) is not int or type(staging_dir_fd) is not int:
            raise ValueError("directory_fd_required")

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
                staging_info.st_dev,
                staging_info.st_ino,
            ):
                raise ValueError("directories_must_be_distinct")
            if destination_info.st_dev != staging_info.st_dev:
                raise ValueError("directories_must_share_device")

            effective_uid = os.geteuid()
            if staging_info.st_uid != effective_uid:
                raise ValueError("staging_owner_mismatch")
            staging_mode = stat.S_IMODE(staging_info.st_mode)
            if staging_mode != 0o700:
                raise ValueError("staging_directory_not_private")
        except Exception:
            if staging_copy >= 0:
                os.close(staging_copy)
            if destination_copy >= 0:
                os.close(destination_copy)
            raise

        self._target_id = target_id
        self._destination_fd = destination_copy
        self._staging_fd = staging_copy
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

    def _ensure_no_ambiguous_stage(self) -> None:
        if any(stage.publication_attempted and not stage.publication_confirmed for stage in self._stages.values()):
            raise PublicationUncertain("store_blocked_after_unknown_publication")

    def _verify_anonymous_fd(self, stage: _Stage, *, link_count: int) -> os.stat_result:
        try:
            info = os.fstat(stage.fd)
        except OSError:
            raise RuntimeError("stage_descriptor_unavailable") from None
        if (info.st_dev, info.st_ino) != (stage.device, stage.inode):
            raise RuntimeError("stage_descriptor_changed")
        if not stat.S_ISREG(info.st_mode) or info.st_uid != self._effective_uid:
            raise RuntimeError("stage_not_owned_regular_file")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise RuntimeError("stage_permissions_invalid")
        if info.st_nlink != link_count:
            raise RuntimeError("stage_link_count_changed")
        return info

    def begin_owned_stage(self, target_id: object) -> object:
        with self._lock:
            self._ensure_open()
            self._ensure_no_ambiguous_stage()
            if target_id != self._target_id:
                raise RuntimeError("target_mismatch")

            flags = os.O_TMPFILE | os.O_DIRECTORY | os.O_RDWR | os.O_CLOEXEC
            try:
                fd = os.open(".", flags, 0o600, dir_fd=self._staging_fd)
            except OSError as exc:
                if exc.errno in {errno.EOPNOTSUPP, errno.EINVAL, errno.ENOSYS}:
                    raise UnsupportedAnonymousStaging("o_tmpfile_unsupported") from None
                if exc.errno in {errno.EACCES, errno.EPERM}:
                    raise UnsupportedAnonymousStaging("o_tmpfile_permission_denied") from None
                raise UnsupportedAnonymousStaging("o_tmpfile_unavailable") from None

            try:
                os.set_inheritable(fd, False)
                # Some ACL-inheriting filesystems create the anonymous inode
                # with owner rwx even when 0600 was requested. Normalize first,
                # then run the unchanged strict verification below and fail
                # closed if the mode did not become exactly 0600.
                _normalize_anonymous_mode(fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode):
                    raise RuntimeError("stage_not_regular")
                if info.st_uid != self._effective_uid:
                    raise RuntimeError("stage_owner_mismatch")
                if stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 0:
                    raise RuntimeError("stage_permissions_or_link_count_invalid")
            except Exception:
                os.close(fd)
                raise

            handle = object()
            self._stages[handle] = _Stage(fd, info.st_dev, info.st_ino)
            return handle

    def append(self, handle: object, data: object) -> None:
        with self._lock:
            self._ensure_open()
            self._ensure_no_ambiguous_stage()
            stage = self._get_stage(handle)
            if stage.sealed or stage.publication_attempted:
                raise RuntimeError("stage_not_writable")
            if type(data) is not bytes or not data or len(data) > core.MAX_CHUNK_BYTES:
                raise ValueError("invalid_chunk")
            self._verify_anonymous_fd(stage, link_count=0)

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
            self._ensure_no_ambiguous_stage()
            stage = self._get_stage(handle)
            if stage.sealed or stage.publication_attempted:
                raise RuntimeError("stage_already_sealed")
            self._verify_anonymous_fd(stage, link_count=0)
            try:
                os.fsync(stage.fd)
            except OSError:
                raise RuntimeError("stage_sync_failed") from None
            self._verify_anonymous_fd(stage, link_count=0)
            stage.sealed = True

    def read_for_verification(self, handle: object, offset: object, length: object) -> bytes:
        """Return copied bytes only; never expose the internal O_RDWR fd.

        This narrow API is for synthetic offline checks. It is not a security
        boundary against hostile code in the same Python process; see the
        module's integration gate and accompanying documentation.
        """
        with self._lock:
            self._ensure_open()
            self._ensure_no_ambiguous_stage()
            stage = self._get_stage(handle)
            if not stage.sealed or stage.publication_attempted:
                raise RuntimeError("stage_not_readable_for_verification")
            if type(offset) is not int or offset < 0:
                raise ValueError("invalid_verification_offset")
            if type(length) is not int or not 1 <= length <= core.MAX_CHUNK_BYTES:
                raise ValueError("invalid_verification_length")
            self._verify_anonymous_fd(stage, link_count=0)
            try:
                result = os.pread(stage.fd, length, offset)
            except OSError:
                raise RuntimeError("verification_read_failed") from None
            self._verify_anonymous_fd(stage, link_count=0)
            return bytes(result)

    def publish_noreplace(self, handle: object, basename: object) -> core.StoreResult:
        with self._lock:
            self._ensure_open()
            self._ensure_no_ambiguous_stage()
            stage = self._get_stage(handle)
            if not stage.sealed or stage.publication_attempted or stage.link_created:
                raise RuntimeError("stage_not_publishable")
            safe_name = _safe_basename(basename)

            # Freeze before identity verification and before the one syscall.
            # Any failure after here is non-retryable; an exception may map to
            # COMMIT_UNKNOWN in manual_save_core even when the kernel denied it.
            stage.publication_attempted = True
            try:
                self._verify_anonymous_fd(stage, link_count=0)
                _linkat_empty_path(stage.fd, self._destination_fd, safe_name)
            except FileExistsError:
                # EEXIST is the atomic no-replace conflict result.
                stage.publication_attempted = False
                return core.StoreResult.CONFLICT
            except UnsupportedAnonymousStaging as exc:
                raise PublicationUncertain(exc.reason) from None
            except Exception:
                raise PublicationUncertain("publication_outcome_unknown") from None

            stage.link_created = True
            try:
                self._verify_anonymous_fd(stage, link_count=1)
                os.fsync(self._destination_fd)
            except Exception:
                raise PublicationUncertain("publication_outcome_unknown") from None
            stage.publication_confirmed = True
            return core.StoreResult.PUBLISHED

    def _close_stage_fd_once(self, stage: _Stage) -> None:
        if stage.fd_close_attempted:
            raise RuntimeError("stage_descriptor_close_already_attempted")
        stage.fd_close_attempted = True
        fd = stage.fd
        stage.fd = -1
        try:
            os.close(fd)
        except OSError:
            raise RuntimeError("stage_descriptor_close_unknown") from None

    def abort_owned_stage(self, handle: object) -> None:
        """Close an unpublished anonymous inode; no directory entry is removed."""
        with self._lock:
            self._ensure_open()
            self._ensure_no_ambiguous_stage()
            stage = self._get_stage(handle)
            if stage.publication_attempted or stage.link_created or stage.publication_confirmed:
                raise RuntimeError("ambiguous_stage_not_abortable")
            self._verify_anonymous_fd(stage, link_count=0)
            self._close_stage_fd_once(stage)
            del self._stages[handle]

    def release_owned_stage(self, handle: object) -> None:
        """Close the descriptor after confirmed publication; never unlink output."""
        with self._lock:
            self._ensure_open()
            self._ensure_no_ambiguous_stage()
            stage = self._get_stage(handle)
            if not stage.publication_confirmed or not stage.link_created:
                raise RuntimeError("stage_publication_not_confirmed")
            self._verify_anonymous_fd(stage, link_count=1)
            self._close_stage_fd_once(stage)
            del self._stages[handle]

    def close(self) -> None:
        """Close the store unless a publication outcome is ambiguous.

        Ambiguous anonymous descriptors remain open for process-lifetime
        inspection. This intentionally avoids retrying, explicitly deleting,
        or silently discarding data whose publication result is unknown.
        """
        with self._lock:
            if self._closed:
                return
            if any(stage.publication_attempted and not stage.publication_confirmed for stage in self._stages.values()):
                raise PublicationUncertain("ambiguous_stage_retained_until_process_exit")

            first_error: OSError | None = None
            for stage in self._stages.values():
                try:
                    self._close_stage_fd_once(stage)
                except RuntimeError:
                    first_error = first_error or OSError("stage_descriptor_close_unknown")
            self._stages.clear()
            self._closed = True
            for fd in (self._staging_fd, self._destination_fd):
                try:
                    os.close(fd)
                except OSError as exc:
                    first_error = first_error or exc
            if first_error is not None:
                raise RuntimeError("descriptor_close_unknown") from None
