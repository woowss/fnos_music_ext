#!/usr/bin/env python3
"""Create an isolated v2 candidate from a stopped, externally backed-up v1 DB.

This tool never opens a live ledger for writing and never installs its output.
The operator remains responsible for obtaining a consistent SQLite backup,
stopping every writer, and providing a genuinely isolated execution context.
"""

from __future__ import annotations

import argparse
from array import array
try:
    import fcntl
except ImportError:  # Windows may run semantic-only tests; the CLI fails closed there.
    fcntl = None
import hashlib
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import sys
from typing import Any


# The in-memory SQLite deserialize/serialize path holds several copies. Keep a
# conservative fixed bound so an unexpectedly large input cannot exhaust RAM.
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
V1_APPLICATION_ID = 0x464E4A42
V1_SCHEMA_VERSION = 1
PROTOCOL_REQUEST = b"MIGRATE_V1_SQLITE_BACKUP_API_COMPLETE"
PROTOCOL_REPORT = "fnmusic-ledger-migration-fd-report-v1"
REQUIRED_SEALS = (
    fcntl.F_SEAL_WRITE | fcntl.F_SEAL_GROW | fcntl.F_SEAL_SHRINK | fcntl.F_SEAL_SEAL
) if fcntl is not None and sys.platform == "linux" else 0

# Pinned schema from the registered v1 baseline. Do not derive these from the
# v2 module: accepting a future schema as v1 would make the source gate drift.
V1_CREATE_JOBS = """CREATE TABLE jobs (
    job_id TEXT NOT NULL PRIMARY KEY,
    actor_scope TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('single', 'playlist')),
    target_id TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    collection_version TEXT NOT NULL,
    enumeration_complete INTEGER NOT NULL CHECK (enumeration_complete IN (0, 1)),
    manifest_json BLOB NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0, 1)),
    UNIQUE (actor_scope, idempotency_key)
)"""
V1_CREATE_POSITIONS = """CREATE TABLE positions (
    job_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK (position >= 1),
    source_id TEXT,
    source_status TEXT NOT NULL CHECK (source_status IN ('ok', 'invalid_id', 'unavailable', 'missing')),
    state TEXT NOT NULL CHECK (state IN ('queued', 'running', 'published', 'published_cleanup_pending', 'conflict', 'cancelled', 'rejected', 'failed', 'commit_unknown')),
    outcome TEXT,
    reason TEXT,
    cleanup_pending INTEGER CHECK (cleanup_pending IN (0, 1)),
    claim_token TEXT,
    PRIMARY KEY (job_id, position),
    FOREIGN KEY (job_id) REFERENCES jobs(job_id) ON DELETE CASCADE,
    CHECK (
        (state = 'queued' AND outcome IS NULL AND reason IS NULL AND cleanup_pending IS NULL AND claim_token IS NULL)
        OR (state = 'running' AND outcome IS NULL AND reason IS NULL AND cleanup_pending IS NULL AND claim_token IS NOT NULL)
        OR (state NOT IN ('queued', 'running') AND outcome = state AND reason IS NOT NULL AND cleanup_pending IS NOT NULL AND claim_token IS NULL)
    )
)"""
V1_CREATE_RUNNING_INDEX = "CREATE UNIQUE INDEX one_running_position_per_job ON positions(job_id) WHERE state = 'running'"


class MigrationError(Exception):
    """Expected fail-closed result with a fixed, non-sensitive code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _ledger_module():
    sys.dont_write_bytecode = True
    repo = Path(__file__).resolve().parents[1]
    proxy = repo / "proxy"
    if str(proxy) not in sys.path:
        sys.path.insert(0, str(proxy))
    try:
        return importlib.import_module("manual_save_jobs")
    except Exception:
        raise MigrationError("ledger_schema_unavailable") from None


def _objects(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
    ).fetchall()


def _expected_v1(module) -> dict[tuple[str, str], str | None]:
    expected: dict[tuple[str, str], str | None] = {
        ("table", "jobs"): V1_CREATE_JOBS,
        ("table", "positions"): V1_CREATE_POSITIONS,
        ("index", "one_running_position_per_job"): V1_CREATE_RUNNING_INDEX,
        ("index", "sqlite_autoindex_jobs_1"): None,
        ("index", "sqlite_autoindex_jobs_2"): None,
        ("index", "sqlite_autoindex_positions_1"): None,
    }
    return expected


def _validate_v1(connection: sqlite3.Connection, module) -> None:
    if connection.execute("PRAGMA application_id").fetchone()[0] != V1_APPLICATION_ID:
        raise MigrationError("source_application_id_invalid")
    if connection.execute("PRAGMA user_version").fetchone()[0] != V1_SCHEMA_VERSION:
        raise MigrationError("source_schema_version_invalid")
    rows = _objects(connection)
    actual = {(row["type"], row["name"]): row["sql"] for row in rows}
    expected = _expected_v1(module)
    if len(actual) != len(rows) or set(actual) != set(expected):
        raise MigrationError("source_schema_invalid")
    for key, sql in expected.items():
        if actual[key] != sql:
            raise MigrationError("source_schema_invalid")
    fk = connection.execute("PRAGMA foreign_key_list(positions)").fetchall()
    if (len(fk) != 1 or tuple(fk[0][2:6]) !=
            ("jobs", "job_id", "job_id", "NO ACTION") or fk[0][6] != "CASCADE"):
        raise MigrationError("source_foreign_key_invalid")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()
    if integrity is None or integrity[0] != "ok":
        raise MigrationError("source_integrity_invalid")
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise MigrationError("source_foreign_key_invalid")
    try:
        for job in connection.execute("SELECT * FROM jobs ORDER BY job_id"):
            module._validate_job_row(job)
            rows_for_job = connection.execute(
                "SELECT * FROM positions WHERE job_id = ? ORDER BY position", (job["job_id"],)
            ).fetchall()
            manifest = module._validate_job_row(job)
            entries = manifest["entries"]
            if len(rows_for_job) != len(entries):
                raise MigrationError("source_contents_invalid")
            for expected_entry, position in zip(entries, rows_for_job, strict=True):
                module._validate_stored_position(position)
                if (position["position"] != expected_entry["position"]
                        or position["source_id"] != expected_entry["source_id"]
                        or position["source_status"] != expected_entry["source_status"]):
                    raise MigrationError("source_contents_invalid")
        if connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] > module.MAX_JOBS:
            raise MigrationError("source_capacity_exceeded")
        if connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0] > module.MAX_POSITIONS:
            raise MigrationError("source_capacity_exceeded")
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("source_contents_invalid") from None
    states = {row[0] for row in connection.execute("SELECT DISTINCT state FROM positions")}
    unresolved_cleanup = connection.execute(
        "SELECT 1 FROM positions WHERE cleanup_pending = 1 LIMIT 1"
    ).fetchone()
    if (states & {"queued", "running", "commit_unknown", "published_cleanup_pending"}
            or unresolved_cleanup is not None):
        raise MigrationError("source_has_unresolved_work")


def _rows(connection: sqlite3.Connection, table: str, order: str) -> tuple[tuple[Any, ...], ...]:
    return tuple(tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"))


def _migrate_connection(connection: sqlite3.Connection, module) -> dict[str, int]:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    _validate_v1(connection, module)
    before_jobs = _rows(connection, "jobs", "job_id")
    before_positions = _rows(connection, "positions", "job_id, position")
    connection.execute("BEGIN EXCLUSIVE")
    try:
        connection.execute(module._CREATE_POSITION_DETAILS)
        connection.execute(f"PRAGMA user_version = {module.SCHEMA_VERSION}")
        connection.commit()
    except Exception:
        connection.rollback()
        raise MigrationError("migration_transaction_failed") from None
    try:
        app_id = connection.execute("PRAGMA application_id").fetchone()[0]
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        module.ManualSaveJobsLedger._validate_schema(connection, app_id, version, _objects(connection))
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise MigrationError("candidate_integrity_invalid")
        module.ManualSaveJobsLedger._validate_contents(connection)
        if (_rows(connection, "jobs", "job_id") != before_jobs
                or _rows(connection, "positions", "job_id, position") != before_positions
                or connection.execute("SELECT COUNT(*) FROM position_details").fetchone()[0] != 0):
            raise MigrationError("candidate_data_mismatch")
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("candidate_validation_failed") from None
    return {
        "jobs": len(before_jobs),
        "positions": len(before_positions),
    }


def _connection_from_bytes(database_bytes: bytes) -> sqlite3.Connection:
    if not hasattr(sqlite3.Connection, "deserialize"):
        raise MigrationError("sqlite_deserialize_unavailable")
    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.deserialize(database_bytes)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    except Exception:
        connection.close()
        raise MigrationError("source_database_invalid") from None


def _sqlite_deserialize_image(snapshot_bytes: bytes) -> bytes:
    """Normalize only the private work image; preserve the sealed source bytes."""
    if len(snapshot_bytes) < 100 or snapshot_bytes[:16] != b"SQLite format 3\x00":
        raise MigrationError("source_database_invalid")
    read_version, write_version = snapshot_bytes[18], snapshot_bytes[19]
    if (read_version, write_version) == (1, 1):
        return snapshot_bytes
    if (read_version, write_version) == (2, 2):
        # SQLite backup() can preserve WAL journaling mode in the image header
        # even though the backup is self-contained. :memory: has no WAL sidecar;
        # normalize a private bytearray copy to rollback-journal mode for deserialize.
        working = bytearray(snapshot_bytes)
        working[18] = 1
        working[19] = 1
        return bytes(working)
    raise MigrationError("source_journal_mode_invalid")


def _read_sealed_input(fd: int) -> bytes:
    if sys.platform != "linux" or fcntl is None:
        raise MigrationError("sealed_memfd_unavailable")
    if not hasattr(fcntl, "F_GET_SEALS") or not hasattr(os, "memfd_create"):
        raise MigrationError("sealed_memfd_unavailable")
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or info.st_size > MAX_SNAPSHOT_BYTES:
            raise MigrationError("snapshot_size_or_type_rejected")
        seals = fcntl.fcntl(fd, fcntl.F_GET_SEALS)
        if seals & REQUIRED_SEALS != REQUIRED_SEALS:
            raise MigrationError("snapshot_not_fully_sealed")
        raw = bytearray()
        offset = 0
        while offset < info.st_size:
            chunk = os.pread(fd, min(1024 * 1024, info.st_size - offset), offset)
            if not chunk:
                raise MigrationError("snapshot_short_read")
            raw.extend(chunk)
            offset += len(chunk)
        after = os.fstat(fd)
        if (after.st_dev, after.st_ino, after.st_size) != (info.st_dev, info.st_ino, info.st_size):
            raise MigrationError("snapshot_changed")
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
            raise MigrationError("snapshot_seals_changed")
        return bytes(raw)
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("snapshot_fd_invalid") from None


def _migrate_bytes(source_bytes: bytes, module) -> tuple[bytes, dict[str, int]]:
    work_image = _sqlite_deserialize_image(source_bytes)
    source = _connection_from_bytes(work_image)
    try:
        _validate_v1(source, module)
        counts = _migrate_connection(source, module)
        if not hasattr(source, "serialize"):
            raise MigrationError("sqlite_serialize_unavailable")
        candidate_bytes = source.serialize()
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("migration_failed") from None
    finally:
        source.close()
    candidate = _connection_from_bytes(candidate_bytes)
    try:
        app_id = candidate.execute("PRAGMA application_id").fetchone()[0]
        version = candidate.execute("PRAGMA user_version").fetchone()[0]
        module.ManualSaveJobsLedger._validate_schema(candidate, app_id, version, _objects(candidate))
        integrity = candidate.execute("PRAGMA integrity_check").fetchone()
        if integrity is None or integrity[0] != "ok":
            raise MigrationError("candidate_integrity_invalid")
        module.ManualSaveJobsLedger._validate_contents(candidate)
        if candidate.execute("SELECT COUNT(*) FROM position_details").fetchone()[0] != 0:
            raise MigrationError("candidate_data_mismatch")
    except MigrationError:
        raise
    except Exception:
        raise MigrationError("candidate_validation_failed") from None
    finally:
        candidate.close()
    return candidate_bytes, counts


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sealed_memfd(name: str, data: bytes) -> int:
    if sys.platform != "linux" or fcntl is None or not hasattr(os, "memfd_create"):
        raise MigrationError("sealed_memfd_unavailable")
    if not data or len(data) > MAX_SNAPSHOT_BYTES:
        raise MigrationError("artifact_size_rejected")
    fd = -1
    try:
        fd = os.memfd_create(name, os.MFD_CLOEXEC | os.MFD_ALLOW_SEALING)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise MigrationError("artifact_write_failed")
            view = view[written:]
        fcntl.fcntl(fd, fcntl.F_ADD_SEALS, REQUIRED_SEALS)
        if fcntl.fcntl(fd, fcntl.F_GET_SEALS) & REQUIRED_SEALS != REQUIRED_SEALS:
            raise MigrationError("artifact_seal_failed")
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size != len(data):
            raise MigrationError("artifact_size_mismatch")
        verify_digest = hashlib.sha256()
        offset = 0
        while offset < info.st_size:
            chunk = os.pread(fd, min(1024 * 1024, info.st_size - offset), offset)
            if not chunk:
                raise MigrationError("artifact_short_read")
            verify_digest.update(chunk)
            offset += len(chunk)
        if offset != len(data) or verify_digest.hexdigest() != _digest(data):
            raise MigrationError("artifact_readback_mismatch")
        return fd
    except MigrationError:
        if fd >= 0:
            os.close(fd)
        raise
    except Exception:
        if fd >= 0:
            os.close(fd)
        raise MigrationError("artifact_seal_failed") from None


def _recv_one_sealed_fd(channel: socket.socket) -> int:
    if channel.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_SEQPACKET:
        raise MigrationError("socket_type_rejected")
    max_fds = 16
    data, ancillary, flags, _address = channel.recvmsg(
        64, socket.CMSG_SPACE(max_fds * array("i").itemsize),
        getattr(socket, "MSG_CMSG_CLOEXEC", 0),
    )
    descriptors: list[int] = []
    valid_rights = True
    for level, kind, payload in ancillary:
        if level != socket.SOL_SOCKET or kind != socket.SCM_RIGHTS or len(payload) % array("i").itemsize:
            valid_rights = False
            continue
        received = array("i")
        received.frombytes(payload[:len(payload) - (len(payload) % received.itemsize)])
        descriptors.extend(received.tolist())
    if flags & (getattr(socket, "MSG_TRUNC", 0) | getattr(socket, "MSG_CTRUNC", 0)):
        valid_rights = False
    if data != PROTOCOL_REQUEST or not valid_rights or len(descriptors) != 1:
        for received_fd in descriptors:
            try:
                os.close(received_fd)
            except OSError:
                pass
        raise MigrationError("request_packet_invalid")
    return descriptors[0]


def _send_result(channel: socket.socket, report: dict[str, object], descriptors: tuple[int, int] | None) -> None:
    payload = json.dumps(report, sort_keys=True, separators=(",", ":")).encode("ascii")
    ancillary = []
    if descriptors is not None:
        passed = array("i", descriptors)
        ancillary = [(socket.SOL_SOCKET, socket.SCM_RIGHTS, passed.tobytes())]
    sent = channel.sendmsg([payload], ancillary)
    if sent != len(payload):
        raise MigrationError("result_handoff_failed")


def _handle_socket_fd(fd: int, module=None) -> bool:
    channel = socket.socket(fileno=os.dup(fd))
    input_fd = -1
    output_fds: list[int] = []
    succeeded = False
    try:
        try:
            if module is None:
                module = _ledger_module()
            input_fd = _recv_one_sealed_fd(channel)
            source_bytes = _read_sealed_input(input_fd)
            candidate_bytes, counts = _migrate_bytes(source_bytes, module)
            original_fd = _sealed_memfd("fnmusic-v1-original", source_bytes)
            output_fds.append(original_fd)
            candidate_fd = _sealed_memfd("fnmusic-v2-candidate", candidate_bytes)
            output_fds.append(candidate_fd)
            report: dict[str, object] = {
                "format": PROTOCOL_REPORT,
                "status": "CANDIDATE_READY",
                "source_schema_version": V1_SCHEMA_VERSION,
                "candidate_schema_version": module.SCHEMA_VERSION,
                "source_sha256": _digest(source_bytes),
                "candidate_sha256": _digest(candidate_bytes),
                "source_bytes": len(source_bytes),
                "candidate_bytes": len(candidate_bytes),
                "counts": counts,
                "artifact_order": ["original-v1", "candidate-v2"],
                "unverified_gates": [
                    "trusted_caller_sqlite_backup_attestation_truth",
                    "snapshot_origin_and_consistency",
                    "production_writer_stopped",
                    "execution_isolation",
                    "receiver_safe_persistence",
                    "service_restore_and_lifecycle",
                    "identity_boundary",
                    "deployment_and_rollback",
                ],
            }
            _send_result(channel, report, (original_fd, candidate_fd))
            succeeded = True
        except MigrationError as error:
            try:
                _send_result(channel, {"status": "BLOCKED", "reason": error.code}, None)
            except Exception:
                pass
        except Exception:
            try:
                _send_result(channel, {"status": "BLOCKED", "reason": "migration_failed"}, None)
            except Exception:
                pass
    finally:
        for output_fd in output_fds:
            try:
                os.close(output_fd)
            except OSError:
                pass
        if input_fd >= 0:
            try:
                os.close(input_fd)
            except OSError:
                pass
        channel.close()
    return succeeded


def main(argv: list[str] | None = None) -> int:
    sys.dont_write_bytecode = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket-fd", required=True, type=int,
                        help="inherited connected AF_UNIX SOCK_SEQPACKET socket descriptor")
    args = parser.parse_args(argv)
    if sys.platform != "linux" or fcntl is None:
        print(json.dumps({"status": "BLOCKED", "reason": "linux_fd_handoff_required"}))
        return 2
    try:
        return 0 if _handle_socket_fd(args.socket_fd) else 2
    except MigrationError as error:
        return 2
    except Exception:
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
