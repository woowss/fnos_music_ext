"""Real Linux-kernel checks for the trusted ingress channel (G0).

These tests exercise the kernel boundary only: ``SO_PEERCRED`` over a real
``AF_UNIX`` stream socket and the accept-time admission in ``serve``. They do
not deploy, do not change host socket permissions, and do not prove that the
official fnOS gateway is the peer. See ``docs/G0_TRUSTED_INGRESS.md``.
"""
from __future__ import annotations

import os
import socket
import stat
import sys
import threading
import time

import pytest

from proxy import gateway_identity as gi
from proxy import webui_gateway as gw


pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="AF_UNIX SO_PEERCRED requires Linux")


def _wait_for_socket(path, mode: int, *, timeout: float = 5.0):
    """Wait until the gateway socket exists with the policy mode re-asserted."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            info = os.stat(path)
        except FileNotFoundError:
            time.sleep(0.01)
            continue
        if stat.S_ISSOCK(info.st_mode) and stat.S_IMODE(info.st_mode) == mode:
            return info
        time.sleep(0.01)
    raise AssertionError(f"gateway socket did not become ready: {path!r}")


def _connect(path, *, timeout: float = 5.0) -> socket.socket:
    """Connect to a listening gateway socket, tolerating the bind→listen gap."""
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(0.5)
        try:
            client.connect(str(path))
        except OSError as exc:
            last = exc
            client.close()
            time.sleep(0.01)
            continue
        client.settimeout(10)
        return client
    raise AssertionError(f"could not connect to gateway socket: {last!r}")


def _start_serve(sock_path, upstream: "tuple[str, int]", policy, *, max_accepts: int = 1):
    errors = []

    def run() -> None:
        try:
            gw.serve(
                sock_path, upstream, policy=policy, _max_accepts=max_accepts,
            )
        except BaseException as exc:  # noqa: BLE001 — asserted by the caller
            errors.append(exc)

    thread = threading.Thread(target=run, daemon=True, name="fnmusic-gateway-test")
    thread.start()
    return thread, errors


def _read_all(client: socket.socket) -> bytes:
    data = bytearray()
    while True:
        chunk = client.recv(4096)
        if not chunk:
            return bytes(data)
        data.extend(chunk)


def test_real_socketpair_reports_this_process_peer_credentials():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        peer = gi.read_peer_credentials(right)
        assert peer.pid == os.getpid()
        assert peer.uid == os.geteuid()
        assert peer.gid == os.getegid()
    finally:
        left.close()
        right.close()


def test_evaluate_channel_decides_on_the_kernel_reported_uid():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        peer = gi.read_peer_credentials(right)
        assert gi.evaluate_channel(peer, frozenset({peer.uid})) is None
        other_uid = peer.uid + 1
        if other_uid >= 0xFFFFFFFF:
            other_uid = 0
        with pytest.raises(gi.TrustedChannelError) as raised:
            gi.evaluate_channel(peer, frozenset({other_uid}))
        assert raised.value.code == "peer_uid_not_allowed"
    finally:
        left.close()
        right.close()


def test_serve_admits_an_allowed_peer_and_forwards_to_the_upstream(tmp_path):
    uid = os.geteuid()
    policy = (frozenset({uid}), 0o600)
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(4)
    upstream.settimeout(10)
    upstream_port = upstream.getsockname()[1]
    received: "list[bytes]" = []

    def accept_once() -> None:
        try:
            connection, _address = upstream.accept()
        except OSError:
            return
        with connection:
            connection.settimeout(10)
            data = bytearray()
            while b"\r\n\r\n" not in data:
                chunk = connection.recv(4096)
                if not chunk:
                    return
                data.extend(chunk)
            received.append(bytes(data))
            connection.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Length: 7\r\n"
                b"Connection: close\r\n\r\nstub-ok"
            )

    upstream_thread = threading.Thread(target=accept_once, daemon=True)
    upstream_thread.start()
    sock_path = tmp_path / "gateway.sock"
    thread, errors = _start_serve(
        sock_path, ("127.0.0.1", upstream_port), policy, max_accepts=1)
    request = (
        b"GET /app/fnmusic-ext/ HTTP/1.1\r\n"
        b"Host: localhost\r\n"
        b"X-Trim-Userid: 1000\r\n"
        b"Connection: close\r\n"
        b"\r\n"
    )
    try:
        _wait_for_socket(sock_path, policy[1])
        client = _connect(sock_path)
        try:
            client.sendall(request)
            data = _read_all(client)
        finally:
            client.close()
        thread.join(10)
        upstream_thread.join(10)
    finally:
        upstream.close()

    assert errors == []
    assert not thread.is_alive()
    assert received and received[0] == request
    assert data.split(b"\r\n", 1)[0] == b"HTTP/1.1 200 OK"
    assert b"stub-ok" in data


def test_serve_rejects_an_unlisted_peer_without_reading_or_upstream_io(tmp_path):
    uid = os.geteuid()
    denied_uid = uid + 1
    if denied_uid >= 0xFFFFFFFF:
        denied_uid = 0
    policy = (frozenset({denied_uid}), 0o600)
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(4)
    upstream.settimeout(0.5)
    upstream_port = upstream.getsockname()[1]
    sock_path = tmp_path / "gateway.sock"
    thread, errors = _start_serve(
        sock_path, ("127.0.0.1", upstream_port), policy, max_accepts=1)
    try:
        _wait_for_socket(sock_path, policy[1])
        client = _connect(sock_path)
        try:
            # The rejected connection is closed by the gateway before any byte
            # is read from this client: EOF without a timeout.
            assert client.recv(1) == b""
        finally:
            client.close()
        thread.join(10)
    finally:
        try:
            with pytest.raises(socket.timeout):
                upstream.accept()
        finally:
            upstream.close()

    assert errors == []
    assert not thread.is_alive()


def test_serve_replaces_a_stale_world_writable_socket_with_policy_mode(tmp_path):
    uid = os.geteuid()
    policy = (frozenset({uid}), 0o600)
    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(1)
    upstream_port = upstream.getsockname()[1]
    sock_path = tmp_path / "gateway.sock"
    # A stale world-writable socket file must be replaced, not reused, and the
    # policy mode must be re-asserted on every start.
    sock_path.write_bytes(b"stale")
    os.chmod(sock_path, 0o666)
    thread, errors = _start_serve(
        sock_path, ("127.0.0.1", upstream_port), policy, max_accepts=1)
    try:
        info = _wait_for_socket(sock_path, 0o600)
        assert stat.S_IMODE(info.st_mode) == 0o600
        client = _connect(sock_path)  # admitted; consumes the single accept
        client.close()
        thread.join(10)
    finally:
        upstream.close()

    assert errors == []
    assert not thread.is_alive()
