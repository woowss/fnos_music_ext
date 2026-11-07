"""Strict parsing of untrusted gateway claims and read-only peer metadata.

The header parser preserves identity values as claims only; it does not
authenticate them or make authorization decisions. Its Content-Length and
Transfer-Encoding checks detect header-level ambiguity only and are not a
complete HTTP parser or a request-smuggling fix.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import re
import socket
import struct
import sys


MAX_HEADER_PAIRS = 128
MAX_HEADER_BYTES = 64 * 1024


class HeaderClaimsError(ValueError):
    """A stable, input-redacted parsing error for header claims."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


class PeerCredentialsError(ValueError):
    """A stable, input-redacted error reading Linux peer metadata."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class UntrustedIdentityClaims:
    """Opaque header claims; these fields do not establish trusted identity."""

    user_id_claim: bytes | None
    admin_claim: bool | None
    username_claim: bytes | None


@dataclass(frozen=True)
class PeerCredentialsMetadata:
    """Kernel-reported peer metadata, without an authentication policy."""

    pid: int
    uid: int
    gid: int


_TOKEN_PUNCTUATION = frozenset(b"!#$%&'*+-.^_`|~")
_IDENTITY_HEADERS = {
    b"x-trim-userid": "user_id_claim",
    b"x-trim-isadmin": "admin_claim",
    b"x-trim-username": "username_claim",
}
_UINT32_RESERVED = 0xFFFFFFFF
_UCRED_SIZE = struct.calcsize("=iII")


def _is_token_name(name: bytes) -> bool:
    if not name:
        return False
    for byte in name:
        if (
            0x30 <= byte <= 0x39
            or 0x41 <= byte <= 0x5A
            or 0x61 <= byte <= 0x7A
            or byte in _TOKEN_PUNCTUATION
        ):
            continue
        return False
    return True


def _has_disallowed_control(value: bytes) -> bool:
    return any(byte == 0x7F or (byte < 0x20 and byte != 0x09) for byte in value)


def _trim_ows(value: bytes) -> bytes:
    """Remove only HTTP optional whitespace: SP and HTAB."""
    return value.strip(b" \t")


def parse_untrusted_identity(
    header_pairs: Iterable[tuple[bytes, bytes]],
) -> UntrustedIdentityClaims:
    """Parse strict identity claims from raw header pairs without trusting them.

    Limits count every yielded pair and the sum of each pair's raw name and
    value byte lengths (including duplicates, before OWS trimming). Separators,
    framing bytes, and any external container overhead are not included.
    """
    if isinstance(header_pairs, Mapping):
        raise HeaderClaimsError("INVALID_HEADER_INPUT")

    try:
        pairs = iter(header_pairs)
    except Exception:
        raise HeaderClaimsError("INVALID_HEADER_INPUT") from None

    seen_identity: set[bytes] = set()
    seen_content_length = False
    seen_transfer_encoding = False
    total_bytes = 0
    pair_count = 0

    user_id_claim: bytes | None = None
    admin_claim: bool | None = None
    username_claim: bytes | None = None

    while True:
        try:
            pair = next(pairs)
        except StopIteration:
            break
        except Exception:
            raise HeaderClaimsError("INVALID_HEADER_INPUT") from None

        pair_count += 1
        if pair_count > MAX_HEADER_PAIRS:
            raise HeaderClaimsError("TOO_MANY_HEADERS")
        if type(pair) is not tuple or len(pair) != 2:
            raise HeaderClaimsError("INVALID_HEADER_INPUT")
        name, raw_value = pair
        if type(name) is not bytes or type(raw_value) is not bytes:
            raise HeaderClaimsError("INVALID_HEADER_INPUT")

        total_bytes += len(name) + len(raw_value)
        if total_bytes > MAX_HEADER_BYTES:
            raise HeaderClaimsError("HEADER_BYTES_LIMIT")
        if not _is_token_name(name):
            raise HeaderClaimsError("INVALID_HEADER_NAME")
        if _has_disallowed_control(raw_value):
            raise HeaderClaimsError("INVALID_HEADER_VALUE")

        normalized_name = name.lower()
        normalized_value = _trim_ows(raw_value)

        claim_field = _IDENTITY_HEADERS.get(normalized_name)
        if claim_field is not None:
            if normalized_name in seen_identity:
                raise HeaderClaimsError("DUPLICATE_IDENTITY_HEADER")
            seen_identity.add(normalized_name)

            if claim_field == "user_id_claim":
                if (
                    not normalized_value
                    or any(byte > 0x7F for byte in normalized_value)
                    or any(byte in (0x09, 0x20, 0x2C) for byte in normalized_value)
                ):
                    raise HeaderClaimsError("INVALID_USER_ID_CLAIM")
                user_id_claim = normalized_value
            elif claim_field == "admin_claim":
                if normalized_value == b"true":
                    admin_claim = True
                elif normalized_value == b"false":
                    admin_claim = False
                else:
                    raise HeaderClaimsError("INVALID_ADMIN_CLAIM")
            else:
                if not normalized_value:
                    raise HeaderClaimsError("INVALID_USERNAME_CLAIM")
                username_claim = normalized_value

        if normalized_name == b"content-length":
            if seen_content_length:
                raise HeaderClaimsError("DUPLICATE_CONTENT_LENGTH")
            if seen_transfer_encoding:
                raise HeaderClaimsError("CONTENT_LENGTH_TRANSFER_ENCODING_CONFLICT")
            seen_content_length = True
            if not normalized_value or any(byte < 0x30 or byte > 0x39 for byte in normalized_value):
                raise HeaderClaimsError("INVALID_CONTENT_LENGTH")

        elif normalized_name == b"transfer-encoding":
            if seen_transfer_encoding:
                raise HeaderClaimsError("DUPLICATE_TRANSFER_ENCODING")
            if seen_content_length:
                raise HeaderClaimsError("CONTENT_LENGTH_TRANSFER_ENCODING_CONFLICT")
            seen_transfer_encoding = True

    return UntrustedIdentityClaims(
        user_id_claim=user_id_claim,
        admin_claim=admin_claim,
        username_claim=username_claim,
    )


def read_peer_credentials(sock: object) -> PeerCredentialsMetadata:
    """Read Linux SO_PEERCRED from a connected AF_UNIX SOCK_STREAM socket.

    This performs a read-only getsockopt call. It does not create, connect,
    accept, read from, write to, or close the supplied socket.
    """
    if sys.platform != "linux":
        raise PeerCredentialsError("UNSUPPORTED_PLATFORM")

    try:
        family = sock.family  # type: ignore[attr-defined]
        sock_type = sock.type  # type: ignore[attr-defined]
    except Exception:
        raise PeerCredentialsError("INVALID_SOCKET_OBJECT") from None

    if family != socket.AF_UNIX:
        raise PeerCredentialsError("UNSUPPORTED_SOCKET_FAMILY")
    if sock_type != socket.SOCK_STREAM:
        raise PeerCredentialsError("UNSUPPORTED_SOCKET_TYPE")

    peercred_option = getattr(socket, "SO_PEERCRED", None)
    if type(peercred_option) is not int:
        raise PeerCredentialsError("PEER_CREDENTIALS_UNSUPPORTED")

    try:
        getsockopt = sock.getsockopt  # type: ignore[attr-defined]
    except Exception:
        raise PeerCredentialsError("INVALID_SOCKET_OBJECT") from None
    if not callable(getsockopt):
        raise PeerCredentialsError("INVALID_SOCKET_OBJECT")

    try:
        payload = getsockopt(socket.SOL_SOCKET, peercred_option, _UCRED_SIZE)
    except OSError:
        raise PeerCredentialsError("PEER_CREDENTIALS_SYSCALL_FAILED") from None
    except Exception:
        raise PeerCredentialsError("PEER_CREDENTIALS_SYSCALL_FAILED") from None

    if type(payload) is not bytes or len(payload) != _UCRED_SIZE:
        raise PeerCredentialsError("INVALID_PEER_CREDENTIALS_PAYLOAD")

    try:
        pid, uid, gid = struct.unpack("=iII", payload)
    except struct.error:
        raise PeerCredentialsError("INVALID_PEER_CREDENTIALS_PAYLOAD") from None

    if pid <= 0:
        raise PeerCredentialsError("INVALID_PEER_PID")
    if uid == _UINT32_RESERVED:
        raise PeerCredentialsError("INVALID_PEER_UID")
    if gid == _UINT32_RESERVED:
        raise PeerCredentialsError("INVALID_PEER_GID")

    return PeerCredentialsMetadata(pid=pid, uid=uid, gid=gid)


# ------------------------------------------------------------ channel policy --
#
# Fail-closed policy values for the trusted ingress channel. These constants
# describe the policy the gateway applies; they do not authenticate anything.
# The official fnOS desktop gateway is a root process, so the allow-list
# defaults to uid 0 only, and a root-only list defaults to mode 0600. A shared
# list defaults to 0660 because the group can never be wider than the explicit
# uid allow-list. 0666/0777 are rejected by construction.

DEFAULT_PEER_UIDS = frozenset({0})
MAX_PEER_ALLOW_ENTRIES = 8
DEFAULT_SOCKET_MODE_ROOT_ONLY = 0o600
DEFAULT_SOCKET_MODE_SHARED = 0o660

_MAX_PEER_UID = 4294967295
_PEER_UID_RE = re.compile(r"[0-9]{1,10}")
_SOCKET_MODE_TOKENS = {
    "0600": DEFAULT_SOCKET_MODE_ROOT_ONLY,
    "600": DEFAULT_SOCKET_MODE_ROOT_ONLY,
    "0660": DEFAULT_SOCKET_MODE_SHARED,
    "660": DEFAULT_SOCKET_MODE_SHARED,
}


class TrustedChannelError(ValueError):
    """A stable, input-redacted trusted-channel policy error.

    ``code`` is one of the fixed policy/authorization strings below and never
    contains an input value: ``peer_allow_invalid`` / ``socket_mode_invalid`` /
    ``peer_uid_not_allowed`` / ``user_id_claim_missing``.

    A failed peer-credential read is not one of these codes: it raises
    ``PeerCredentialsError``, which the caller reports in the two documented
    log classes ``peer_credentials_unsupported`` / ``peer_credentials_invalid``.
    """

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def parse_peer_allow_list(raw: object) -> frozenset[int]:
    """Parse a peer uid allow-list from its text configuration value.

    ``None`` means the variable is unset and falls back to root-only. The value
    must be a comma-separated list of plain ASCII decimal uids (no sign, no
    whitespace, no empty item); duplicates are ignored and the remaining count
    must be 1..8. Every rejection is ``peer_allow_invalid``.
    """
    if raw is None:
        return DEFAULT_PEER_UIDS
    if type(raw) is not str:
        raise TrustedChannelError("peer_allow_invalid")
    entries: set[int] = set()
    for item in raw.split(","):
        if _PEER_UID_RE.fullmatch(item) is None:
            raise TrustedChannelError("peer_allow_invalid")
        value = int(item, 10)
        if value > _MAX_PEER_UID:
            raise TrustedChannelError("peer_allow_invalid")
        entries.add(value)
    if not 1 <= len(entries) <= MAX_PEER_ALLOW_ENTRIES:
        raise TrustedChannelError("peer_allow_invalid")
    return frozenset(entries)


def parse_socket_mode(raw: object, allow_uids: frozenset[int]) -> int:
    """Resolve the socket permission bits for the selected allow-list.

    ``None`` derives the mode from the allow-list (0600 for root-only, 0660
    otherwise). Explicit values are limited to 0600/0660 spellings so a
    world-writable mode can never be configured through the environment.
    """
    if raw is None:
        return (
            DEFAULT_SOCKET_MODE_ROOT_ONLY
            if allow_uids == DEFAULT_PEER_UIDS
            else DEFAULT_SOCKET_MODE_SHARED
        )
    if type(raw) is not str:
        raise TrustedChannelError("socket_mode_invalid")
    mode = _SOCKET_MODE_TOKENS.get(raw)
    if mode is None:
        raise TrustedChannelError("socket_mode_invalid")
    return mode


def evaluate_channel(
    peer: PeerCredentialsMetadata,
    allow_uids: frozenset[int],
) -> None:
    """L2 decision: the kernel-reported peer uid must be explicitly allowed.

    This is a policy check over kernel metadata, never an authentication
    inference: a missing uid in the allow-list is the only rejection.
    """
    if peer.uid not in allow_uids:
        raise TrustedChannelError("peer_uid_not_allowed")


def require_user_id(claims: UntrustedIdentityClaims) -> None:
    """L4 prerequisite: a syntactically valid user-id claim must be present.

    The claim is still untrusted; this only rejects the absence of the claim.
    """
    if claims.user_id_claim is None:
        raise TrustedChannelError("user_id_claim_missing")
