"""Synthetic SQLite v1 to v2 migration tests; no NAS or live ledger access."""

from __future__ import annotations

from array import array
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from offline_tests._audit_support import WriteAudit


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "fnmusic_migrate_ledger_snapshot", ROOT / "scripts" / "migrate_ledger_snapshot.py"
)
assert SPEC is not None and SPEC.loader is not None
migrator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migrator)
module = migrator._ledger_module()


def _sealed_memfd(data: bytes, *, seals: int | None = None) -> int:
    """Create a real memfd and apply the four required seals by default."""
    import fcntl

    fd = os.memfd_create("synthetic-ledger", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        if data:
            os.write(fd, data)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, migrator.REQUIRED_SEALS if seals is None else seals)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _raw_memfd(data: bytes) -> int:
    """Create a real memfd without any seal."""
    fd = os.memfd_create("synthetic-ledger", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        if data:
            os.write(fd, data)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _sized_sealed_memfd(size: int) -> int:
    """Create a sparse fully sealed regular memfd of exactly ``size`` bytes."""
    import fcntl

    fd = os.memfd_create("synthetic-ledger", os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
    try:
        os.ftruncate(fd, size)
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, migrator.REQUIRED_SEALS)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _spawn_worker(*, socket_type: int = socket.SOCK_SEQPACKET, env=None, cwd=None):
    """Start the migration worker with one inherited socket endpoint."""
    parent, child = socket.socketpair(socket.AF_UNIX, socket_type)
    command = [
        sys.executable, "-I", "-B",
        str(ROOT / "scripts" / "migrate_ledger_snapshot.py"),
        "--socket-fd", str(child.fileno()),
    ]
    try:
        process = subprocess.Popen(
            command, pass_fds=(child.fileno(),),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env=env, cwd=cwd,
        )
    finally:
        child.close()
    return parent, process


def _extract_fds(control) -> list[int]:
    descriptors: list[int] = []
    for level, kind, payload in control:
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS:
            continue
        received = array("i")
        received.frombytes(payload[: len(payload) - len(payload) % received.itemsize])
        descriptors.extend(received.tolist())
    return descriptors


def _run_worker(input_fds, *, request: bytes = migrator.PROTOCOL_REQUEST,
                socket_type: int = socket.SOCK_SEQPACKET, env=None, cwd=None):
    """Send one request packet to a fresh worker and return its typed reply."""
    parent, process = _spawn_worker(socket_type=socket_type, env=env, cwd=cwd)
    received: list[int] = []
    try:
        parent.settimeout(30)
        ancillary = []
        if input_fds:
            ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", input_fds).tobytes())]
        parent.sendmsg([request], ancillary)
        try:
            data, control, flags, _address = parent.recvmsg(
                8192, socket.CMSG_SPACE(8 * array("i").itemsize)
            )
        except socket.timeout:
            process.kill()
            process.communicate(timeout=10)
            raise AssertionError("worker did not send a protocol reply within 30 seconds") from None
        received = _extract_fds(control)
        stdout, stderr = process.communicate(timeout=30)
        if flags & (getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)):
            for fd in received:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise AssertionError("response packet was truncated")
        try:
            report = json.loads(data)
        except ValueError:
            for fd in received:
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise AssertionError(f"response packet was not JSON: {data[:200]!r}") from None
        return process.returncode, report, received, stdout, stderr
    finally:
        parent.close()


def _make_v1(path: Path, *, states: tuple[str, ...] = ("published", "failed"),
             cleanup_pending: tuple[bool, ...] | None = None) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(module._CREATE_JOBS)
        connection.execute(module._CREATE_POSITIONS)
        connection.execute(module._CREATE_RUNNING_INDEX)
        connection.execute(f"PRAGMA application_id = {module.APPLICATION_ID}")
        connection.execute("PRAGMA user_version = 1")
        manifest = {
            "kind": "playlist",
            "target_id": "target-test",
            "provider_id": "provider-test",
            "collection_id": "collection-test",
            "collection_version": "snapshot-v1",
            "enumeration_complete": True,
            "entries": [
                {"position": position, "source_id": "123",
                 "source_status": "ok" if position == 1 else "unavailable"}
                for position in range(1, len(states) + 1)
            ],
        }
        raw = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
        job_id = "a" * 48
        connection.execute(
            """INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (job_id, "actor-test", "idempotency-test", "playlist", "target-test",
             "provider-test", "collection-test", "snapshot-v1", 1, raw,
             hashlib.sha256(raw).hexdigest(), 0),
        )
        reasons = {
            "published": "bytes_published", "failed": "staging_failed",
            "conflict": "destination_conflict", "cancelled": "cancelled_before_publish",
            "rejected": "invalid_target", "published_cleanup_pending": "published_cleanup_pending",
            "queued": None, "running": None, "commit_unknown": "commit_unknown",
        }
        for position, state in enumerate(states, start=1):
            source_status = "ok" if position == 1 else "unavailable"
            pending = (cleanup_pending[position - 1] if cleanup_pending is not None
                       else state in {"commit_unknown", "published_cleanup_pending"})
            if state in {"queued", "running"}:
                connection.execute(
                    "INSERT INTO positions VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, ?)",
                    (job_id, position, "123", source_status, state,
                     "b" * 48 if state == "running" else None),
                )
            else:
                connection.execute(
                    "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (job_id, position, "123", source_status, state, state, reasons[state],
                     int(pending)),
                )
        connection.commit()
    finally:
        connection.close()


def _rows(path: Path, table: str, order: str):
    connection = sqlite3.connect(path)
    try:
        return tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"))
    finally:
        connection.close()


def _connection_rows(connection: sqlite3.Connection, table: str, order: str):
    return tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"))


class MigrationSemanticsTests(unittest.TestCase):
    def test_in_memory_pipeline_migrates_and_preserves_every_v1_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            before_jobs = _rows(path, "jobs", "job_id")
            before_positions = _rows(path, "positions", "job_id, position")
            source_bytes = path.read_bytes()
        candidate_bytes, counts = migrator._migrate_bytes(source_bytes, module)
        self.assertEqual(counts, {"jobs": 1, "positions": 2})
        candidate = migrator._connection_from_bytes(candidate_bytes)
        try:
            self.assertEqual(_connection_rows(candidate, "jobs", "job_id"), before_jobs)
            self.assertEqual(_connection_rows(candidate, "positions", "job_id, position"), before_positions)
            self.assertEqual(candidate.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(candidate.execute("SELECT COUNT(*) FROM position_details").fetchone()[0], 0)
            self.assertEqual(candidate.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            candidate.close()


class V1ValidationRejectionTests(unittest.TestCase):
    def _assert_invalid_without_mutation(self, edit, expected_code):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            connection = sqlite3.connect(path)
            connection.execute("PRAGMA foreign_keys = OFF")
            edit(connection)
            connection.commit()
            before = path.read_bytes()
            connection.row_factory = sqlite3.Row
            try:
                with self.assertRaises(migrator.MigrationError) as caught:
                    migrator._validate_v1(connection, module)
                self.assertEqual(caught.exception.code, expected_code)
            finally:
                connection.close()
            self.assertEqual(path.read_bytes(), before)

    def test_wrong_version_and_extra_object_are_rejected(self):
        self._assert_invalid_without_mutation(
            lambda db: db.execute("PRAGMA user_version = 99"), "source_schema_version_invalid"
        )
        self._assert_invalid_without_mutation(
            lambda db: db.execute("CREATE VIEW unexpected AS SELECT 1"), "source_schema_invalid"
        )

    def test_manifest_and_foreign_key_corruption_are_rejected(self):
        self._assert_invalid_without_mutation(
            lambda db: db.execute("UPDATE jobs SET manifest_json = ?", (b"not-json",)),
            "source_contents_invalid",
        )

        def add_orphan(db):
            db.execute(
                "INSERT INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                ("f" * 48, 1, "123", "ok", "failed", "failed", "staging_failed", 0),
            )

        self._assert_invalid_without_mutation(add_orphan, "source_foreign_key_invalid")

    def test_unresolved_positions_are_refused_without_mutating_snapshot(self):
        for state in ("queued", "running", "commit_unknown", "published_cleanup_pending"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as folder:
                path = Path(folder) / "fixture.sqlite3"
                _make_v1(path, states=(state, "failed"))
                before = hashlib.sha256(path.read_bytes()).digest()
                connection = sqlite3.connect(path, isolation_level=None)
                try:
                    with self.assertRaises(migrator.MigrationError) as caught:
                        migrator._migrate_connection(connection, module)
                    self.assertEqual(caught.exception.code, "source_has_unresolved_work")
                    self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                    self.assertEqual(connection.execute(
                        "SELECT COUNT(*) FROM sqlite_master WHERE name='position_details'"
                    ).fetchone()[0], 0)
                finally:
                    connection.close()
                self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_any_cleanup_pending_position_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path, states=("published", "failed"), cleanup_pending=(False, True))
            before = hashlib.sha256(path.read_bytes()).digest()
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                with self.assertRaises(migrator.MigrationError) as caught:
                    migrator._migrate_connection(connection, module)
                self.assertEqual(caught.exception.code, "source_has_unresolved_work")
            finally:
                connection.close()
            self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), before)

    def test_supported_terminal_states_migrate_without_cleanup(self):
        states = ("published", "failed", "conflict", "cancelled", "rejected")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path, states=states, cleanup_pending=(False,) * len(states))
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                counts = migrator._migrate_connection(connection, module)
                self.assertEqual(counts, {"jobs": 1, "positions": len(states)})
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM positions WHERE cleanup_pending = 1"
                ).fetchone()[0], 0)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM position_details").fetchone()[0], 0)
            finally:
                connection.close()

    def test_transaction_failure_keeps_v1_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            connection = sqlite3.connect(path, isolation_level=None)
            connection.set_authorizer(
                lambda action, arg1, _arg2, _db, _trigger:
                    sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_PRAGMA
                    and arg1 == "user_version" and _arg2 is not None
                    else sqlite3.SQLITE_OK
            )
            try:
                with self.assertRaises(migrator.MigrationError) as caught:
                    migrator._migrate_connection(connection, module)
                self.assertEqual(caught.exception.code, "migration_transaction_failed")
                connection.set_authorizer(None)
                self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
                self.assertEqual(connection.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='position_details'"
                ).fetchone()[0], 0)
            finally:
                connection.close()


class MemoryPipelineTests(unittest.TestCase):
    def test_bytes_pipeline_returns_verified_original_and_candidate_content(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            source_bytes = path.read_bytes()
        candidate_bytes, counts = migrator._migrate_bytes(source_bytes, module)
        self.assertEqual(counts, {"jobs": 1, "positions": 2})
        self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), migrator._digest(source_bytes))
        candidate = migrator._connection_from_bytes(candidate_bytes)
        try:
            self.assertEqual(candidate.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(candidate.execute("SELECT COUNT(*) FROM position_details").fetchone()[0], 0)
            self.assertEqual(candidate.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            candidate.close()

    def test_mutating_and_unresolved_input_bytes_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path, states=("published", "failed"), cleanup_pending=(False, True))
            source_bytes = path.read_bytes()
        with self.assertRaises(migrator.MigrationError) as caught:
            migrator._migrate_bytes(source_bytes, module)
        self.assertEqual(caught.exception.code, "source_has_unresolved_work")

    def test_real_sqlite_wal_backup_is_normalized_only_in_working_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source_path = root / "source.sqlite3"
            snapshot_path = root / "sqlite-backup.sqlite3"
            _make_v1(source_path)
            source = sqlite3.connect(source_path)
            destination = sqlite3.connect(snapshot_path)
            try:
                self.assertEqual(source.execute("PRAGMA journal_mode = WAL").fetchone()[0], "wal")
                source.execute("UPDATE jobs SET cancel_requested = 1")
                source.commit()
                source.backup(destination)
            finally:
                destination.close()
                source.close()
            raw_snapshot = snapshot_path.read_bytes()
            self.assertEqual(raw_snapshot[18:20], bytes((2, 2)))
            self.assertFalse(Path(str(snapshot_path) + "-wal").exists())
            before_rows = _rows(snapshot_path, "jobs", "job_id")
            original_digest = hashlib.sha256(raw_snapshot).hexdigest()

            working_image = migrator._sqlite_deserialize_image(raw_snapshot)
            self.assertEqual(working_image[18:20], bytes((1, 1)))
            self.assertEqual(raw_snapshot[18:20], bytes((2, 2)))
            candidate_bytes, counts = migrator._migrate_bytes(raw_snapshot, module)
            self.assertEqual(counts, {"jobs": 1, "positions": 2})
            self.assertEqual(hashlib.sha256(raw_snapshot).hexdigest(), original_digest)
            self.assertEqual(_rows(snapshot_path, "jobs", "job_id"), before_rows)

            candidate = migrator._connection_from_bytes(candidate_bytes)
            try:
                self.assertEqual(candidate.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(_connection_rows(candidate, "jobs", "job_id"), before_rows)
                self.assertEqual(candidate.execute("SELECT COUNT(*) FROM position_details").fetchone()[0], 0)
            finally:
                candidate.close()

    def test_unknown_or_mixed_journal_header_is_rejected(self):
        malformed = bytearray(b"SQLite format 3" + bytes((0,)) + bytes(84))
        malformed[18:20] = bytes((2, 1))
        with self.assertRaises(migrator.MigrationError) as caught:
            migrator._sqlite_deserialize_image(bytes(malformed))
        self.assertEqual(caught.exception.code, "source_journal_mode_invalid")


@unittest.skipUnless(sys.platform == "linux", "memfd/SCM_RIGHTS integration requires Linux")
class SealedDescriptorProtocolTests(unittest.TestCase):
    def _make_memfd(self, data: bytes, *, seal: bool = True, seals: int | None = None) -> int:
        """Create a real memfd; ``seals`` selects an explicit seal combination."""
        if seals is not None:
            return _sealed_memfd(data, seals=seals)
        return _sealed_memfd(data) if seal else _raw_memfd(data)

    def _run_worker(self, input_fd: int, *, request: bytes = migrator.PROTOCOL_REQUEST,
                    pass_input: bool = True, socket_type: int = socket.SOCK_SEQPACKET,
                    extra_input_fds: tuple[int, ...] = ()):
        input_fds = ((input_fd,) + tuple(extra_input_fds)) if pass_input else ()
        status, report, outputs, stdout, stderr = _run_worker(
            input_fds, request=request, socket_type=socket_type
        )
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")
        return status, report, outputs

    def test_sealed_input_returns_two_immutable_verified_artifacts(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            source_bytes = path.read_bytes()
        input_fd = self._make_memfd(source_bytes)
        try:
            status, report, outputs = self._run_worker(input_fd)
            self.assertEqual(status, 0)
            self.assertEqual(report["status"], "CANDIDATE_READY")
            self.assertEqual(report["source_sha256"], hashlib.sha256(source_bytes).hexdigest())
            self.assertEqual(report["artifact_order"], ["original-v1", "candidate-v2"])
            self.assertIn("trusted_caller_sqlite_backup_attestation_truth", report["unverified_gates"])
            self.assertIn("snapshot_origin_and_consistency", report["unverified_gates"])
            self.assertEqual(len(outputs), 2)
            import fcntl
            actual = []
            for fd in outputs:
                seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
                self.assertEqual(seals & migrator.REQUIRED_SEALS, migrator.REQUIRED_SEALS)
                size = os.fstat(fd).st_size
                actual.append(os.pread(fd, size, 0))
            self.assertEqual(actual[0], source_bytes)
            self.assertEqual(hashlib.sha256(actual[1]).hexdigest(), report["candidate_sha256"])
            candidate = migrator._connection_from_bytes(actual[1])
            try:
                self.assertEqual(candidate.execute("PRAGMA user_version").fetchone()[0], 2)
                self.assertEqual(candidate.execute("SELECT COUNT(*) FROM position_details").fetchone()[0], 0)
                self.assertEqual(candidate.execute("SELECT COUNT(*) FROM positions").fetchone()[0], 2)
            finally:
                candidate.close()
            self.assertNotIn("actor-test", json.dumps(report))
            self.assertNotIn("123", json.dumps(report))
        finally:
            os.close(input_fd)
            for fd in locals().get("outputs", []):
                os.close(fd)

    def test_unsealed_input_is_rejected_without_output_descriptors(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            source_bytes = path.read_bytes()
        input_fd = self._make_memfd(source_bytes, seal=False)
        try:
            status, report, outputs = self._run_worker(input_fd)
            self.assertEqual(status, 2)
            self.assertEqual(report, {"reason": "snapshot_not_fully_sealed", "status": "BLOCKED"})
            self.assertEqual(outputs, [])
        finally:
            os.close(input_fd)

    def test_missing_descriptor_and_malformed_request_are_rejected(self):
        status, report, outputs = self._run_worker(-1, pass_input=False)
        self.assertEqual(status, 2)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(outputs, [])
        input_fd = self._make_memfd(b"synthetic")
        try:
            status, report, outputs = self._run_worker(input_fd, request=b"MIGRATE_V1")
            self.assertEqual(status, 2)
            self.assertEqual(report["status"], "BLOCKED")
            self.assertEqual(outputs, [])
        finally:
            os.close(input_fd)

    def test_stream_socket_is_rejected(self):
        input_fd = self._make_memfd(b"synthetic")
        try:
            status, report, outputs = self._run_worker(input_fd, socket_type=socket.SOCK_STREAM)
            self.assertEqual(status, 2)
            self.assertEqual(report, {"reason": "socket_type_rejected", "status": "BLOCKED"})
            self.assertEqual(outputs, [])
        finally:
            os.close(input_fd)

    def test_zero_byte_and_non_regular_inputs_are_size_or_type_rejected(self):
        """Real 0-byte memfd and a real non-regular fd are refused by fstat."""
        zero_fd = self._make_memfd(b"")
        try:
            status, report, outputs = self._run_worker(zero_fd)
            self.assertEqual(status, 2)
            self.assertEqual(
                report, {"reason": "snapshot_size_or_type_rejected", "status": "BLOCKED"}
            )
            self.assertEqual(outputs, [])
        finally:
            os.close(zero_fd)

        read_fd, write_fd = os.pipe()
        try:
            status, report, outputs = self._run_worker(read_fd)
            self.assertEqual(status, 2)
            self.assertEqual(
                report, {"reason": "snapshot_size_or_type_rejected", "status": "BLOCKED"}
            )
            self.assertEqual(outputs, [])
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_one_byte_and_exact_64_mib_inputs_pass_the_size_gate(self):
        """Real boundary: 1 B and exactly MAX_SNAPSHOT_BYTES are size-accepted."""
        minimum_fd = self._make_memfd(b"x")
        try:
            status, report, outputs = self._run_worker(minimum_fd)
            self.assertEqual(status, 2)
            self.assertEqual(report, {"reason": "source_database_invalid", "status": "BLOCKED"})
            self.assertEqual(outputs, [])
        finally:
            os.close(minimum_fd)

        boundary_fd = _sized_sealed_memfd(migrator.MAX_SNAPSHOT_BYTES)
        try:
            status, report, outputs = self._run_worker(boundary_fd)
            self.assertEqual(status, 2)
            self.assertEqual(report, {"reason": "source_database_invalid", "status": "BLOCKED"})
            self.assertEqual(outputs, [])
        finally:
            os.close(boundary_fd)

    def test_oversize_snapshot_is_rejected_before_content_validation(self):
        """Real kernel fd with MAX_SNAPSHOT_BYTES + 1: size gate rejects first."""
        oversize_fd = _sized_sealed_memfd(migrator.MAX_SNAPSHOT_BYTES + 1)
        try:
            status, report, outputs = self._run_worker(oversize_fd)
            self.assertEqual(status, 2)
            self.assertEqual(
                report, {"reason": "snapshot_size_or_type_rejected", "status": "BLOCKED"}
            )
            self.assertEqual(outputs, [])
        finally:
            os.close(oversize_fd)

    def test_each_missing_seal_is_rejected(self):
        """Real partial-seal combinations: exactly one required seal missing."""
        import fcntl

        data = b"synthetic partially sealed snapshot"
        for missing in (
            fcntl.F_SEAL_WRITE,
            fcntl.F_SEAL_GROW,
            fcntl.F_SEAL_SHRINK,
            fcntl.F_SEAL_SEAL,
        ):
            with self.subTest(missing_seal=missing):
                fd = self._make_memfd(data, seals=migrator.REQUIRED_SEALS & ~missing)
                try:
                    status, report, outputs = self._run_worker(fd)
                    self.assertEqual(status, 2)
                    self.assertEqual(
                        report,
                        {"reason": "snapshot_not_fully_sealed", "status": "BLOCKED"},
                    )
                    self.assertEqual(outputs, [])
                finally:
                    os.close(fd)

    def test_two_input_descriptors_are_rejected(self):
        """Real SCM_RIGHTS packet with two descriptors: exactly one is required."""
        first = self._make_memfd(b"synthetic-first-descriptor")
        second = self._make_memfd(b"synthetic-second-descriptor")
        try:
            status, report, outputs = self._run_worker(first, extra_input_fds=(second,))
            self.assertEqual(status, 2)
            self.assertEqual(report, {"reason": "request_packet_invalid", "status": "BLOCKED"})
            self.assertEqual(outputs, [])
        finally:
            os.close(first)
            os.close(second)

    def test_peer_close_without_receiver_sends_no_success_artifact(self):
        """Real closed peer: the worker must fail, not fabricate a success reply."""
        parent, process = _spawn_worker()
        parent.close()
        stdout, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")

    def test_invalid_socket_descriptor_sends_no_success_artifact(self):
        """Real non-socket descriptor: the worker exits BLOCKED, not success."""
        with tempfile.TemporaryFile() as regular_file:
            command = [
                sys.executable, "-I", "-B",
                str(ROOT / "scripts" / "migrate_ledger_snapshot.py"),
                "--socket-fd", str(regular_file.fileno()),
            ]
            process = subprocess.Popen(
                command, pass_fds=(regular_file.fileno(),),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            stdout, stderr = process.communicate(timeout=30)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(stdout, "")
        self.assertEqual(stderr, "")


@unittest.skipUnless(sys.platform == "linux", "memfd/fcntl integration requires Linux")
class DefensiveInputRecheckTests(unittest.TestCase):
    """MOCK-ONLY: the input re-read guards of ``_read_sealed_input``.

    A conforming kernel cannot resize a fully sealed memfd, swap its inode, or
    remove a seal after sealing, so ``snapshot_changed`` and
    ``snapshot_seals_changed`` cannot be induced by a real kernel state change.
    These tests inject changed observations (mock ``os.fstat`` / ``fcntl``) to
    prove the worker's second check fails closed. They are NOT real kernel
    failure-path coverage and are reported as mock coverage.
    """

    def _sealed_fd(self) -> int:
        return _sealed_memfd(b"synthetic sealed input for recheck tests")

    def test_size_change_between_checks_reports_snapshot_changed(self):
        fd = self._sealed_fd()
        try:
            real = os.fstat(fd)
            changed = os.stat_result((
                real.st_mode, real.st_ino, real.st_dev, real.st_nlink,
                real.st_uid, real.st_gid, real.st_size + 1,
                real.st_atime, real.st_mtime, real.st_ctime,
            ))
            observations = iter((real, changed))
            with mock.patch.object(migrator.os, "fstat", side_effect=lambda _fd: next(observations)):
                with self.assertRaises(migrator.MigrationError) as caught:
                    migrator._read_sealed_input(fd)
            self.assertEqual(caught.exception.code, "snapshot_changed")
        finally:
            os.close(fd)

    def test_seal_removal_between_checks_reports_snapshot_seals_changed(self):
        import fcntl

        fd = self._sealed_fd()
        try:
            real_fcntl = fcntl.fcntl
            observations = {"count": 0}

            def fake_fcntl(descriptor, operation, argument=0):
                if operation == fcntl.F_GET_SEALS:
                    observations["count"] += 1
                    if observations["count"] == 2:
                        return migrator.REQUIRED_SEALS & ~fcntl.F_SEAL_SHRINK
                    return migrator.REQUIRED_SEALS
                return real_fcntl(descriptor, operation, argument)

            with mock.patch.object(migrator.fcntl, "fcntl", side_effect=fake_fcntl):
                with self.assertRaises(migrator.MigrationError) as caught:
                    migrator._read_sealed_input(fd)
            self.assertEqual(caught.exception.code, "snapshot_seals_changed")
        finally:
            os.close(fd)


@unittest.skipUnless(sys.platform == "linux", "memfd/socket integration requires Linux")
class NoDiskWriteTests(unittest.TestCase):
    """Direct no-write assertions for the migration worker contract (F9)."""

    def _entries(self, root: Path) -> list[str]:
        found = []
        for current, directories, files in os.walk(root):
            for name in sorted(directories + files):
                found.append(os.path.relpath(os.path.join(current, name), root))
        return sorted(found)

    def _empty_environment(self, cwd: Path, temp: Path, home: Path) -> dict[str, str]:
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("FNMUSIC_")
        }
        environment.update({
            "TMPDIR": str(temp),
            "TMP": str(temp),
            "TEMP": str(temp),
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        return environment

    def test_successful_and_rejected_worker_runs_write_no_files(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            cwd = base / "cwd"
            temp = base / "tmp"
            home = base / "home"
            for directory in (cwd, temp, home):
                directory.mkdir()
            environment = self._empty_environment(cwd, temp, home)
            with tempfile.TemporaryDirectory() as source_folder:
                path = Path(source_folder) / "fixture.sqlite3"
                _make_v1(path)
                source_bytes = path.read_bytes()

            input_fd = _sealed_memfd(source_bytes)
            try:
                status, report, outputs, stdout, stderr = _run_worker(
                    (input_fd,), env=environment, cwd=str(cwd)
                )
                self.assertEqual(status, 0)
                self.assertEqual(report["status"], "CANDIDATE_READY")
                self.assertEqual(len(outputs), 2)
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")
                for fd in outputs:
                    os.close(fd)
            finally:
                os.close(input_fd)

            rejected_fd = _raw_memfd(source_bytes)
            try:
                status, report, outputs, stdout, stderr = _run_worker(
                    (rejected_fd,), env=environment, cwd=str(cwd)
                )
                self.assertEqual(status, 2)
                self.assertEqual(
                    report, {"reason": "snapshot_not_fully_sealed", "status": "BLOCKED"}
                )
                self.assertEqual(outputs, [])
                self.assertEqual(stdout, "")
                self.assertEqual(stderr, "")
            finally:
                os.close(rejected_fd)

            self.assertEqual(self._entries(cwd), [], "worker wrote into its working directory")
            self.assertEqual(self._entries(temp), [], "worker wrote into TMPDIR")
            self.assertEqual(self._entries(home), [], "worker wrote into HOME")

    def test_in_process_successful_handoff_emits_no_write_intent_audit_events(self):
        """Python-level audit: no named file is opened for writing."""
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.sqlite3"
            _make_v1(path)
            source_bytes = path.read_bytes()
        input_fd = _sealed_memfd(source_bytes)
        parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
        outputs: list[int] = []
        try:
            parent.sendmsg(
                [migrator.PROTOCOL_REQUEST],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array("i", [input_fd]).tobytes())],
            )
            audit = WriteAudit()
            audit.active = True
            try:
                succeeded = migrator._handle_socket_fd(child.fileno(), module)
            finally:
                audit.active = False
            self.assertTrue(succeeded)
            parent.settimeout(30)
            data, control, flags, _address = parent.recvmsg(
                8192, socket.CMSG_SPACE(8 * array("i").itemsize)
            )
            self.assertFalse(
                flags & (getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0))
            )
            outputs = _extract_fds(control)
            report = json.loads(data)
            self.assertEqual(report["status"], "CANDIDATE_READY")
            self.assertEqual(len(outputs), 2)
            self.assertEqual(audit.violations(), [])
        finally:
            parent.close()
            child.close()
            os.close(input_fd)
            for fd in outputs:
                try:
                    os.close(fd)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
