"""Linux process-level integration for the isolated gateway observer.

This test is intended to live at ``proxy/tests/test_gateway_process_linux.py``
in fnmusic-ext. It deliberately calls ``serve`` in a disposable child process
instead of the production ``main``/``prepare_output_fd`` paths, which touch
fixed ``/run`` state and daemonize.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import select
import socket
import subprocess
import sys
import threading
import time
import uuid

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="requires Linux SO_PEERCRED")


_CHILD_SOURCE = r"""
import os
from pathlib import Path
import socket
import sys
import threading
import time

repo_root, socket_name, upstream_host, upstream_port, marker, output_fd = sys.argv[1:]
sys.path.insert(0, repo_root)
from proxy import gateway_observation, webui_gateway

socket_path = Path(socket_name)
observer = gateway_observation.GatewayObserver(marker, 15, int(output_fd))
server_thread = threading.Thread(
    target=webui_gateway.serve,
    args=(socket_path, (upstream_host, int(upstream_port)), observer),
    daemon=True,
)
server_thread.start()

deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(0.25)
    try:
        probe.connect(str(socket_path))
    except OSError:
        probe.close()
        time.sleep(0.02)
        continue
    probe.close()
    print("READY", flush=True)
    break
else:
    raise SystemExit("gateway socket did not become ready")

server_thread.join()
"""


_COOKIE_SECRET = b"cookie-value-must-not-be-recorded"
_AUTH_SECRET = b"authorization-value-must-not-be-recorded"
_USERNAME_SECRET = b"username-value-must-not-be-recorded"
_BODY_SECRET = b"body-value-must-not-be-recorded"
_RESPONSE = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nOK"


def _repo_root() -> Path:
    configured = os.environ.get("FNMUSIC_EXT_TEST_SOURCE_ROOT")
    if configured:
        root = Path(configured).resolve()
        if (root / "proxy" / "webui_gateway.py").is_file():
            return root
        raise RuntimeError("FNMUSIC_EXT_TEST_SOURCE_ROOT has no proxy/webui_gateway.py")
    for parent in Path(__file__).resolve().parents:
        if (parent / "proxy" / "webui_gateway.py").is_file():
            return parent
    raise RuntimeError("cannot locate fnmusic-ext source root")


def _read_http_request(conn: socket.socket) -> bytes:
    data = bytearray()
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(4096)
        if not chunk:
            raise AssertionError("upstream received an incomplete HTTP header")
        data.extend(chunk)
    head, _, body = bytes(data).partition(b"\r\n\r\n")
    content_length = 0
    for line in head.split(b"\r\n")[1:]:
        name, separator, value = line.partition(b":")
        if separator and name.strip().lower() == b"content-length":
            content_length = int(value.strip())
            break
    body = bytearray(body)
    while len(body) < content_length:
        chunk = conn.recv(4096)
        if not chunk:
            raise AssertionError("upstream received an incomplete HTTP body")
        body.extend(chunk)
    return head + b"\r\n\r\n" + bytes(body[:content_length])


def _request(target: str) -> bytes:
    headers = (
        f"GET {target} HTTP/1.1\r\n".encode("ascii")
        + b"Host: 127.0.0.1\r\n"
        + b"X-Trim-Userid: 424242\r\n"
        + b"X-Trim-Isadmin: true\r\n"
        + b"X-Trim-Username: " + _USERNAME_SECRET + b"\r\n"
        + b"Cookie: " + _COOKIE_SECRET + b"\r\n"
        + b"Authorization: Bearer " + _AUTH_SECRET + b"\r\n"
        + f"Content-Length: {len(_BODY_SECRET)}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
    )
    return headers + _BODY_SECRET


def _send_unix_request(socket_path: Path, request: bytes) -> bytes:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(5)
    try:
        client.connect(str(socket_path))
        client.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            chunk = client.recv(4096)
            if not chunk:
                raise AssertionError("gateway closed before a complete HTTP response header")
            response.extend(chunk)
        head, _, body = bytes(response).partition(b"\r\n\r\n")
        content_length = None
        for line in head.split(b"\r\n")[1:]:
            name, separator, value = line.partition(b":")
            if separator and name.strip().lower() == b"content-length":
                content_length = int(value.strip())
                break
        if content_length is None:
            raise AssertionError("gateway response has no Content-Length")
        body = bytearray(body)
        while len(body) < content_length:
            chunk = client.recv(4096)
            if not chunk:
                raise AssertionError("gateway closed before the complete HTTP response body")
            body.extend(chunk)
        return head + b"\r\n\r\n" + bytes(body[:content_length])
    finally:
        client.close()


def _read_records(output_path: Path) -> list[dict]:
    try:
        text = output_path.read_text(encoding="ascii")
    except FileNotFoundError:
        return []
    complete_lines = [line[:-1] for line in text.splitlines(keepends=True) if line.endswith("\n")]
    return [json.loads(line) for line in complete_lines if line]


def _wait_for_events(output_path: Path, expected: set[str], timeout: float = 5.0) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = _read_records(output_path)
        if expected.issubset({record.get("event") for record in records}):
            return records
        time.sleep(0.02)
    raise AssertionError(f"observer did not write expected events: {sorted(expected)}")


def _child_environment(tmp_path: Path) -> dict[str, str]:
    # Do not pass the caller's full environment (which may contain account
    # tokens or host configuration) into the isolated gateway subprocess.
    allowed = ("PATH", "LANG", "LC_ALL", "LD_LIBRARY_PATH")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    env.update({
        "HOME": str(isolated_home),
        "TMPDIR": str(tmp_path),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "FNMUSIC_HOME": str(isolated_home / "fnmusic"),
        "FNMUSIC_LOG_DIR": str(tmp_path / "logs"),
    })
    return env


def test_real_gateway_process_observes_one_matching_request_and_forwards_unchanged(tmp_path):
    marker = uuid.uuid4().hex
    other_marker = uuid.uuid4().hex
    socket_path = tmp_path / "gateway.sock"
    output_path = tmp_path / "observer.jsonl"
    output_fd = os.open(output_path, os.O_CREAT | os.O_TRUNC | os.O_RDWR, 0o600)

    upstream = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    upstream.bind(("127.0.0.1", 0))
    upstream.listen(4)
    upstream.settimeout(2)
    upstream_requests: queue.Queue[bytes] = queue.Queue()
    upstream_errors: queue.Queue[BaseException] = queue.Queue()
    stop_upstream = threading.Event()

    def fake_upstream() -> None:
        try:
            while not stop_upstream.is_set():
                try:
                    conn, _address = upstream.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with conn:
                    conn.settimeout(5)
                    upstream_requests.put(_read_http_request(conn))
                    conn.sendall(_RESPONSE)
        except BaseException as exc:
            upstream_errors.put(exc)

    upstream_thread = threading.Thread(target=fake_upstream, daemon=True)
    upstream_thread.start()

    child = None
    try:
        child = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-c",
                _CHILD_SOURCE,
                str(_repo_root()),
                str(socket_path),
                "127.0.0.1",
                str(upstream.getsockname()[1]),
                marker,
                str(output_fd),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            pass_fds=(output_fd,),
            cwd=str(_repo_root()),
            env=_child_environment(tmp_path),
        )
        assert child.stdout is not None
        ready = select.select([child.stdout], [], [], 10)[0]
        assert ready, "gateway child did not report readiness"
        assert child.stdout.readline().rstrip() == b"READY"
        records = _wait_for_events(output_path, {"ready"})
        assert [record["event"] for record in records] == ["ready"]

        expected_requests = [
            _request(f"/app/fnmusic-ext/healthz?g0_observe={other_marker}"),
            _request(f"/app/fnmusic-ext/healthz?g0_observe={marker}"),
            _request(f"/app/fnmusic-ext/healthz?g0_observe={marker}"),
        ]
        for request in expected_requests:
            assert _send_unix_request(socket_path, request) == _RESPONSE

        forwarded = [upstream_requests.get(timeout=3) for _ in expected_requests]
        assert forwarded == expected_requests
        assert upstream_errors.empty()

        records = _wait_for_events(output_path, {"ready", "observation"})
        assert [record["event"] for record in records] == ["ready", "observation"]
        observation = records[1]
        assert observation["diagnostic_only"] is True
        assert observation["marker"] == marker
        assert observation["header_result"] == "OK"
        assert observation["peer_pid"] == os.getpid()
        assert observation["peer_uid"] == os.getuid()
        assert observation["peer_gid"] == os.getgid()
        assert observation["peer_error"] is None
        assert observation["user_id_count"] == 1
        assert observation["admin_count"] == 1
        assert observation["username_count"] == 1

        serialized = output_path.read_bytes()
        for secret in (_COOKIE_SECRET, _AUTH_SECRET, _USERNAME_SECRET, _BODY_SECRET):
            assert secret not in serialized
        assert b"424242" not in serialized
        assert b'"admin"' not in serialized
        assert b'"username"' not in serialized
        assert b'"cookie"' not in serialized
        assert b'"authorization"' not in serialized
        assert b'"body"' not in serialized
    finally:
        child_failure = None
        if child is not None:
            if child.poll() is None:
                child.kill()
            try:
                _stdout, stderr = child.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                _stdout, stderr = child.communicate(timeout=5)
            if child.returncode not in (0, -9, 137):
                child_failure = f"gateway child exited unexpectedly: {stderr!r}"
        os.close(output_fd)
        stop_upstream.set()
        upstream.close()
        upstream_thread.join(timeout=4)
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass
        if child_failure:
            pytest.fail(child_failure)
        if upstream_thread.is_alive():
            pytest.fail("fake upstream thread did not stop")
