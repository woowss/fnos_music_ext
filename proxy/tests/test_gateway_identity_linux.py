"""Real Linux-kernel checks for read-only AF_UNIX peer credentials."""
from __future__ import annotations

import json
import socket
import subprocess
import sys

import pytest

from proxy import gateway_identity as gi


_CHILD_SOURCE = r"""
import json
import os
import socket
import sys

connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.settimeout(10)
connection.connect(sys.argv[1])
connection.sendall(
    b"GET /admin HTTP/1.1\r\n"
    b"X-Trim-Userid: 4294967294\r\n"
    b"X-Trim-IsAdmin: true\r\n"
    b"X-Trim-Username: forged\r\n\r\n"
    b"application-payload"
)
print(json.dumps({"pid": os.getpid(), "uid": os.geteuid(), "gid": os.getegid()}), flush=True)
if connection.recv(3) != b"ACK":
    raise SystemExit("ack mismatch")
connection.close()
"""

_REQUEST_BYTES = (
    b"GET /admin HTTP/1.1\r\n"
    b"X-Trim-Userid: 4294967294\r\n"
    b"X-Trim-IsAdmin: true\r\n"
    b"X-Trim-Username: forged\r\n\r\n"
    b"application-payload"
)


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    received = bytearray()
    while len(received) < size:
        chunk = sock.recv(size - len(received))
        if not chunk:
            break
        received.extend(chunk)
    return bytes(received)


@pytest.mark.skipif(sys.platform != "linux", reason="SO_PEERCRED requires Linux")
def test_real_accepted_socket_reports_kernel_peer_without_consuming_request_bytes(tmp_path):
    socket_path = tmp_path / "peer.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    accepted = None
    child = None
    try:
        listener.bind(str(socket_path))
        listener.listen(1)
        listener.settimeout(10)
        child = subprocess.Popen(
            [sys.executable, "-c", _CHILD_SOURCE, str(socket_path)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            close_fds=True,
        )

        accepted, _ = listener.accept()
        accepted.settimeout(10)
        peer = gi.read_peer_credentials(accepted)

        assert peer.pid == child.pid
        assert accepted.fileno() >= 0
        assert _recv_exact(accepted, len(_REQUEST_BYTES)) == _REQUEST_BYTES
        accepted.sendall(b"ACK")

        stdout, stderr = child.communicate(timeout=10)
        assert child.returncode == 0, stderr
        child_identity = json.loads(stdout)
        assert peer.pid == child_identity["pid"]
        assert peer.uid == child_identity["uid"]
        assert peer.gid == child_identity["gid"]
        assert accepted.fileno() >= 0
    finally:
        try:
            if accepted is not None:
                accepted.close()
        finally:
            try:
                listener.close()
            finally:
                try:
                    if child is not None:
                        if child.poll() is None:
                            try:
                                child.kill()
                            except ProcessLookupError:
                                pass
                        child.communicate(timeout=10)
                finally:
                    try:
                        socket_path.unlink()
                    except FileNotFoundError:
                        pass
