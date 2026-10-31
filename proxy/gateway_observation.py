"""Default-off, one-shot, redacted observation for the desktop gateway.

This module records bounded metadata for one exact health probe request. It never
authenticates a caller or changes gateway routing decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
import queue
import re
import stat
import sys
import threading
import time
from typing import Any

if __package__:
    from . import gateway_identity as _identity
else:  # direct execution from the proxy source directory
    import gateway_identity as _identity


SCHEMA = "fnmusic-ext-g0-observation-v2"
MAX_OUTPUT_BYTES = 8 * 1024
_MARKER_RE = re.compile(r"[0-9a-f]{32}\Z", re.ASCII)
_REQUEST_PREFIX = b"GET /app/fnmusic-ext/healthz?g0_observe="
_HEADER_NAMES = (
    (b"x-trim-userid", "user_id"),
    (b"x-trim-isadmin", "admin"),
    (b"x-trim-username", "username"),
)
_HEADER_ERROR_CODES = frozenset({
    "INVALID_HEADER_INPUT", "TOO_MANY_HEADERS", "HEADER_BYTES_LIMIT",
    "INVALID_HEADER_NAME", "INVALID_HEADER_VALUE", "DUPLICATE_IDENTITY_HEADER",
    "INVALID_USER_ID_CLAIM", "INVALID_ADMIN_CLAIM", "INVALID_USERNAME_CLAIM",
    "DUPLICATE_CONTENT_LENGTH", "INVALID_CONTENT_LENGTH",
    "DUPLICATE_TRANSFER_ENCODING", "CONTENT_LENGTH_TRANSFER_ENCODING_CONFLICT",
})
_PEER_ERROR_CODES = frozenset({
    "UNSUPPORTED_PLATFORM", "INVALID_SOCKET_OBJECT", "UNSUPPORTED_SOCKET_FAMILY",
    "UNSUPPORTED_SOCKET_TYPE", "PEER_CREDENTIALS_UNSUPPORTED",
    "PEER_CREDENTIALS_SYSCALL_FAILED", "INVALID_PEER_CREDENTIALS_PAYLOAD",
    "INVALID_PEER_PID", "INVALID_PEER_UID", "INVALID_PEER_GID",
})


class ObservationSetupError(RuntimeError):
    """Stable setup error; exception text never includes paths or input."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class ObservationOptions:
    marker: str
    seconds: int = 60


def validate_options(marker: str, seconds: int) -> ObservationOptions:
    if type(marker) is not str or _MARKER_RE.fullmatch(marker) is None:
        raise ObservationSetupError("INVALID_MARKER")
    if type(seconds) is not int or not 15 <= seconds <= 120:
        raise ObservationSetupError("INVALID_DURATION")
    return ObservationOptions(marker, seconds)


def _require_dir_policy(fd: int, *, private: bool) -> None:
    try:
        info = os.fstat(fd)
    except OSError:
        raise ObservationSetupError("DIRECTORY_OPEN_FAILED") from None
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise ObservationSetupError("DIRECTORY_POLICY_FAILED")
    mode = stat.S_IMODE(info.st_mode)
    if (private and mode != 0o700) or (not private and mode & 0o022):
        raise ObservationSetupError("DIRECTORY_POLICY_FAILED")


def prepare_output_fd(marker: str) -> int:
    """Securely create the fixed root-owned JSONL file and return its fd.

    Directory descriptors are walked from `/` with O_NOFOLLOW. Accounting
    covers `/run/fnmusic-ext/g0-observe/<marker>.jsonl`; no path is configurable.
    """
    validate_options(marker, 60)
    if sys.platform != "linux":
        raise ObservationSetupError("UNSUPPORTED_PLATFORM")

    required = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if any(not hasattr(os, name) for name in required):
        raise ObservationSetupError("UNSUPPORTED_SECURE_OPEN")
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    file_flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    )
    opened_dirs: list[int] = []
    out_fd: int | None = None
    try:
        try:
            current_fd = os.open("/", dir_flags)
        except OSError:
            raise ObservationSetupError("DIRECTORY_OPEN_FAILED") from None
        opened_dirs.append(current_fd)
        _require_dir_policy(current_fd, private=False)

        for component in ("run", "fnmusic-ext"):
            try:
                next_fd = os.open(component, dir_flags, dir_fd=current_fd)
            except OSError:
                raise ObservationSetupError("DIRECTORY_OPEN_FAILED") from None
            opened_dirs.append(next_fd)
            _require_dir_policy(next_fd, private=False)
            current_fd = next_fd

        try:
            observe_fd = os.open("g0-observe", dir_flags, dir_fd=current_fd)
        except FileNotFoundError:
            try:
                os.mkdir("g0-observe", 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            except OSError:
                raise ObservationSetupError("DIRECTORY_CREATE_FAILED") from None
            try:
                observe_fd = os.open("g0-observe", dir_flags, dir_fd=current_fd)
            except OSError:
                raise ObservationSetupError("DIRECTORY_OPEN_FAILED") from None
        except OSError:
            raise ObservationSetupError("DIRECTORY_OPEN_FAILED") from None
        opened_dirs.append(observe_fd)
        _require_dir_policy(observe_fd, private=True)

        filename = marker + ".jsonl"
        try:
            out_fd = os.open(filename, file_flags, 0o600, dir_fd=observe_fd)
        except OSError:
            raise ObservationSetupError("OUTPUT_FILE_CREATE_FAILED") from None
        try:
            info = os.fstat(out_fd)
        except OSError:
            raise ObservationSetupError("OUTPUT_FILE_POLICY_FAILED") from None
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
        ):
            raise ObservationSetupError("OUTPUT_FILE_POLICY_FAILED")

        if out_fd < 3:
            try:
                import fcntl

                duplicated_fd = fcntl.fcntl(out_fd, fcntl.F_DUPFD_CLOEXEC, 3)
                os.close(out_fd)
                out_fd = duplicated_fd
            except Exception:
                raise ObservationSetupError("OUTPUT_FD_POLICY_FAILED") from None
        if out_fd < 3:
            raise ObservationSetupError("OUTPUT_FD_POLICY_FAILED")
        result_fd = out_fd
        out_fd = None
        return result_fd
    except ObservationSetupError:
        raise
    except Exception:
        raise ObservationSetupError("OUTPUT_INITIALIZATION_FAILED") from None
    finally:
        if out_fd is not None:
            try:
                os.close(out_fd)
            except OSError:
                pass
        for directory_fd in reversed(opened_dirs):
            try:
                os.close(directory_fd)
            except OSError:
                pass


def _request_matches(head_raw: bytes, marker: str) -> bool:
    if type(head_raw) is not bytes:
        return False
    request_line_end = head_raw.find(b"\r\n", 0, len(_REQUEST_PREFIX) + 32 + 12)
    if request_line_end < 0:
        return False
    request_line = head_raw[:request_line_end]
    target = _REQUEST_PREFIX + marker.encode("ascii")
    return request_line in (target + b" HTTP/1.0", target + b" HTTP/1.1")


def _parse_raw_header_pairs(head_raw: bytes, marker: str) -> list[tuple[bytes, bytes]]:
    """Boundedly parse only the bytes before the first CRLFCRLF."""
    expected = _REQUEST_PREFIX + marker.encode("ascii")
    max_line_length = len(expected) + len(b" HTTP/1.1")
    request_line_end = head_raw.find(b"\r\n", 0, max_line_length + 2)
    if request_line_end < 0 or not _request_matches(head_raw, marker):
        raise _identity.HeaderClaimsError("INVALID_HEADER_INPUT")

    max_head_end = (
        request_line_end + 2 + _identity.MAX_HEADER_BYTES
        + (_identity.MAX_HEADER_PAIRS + 1) * 3 + 4
    )
    delimiter = head_raw.find(b"\r\n\r\n", request_line_end, max_head_end)
    if delimiter < 0:
        raise _identity.HeaderClaimsError("INVALID_HEADER_INPUT")

    pairs: list[tuple[bytes, bytes]] = []
    byte_total = 0
    cursor = request_line_end + 2
    while cursor < delimiter:
        line_end = head_raw.find(b"\r\n", cursor, delimiter + 2)
        if line_end < 0 or line_end > delimiter:
            line_end = delimiter
        line = head_raw[cursor:line_end]
        colon = line.find(b":")
        if colon < 0:
            raise _identity.HeaderClaimsError("INVALID_HEADER_INPUT")
        name, value = line[:colon], line[colon + 1:]
        if len(pairs) >= _identity.MAX_HEADER_PAIRS:
            raise _identity.HeaderClaimsError("TOO_MANY_HEADERS")
        byte_total += len(name) + len(value)
        if byte_total > _identity.MAX_HEADER_BYTES:
            raise _identity.HeaderClaimsError("HEADER_BYTES_LIMIT")
        pairs.append((name, value))
        cursor = line_end + 2
    return pairs


def _unknown_header_summary() -> dict[str, Any]:
    result: dict[str, Any] = {}
    for _header, label in _HEADER_NAMES:
        result[f"{label}_count"] = None
        result[f"{label}_present"] = None
        result[f"{label}_canonical"] = None
    return result


def _header_summary(
    pairs: list[tuple[bytes, bytes]], claims: _identity.UntrustedIdentityClaims,
) -> dict[str, Any]:
    claims_by_label = {
        "user_id": claims.user_id_claim,
        "admin": claims.admin_claim,
        "username": claims.username_claim,
    }
    summary: dict[str, Any] = {}
    for normalized_name, label in _HEADER_NAMES:
        count = sum(1 for name, _value in pairs if name.lower() == normalized_name)
        summary[f"{label}_count"] = count
        summary[f"{label}_present"] = count == 1
        summary[f"{label}_canonical"] = (
            True if count == 1 and claims_by_label[label] is not None else None
        )
    return summary


def _fixed_reason(exc: BaseException, allowed: frozenset[str], fallback: str) -> str:
    value = getattr(exc, "reason_code", None)
    return value if type(value) is str and value in allowed else fallback


class GatewayObserver:
    """One-shot observer whose request-facing path never writes files."""

    def __init__(
        self,
        marker: str,
        seconds: int,
        output_fd: int,
        *,
        clock=time.monotonic,
        thread_factory=threading.Thread,
        queue_factory=queue.Queue,
    ):
        options = validate_options(marker, seconds)
        if type(output_fd) is not int or output_fd < 3:
            raise ObservationSetupError("OUTPUT_FD_POLICY_FAILED")
        self.marker = options.marker
        self.seconds = options.seconds
        self._fd: int | None = output_fd
        self._clock = clock
        self._thread_factory = thread_factory
        self._queue = queue_factory(maxsize=1)
        self._lock = threading.Lock()
        self._started = False
        self._consumed = False
        self._closed = False
        self._stop_requested = False
        self._start_time: float | None = None
        self._deadline: float | None = None
        self._written_bytes = 0

    def start(self) -> None:
        """Start the file writer only after the final listener is active."""
        with self._lock:
            if self._started or self._closed or self._fd is None:
                return
            self._start_time = self._clock()
            self._deadline = self._start_time + self.seconds
            self._started = True
        try:
            writer = self._thread_factory(
                target=self._writer_main,
                name="fnmusic-g0-observer",
                daemon=True,
            )
            writer.start()
        except Exception:
            self._close_fd()

    def observe(self, client: object, head_raw: bytes) -> None:
        """Inspect one exact request, then submit one redacted record without blocking."""
        if not _request_matches(head_raw, self.marker):
            return
        with self._lock:
            if (
                not self._started or self._closed or self._consumed
                or self._deadline is None or self._clock() >= self._deadline
            ):
                return
            self._consumed = True
            started_at = self._start_time
        try:
            pairs = _parse_raw_header_pairs(head_raw, self.marker)
            try:
                claims = _identity.parse_untrusted_identity(pairs)
                header_result = "OK"
                headers = _header_summary(pairs, claims)
            except _identity.HeaderClaimsError as exc:
                header_result = _fixed_reason(
                    exc, _HEADER_ERROR_CODES, "HEADER_ANALYSIS_FAILED",
                )
                headers = _unknown_header_summary()
        except _identity.HeaderClaimsError as exc:
            header_result = _fixed_reason(
                exc, _HEADER_ERROR_CODES, "HEADER_ANALYSIS_FAILED",
            )
            headers = _unknown_header_summary()
        except Exception:
            header_result = "HEADER_ANALYSIS_FAILED"
            headers = _unknown_header_summary()

        peer_fields: dict[str, Any]
        try:
            peer = _identity.read_peer_credentials(client)
            peer_fields = {
                "peer_pid": peer.pid,
                "peer_uid": peer.uid,
                "peer_gid": peer.gid,
                "peer_error": None,
            }
        except Exception as exc:
            peer_fields = {
                "peer_pid": None,
                "peer_uid": None,
                "peer_gid": None,
                "peer_error": _fixed_reason(
                    exc, _PEER_ERROR_CODES, "PEER_METADATA_FAILED",
                ),
            }

        elapsed_ms = max(0, int((self._clock() - (started_at or 0.0)) * 1000))
        record = {
            "schema": SCHEMA,
            "event": "observation",
            "diagnostic_only": True,
            "marker": self.marker,
            "elapsed_ms": elapsed_ms,
            "header_result": header_result,
            **headers,
            **peer_fields,
        }
        try:
            with self._lock:
                if (
                    self._closed or self._deadline is None
                    or self._clock() >= self._deadline
                ):
                    return
                self._queue.put_nowait(record)
        except Exception:
            # Diagnostics are expendable. The writer will still close on expiry.
            pass

    def stop(self) -> None:
        """Request a bounded terminal record when the serving loop exits."""
        with self._lock:
            if self._closed or self._stop_requested:
                return
            self._stop_requested = True
            if not self._started:
                self._closed = True
                fd = self._fd
                self._fd = None
            else:
                fd = None
                should_wake = not self._consumed
                if should_wake:
                    try:
                        self._queue.put_nowait({"terminal": "OBSERVER_STOPPED"})
                    except Exception:
                        pass
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _write_record(self, record: dict[str, Any]) -> bool:
        fd = self._fd
        if fd is None:
            return False
        try:
            encoded = (json.dumps(record, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")
        except Exception:
            return False
        if len(encoded) + self._written_bytes > MAX_OUTPUT_BYTES:
            return False
        offset = 0
        while offset < len(encoded):
            try:
                written = os.write(fd, encoded[offset:])
            except OSError:
                return False
            if type(written) is not int or written <= 0:
                return False
            offset += written
        self._written_bytes += len(encoded)
        return True

    def _close_fd(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            fd = self._fd
            self._fd = None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass

    def _terminal_record(self, reason: str, elapsed_ms: int) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "event": "terminal",
            "diagnostic_only": True,
            "marker": self.marker,
            "elapsed_ms": max(0, elapsed_ms),
            "reason": reason,
        }

    def _writer_main(self) -> None:
        start_time = self._start_time
        deadline = self._deadline
        if start_time is None or deadline is None:
            self._close_fd()
            return
        ready = {
            "schema": SCHEMA,
            "event": "ready",
            "diagnostic_only": True,
            "marker": self.marker,
            "elapsed_ms": 0,
        }
        if not self._write_record(ready):
            self._close_fd()
            return
        reason = "OBSERVATION_EXPIRED"
        event: dict[str, Any] | None = None
        try:
            remaining = max(0.0, deadline - self._clock())
            item = self._queue.get(timeout=remaining)
            if type(item) is dict and "terminal" in item:
                reason = "OBSERVER_STOPPED"
            elif type(item) is dict:
                event = item
        except queue.Empty:
            pass
        except Exception:
            reason = "OBSERVER_QUEUE_FAILED"

        if event is not None:
            if not self._write_record(event):
                # No exception or untrusted data is written as a fallback.
                pass
        else:
            elapsed_ms = int((self._clock() - start_time) * 1000)
            self._write_record(self._terminal_record(reason, elapsed_ms))
        self._close_fd()
