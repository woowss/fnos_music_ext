"""Synthetic tests for strict untrusted claims and read-only peer metadata."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
import socket
import struct
from types import MappingProxyType, SimpleNamespace

import pytest

from proxy import gateway_identity as gi


def _reason_code(error_type, call):
    with pytest.raises(error_type) as raised:
        call()
    return raised.value.reason_code, str(raised.value)


def test_missing_full_and_partial_claims_are_preserved_as_immutable_claims():
    missing = gi.parse_untrusted_identity([])
    assert missing == gi.UntrustedIdentityClaims(None, None, None)

    full = gi.parse_untrusted_identity([
        (b"X-Trim-UserID", b" \tuid:opaque-42\t "),
        (b"x-trim-isadmin", b" true "),
        (b"X-TRIM-USERNAME", b" Display\xff Name\t"),
    ])
    assert full == gi.UntrustedIdentityClaims(
        user_id_claim=b"uid:opaque-42",
        admin_claim=True,
        username_claim=b"Display\xff Name",
    )

    partial = gi.parse_untrusted_identity([(b"X-Trim-IsAdmin", b"false")])
    assert partial == gi.UntrustedIdentityClaims(None, False, None)
    assert [field.name for field in fields(full)] == [
        "user_id_claim", "admin_claim", "username_claim",
    ]
    assert not any(hasattr(full, name) for name in ("is_admin", "is_trusted", "allow"))
    with pytest.raises(FrozenInstanceError):
        full.admin_claim = False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (b"x-trim-userid", b"42"),
        (b"x-trim-isadmin", b"true"),
        (b"x-trim-username", b"name"),
    ],
)
def test_any_duplicate_identity_header_is_rejected_case_insensitively(name, value):
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([
            (name, value), (name.upper(), value),
        ]),
    )
    assert code == "DUPLICATE_IDENTITY_HEADER"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(b"true", True), (b"false", False), (b" \ttrue\t ", True), (b" false ", False)],
)
def test_admin_claim_accepts_only_exact_canonical_values_after_ows(raw, expected):
    result = gi.parse_untrusted_identity([(b"x-trim-isadmin", raw)])
    assert result.admin_claim is expected


@pytest.mark.parametrize("raw", [b"", b" ", b"\t", b"True", b"TRUE", b"False", b"1", b"yes"])
def test_admin_claim_rejects_noncanonical_values(raw):
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([(b"x-trim-isadmin", raw)]),
    )
    assert code == "INVALID_ADMIN_CLAIM"


@pytest.mark.parametrize(
    "raw",
    [b"", b" ", b"\t", b"user name", b"user\tname", b"user,name", b"user\xff"],
)
def test_user_id_claim_rejects_empty_whitespace_nonascii_and_comma(raw):
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([(b"x-trim-userid", raw)]),
    )
    assert code == "INVALID_USER_ID_CLAIM"


def test_user_id_claim_stays_opaque_and_only_ows_is_trimmed():
    result = gi.parse_untrusted_identity([(b"x-trim-userid", b" \topaque:/+._-42\t ")])
    assert result.user_id_claim == b"opaque:/+._-42"


def test_username_claim_is_nonempty_after_ows_and_keeps_unassumed_bytes():
    result = gi.parse_untrusted_identity([(b"x-trim-username", b" \tname\xff\t ")])
    assert result.username_claim == b"name\xff"
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([(b"x-trim-username", b" \t ")]),
    )
    assert code == "INVALID_USERNAME_CLAIM"


@pytest.mark.parametrize(
    "name",
    [b"", b"Bad Name", b"Bad:Name", b"Bad\x00Name", b"Bad\x80Name", b" Bad", b"Bad\t"],
)
def test_header_names_must_use_only_the_ascii_http_token_charset(name):
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([(name, b"value")]),
    )
    assert code == "INVALID_HEADER_NAME"


@pytest.mark.parametrize("control", [0x00, 0x01, 0x08, 0x0A, 0x0B, 0x0C, 0x0D, 0x1F, 0x7F])
def test_header_values_reject_controls_except_htab(control):
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([(b"x-test", b"a" + bytes([control]) + b"b")]),
    )
    assert code == "INVALID_HEADER_VALUE"


def test_header_values_allow_htab_and_obs_text_where_claim_rules_allow_them():
    result = gi.parse_untrusted_identity([
        (b"x-note", b"left\tright\xff"),
        (b"x-trim-username", b"left\tright\xff"),
    ])
    assert result.username_claim == b"left\tright\xff"


@pytest.mark.parametrize(
    "headers",
    [
        {b"x-trim-userid": b"42"},
        MappingProxyType({b"x-trim-userid": b"42"}),
        None,
        [(b"x-test",)],
        [[b"x-test", b"value"]],
        [("x-test", b"value")],
        [(b"x-test", "value")],
        [(b"x-test", bytearray(b"value"))],
    ],
)
def test_mappings_noniterables_and_malformed_pairs_have_stable_errors(headers):
    code, _ = _reason_code(
        gi.HeaderClaimsError, lambda: gi.parse_untrusted_identity(headers),
    )
    assert code == "INVALID_HEADER_INPUT"


def test_iterator_failure_is_redacted_and_uses_a_stable_code():
    marker = "SYNTHETIC_RAW_HEADER_MARKER"

    def broken_pairs():
        yield (b"x-test", b"ok")
        raise RuntimeError(marker)

    code, message = _reason_code(
        gi.HeaderClaimsError, lambda: gi.parse_untrusted_identity(broken_pairs()),
    )
    assert code == "INVALID_HEADER_INPUT"
    assert marker not in message


def test_header_pair_limit_stops_on_first_overlimit_item():
    consumed = []

    def many_pairs():
        for index in range(gi.MAX_HEADER_PAIRS + 20):
            consumed.append(index)
            yield (b"x", b"v")

    code, _ = _reason_code(
        gi.HeaderClaimsError, lambda: gi.parse_untrusted_identity(many_pairs()),
    )
    assert code == "TOO_MANY_HEADERS"
    assert len(consumed) == gi.MAX_HEADER_PAIRS + 1


def test_exact_header_byte_limit_is_accepted_and_accounting_excludes_framing():
    result = gi.parse_untrusted_identity([
        (b"x", b"a" * (gi.MAX_HEADER_BYTES - 1)),
    ])
    assert result == gi.UntrustedIdentityClaims(None, None, None)


def test_header_byte_limit_stops_on_first_overlimit_item():
    consumed = []

    def oversized_pairs():
        consumed.append("first")
        yield (b"x", b"a" * gi.MAX_HEADER_BYTES)
        consumed.append("second")
        yield (b"x", b"b")

    code, _ = _reason_code(
        gi.HeaderClaimsError, lambda: gi.parse_untrusted_identity(oversized_pairs()),
    )
    assert code == "HEADER_BYTES_LIMIT"
    assert consumed == ["first"]


@pytest.mark.parametrize("raw", [b"0", b"42", b"00042", b" \t42\t "])
def test_single_decimal_content_length_is_accepted_after_ows(raw):
    assert gi.parse_untrusted_identity([(b"Content-Length", raw)]) == (
        gi.UntrustedIdentityClaims(None, None, None)
    )


@pytest.mark.parametrize("raw", [b"", b" \t ", b"+1", b"-1", b"1,1", b"1 2", b"1\t2", b"\xff"])
def test_content_length_requires_nonempty_ascii_decimal_after_ows(raw):
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([(b"content-length", raw)]),
    )
    assert code == "INVALID_CONTENT_LENGTH"


def test_duplicate_content_length_is_rejected_even_when_identical_and_case_varied():
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([
            (b"Content-Length", b"42"), (b"content-length", b"42"),
        ]),
    )
    assert code == "DUPLICATE_CONTENT_LENGTH"


def test_duplicate_transfer_encoding_is_rejected_case_insensitively():
    code, _ = _reason_code(
        gi.HeaderClaimsError,
        lambda: gi.parse_untrusted_identity([
            (b"Transfer-Encoding", b"chunked"), (b"transfer-encoding", b"chunked"),
        ]),
    )
    assert code == "DUPLICATE_TRANSFER_ENCODING"


@pytest.mark.parametrize(
    "headers",
    [
        [(b"Content-Length", b"5"), (b"Transfer-Encoding", b"chunked")],
        [(b"Transfer-Encoding", b"chunked"), (b"Content-Length", b"5")],
    ],
)
def test_transfer_encoding_and_content_length_conflict_in_either_order(headers):
    code, _ = _reason_code(
        gi.HeaderClaimsError, lambda: gi.parse_untrusted_identity(headers),
    )
    assert code == "CONTENT_LENGTH_TRANSFER_ENCODING_CONFLICT"


def test_single_transfer_encoding_is_not_treated_as_a_complete_http_parse():
    assert gi.parse_untrusted_identity([(b"transfer-encoding", b"chunked")]) == (
        gi.UntrustedIdentityClaims(None, None, None)
    )


class StubUnixStream:
    # Linux AF_UNIX and SOCK_STREAM values, also usable as inert stubs on Windows.
    family = 1
    type = 1

    def __init__(self, payload):
        self.payload = payload
        self.calls = []
        self.forbidden_calls = []
        self.state = {"sent": 0, "received": 0, "closed": False}

    def getsockopt(self, level, option, length):
        self.calls.append((level, option, length))
        return self.payload

    def _forbidden(self, name, *_args, **_kwargs):
        self.forbidden_calls.append(name)
        raise AssertionError("forbidden socket operation")

    def bind(self, *args, **kwargs):
        return self._forbidden("bind", *args, **kwargs)

    def connect(self, *args, **kwargs):
        return self._forbidden("connect", *args, **kwargs)

    def accept(self, *args, **kwargs):
        return self._forbidden("accept", *args, **kwargs)

    def recv(self, *args, **kwargs):
        return self._forbidden("recv", *args, **kwargs)

    def send(self, *args, **kwargs):
        return self._forbidden("send", *args, **kwargs)

    def close(self, *args, **kwargs):
        return self._forbidden("close", *args, **kwargs)

    def setsockopt(self, *args, **kwargs):
        return self._forbidden("setsockopt", *args, **kwargs)


def _force_linux_peercred(monkeypatch):
    monkeypatch.setattr(gi, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(socket, "SOCK_STREAM", 1, raising=False)
    monkeypatch.setattr(socket, "SOCK_DGRAM", 2, raising=False)
    monkeypatch.setattr(socket, "SOL_SOCKET", 1, raising=False)
    monkeypatch.setattr(socket, "SO_PEERCRED", 17, raising=False)


def test_peer_credentials_accept_high_unsigned_ids_without_authentication_inference(monkeypatch):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 123, 0xFFFFFFFE, 0xFEDCBA98))
    before = dict(sock.state)

    result = gi.read_peer_credentials(sock)

    assert result == gi.PeerCredentialsMetadata(123, 0xFFFFFFFE, 0xFEDCBA98)
    assert [field.name for field in fields(result)] == ["pid", "uid", "gid"]
    assert not any(hasattr(result, name) for name in ("is_admin", "is_trusted", "allow"))
    assert sock.calls == [(socket.SOL_SOCKET, 17, 12)]
    assert sock.forbidden_calls == []
    assert sock.state == before


def test_root_peer_credentials_are_only_metadata(monkeypatch):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 1, 0, 0))

    assert gi.read_peer_credentials(sock) == gi.PeerCredentialsMetadata(1, 0, 0)
    assert sock.forbidden_calls == []


def test_unsupported_platform_fails_closed_without_socket_calls(monkeypatch):
    monkeypatch.setattr(gi, "sys", SimpleNamespace(platform="win32"))
    sock = StubUnixStream(struct.pack("=iII", 1, 0, 0))

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "UNSUPPORTED_PLATFORM"
    assert sock.calls == []


def test_non_unix_socket_fails_before_syscall(monkeypatch):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 1, 0, 0))
    sock.family = socket.AF_INET

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "UNSUPPORTED_SOCKET_FAMILY"
    assert sock.calls == []


def test_non_stream_socket_fails_before_syscall(monkeypatch):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 1, 0, 0))
    sock.type = socket.SOCK_DGRAM

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "UNSUPPORTED_SOCKET_TYPE"
    assert sock.calls == []


@pytest.mark.parametrize("payload", [b"", b"\x00" * 11, b"\x00" * 13, (1, 2, 3)])
def test_peer_credentials_require_exact_native_ucred_payload(monkeypatch, payload):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(payload)

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "INVALID_PEER_CREDENTIALS_PAYLOAD"
    assert sock.calls == [(socket.SOL_SOCKET, 17, 12)]


@pytest.mark.parametrize("pid", [0, -1])
def test_peer_credentials_reject_nonpositive_pid(monkeypatch, pid):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", pid, 0, 0))

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "INVALID_PEER_PID"


def test_peer_credentials_reject_reserved_unsigned_uid(monkeypatch):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 1, 0xFFFFFFFF, 0))

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "INVALID_PEER_UID"


def test_peer_credentials_reject_reserved_unsigned_gid(monkeypatch):
    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 1, 0, 0xFFFFFFFF))

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "INVALID_PEER_GID"


def test_peer_credentials_syscall_failure_is_stable_and_redacted(monkeypatch):
    _force_linux_peercred(monkeypatch)
    marker = "SYNTHETIC_SOCKET_ERROR_MARKER"
    sock = StubUnixStream(b"")

    def fail_getsockopt(_level, _option, _length):
        raise OSError(marker)

    sock.getsockopt = fail_getsockopt
    code, message = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "PEER_CREDENTIALS_SYSCALL_FAILED"
    assert marker not in message


def test_peer_credentials_reader_performs_no_other_io_or_socket_operations(monkeypatch):
    import builtins
    import os
    from pathlib import Path
    import subprocess

    _force_linux_peercred(monkeypatch)
    sock = StubUnixStream(struct.pack("=iII", 7, 1000, 1000))
    before = dict(sock.state)
    forbidden_calls = []

    def forbidden(name):
        def record(*_args, **_kwargs):
            forbidden_calls.append(name)
            raise AssertionError("forbidden I/O operation")
        return record

    for name in ("socket", "create_connection", "create_server", "getaddrinfo"):
        if hasattr(socket, name):
            monkeypatch.setattr(socket, name, forbidden(f"socket.{name}"))
    for target, name in (
        (builtins, "open"), (os, "open"), (os, "getenv"), (os, "system"),
        (os, "popen"), (Path, "open"), (subprocess, "Popen"),
    ):
        if hasattr(target, name):
            monkeypatch.setattr(target, name, forbidden(f"{target.__name__}.{name}"))

    assert gi.read_peer_credentials(sock) == gi.PeerCredentialsMetadata(7, 1000, 1000)
    assert forbidden_calls == []
    assert sock.calls == [(socket.SOL_SOCKET, 17, 12)]
    assert sock.forbidden_calls == []
    assert sock.state == before


def test_peer_credentials_invalid_socket_object_fails_closed(monkeypatch):
    _force_linux_peercred(monkeypatch)

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(object()))

    assert code == "INVALID_SOCKET_OBJECT"


def test_peer_credentials_unavailable_platform_constant_fails_closed(monkeypatch):
    _force_linux_peercred(monkeypatch)
    monkeypatch.delattr(socket, "SO_PEERCRED")
    sock = StubUnixStream(struct.pack("=iII", 1, 0, 0))

    code, _ = _reason_code(gi.PeerCredentialsError, lambda: gi.read_peer_credentials(sock))

    assert code == "PEER_CREDENTIALS_UNSUPPORTED"
    assert sock.calls == []
