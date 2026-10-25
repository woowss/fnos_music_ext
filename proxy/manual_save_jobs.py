"""Offline SQLite ledger for explicitly dispatched manual-save positions.

This module stores job intent and byte-publication outcomes only. It does not
authenticate callers, access media, publish files, or start background work.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import secrets
import sqlite3
from typing import Callable

try:  # Support both ``proxy.manual_save_jobs`` and a direct proxy path import.
    from .manual_save_core import Outcome, SaveResult
except ImportError:  # pragma: no cover - exercised by the repository's test style.
    from manual_save_core import Outcome, SaveResult


APPLICATION_ID = 0x464E4A42  # "FNJB"
SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 1000
MAX_JOBS = 1000
MAX_POSITIONS = 10_000
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ENTRIES_PER_JOB = 1000
MAX_IDENTIFIER_CHARS = 128

_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_JOB_ID_RE = re.compile(r"^[0-9a-f]{48}$")
_SOURCE_STATUSES = frozenset({"ok", "invalid_id", "unavailable", "missing"})
_POSITION_STATES = frozenset({"queued", "running"} | {item.value for item in Outcome})
_OUTCOME_REASONS = {
    Outcome.PUBLISHED.value: frozenset({"bytes_published"}),
    Outcome.PUBLISHED_CLEANUP_PENDING.value: frozenset({"published_cleanup_pending"}),
    Outcome.CONFLICT.value: frozenset({"destination_conflict"}),
    Outcome.CANCELLED.value: frozenset({"cancelled_before_publish"}),
    Outcome.REJECTED.value: frozenset({
        "invalid_target", "invalid_format", "unsupported_ncm", "unsupported_format",
        "invalid_basename", "invalid_limit", "invalid_length", "invalid_plan",
        "invalid_chunks", "invalid_storage", "invalid_cancel_check", "too_many_chunks",
        "invalid_chunk", "empty_chunk", "chunk_too_large", "stream_too_long",
        "length_mismatch",
    }),
    Outcome.FAILED.value: frozenset({"stage_unavailable", "staging_failed", "cancel_check_failed"}),
    Outcome.COMMIT_UNKNOWN.value: frozenset({"commit_unknown"}),
}


_CREATE_JOBS = """CREATE TABLE jobs (
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
_CREATE_POSITIONS = """CREATE TABLE positions (
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
_CREATE_RUNNING_INDEX = "CREATE UNIQUE INDEX one_running_position_per_job ON positions(job_id) WHERE state = 'running'"
_EXPECTED_TABLES = {"jobs": _CREATE_JOBS, "positions": _CREATE_POSITIONS}
_EXPECTED_INDEXES = {"one_running_position_per_job": _CREATE_RUNNING_INDEX}
_EXPECTED_INTERNAL_INDEXES = frozenset({
    ("index", "sqlite_autoindex_jobs_1"),
    ("index", "sqlite_autoindex_jobs_2"),
    ("index", "sqlite_autoindex_positions_1"),
})


class LedgerError(Exception):
    """Base error with a fixed, non-sensitive public code."""

    code = "ledger_error"

    def __init__(self, code: str | None = None):
        self.code = code or type(self).code
        super().__init__(self.code)


class ValidationError(LedgerError):
    code = "invalid_request"


class NotFoundError(LedgerError):
    code = "not_found"


class IdempotencyConflict(LedgerError):
    code = "idempotency_conflict"


class CapacityExceeded(LedgerError):
    code = "capacity_exceeded"


class SchemaError(LedgerError):
    code = "invalid_schema"


class CorruptLedgerError(LedgerError):
    code = "corrupt_ledger"


class LedgerUnavailable(LedgerError):
    code = "ledger_unavailable"


@dataclass(frozen=True, slots=True)
class PositionSnapshot:
    position: int
    source_id: str | None
    source_status: str
    state: str
    outcome: str | None
    reason: str | None
    cleanup_pending: bool | None


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    job_id: str
    manifest: dict[str, object]
    manifest_sha256: str
    cancel_requested: bool
    state: str
    positions: tuple[PositionSnapshot, ...]
    counts: dict[str, int]
    audio_verified: bool = False
    production_ready: bool = False
    import_ready: bool = False


@dataclass(frozen=True, slots=True)
class RunReport:
    status: str
    position: PositionSnapshot | None = None
    result: SaveResult | None = None


def _validate_identifier(value: object) -> str:
    if type(value) is not str or len(value) > MAX_IDENTIFIER_CHARS or not _IDENTIFIER_RE.fullmatch(value):
        raise ValidationError("invalid_identifier")
    return value


def _validate_manifest(manifest: object) -> tuple[dict[str, object], bytes, str]:
    required = {
        "kind", "target_id", "provider_id", "collection_id", "collection_version",
        "enumeration_complete", "entries",
    }
    if type(manifest) is not dict or set(manifest) != required:
        raise ValidationError("invalid_manifest_fields")

    kind = manifest["kind"]
    if type(kind) is not str or kind not in {"single", "playlist"}:
        raise ValidationError("invalid_kind")
    target_id = _validate_identifier(manifest["target_id"])
    provider_id = _validate_identifier(manifest["provider_id"])
    collection_id = _validate_identifier(manifest["collection_id"])
    collection_version = _validate_identifier(manifest["collection_version"])
    complete = manifest["enumeration_complete"]
    if type(complete) is not bool:
        raise ValidationError("invalid_enumeration_complete")
    entries = manifest["entries"]
    if type(entries) is not list:
        raise ValidationError("invalid_entries")
    if kind == "single" and len(entries) != 1:
        raise ValidationError("single_requires_one_entry")
    if kind == "playlist" and not 1 <= len(entries) <= MAX_ENTRIES_PER_JOB:
        raise ValidationError("invalid_playlist_size")

    canonical_entries: list[dict[str, object]] = []
    for expected_position, entry in enumerate(entries, start=1):
        if type(entry) is not dict or set(entry) != {"position", "source_id", "source_status"}:
            raise ValidationError("invalid_entry_fields")
        position = entry["position"]
        if type(position) is not int or position != expected_position:
            raise ValidationError("invalid_position_order")
        source_id = entry["source_id"]
        if source_id is not None:
            source_id = _validate_identifier(source_id)
        source_status = entry["source_status"]
        if type(source_status) is not str or source_status not in _SOURCE_STATUSES:
            raise ValidationError("invalid_source_status")
        canonical_entries.append({
            "position": position,
            "source_id": source_id,
            "source_status": source_status,
        })

    canonical: dict[str, object] = {
        "kind": kind,
        "target_id": target_id,
        "provider_id": provider_id,
        "collection_id": collection_id,
        "collection_version": collection_version,
        "enumeration_complete": complete,
        "entries": canonical_entries,
    }
    try:
        manifest_bytes = json.dumps(
            canonical, ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError):
        raise ValidationError("invalid_manifest") from None
    if len(manifest_bytes) > MAX_MANIFEST_BYTES:
        raise ValidationError("manifest_too_large")
    return canonical, manifest_bytes, hashlib.sha256(manifest_bytes).hexdigest()


def _manifest_from_bytes(value: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(value.decode("utf-8"), parse_constant=lambda _item: (_ for _ in ()).throw(ValueError()))
        canonical, encoded, _digest = _validate_manifest(decoded)
    except Exception:
        raise CorruptLedgerError() from None
    if encoded != value:
        raise CorruptLedgerError()
    return canonical


def _fixed_result(outcome: Outcome = Outcome.COMMIT_UNKNOWN) -> SaveResult:
    return SaveResult(outcome, "commit_unknown", cleanup_pending=True)


def _validated_result(value: object) -> SaveResult | None:
    if type(value) is not SaveResult:
        return None
    outcome = value.outcome
    if type(outcome) is not Outcome:
        return None
    if not _valid_outcome_fields(outcome.value, value.reason, value.cleanup_pending, database_value=False):
        return None
    if type(value.audio_verified) is not bool or value.audio_verified is not False:
        return None
    if type(value.production_ready) is not bool or value.production_ready is not False:
        return None
    return value


def _valid_outcome_fields(outcome: object, reason: object, cleanup_pending: object, *, database_value: bool) -> bool:
    expected_cleanup_type = int if database_value else bool
    if type(outcome) is not str or outcome not in _OUTCOME_REASONS:
        return False
    if type(reason) is not str or reason not in _OUTCOME_REASONS[outcome]:
        return False
    if type(cleanup_pending) is not expected_cleanup_type:
        return False
    if cleanup_pending not in ((0, 1) if database_value else (False, True)):
        return False
    if outcome in {Outcome.COMMIT_UNKNOWN.value, Outcome.PUBLISHED_CLEANUP_PENDING.value}:
        return cleanup_pending in (1, True)
    if outcome == Outcome.PUBLISHED.value:
        return cleanup_pending in (0, False)
    return True


def _validate_stored_position(row: sqlite3.Row) -> bool | None:
    """Validate raw SQLite values before any snapshot conversion."""
    try:
        position = row["position"]
        source_id = row["source_id"]
        source_status = row["source_status"]
        state = row["state"]
        outcome = row["outcome"]
        reason = row["reason"]
        cleanup_pending = row["cleanup_pending"]
        claim_token = row["claim_token"]
        if type(position) is not int or position < 1:
            raise ValueError
        if source_id is not None:
            _validate_identifier(source_id)
        if type(source_status) is not str or source_status not in _SOURCE_STATUSES:
            raise ValueError
        if type(state) is not str or state not in _POSITION_STATES:
            raise ValueError
        if state == "queued":
            if any(value is not None for value in (outcome, reason, cleanup_pending, claim_token)):
                raise ValueError
            return None
        if state == "running":
            if (
                outcome is not None or reason is not None or cleanup_pending is not None
                or type(claim_token) is not str or not _JOB_ID_RE.fullmatch(claim_token)
            ):
                raise ValueError
            return None
        if (
            outcome != state
            or claim_token is not None
            or not _valid_outcome_fields(outcome, reason, cleanup_pending, database_value=True)
        ):
            raise ValueError
        return bool(cleanup_pending)
    except CorruptLedgerError:
        raise
    except Exception:
        raise CorruptLedgerError() from None


def _validate_job_row(job: sqlite3.Row) -> dict[str, object]:
    try:
        if type(job["job_id"]) is not str or not _JOB_ID_RE.fullmatch(job["job_id"]):
            raise ValueError
        _validate_identifier(job["actor_scope"])
        _validate_identifier(job["idempotency_key"])
        manifest_bytes = job["manifest_json"]
        if type(manifest_bytes) is not bytes or len(manifest_bytes) > MAX_MANIFEST_BYTES:
            raise ValueError
        manifest = _manifest_from_bytes(manifest_bytes)
        digest = hashlib.sha256(manifest_bytes).hexdigest()
        if digest != job["manifest_sha256"]:
            raise ValueError
        if (
            job["kind"] != manifest["kind"]
            or job["target_id"] != manifest["target_id"]
            or job["provider_id"] != manifest["provider_id"]
            or job["collection_id"] != manifest["collection_id"]
            or job["collection_version"] != manifest["collection_version"]
            or type(job["enumeration_complete"]) is not int
            or job["enumeration_complete"] not in (0, 1)
            or bool(job["enumeration_complete"]) is not manifest["enumeration_complete"]
            or type(job["cancel_requested"]) is not int
            or job["cancel_requested"] not in (0, 1)
        ):
            raise ValueError
        return manifest
    except CorruptLedgerError:
        raise
    except Exception:
        raise CorruptLedgerError() from None


class ManualSaveJobsLedger:
    """A caller-owned, durable SQLite ledger with explicit synchronous dispatch."""

    def __init__(self, database_path: str | os.PathLike[str]):
        try:
            path = os.fspath(database_path)
        except TypeError:
            raise ValidationError("absolute_database_path_required") from None
        if type(path) is not str or not os.path.isabs(path):
            raise ValidationError("absolute_database_path_required")
        self._database_path = path
        self._initialize_or_validate()

    def _connect(self) -> sqlite3.Connection:
        connection = None
        try:
            connection = sqlite3.connect(
                self._database_path,
                timeout=BUSY_TIMEOUT_MS / 1000,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
            foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
            synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]
            if foreign_keys != 1 or synchronous != 2:
                connection.close()
                raise LedgerUnavailable()
            return connection
        except LedgerError:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            raise
        except sqlite3.DatabaseError as error:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            corrupt_codes = {
                getattr(sqlite3, "SQLITE_CORRUPT", -1),
                getattr(sqlite3, "SQLITE_NOTADB", -2),
            }
            if getattr(error, "sqlite_errorcode", None) in corrupt_codes:
                raise CorruptLedgerError() from None
            raise LedgerUnavailable() from None
        except sqlite3.Error:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass
            raise LedgerUnavailable() from None

    def _initialize_or_validate(self) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
            objects = connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
            ).fetchall()

            is_new_database = not objects and application_id == 0 and schema_version == 0
            if not is_new_database:
                self._validate_schema(connection, application_id, schema_version, objects)

            integrity = connection.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or integrity[0] != "ok":
                raise CorruptLedgerError()

            if is_new_database:
                connection.execute(_CREATE_JOBS)
                connection.execute(_CREATE_POSITIONS)
                connection.execute(_CREATE_RUNNING_INDEX)
                connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
            self._validate_contents(connection)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.DatabaseError as error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            corrupt_codes = {
                getattr(sqlite3, "SQLITE_CORRUPT", -1),
                getattr(sqlite3, "SQLITE_NOTADB", -2),
            }
            if getattr(error, "sqlite_errorcode", None) in corrupt_codes:
                raise CorruptLedgerError() from None
            raise LedgerUnavailable() from None
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    @staticmethod
    def _validate_schema(connection, application_id, schema_version, objects) -> None:
        expected_objects = {
            ("table", name): sql for name, sql in _EXPECTED_TABLES.items()
        }
        expected_objects.update({
            ("index", name): sql for name, sql in _EXPECTED_INDEXES.items()
        })
        expected_objects.update({name: None for name in _EXPECTED_INTERNAL_INDEXES})
        actual_objects = {(row["type"], row["name"]): row["sql"] for row in objects}
        if len(actual_objects) != len(objects) or set(actual_objects) != set(expected_objects):
            raise SchemaError()
        if application_id != APPLICATION_ID or schema_version != SCHEMA_VERSION:
            raise SchemaError("unknown_schema_version")
        for object_key, expected_sql in expected_objects.items():
            actual_sql = actual_objects[object_key]
            if expected_sql is None:
                if actual_sql is not None:
                    raise SchemaError()
            elif type(actual_sql) is not str or actual_sql != expected_sql:
                raise SchemaError()
        foreign_keys = connection.execute("PRAGMA foreign_key_list(positions)").fetchall()
        if len(foreign_keys) != 1 or tuple(foreign_keys[0][2:6]) != ("jobs", "job_id", "job_id", "NO ACTION"):
            raise SchemaError()
        if foreign_keys[0][6] != "CASCADE":
            raise SchemaError()
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            raise SchemaError()

    @staticmethod
    def _validate_contents(connection: sqlite3.Connection) -> None:
        jobs = connection.execute("SELECT * FROM jobs ORDER BY job_id").fetchall()
        if len(jobs) > MAX_JOBS:
            raise CorruptLedgerError()
        position_total = connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        if position_total > MAX_POSITIONS:
            raise CorruptLedgerError()
        for job in jobs:
            manifest = _validate_job_row(job)
            entries = manifest["entries"]
            rows = connection.execute(
                "SELECT * FROM positions WHERE job_id = ? ORDER BY position", (job["job_id"],),
            ).fetchall()
            if len(rows) != len(entries):
                raise CorruptLedgerError()
            running_count = 0
            for expected, row in zip(entries, rows, strict=True):
                _validate_stored_position(row)
                if (
                    row["position"] != expected["position"]
                    or row["source_id"] != expected["source_id"]
                    or row["source_status"] != expected["source_status"]
                ):
                    raise CorruptLedgerError()
                if row["state"] == "running":
                    running_count += 1
            if running_count > 1:
                raise CorruptLedgerError()
        foreign_key_errors = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise CorruptLedgerError()

    def _begin_immediate(self, connection: sqlite3.Connection) -> None:
        try:
            connection.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            raise LedgerUnavailable() from None

    def create_job(self, actor_scope: object, idempotency_key: object, manifest: object) -> JobSnapshot:
        scope = _validate_identifier(actor_scope)
        key = _validate_identifier(idempotency_key)
        canonical, manifest_bytes, digest = _validate_manifest(manifest)
        entries = canonical["entries"]
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            prior = connection.execute(
                "SELECT job_id, manifest_json FROM jobs WHERE actor_scope = ? AND idempotency_key = ?",
                (scope, key),
            ).fetchone()
            if prior is not None:
                if bytes(prior["manifest_json"]) != manifest_bytes:
                    raise IdempotencyConflict()
                connection.commit()
                return self._snapshot(connection, prior["job_id"])

            jobs_count = connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            positions_count = connection.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            if jobs_count + 1 > MAX_JOBS or positions_count + len(entries) > MAX_POSITIONS:
                raise CapacityExceeded()

            job_id = secrets.token_hex(24)
            connection.execute(
                """INSERT INTO jobs (
                    job_id, actor_scope, idempotency_key, kind, target_id, provider_id,
                    collection_id, collection_version, enumeration_complete,
                    manifest_json, manifest_sha256, cancel_requested
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (
                    job_id, scope, key, canonical["kind"], canonical["target_id"],
                    canonical["provider_id"], canonical["collection_id"], canonical["collection_version"],
                    int(canonical["enumeration_complete"]), sqlite3.Binary(manifest_bytes), digest,
                ),
            )
            connection.executemany(
                "INSERT INTO positions (job_id, position, source_id, source_status, state) VALUES (?, ?, ?, ?, 'queued')",
                [
                    (job_id, entry["position"], entry["source_id"], entry["source_status"])
                    for entry in entries
                ],
            )
            connection.commit()
            return self._snapshot(connection, job_id)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def get_job(self, actor_scope: object, job_id: object) -> JobSnapshot:
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE actor_scope = ? AND job_id = ?", (scope, identifier),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            return self._snapshot(connection, identifier)
        except LedgerError:
            raise
        except sqlite3.Error:
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def cancel_job(self, actor_scope: object, job_id: object) -> JobSnapshot:
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            row = connection.execute(
                "SELECT job_id FROM jobs WHERE actor_scope = ? AND job_id = ?", (scope, identifier),
            ).fetchone()
            if row is None:
                raise NotFoundError()
            connection.execute("UPDATE jobs SET cancel_requested = 1 WHERE job_id = ?", (identifier,))
            connection.execute(
                """UPDATE positions
                   SET state = 'cancelled', outcome = 'cancelled', reason = 'cancelled_before_publish',
                       cleanup_pending = 0, claim_token = NULL
                   WHERE job_id = ? AND state = 'queued'""",
                (identifier,),
            )
            connection.commit()
            return self._snapshot(connection, identifier)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def run_next(
        self,
        actor_scope: object,
        job_id: object,
        executor: Callable[[PositionSnapshot, Callable[[], bool]], object],
    ) -> RunReport:
        """Claim one earliest position, commit it, then invoke the executor outside SQL."""
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        if not callable(executor):
            raise ValidationError("invalid_executor")
        claimed = self._claim_next(scope, identifier)
        if claimed is None:
            return RunReport("no_work")
        position, claim_token = claimed

        def cancellation_requested() -> bool:
            try:
                return self.get_job(scope, identifier).cancel_requested
            except Exception:
                return True

        try:
            candidate = executor(position, cancellation_requested)
        except Exception:
            candidate = None
        result = _validated_result(candidate)
        if result is None:
            result = _fixed_result()

        completion = self._complete(scope, identifier, position.position, claim_token, result)
        if completion is None:
            return RunReport("completion_unknown", result=_fixed_result())
        if completion is False:
            return RunReport("stale_completion", position=position, result=result)
        status = "commit_unknown" if result.outcome is Outcome.COMMIT_UNKNOWN else "completed"
        final_position = PositionSnapshot(
            position=position.position,
            source_id=position.source_id,
            source_status=position.source_status,
            state=result.outcome.value,
            outcome=result.outcome.value,
            reason=result.reason,
            cleanup_pending=result.cleanup_pending,
        )
        return RunReport(status, position=final_position, result=result)

    def _claim_next(self, actor_scope: str, job_id: str) -> tuple[PositionSnapshot, str] | None:
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            job = connection.execute(
                "SELECT * FROM jobs WHERE actor_scope = ? AND job_id = ?", (actor_scope, job_id),
            ).fetchone()
            if job is None:
                raise NotFoundError()
            if connection.execute(
                "SELECT 1 FROM positions WHERE job_id = ? AND state = 'commit_unknown' LIMIT 1", (job_id,),
            ).fetchone() is not None:
                connection.commit()
                return None
            if connection.execute(
                "SELECT 1 FROM positions WHERE job_id = ? AND state = 'running' LIMIT 1", (job_id,),
            ).fetchone() is not None:
                connection.commit()
                return None
            if job["cancel_requested"]:
                connection.commit()
                return None
            if not job["enumeration_complete"]:
                connection.commit()
                return None
            row = connection.execute(
                "SELECT * FROM positions WHERE job_id = ? AND state = 'queued' ORDER BY position LIMIT 1",
                (job_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            token = secrets.token_hex(24)
            cursor = connection.execute(
                "UPDATE positions SET state = 'running', claim_token = ? WHERE job_id = ? AND position = ? AND state = 'queued'",
                (token, job_id, row["position"]),
            )
            if cursor.rowcount != 1:
                raise LedgerUnavailable()
            connection.commit()
            position = PositionSnapshot(
                position=row["position"],
                source_id=row["source_id"],
                source_status=row["source_status"],
                state="running",
                outcome=None,
                reason=None,
                cleanup_pending=None,
            )
            return position, token
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def _complete(
        self, actor_scope: str, job_id: str, position: int, claim_token: str, result: SaveResult,
    ) -> bool | None:
        connection = None
        try:
            connection = self._connect()
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """UPDATE positions SET state = ?, outcome = ?, reason = ?, cleanup_pending = ?, claim_token = NULL
                   WHERE job_id = ? AND position = ? AND state = 'running' AND claim_token = ?
                   AND EXISTS (SELECT 1 FROM jobs WHERE job_id = ? AND actor_scope = ?)""",
                (
                    result.outcome.value, result.outcome.value, result.reason, int(result.cleanup_pending),
                    job_id, position, claim_token, job_id, actor_scope,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                return False
            connection.commit()
            return True
        except LedgerError:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            return None
        except sqlite3.Error:
            if connection is not None:
                try:
                    connection.rollback()
                except sqlite3.Error:
                    pass
            return None
        finally:
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

    def recover_stale_running(
        self, actor_scope: object, job_id: object, *, worker_stopped: bool,
    ) -> JobSnapshot:
        """Explicitly freeze running positions after the caller asserts the old worker stopped."""
        scope = _validate_identifier(actor_scope)
        identifier = self._validate_job_id(job_id)
        if type(worker_stopped) is not bool or not worker_stopped:
            raise ValidationError("worker_stopped_assertion_required")
        connection = self._connect()
        try:
            self._begin_immediate(connection)
            job = connection.execute(
                "SELECT job_id FROM jobs WHERE actor_scope = ? AND job_id = ?", (scope, identifier),
            ).fetchone()
            if job is None:
                raise NotFoundError()
            connection.execute(
                """UPDATE positions
                   SET state = 'commit_unknown', outcome = 'commit_unknown', reason = 'commit_unknown',
                       cleanup_pending = 1, claim_token = NULL
                   WHERE job_id = ? AND state = 'running'""",
                (identifier,),
            )
            connection.commit()
            return self._snapshot(connection, identifier)
        except LedgerError:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise
        except sqlite3.Error:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
            raise LedgerUnavailable() from None
        finally:
            connection.close()

    def _snapshot(self, connection: sqlite3.Connection, job_id: str) -> JobSnapshot:
        job = connection.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if job is None:
            raise NotFoundError()
        manifest = _validate_job_row(job)
        rows = connection.execute(
            "SELECT * FROM positions WHERE job_id = ? ORDER BY position", (job_id,),
        ).fetchall()
        entries = manifest["entries"]
        if len(rows) != len(entries):
            raise CorruptLedgerError()
        positions_list = []
        running_count = 0
        for expected, row in zip(entries, rows, strict=True):
            cleanup_pending = _validate_stored_position(row)
            if (
                row["position"] != expected["position"]
                or row["source_id"] != expected["source_id"]
                or row["source_status"] != expected["source_status"]
            ):
                raise CorruptLedgerError()
            if row["state"] == "running":
                running_count += 1
            positions_list.append(PositionSnapshot(
                position=row["position"],
                source_id=row["source_id"],
                source_status=row["source_status"],
                state=row["state"],
                outcome=row["outcome"],
                reason=row["reason"],
                cleanup_pending=cleanup_pending,
            ))
        if running_count > 1:
            raise CorruptLedgerError()
        positions = tuple(positions_list)
        counts = {state: 0 for state in sorted(_POSITION_STATES)}
        for position in positions:
            counts[position.state] += 1
        if counts[Outcome.COMMIT_UNKNOWN.value]:
            summary_state = "reconciliation_required"
        elif counts["running"]:
            summary_state = "cancelling" if job["cancel_requested"] else "running"
        elif counts["queued"]:
            summary_state = "cancelled" if job["cancel_requested"] else "queued"
        elif counts["cancelled"] == len(positions):
            summary_state = "cancelled"
        elif counts[Outcome.PUBLISHED.value] == len(positions):
            summary_state = "completed_bytes_reported"
        elif counts[Outcome.PUBLISHED.value] or counts[Outcome.PUBLISHED_CLEANUP_PENDING.value]:
            summary_state = "partial"
        else:
            summary_state = "completed_with_issues"
        return JobSnapshot(
            job_id=job_id,
            manifest=manifest,
            manifest_sha256=job["manifest_sha256"],
            cancel_requested=bool(job["cancel_requested"]),
            state=summary_state,
            positions=positions,
            counts=counts,
            audio_verified=False,
            production_ready=False,
            import_ready=False,
        )

    @staticmethod
    def _validate_job_id(job_id: object) -> str:
        if type(job_id) is not str or not _JOB_ID_RE.fullmatch(job_id):
            raise NotFoundError()
        return job_id


__all__ = [
    "CapacityExceeded", "CorruptLedgerError", "IdempotencyConflict", "JobSnapshot",
    "LedgerError", "LedgerUnavailable", "ManualSaveJobsLedger", "NotFoundError",
    "PositionSnapshot", "RunReport", "SchemaError", "ValidationError",
]
