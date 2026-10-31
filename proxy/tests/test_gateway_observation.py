"""Synthetic tests for default-off one-shot gateway observation.

All sockets, file descriptors, clocks, and threads in this module are stubs;
the tests do not create real sockets or write files.
"""
from __future__ import annotations

import errno
import json
import os
import queue
import socket
import stat
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from proxy import gateway_observation as go
from proxy import webui_gateway as gw


MARKER = "0123456789abcdef0123456789abcdef"
TARGET = b"/app/fnmusic-ext/healthz?g0_observe=" + MARKER.encode("ascii")


class NoStartThread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True


class FakeClock:
    def __init__(self, value=10.0):
        self.value = value

    def __call__(self):
        return self.value


class FakeClient:
    family = getattr(socket, "AF_UNIX", 1)
    type = getattr(socket, "SOCK_STREAM", 1)

    def __init__(self):
        self.calls = []


def _head(headers=b"", body=b"", version=b"HTTP/1.1"):
    return b"GET " + TARGET + b" " + version + b"\r\n" + headers + b"\r\n" + body


def _make_observer(monkeypatch, *, clock=None, seconds=60):
    observer = go.GatewayObserver(
        MARKER,
        seconds,
        7,
        clock=clock or FakeClock(),
        thread_factory=NoStartThread,
    )
    observer.start()
    return observer


def _capture_writer(monkeypatch, observer):
    output: list[bytes] = []
    closed: list[int] = []

    def write(fd, data):
        assert fd == 7
        output.append(bytes(data))
        return len(data)

    monkeypatch.setattr(go.os, "write", write)
    monkeypatch.setattr(go.os, "close", closed.append)
    return output, closed


def test_default_args_remain_off_and_do_not_prepare_diagnostics(monkeypatch):
    events = []

    class FakePID:
        parent = SimpleNamespace(mkdir=lambda **_kwargs: events.append("pid-parent"))

        def __init__(self):
            self.value = None

        def write_text(self, value):
            self.value = value
            events.append(("pid-write", value))

        def read_text(self, **_kwargs):
            return self.value or "404"

        def unlink(self):
            events.append("pid-unlink")

    fake_pid = FakePID()
    monkeypatch.setattr(gw, "PID_FILE", fake_pid)
    monkeypatch.setattr(gw, "socket_path_for", lambda _base: events.append("path") or "stub-path")
    monkeypatch.setattr(gw, "_stop_previous", lambda: events.append("stop-previous"))
    monkeypatch.setattr(gw, "_daemonize", lambda: events.append("daemonize"))
    monkeypatch.setattr(gw, "serve", lambda path: events.append(("serve", path)))
    monkeypatch.setattr(
        go, "prepare_output_fd",
        lambda _marker: pytest.fail("default-off startup must not prepare output"),
    )
    monkeypatch.setattr(
        go, "GatewayObserver",
        lambda *_args, **_kwargs: pytest.fail("default-off startup must not construct observer"),
    )

    assert gw.main([]) == 0
    assert events == ["path", "stop-previous", "daemonize", "pid-parent",
                      ("pid-write", str(os.getpid())), ("serve", "stub-path"), "pid-unlink"]


@pytest.mark.parametrize(
    "args",
    [
        ["--g0-observe-seconds", "60"],
        ["--g0-observe-marker", "UPPERCASE0123456789abcdef012345"],
        ["--g0-observe-marker", MARKER, "--g0-observe-seconds", "14"],
        ["--g0-observe-marker", MARKER, "--g0-observe-seconds", "121"],
        ["--g0-observe-marker", MARKER, "--g0-observe-marker", MARKER],
        ["--g0-observe-marker"],
        ["unexpected", "second-base"],
    ],
)
def test_invalid_cli_fails_before_pid_socket_or_process_operations(monkeypatch, args):
    events = []
    for name in ("_diagnostic_previous_pid_is_live", "socket_path_for", "_stop_previous", "_daemonize"):
        monkeypatch.setattr(gw, name, lambda *a, _name=name, **k: events.append(_name))
    monkeypatch.setattr(go, "prepare_output_fd", lambda *_a: events.append("output"))

    assert gw.main(args) == 2
    assert events == []


def test_diagnostic_output_initialization_failure_precedes_gateway_changes(monkeypatch):
    events = []
    monkeypatch.setattr(gw, "_diagnostic_previous_pid_is_live", lambda: events.append("pid-check") or False)
    monkeypatch.setattr(
        go, "prepare_output_fd",
        lambda _marker: (_ for _ in ()).throw(go.ObservationSetupError("DIRECTORY_POLICY_FAILED")),
    )
    for name in ("socket_path_for", "_stop_previous", "_daemonize", "serve"):
        monkeypatch.setattr(gw, name, lambda *a, _name=name, **k: events.append(_name))

    assert gw.main(["--g0-observe-marker", MARKER]) == 3
    assert events == ["pid-check"]


def test_live_diagnostic_pid_refuses_without_output_or_socket_mutation(monkeypatch):
    events = []
    monkeypatch.setattr(gw, "_diagnostic_previous_pid_is_live", lambda: True)
    monkeypatch.setattr(go, "prepare_output_fd", lambda *_a: events.append("output"))
    monkeypatch.setattr(gw, "socket_path_for", lambda *_a: events.append("path"))
    monkeypatch.setattr(gw, "_stop_previous", lambda: events.append("stop"))

    assert gw.main(["--g0-observe-marker", MARKER]) == 3
    assert events == []


def test_diagnostic_pid_probe_uses_signal_zero_and_does_not_terminate(monkeypatch):
    class PIDFile:
        def read_text(self, **_kwargs):
            return "321"

    calls = []
    monkeypatch.setattr(gw, "PID_FILE", PIDFile())
    monkeypatch.setattr(gw.os, "kill", lambda pid, sig: calls.append((pid, sig)))
    assert gw._diagnostic_previous_pid_is_live() is True
    assert calls == [(321, 0)]


@pytest.mark.parametrize("serve_raises", [False, True])
def test_diagnostic_exit_paths_leave_pid_record_for_serial_manual_cleanup(monkeypatch, serve_raises):
    events = []

    class PIDFile:
        parent = SimpleNamespace(mkdir=lambda **_kwargs: events.append("pid-parent"))

        def write_text(self, value):
            events.append(("pid-write", value))

        def unlink(self):
            events.append("pid-unlink")

    class FakeObserver:
        def __init__(self, *_args, **_kwargs):
            pass

        def stop(self):
            events.append("observer-stop")

    monkeypatch.setattr(gw, "PID_FILE", PIDFile())
    monkeypatch.setattr(gw, "_diagnostic_previous_pid_is_live", lambda: False)
    monkeypatch.setattr(go, "prepare_output_fd", lambda _marker: 7)
    monkeypatch.setattr(go, "GatewayObserver", FakeObserver)
    monkeypatch.setattr(gw, "socket_path_for", lambda _base: "stub-path")
    monkeypatch.setattr(gw, "_daemonize", lambda: events.append("daemonize"))

    def serve(_path, *, observer):
        events.append(("serve", observer))
        if serve_raises:
            raise RuntimeError("stub serving failure")

    monkeypatch.setattr(gw, "serve", serve)
    if serve_raises:
        with pytest.raises(RuntimeError, match="stub serving failure"):
            gw.main(["--g0-observe-marker", MARKER])
    else:
        assert gw.main(["--g0-observe-marker", MARKER]) == 0
    assert "pid-unlink" not in events
    assert events.count("observer-stop") == 1


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (b"GET " + TARGET + b" HTTP/1.0", True),
        (b"GET " + TARGET + b" HTTP/1.1", True),
        (b"GET " + TARGET + b" HTTP/2", False),
        (b"get " + TARGET + b" HTTP/1.1", False),
        (b"POST " + TARGET + b" HTTP/1.1", False),
        (b"GET " + TARGET + b"&extra=1 HTTP/1.1", False),
        (b"GET /app/fnmusic-ext/healthz?g0_observe=%30" + TARGET[1:] + b" HTTP/1.1", False),
        (b"GET /app/fnmusic-ext/api/status?g0_observe=" + MARKER.encode("ascii") + b" HTTP/1.1", False),
        (b"GET /healthz?g0_observe=" + MARKER.encode("ascii") + b" HTTP/1.1", False),
        (b"GET /app/fnmusic-ext/healthz/?g0_observe=" + MARKER.encode("ascii") + b" HTTP/1.1", False),
        (b"GET /app/fnmusic-ext/healthz?g0_observe=" + MARKER.upper().encode("ascii") + b" HTTP/1.1", False),
    ],
)
def test_exact_request_matching(line, expected):
    assert go._request_matches(line + b"\r\nX: y\r\n\r\n", MARKER) is expected


def test_observation_schema_v2_covers_ready_observation_and_terminal_records():
    assert go.SCHEMA == "fnmusic-ext-g0-observation-v2"
    observer = go.GatewayObserver(MARKER, 60, 7, clock=FakeClock(), thread_factory=NoStartThread)
    assert observer._terminal_record("OBSERVER_STOPPED", 0)["schema"] == go.SCHEMA


def test_request_parser_ignores_body_and_preserves_duplicate_header_pairs():
    raw = _head(
        b"X-Trim-UserID: one\r\nx-trim-userid: two\r\nCookie: hidden\r\n",
        b"X-Trim-Username: body-only",
    )
    pairs = go._parse_raw_header_pairs(raw, MARKER)
    assert pairs == [
        (b"X-Trim-UserID", b" one"),
        (b"x-trim-userid", b" two"),
        (b"Cookie", b" hidden"),
    ]


@pytest.mark.parametrize("version", [b"HTTP/1.0", b"HTTP/1.1"])
@pytest.mark.parametrize(
    "body",
    [
        b"",
        (
            b"X-Trim-UserID: body-user-secret\r\n"
            b"X-Trim-IsAdmin: true\r\n"
            b"Cookie: body-cookie-secret\r\n\r\n"
            b"Authorization: Bearer body-auth-secret"
        ),
    ],
)
def test_zero_header_request_is_observed_without_parsing_or_leaking_body(
    monkeypatch, version, body,
):
    observer = _make_observer(monkeypatch)
    output, closed = _capture_writer(monkeypatch, observer)
    peer = FakeClient()
    peer_reads = []
    monkeypatch.setattr(
        go._identity,
        "read_peer_credentials",
        lambda client: peer_reads.append(client) or go._identity.PeerCredentialsMetadata(8, 9, 10),
    )

    observer.observe(peer, _head(version=version, body=body))
    observer._writer_main()

    data = b"".join(output)
    ready = json.loads(output[0])
    record = json.loads(output[1])
    assert ready["schema"] == go.SCHEMA and ready["event"] == "ready"
    assert record["schema"] == go.SCHEMA
    assert record["event"] == "observation"
    assert record["header_result"] == "OK"
    for label in ("user_id", "admin", "username"):
        assert record[f"{label}_count"] == 0
        assert record[f"{label}_present"] is False
        assert record[f"{label}_canonical"] is None
    assert peer_reads == [peer]
    assert len(output) == 2
    assert len(data) <= go.MAX_OUTPUT_BYTES
    for secret in (
        b"body-user-secret", b"body-cookie-secret", b"body-auth-secret",
    ):
        assert secret not in data
    assert closed == [7]


def test_ordinary_headers_remain_parsed_before_body_delimiter():
    raw = _head(
        b"X-Trim-UserID: header-user\r\nCookie: header-cookie\r\n",
        b"X-Trim-Username: body-only\r\n\r\nsecond-delimiter",
    )
    assert go._parse_raw_header_pairs(raw, MARKER) == [
        (b"X-Trim-UserID", b" header-user"),
        (b"Cookie", b" header-cookie"),
    ]


def test_request_parser_enforces_header_pair_and_byte_caps():
    too_many = b"".join(b"X: y\r\n" for _ in range(129))
    with pytest.raises(go._identity.HeaderClaimsError) as count_error:
        go._parse_raw_header_pairs(_head(too_many), MARKER)
    assert count_error.value.reason_code == "TOO_MANY_HEADERS"

    too_large = b"X: " + (b"a" * (64 * 1024)) + b"\r\n"
    with pytest.raises(go._identity.HeaderClaimsError) as size_error:
        go._parse_raw_header_pairs(_head(too_large), MARKER)
    assert size_error.value.reason_code == "HEADER_BYTES_LIMIT"


def test_matching_observation_redacts_all_claim_values_cookie_auth_and_body(monkeypatch):
    clock = FakeClock(20.0)
    observer = _make_observer(monkeypatch, clock=clock)
    output, closed = _capture_writer(monkeypatch, observer)
    peer = FakeClient()
    peer_values = []

    def read_peer(client):
        peer_values.append(client)
        return go._identity.PeerCredentialsMetadata(25, 1001, 1002)

    monkeypatch.setattr(go._identity, "read_peer_credentials", read_peer)
    secret_user = b"opaque-user-SECRET"
    secret_name = b"display-name-SECRET"
    raw = _head(
        b"X-Trim-UserID: " + secret_user + b"\r\n"
        b"X-Trim-IsAdmin: true\r\n"
        b"X-Trim-Username: " + secret_name + b"\r\n"
        b"Cookie: cookie-SECRET\r\n"
        b"Authorization: Bearer auth-SECRET\r\n"
        b"Content-Length: 11\r\n",
        b"body-SECRET",
    )
    observer.observe(peer, raw)
    observer._writer_main()

    data = b"".join(output)
    ready = json.loads(output[0])
    record = json.loads(output[1])
    assert ready["schema"] == go.SCHEMA and ready["event"] == "ready"
    assert record["schema"] == go.SCHEMA
    assert record["event"] == "observation"
    assert record["diagnostic_only"] is True
    assert record["marker"] == MARKER
    assert record["peer_pid"] == 25 and record["peer_uid"] == 1001
    assert record["user_id_count"] == record["admin_count"] == record["username_count"] == 1
    assert record["user_id_present"] is record["admin_present"] is record["username_present"] is True
    assert record["user_id_canonical"] is record["admin_canonical"] is record["username_canonical"] is True
    assert record["header_result"] == "OK"
    assert len(output) == 2
    assert len(data) <= go.MAX_OUTPUT_BYTES
    for secret in (secret_user, secret_name, b"cookie-SECRET", b"auth-SECRET", b"body-SECRET"):
        assert secret not in data
    assert b"admin_claim" not in data and b"username_claim" not in data
    assert peer_values == [peer]
    assert closed == [7]


def test_admin_claim_true_and_false_produce_identical_non_value_summary(monkeypatch):
    monkeypatch.setattr(
        go._identity, "read_peer_credentials",
        lambda _client: go._identity.PeerCredentialsMetadata(3, 4, 5),
    )
    summaries = []
    for claim in (b"true", b"false"):
        observer = _make_observer(monkeypatch)
        output, _closed = _capture_writer(monkeypatch, observer)
        observer.observe(FakeClient(), _head(b"X-Trim-IsAdmin: " + claim + b"\r\n"))
        observer._writer_main()
        summaries.append(json.loads(output[1]))
    assert summaries[0] == summaries[1]
    assert summaries[0]["admin_canonical"] is True


def test_invalid_headers_report_fixed_code_and_unknown_counts_without_raw_values(monkeypatch):
    observer = _make_observer(monkeypatch)
    output, _closed = _capture_writer(monkeypatch, observer)
    monkeypatch.setattr(
        go._identity, "read_peer_credentials",
        lambda _client: go._identity.PeerCredentialsMetadata(2, 3, 4),
    )
    observer.observe(
        FakeClient(),
        _head(b"X-Trim-Username: secret-username\r\nx-trim-username: secret-2\r\n"),
    )
    observer._writer_main()
    record = json.loads(output[1])
    assert record["header_result"] == "DUPLICATE_IDENTITY_HEADER"
    assert record["username_count"] is None
    assert record["username_present"] is None
    assert b"secret-username" not in b"".join(output)
    assert b"secret-2" not in b"".join(output)


def test_peer_failure_is_a_fixed_code_and_does_not_emit_exception_text(monkeypatch):
    observer = _make_observer(monkeypatch)
    output, _closed = _capture_writer(monkeypatch, observer)

    class SecretPeerError(Exception):
        reason_code = "UNSUPPORTED_PLATFORM"

        def __str__(self):
            return "PEER_SECRET_TEXT"

    def fail_peer(_client):
        raise SecretPeerError()

    monkeypatch.setattr(go._identity, "read_peer_credentials", fail_peer)
    observer.observe(FakeClient(), _head(b"X-Test: ok\r\n"))
    observer._writer_main()
    record = json.loads(output[1])
    assert record["peer_error"] == "UNSUPPORTED_PLATFORM"
    assert "PEER_SECRET_TEXT" not in b"".join(output).decode("ascii")


def test_nonmatching_and_repeated_requests_do_not_read_peer_twice(monkeypatch):
    observer = _make_observer(monkeypatch)
    calls = []
    monkeypatch.setattr(
        go._identity, "read_peer_credentials",
        lambda client: calls.append(client) or go._identity.PeerCredentialsMetadata(7, 8, 9),
    )
    observer.observe(FakeClient(), b"GET /other HTTP/1.1\r\n\r\n")
    first = FakeClient()
    observer.observe(first, _head(b"X-Test: first\r\n"))
    observer.observe(FakeClient(), _head(b"X-Test: second\r\n"))
    assert calls == [first]
    assert observer._queue.qsize() == 1


def test_concurrent_matching_requests_enqueue_exactly_one_record(monkeypatch):
    observer = _make_observer(monkeypatch)
    barrier = threading.Barrier(13)
    calls = []
    monkeypatch.setattr(
        go._identity, "read_peer_credentials",
        lambda client: calls.append(client) or go._identity.PeerCredentialsMetadata(7, 8, 9),
    )
    raw = _head(b"X-Test: value\r\n")

    def submit():
        barrier.wait()
        observer.observe(FakeClient(), raw)

    workers = [threading.Thread(target=submit) for _ in range(12)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join()
    assert len(calls) == 1
    assert observer._queue.qsize() == 1


def test_deadline_prevents_peer_read_and_writer_emits_terminal_then_closes_fd(monkeypatch):
    clock = FakeClock(50.0)
    observer = _make_observer(monkeypatch, clock=clock, seconds=15)
    output, closed = _capture_writer(monkeypatch, observer)
    calls = []
    monkeypatch.setattr(go._identity, "read_peer_credentials", lambda client: calls.append(client))
    clock.value = 65.0
    observer.observe(FakeClient(), _head())
    observer._writer_main()
    assert calls == []
    assert len(output) == 2
    assert json.loads(output[1])["reason"] == "OBSERVATION_EXPIRED"
    assert closed == [7]


def test_stop_before_writer_start_closes_descriptor_without_output(monkeypatch):
    closed = []
    monkeypatch.setattr(go.os, "close", closed.append)
    observer = go.GatewayObserver(MARKER, 60, 7, thread_factory=NoStartThread)
    observer.stop()
    assert closed == [7]


def test_writer_write_error_closes_fd_without_fallback_output(monkeypatch):
    observer = _make_observer(monkeypatch)
    attempts = []
    closed = []

    def write(fd, data):
        assert fd == 7
        attempts.append(bytes(data))
        if len(attempts) == 1:
            return min(4, len(data))
        raise OSError(errno.EIO, "private write error")

    monkeypatch.setattr(go.os, "write", write)
    monkeypatch.setattr(go.os, "close", closed.append)

    observer._writer_main()

    assert len(attempts) == 2
    assert closed == [7]
    assert observer._closed is True
    assert observer._fd is None
    assert b"private write error" not in b"".join(attempts)


def test_full_queue_is_nonblocking_and_drops_observation(monkeypatch):
    class FullQueue:
        def __init__(self):
            self.items = []
            self.calls = []

        def put_nowait(self, item):
            self.calls.append(item)
            raise queue.Full

    full_queue = FullQueue()
    observer = go.GatewayObserver(
        MARKER,
        60,
        7,
        clock=FakeClock(),
        thread_factory=NoStartThread,
        queue_factory=lambda *, maxsize: full_queue,
    )
    observer.start()
    peer = FakeClient()
    peer_reads = []
    monkeypatch.setattr(
        go._identity,
        "read_peer_credentials",
        lambda client: peer_reads.append(client) or go._identity.PeerCredentialsMetadata(8, 9, 10),
    )

    observer.observe(peer, _head())

    assert len(full_queue.calls) == 1
    assert full_queue.items == []
    assert peer_reads == [peer]
    assert observer._consumed is True


class FakeSecureOS:
    O_RDONLY = 1
    O_WRONLY = 2
    O_CREAT = 4
    O_EXCL = 8
    O_DIRECTORY = 16
    O_NOFOLLOW = 32
    O_CLOEXEC = 64

    def __init__(self, *, private_mode=0o700, root_mode=0o755, missing_private=False,
                 fail_component=None, fail_file=False, file_mode=0o600,
                 file_owner=0, file_links=1):
        self.calls = []
        self.fds = {"/": 10, "run": 11, "fnmusic-ext": 12, "g0-observe": 13}
        self.private_mode = private_mode
        self.root_mode = root_mode
        self.missing_private = missing_private
        self.private_opened = False
        self.fail_component = fail_component
        self.fail_file = fail_file
        self.file_info = SimpleNamespace(
            st_mode=stat.S_IFREG | file_mode, st_uid=file_owner, st_nlink=file_links,
        )

    def open(self, path, flags, mode=None, *, dir_fd=None):
        self.calls.append(("open", path, flags, mode, dir_fd))
        if path == self.fail_component:
            raise OSError(errno.ELOOP, "untrusted symlink")
        if path == "g0-observe" and self.missing_private and not self.private_opened:
            self.private_opened = True
            raise FileNotFoundError(errno.ENOENT, "missing")
        if path == MARKER + ".jsonl" and self.fail_file:
            raise FileExistsError(errno.EEXIST, "exists")
        return self.fds.get(path, 14)

    def mkdir(self, path, mode, *, dir_fd=None):
        self.calls.append(("mkdir", path, mode, dir_fd))

    def fstat(self, fd):
        if fd == 10:
            return SimpleNamespace(st_mode=stat.S_IFDIR | self.root_mode, st_uid=0, st_nlink=2)
        if fd == 11 or fd == 12:
            return SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_nlink=2)
        if fd == 13:
            return SimpleNamespace(st_mode=stat.S_IFDIR | self.private_mode, st_uid=0, st_nlink=2)
        return self.file_info

    def close(self, fd):
        self.calls.append(("close", fd))


def test_secure_output_open_uses_fixed_relative_walk_and_exclusive_flags(monkeypatch):
    fake_os = FakeSecureOS()
    monkeypatch.setattr(go, "os", fake_os)
    monkeypatch.setattr(go, "sys", SimpleNamespace(platform="linux"))
    fd = go.prepare_output_fd(MARKER)
    assert fd == 14
    calls = [call for call in fake_os.calls if call[0] == "open"]
    assert [call[1] for call in calls] == ["/", "run", "fnmusic-ext", "g0-observe", MARKER + ".jsonl"]
    flags = calls[-1][2]
    assert flags & fake_os.O_CREAT and flags & fake_os.O_EXCL and flags & fake_os.O_NOFOLLOW
    assert flags & fake_os.O_CLOEXEC
    assert calls[-1][3] == 0o600
    assert all(call[0] == "close" for call in fake_os.calls[-4:])


def test_secure_output_creates_only_missing_private_directory_at_0700(monkeypatch):
    fake_os = FakeSecureOS(missing_private=True)
    monkeypatch.setattr(go, "os", fake_os)
    monkeypatch.setattr(go, "sys", SimpleNamespace(platform="linux"))
    assert go.prepare_output_fd(MARKER) == 14
    assert ("mkdir", "g0-observe", 0o700, 12) in fake_os.calls


@pytest.mark.parametrize(
    ("fake_os", "expected"),
    [
        (FakeSecureOS(private_mode=0o750), "DIRECTORY_POLICY_FAILED"),
        (FakeSecureOS(root_mode=0o777), "DIRECTORY_POLICY_FAILED"),
        (FakeSecureOS(fail_component="fnmusic-ext"), "DIRECTORY_OPEN_FAILED"),
        (FakeSecureOS(fail_file=True), "OUTPUT_FILE_CREATE_FAILED"),
        (FakeSecureOS(file_mode=0o640), "OUTPUT_FILE_POLICY_FAILED"),
        (FakeSecureOS(file_owner=1), "OUTPUT_FILE_POLICY_FAILED"),
        (FakeSecureOS(file_links=2), "OUTPUT_FILE_POLICY_FAILED"),
    ],
)
def test_secure_output_rejects_unsafe_or_existing_targets(monkeypatch, fake_os, expected):
    monkeypatch.setattr(go, "os", fake_os)
    monkeypatch.setattr(go, "sys", SimpleNamespace(platform="linux"))
    with pytest.raises(go.ObservationSetupError) as raised:
        go.prepare_output_fd(MARKER)
    assert raised.value.reason_code == expected
    assert "untrusted symlink" not in str(raised.value)


def test_secure_output_duplicates_low_fd_to_at_least_three(monkeypatch):
    fake_os = FakeSecureOS()
    fake_os.fds[MARKER + ".jsonl"] = 2
    monkeypatch.setattr(go, "os", fake_os)
    monkeypatch.setattr(go, "sys", SimpleNamespace(platform="linux"))
    fcntl_stub = SimpleNamespace(
        F_DUPFD_CLOEXEC=77,
        fcntl=lambda fd, command, minimum: 22 if (fd, command, minimum) == (2, 77, 3) else -1,
    )
    monkeypatch.setitem(sys.modules, "fcntl", fcntl_stub)

    assert go.prepare_output_fd(MARKER) == 22
    assert ("close", 2) in fake_os.calls


def test_serve_starts_observer_only_after_final_listener(monkeypatch):
    events = []

    class FakePath:
        parent = None

        def __init__(self):
            self.parent = self

        def mkdir(self, **_kwargs):
            events.append("mkdir")

        def unlink(self):
            raise FileNotFoundError

        def __str__(self):
            return "/stub/socket"

    class FakeServer:
        def bind(self, _path):
            events.append("bind")

        def listen(self, _backlog):
            events.append("listen")

        def accept(self):
            events.append("accept")
            raise KeyboardInterrupt

        def close(self):
            events.append("server-close")

    class FakeObserver:
        def start(self):
            events.append("observer-start")

        def stop(self):
            events.append("observer-stop")

    monkeypatch.setattr(gw.socket, "socket", lambda *_args: events.append("socket") or FakeServer())
    monkeypatch.setattr(gw.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(gw.socket, "SOCK_STREAM", 1, raising=False)
    monkeypatch.setattr(gw.os, "chmod", lambda *_args: events.append("chmod"))
    with pytest.raises(KeyboardInterrupt):
        gw.serve(FakePath(), observer=FakeObserver())
    assert events.index("listen") < events.index("observer-start") < events.index("accept")
    assert events[-2:] == ["observer-stop", "server-close"]


@pytest.mark.parametrize(
    ("fork_results", "expected"),
    [
        ((4,), ["fork", ("exit", 0)]),
        ((0, 4), ["fork", "setsid", "fork", ("exit", 0)]),
    ],
)
def test_daemonize_exits_each_intermediate_fork_parent(monkeypatch, fork_results, expected):
    class StubExit(Exception):
        pass

    events = []
    forks = iter(fork_results)

    def stub_exit(status):
        events.append(("exit", status))
        raise StubExit

    monkeypatch.setattr(gw.os, "fork", lambda: events.append("fork") or next(forks), raising=False)
    monkeypatch.setattr(gw.os, "setsid", lambda: events.append("setsid"), raising=False)
    monkeypatch.setattr(gw.os, "_exit", stub_exit, raising=False)

    with pytest.raises(StubExit):
        gw._daemonize()

    assert events == expected


def test_diagnostic_main_double_fork_stdio_and_writer_start_order(monkeypatch):
    events = []

    class FakePID:
        parent = SimpleNamespace(mkdir=lambda **_kwargs: events.append("pid-parent"))

        def write_text(self, value):
            events.append(("pid-write", value))

        def unlink(self):
            events.append("pid-unlink")

    class FakePath:
        parent = None

        def __init__(self):
            self.parent = self

        def mkdir(self, **_kwargs):
            events.append("socket-parent-mkdir")

        def unlink(self):
            events.append("socket-unlink")

        def __str__(self):
            return "/stub/socket"

    class FakeObserver:
        def __init__(self, marker, seconds, output_fd):
            events.append(("observer-init", marker, seconds, output_fd))

        def start(self):
            events.append("writer-thread-start")

        def stop(self):
            events.append("observer-stop")

    class FakeServer:
        def bind(self, path):
            events.append(("bind", path))

        def listen(self, backlog):
            events.append(("listen", backlog))

        def accept(self):
            events.append("accept")
            raise KeyboardInterrupt

        def close(self):
            events.append("server-close")

    forks = iter((0, 0))
    monkeypatch.setattr(gw, "PID_FILE", FakePID())
    monkeypatch.setattr(gw, "_diagnostic_previous_pid_is_live", lambda: events.append("pid-probe") or False)
    monkeypatch.setattr(go, "prepare_output_fd", lambda _marker: events.append("output-fd") or 7)
    monkeypatch.setattr(go, "GatewayObserver", FakeObserver)
    monkeypatch.setattr(gw, "socket_path_for", lambda _base: events.append("socket-path") or FakePath())
    monkeypatch.setattr(gw.os, "fork", lambda: events.append("fork") or next(forks), raising=False)
    monkeypatch.setattr(gw.os, "setsid", lambda: events.append("setsid"), raising=False)
    monkeypatch.setattr(gw.os, "chdir", lambda path: events.append(("chdir", path)))
    monkeypatch.setattr(
        gw.os,
        "open",
        lambda path, flags: events.append(("stdio-open", path, flags)) or 99,
    )
    monkeypatch.setattr(gw.os, "dup2", lambda source, target: events.append(("dup2", source, target)))
    monkeypatch.setattr(gw.os, "getpid", lambda: 123)
    monkeypatch.setattr(gw.os, "chmod", lambda path, mode: events.append(("chmod", path, mode)))
    monkeypatch.setattr(gw.socket, "socket", lambda *_args: events.append("socket-create") or FakeServer())
    monkeypatch.setattr(gw.socket, "AF_UNIX", 1, raising=False)
    monkeypatch.setattr(gw.socket, "SOCK_STREAM", 1, raising=False)

    with pytest.raises(KeyboardInterrupt):
        gw.main(["--g0-observe-marker", MARKER])

    assert events.count("fork") == 2
    assert events.index("output-fd") < events.index("fork")
    assert events.index("fork") < events.index("setsid") < events.index(("chdir", "/"))
    assert events.index(("dup2", 99, 0)) < events.index(("dup2", 99, 1))
    assert events.index(("dup2", 99, 1)) < events.index(("dup2", 99, 2))
    assert events.index(("dup2", 99, 2)) < events.index(("pid-write", "123"))
    assert events.index(("pid-write", "123")) < events.index(("listen", 64))
    assert events.index(("listen", 64)) < events.index("writer-thread-start") < events.index("accept")
    assert "pid-unlink" not in events
    assert events[-2:] == ["server-close", "observer-stop"]


def test_observer_exception_does_not_change_forwarded_head_bytes(monkeypatch):
    raw = b"GET /unchanged HTTP/1.1\r\nX-Data: secret\r\n\r\nBODY"
    client = SimpleNamespace(settimeout=lambda _timeout: None, close=lambda: None)
    remote = SimpleNamespace(settimeout=lambda _timeout: None, close=lambda: None)
    observer_calls = []

    class ThrowingObserver:
        def observe(self, supplied_client, supplied_raw):
            observer_calls.append((supplied_client, supplied_raw))
            raise RuntimeError("diagnostic exception")

    threads = []

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            threads.append((target, args, daemon))

        def start(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr(gw, "_read_request_head", lambda _client: (raw, {}))
    monkeypatch.setattr(gw.socket, "create_connection", lambda *_a, **_k: remote)
    monkeypatch.setattr(gw.threading, "Thread", FakeThread)

    gw._handle(client, ("unused", 0), ThrowingObserver())

    assert observer_calls == [(client, raw)]
    assert threads[0][1][0] is client and threads[0][1][1] is remote
    assert threads[0][1][2] == raw


def test_default_handle_does_not_read_peer_or_parse_identity(monkeypatch):
    raw = b"GET /ordinary HTTP/1.1\r\nX-Trim-IsAdmin: true\r\n\r\n"
    client = SimpleNamespace(settimeout=lambda _timeout: None, close=lambda: None)
    remote = SimpleNamespace(settimeout=lambda _timeout: None, close=lambda: None)

    class FakeThread:
        def __init__(self, *, target, args, daemon):
            pass

        def start(self):
            pass

        def join(self):
            pass

    monkeypatch.setattr(gw, "_read_request_head", lambda _client: (raw, {}))
    monkeypatch.setattr(gw.socket, "create_connection", lambda *_a, **_k: remote)
    monkeypatch.setattr(gw.threading, "Thread", FakeThread)
    monkeypatch.setattr(go._identity, "read_peer_credentials", lambda *_a: pytest.fail("off mode peer read"))
    monkeypatch.setattr(go._identity, "parse_untrusted_identity", lambda *_a: pytest.fail("off mode parse"))

    gw._handle(client, ("unused", 0))
