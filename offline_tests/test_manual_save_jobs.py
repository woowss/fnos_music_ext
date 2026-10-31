"""Real-SQLite tests for the offline manual-save job ledger."""

from __future__ import annotations

import importlib
import importlib.util
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

jobs = importlib.import_module("proxy.manual_save_jobs")
core = importlib.import_module("proxy.manual_save_core")


def _load_import_probe(module_name):
    probe_name = f"proxy._{module_name}_import_probe"
    source_path = Path(__file__).resolve().parents[1] / "proxy" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(probe_name, source_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"cannot load import probe for {module_name}")
    module = importlib.util.module_from_spec(spec)
    if probe_name in sys.modules:
        raise AssertionError(f"import probe already loaded: {probe_name}")
    sys.modules[probe_name] = module
    try:
        spec.loader.exec_module(module)
        return module
    finally:
        sys.modules.pop(probe_name, None)


def manifest(*, kind="playlist", entries=None, complete=True):
    if entries is None:
        entries = [
            {"position": 1, "source_id": "song-1", "source_status": "ok"},
            {"position": 2, "source_id": "song-1", "source_status": "unavailable"},
            {"position": 3, "source_id": None, "source_status": "invalid_id"},
        ]
    return {
        "kind": kind,
        "target_id": "target-1",
        "provider_id": "provider-1",
        "collection_id": "collection-1",
        "collection_version": "v1",
        "enumeration_complete": complete,
        "entries": entries,
    }


def one_entry_manifest(source_id="song-1", source_status="ok", complete=True):
    return manifest(
        kind="single",
        complete=complete,
        entries=[{"position": 1, "source_id": source_id, "source_status": source_status}],
    )


def published_result():
    return core.SaveResult(core.Outcome.PUBLISHED, "bytes_published")


class ManualSaveJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="fnmusic-jobs-")
        self.addCleanup(self.temp_dir.cleanup)
        self.database_path = str(Path(self.temp_dir.name) / "ledger.sqlite3")
        self.ledger = jobs.ManualSaveJobsLedger(self.database_path)

    def new_ledger(self):
        return jobs.ManualSaveJobsLedger(self.database_path)

    def assert_not_found(self, action):
        with self.assertRaises(jobs.NotFoundError) as caught:
            action()
        self.assertEqual(caught.exception.code, "not_found")
        self.assertEqual(str(caught.exception), "not_found")

    def db_rows(self, sql, params=()):
        connection = sqlite3.connect(self.database_path)
        try:
            return connection.execute(sql, params).fetchall()
        finally:
            connection.close()

    def database_snapshot(self, path):
        connection = sqlite3.connect(path)
        try:
            objects = tuple(connection.execute(
                "SELECT type, name, sql FROM sqlite_master ORDER BY type, name",
            ).fetchall())
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        finally:
            connection.close()
        return Path(path).read_bytes(), objects, application_id, schema_version

    def test_create_reopen_preserves_manifest_order_duplicates_errors_and_cancel_flag(self):
        created = self.ledger.create_job("actor-A", "idem-1", manifest())
        self.assertEqual([p.position for p in created.positions], [1, 2, 3])
        self.assertEqual([p.source_id for p in created.positions], ["song-1", "song-1", None])
        self.assertEqual([p.source_status for p in created.positions], ["ok", "unavailable", "invalid_id"])

        result = self.ledger.run_next("actor-A", created.job_id, lambda _p, _cancel: core.SaveResult(
            core.Outcome.FAILED, "staging_failed",
        ))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.position.reason, "staging_failed")
        cancelled = self.ledger.cancel_job("actor-A", created.job_id)
        self.assertTrue(cancelled.cancel_requested)
        self.assertEqual([p.state for p in cancelled.positions], ["failed", "cancelled", "cancelled"])

        reopened = self.new_ledger().get_job("actor-A", created.job_id)
        self.assertEqual(reopened.manifest, created.manifest)
        self.assertEqual(reopened.manifest_sha256, created.manifest_sha256)
        self.assertEqual([p.position for p in reopened.positions], [1, 2, 3])
        self.assertEqual([p.source_id for p in reopened.positions], ["song-1", "song-1", None])
        self.assertEqual([p.reason for p in reopened.positions], ["staging_failed", "cancelled_before_publish", "cancelled_before_publish"])
        self.assertTrue(reopened.cancel_requested)

    def test_manifest_validation_limits_and_incomplete_manifests_are_not_dispatched(self):
        valid = one_entry_manifest()
        invalid_manifests = []

        extra = dict(valid, arbitrary="metadata")
        invalid_manifests.append(extra)
        missing = dict(valid)
        del missing["collection_version"]
        invalid_manifests.append(missing)
        invalid_manifests.append(dict(valid, enumeration_complete=1))
        invalid_manifests.append(one_entry_manifest().copy() | {"entries": []})
        invalid_manifests.append(one_entry_manifest().copy() | {"entries": [
            {"position": True, "source_id": "song-1", "source_status": "ok"},
        ]})
        invalid_manifests.append(one_entry_manifest().copy() | {"entries": [
            {"position": 2, "source_id": "song-1", "source_status": "ok"},
        ]})
        invalid_manifests.append(one_entry_manifest().copy() | {"entries": [
            {"position": 1, "source_id": "bad/id", "source_status": "ok"},
        ]})
        invalid_manifests.append(one_entry_manifest().copy() | {"entries": [
            {"position": 1, "source_id": None, "source_status": "other"},
        ]})
        invalid_manifests.append(dict(valid, target_id="x" * (jobs.MAX_IDENTIFIER_CHARS + 1)))

        for index, item in enumerate(invalid_manifests):
            with self.subTest(index=index):
                with self.assertRaises(jobs.ValidationError):
                    self.ledger.create_job("actor-A", f"bad-{index}", item)

        too_many = manifest(entries=[
            {"position": i, "source_id": "song-1", "source_status": "ok"}
            for i in range(1, jobs.MAX_ENTRIES_PER_JOB + 2)
        ])
        with self.assertRaises(jobs.ValidationError):
            self.ledger.create_job("actor-A", "too-many", too_many)
        with mock.patch.object(jobs, "MAX_MANIFEST_BYTES", 10):
            with self.assertRaises(jobs.ValidationError) as caught:
                self.ledger.create_job("actor-A", "too-large", valid)
        self.assertEqual(caught.exception.code, "manifest_too_large")

        incomplete = self.ledger.create_job("actor-A", "incomplete", one_entry_manifest(complete=False))
        invoked = []
        report = self.ledger.run_next(
            "actor-A", incomplete.job_id, lambda *_args: invoked.append(True) or published_result(),
        )
        self.assertEqual(report.status, "no_work")
        self.assertEqual(invoked, [])
        self.assertEqual(self.ledger.get_job("actor-A", incomplete.job_id).positions[0].state, "queued")

    def test_defensive_copies_and_idempotency_compare_canonical_bytes(self):
        original = manifest()
        first = self.ledger.create_job("actor-A", "same-key", original)
        original["entries"][0]["source_id"] = "mutated"
        first.manifest["entries"][0]["source_id"] = "returned-mutation"
        second = self.ledger.get_job("actor-A", first.job_id)
        self.assertEqual(second.manifest["entries"][0]["source_id"], "song-1")
        self.assertEqual(second.positions[0].source_id, "song-1")

        # Object key order is immaterial after strict canonical serialization.
        equivalent = {
            "entries": [dict(reversed(list(entry.items()))) for entry in manifest()["entries"]],
            "enumeration_complete": True,
            "collection_version": "v1",
            "collection_id": "collection-1",
            "provider_id": "provider-1",
            "target_id": "target-1",
            "kind": "playlist",
        }
        replay = self.ledger.create_job("actor-A", "same-key", equivalent)
        self.assertEqual(replay.job_id, first.job_id)
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM jobs")[0][0], 1)

        changed = manifest()
        changed["entries"][1]["source_status"] = "missing"
        with self.assertRaises(jobs.IdempotencyConflict) as caught:
            self.ledger.create_job("actor-A", "same-key", changed)
        self.assertEqual(caught.exception.code, "idempotency_conflict")
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM jobs")[0][0], 1)

        other_actor = self.ledger.create_job("actor-B", "same-key", changed)
        self.assertNotEqual(other_actor.job_id, first.job_id)
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM jobs")[0][0], 2)

    def test_capacity_failure_rolls_back_and_idempotent_replay_does_not_consume_capacity(self):
        with mock.patch.object(jobs, "MAX_JOBS", 2), mock.patch.object(jobs, "MAX_POSITIONS", 1):
            first = self.ledger.create_job("actor-A", "key-1", one_entry_manifest())
            replay = self.ledger.create_job("actor-A", "key-1", one_entry_manifest())
            self.assertEqual(replay.job_id, first.job_id)
            with self.assertRaises(jobs.CapacityExceeded) as caught:
                self.ledger.create_job("actor-A", "key-2", one_entry_manifest())
            self.assertEqual(caught.exception.code, "capacity_exceeded")
            self.assertEqual(self.db_rows("SELECT COUNT(*) FROM jobs")[0][0], 1)
            self.assertEqual(self.db_rows("SELECT COUNT(*) FROM positions")[0][0], 1)
            self.assertEqual(self.db_rows("SELECT idempotency_key FROM jobs"), [("key-1",)])

    def test_schema_unknown_foreign_and_corrupt_databases_are_refused(self):
        with tempfile.TemporaryDirectory(prefix="fnmusic-bad-schema-") as temp:
            def reject_unchanged(path):
                before = self.database_snapshot(path)
                with self.assertRaises(jobs.SchemaError) as caught:
                    jobs.ManualSaveJobsLedger(path)
                self.assertEqual(caught.exception.code, "invalid_schema")
                self.assertEqual(self.database_snapshot(path), before)

            view_only = str(Path(temp) / "view-only.sqlite3")
            connection = sqlite3.connect(view_only)
            connection.execute("CREATE VIEW extra_view AS SELECT 1 AS value")
            connection.commit()
            connection.close()
            reject_unchanged(view_only)

            sqlite_prefix_lookalike = str(Path(temp) / "sqlite-prefix-lookalike.sqlite3")
            connection = sqlite3.connect(sqlite_prefix_lookalike)
            connection.execute("CREATE TABLE sqliteXforeign (value TEXT)")
            connection.commit()
            connection.close()
            reject_unchanged(sqlite_prefix_lookalike)

            ledger_with_view = str(Path(temp) / "ledger-with-view.sqlite3")
            jobs.ManualSaveJobsLedger(ledger_with_view)
            connection = sqlite3.connect(ledger_with_view)
            connection.execute("CREATE VIEW extra_view AS SELECT 1 AS value")
            connection.commit()
            connection.close()
            reject_unchanged(ledger_with_view)

            wrong_version = str(Path(temp) / "wrong-version.sqlite3")
            jobs.ManualSaveJobsLedger(wrong_version)
            connection = sqlite3.connect(wrong_version)
            connection.execute("PRAGMA user_version = 99")
            connection.commit()
            connection.close()
            with self.assertRaises(jobs.SchemaError) as caught:
                jobs.ManualSaveJobsLedger(wrong_version)
            self.assertEqual(caught.exception.code, "unknown_schema_version")

            corrupted = str(Path(temp) / "corrupt.sqlite3")
            original = b"not-a-sqlite-database"
            Path(corrupted).write_bytes(original)
            with self.assertRaises(jobs.CorruptLedgerError):
                jobs.ManualSaveJobsLedger(corrupted)
            self.assertEqual(Path(corrupted).read_bytes(), original)

    def test_schema_literal_case_change_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory(prefix="fnmusic-schema-literal-") as temp:
            path = str(Path(temp) / "ledger.sqlite3")
            ledger = jobs.ManualSaveJobsLedger(path)
            single = ledger.create_job("actor-A", "single-key", one_entry_manifest())
            playlist = ledger.create_job("actor-A", "playlist-key", manifest())
            reopened = jobs.ManualSaveJobsLedger(path)
            self.assertEqual(reopened.get_job("actor-A", single.job_id).manifest["kind"], "single")
            self.assertEqual(reopened.get_job("actor-A", playlist.job_id).manifest["kind"], "playlist")
            single_after_reopen = reopened.create_job("actor-B", "single-after-reopen", one_entry_manifest())
            playlist_after_reopen = reopened.create_job("actor-B", "playlist-after-reopen", manifest())
            self.assertEqual(single_after_reopen.manifest["kind"], "single")
            self.assertEqual(playlist_after_reopen.manifest["kind"], "playlist")

            before_change = self.database_snapshot(path)
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA writable_schema = ON")
                changed = connection.execute(
                    "UPDATE sqlite_master SET sql = replace(sql, ?, ?) WHERE type = 'table' AND name = 'jobs'",
                    ("'single'", "'SINGLE'"),
                )
                self.assertEqual(changed.rowcount, 1)
                connection.commit()
                connection.execute("PRAGMA writable_schema = OFF")
            finally:
                connection.close()

            after_change = self.database_snapshot(path)
            before_bytes, before_objects, before_application_id, before_schema_version = before_change
            changed_bytes, changed_objects, changed_application_id, changed_schema_version = after_change
            self.assertNotEqual(changed_bytes, before_bytes)
            self.assertEqual(changed_application_id, before_application_id)
            self.assertEqual(changed_schema_version, before_schema_version)
            expected_objects = []
            for object_type, name, sql in before_objects:
                if object_type == "table" and name == "jobs":
                    self.assertEqual(sql.count("'single'"), 1)
                    sql = sql.replace("'single'", "'SINGLE'")
                expected_objects.append((object_type, name, sql))
            self.assertEqual(changed_objects, tuple(expected_objects))

            before_rejection = self.database_snapshot(path)
            with self.assertRaises(jobs.SchemaError) as caught:
                jobs.ManualSaveJobsLedger(path)
            self.assertEqual(caught.exception.code, "invalid_schema")
            self.assertEqual(self.database_snapshot(path), before_rejection)

    def test_terminal_outcome_reason_and_cleanup_are_validated_on_read_and_reopen(self):
        corruptions = (
            ("outcome_state_mismatch", "UPDATE positions SET outcome = 'failed' WHERE job_id = ?"),
            ("reason_not_allowed", "UPDATE positions SET reason = 'not_a_core_reason' WHERE job_id = ?"),
            ("cleanup_not_boolean", "UPDATE positions SET cleanup_pending = 1.5 WHERE job_id = ?"),
        )
        with tempfile.TemporaryDirectory(prefix="fnmusic-terminal-corruption-") as temp:
            for index, (label, sql) in enumerate(corruptions):
                with self.subTest(invariant=label):
                    path = str(Path(temp) / f"{index}.sqlite3")
                    ledger = jobs.ManualSaveJobsLedger(path)
                    created = ledger.create_job("actor-A", f"corrupt-{index}", one_entry_manifest())
                    ledger.run_next("actor-A", created.job_id, lambda _p, _c: published_result())

                    connection = sqlite3.connect(path)
                    try:
                        connection.execute("PRAGMA ignore_check_constraints = ON")
                        connection.execute(sql, (created.job_id,))
                        connection.commit()
                        stored_cleanup = connection.execute(
                            "SELECT cleanup_pending FROM positions WHERE job_id = ?", (created.job_id,),
                        ).fetchone()[0]
                    finally:
                        connection.close()
                    if label == "cleanup_not_boolean":
                        self.assertIs(type(stored_cleanup), float)

                    with self.assertRaises(jobs.CorruptLedgerError) as read_error:
                        ledger.get_job("actor-A", created.job_id)
                    self.assertEqual(read_error.exception.code, "corrupt_ledger")
                    with self.assertRaises(jobs.CorruptLedgerError) as reopen_error:
                        jobs.ManualSaveJobsLedger(path)
                    self.assertEqual(reopen_error.exception.code, "corrupt_ledger")

    def test_import_has_no_file_database_network_process_or_thread_effect(self):
        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
             mock.patch("sqlite3.connect", side_effect=AssertionError("sqlite")), \
             mock.patch("socket.socket", side_effect=AssertionError("socket")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
             mock.patch("threading.Thread", side_effect=AssertionError("thread")):
            probe = _load_import_probe("manual_save_jobs")
        self.assertTrue(callable(probe.ManualSaveJobsLedger))

    def test_actor_isolation_for_read_cancel_execute_and_recovery(self):
        created = self.ledger.create_job("actor-A", "key-1", one_entry_manifest())
        calls = []
        absent_id = "f" * 48
        for action in (
            lambda: self.ledger.get_job("actor-B", created.job_id),
            lambda: self.ledger.cancel_job("actor-B", created.job_id),
            lambda: self.ledger.run_next("actor-B", created.job_id, lambda *_args: calls.append(1)),
            lambda: self.ledger.recover_stale_running("actor-B", created.job_id, worker_stopped=True),
            lambda: self.ledger.get_job("actor-A", absent_id),
            lambda: self.ledger.cancel_job("actor-A", absent_id),
            lambda: self.ledger.run_next("actor-A", absent_id, lambda *_args: calls.append(2)),
            lambda: self.ledger.recover_stale_running("actor-A", absent_id, worker_stopped=True),
        ):
            self.assert_not_found(action)
        self.assertEqual(calls, [])

    def test_concurrent_same_key_creation_returns_exactly_one_job(self):
        ledgers = [self.new_ledger() for _ in range(6)]
        barrier = threading.Barrier(len(ledgers))
        results = []
        failures = []

        def create(ledger):
            try:
                barrier.wait(timeout=5)
                results.append(ledger.create_job("actor-A", "concurrent-key", manifest()))
            except Exception as exc:  # Captured for assertion in the test thread.
                failures.append(type(exc).__name__)

        threads = [threading.Thread(target=create, args=(ledger,)) for ledger in ledgers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(len({result.job_id for result in results}), 1)
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM jobs")[0][0], 1)
        self.assertEqual(self.db_rows("SELECT COUNT(*) FROM positions")[0][0], 3)

    def test_separate_connections_competing_claims_dispatch_only_earliest_once(self):
        created = self.ledger.create_job("actor-A", "key-1", manifest())
        ledgers = [self.new_ledger(), self.new_ledger()]
        barrier = threading.Barrier(2)
        entered = threading.Event()
        release = threading.Event()
        calls = []
        reports = []

        def executor(position, _cancel):
            calls.append(position.position)
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test timeout")
            return published_result()

        def run(ledger):
            barrier.wait(timeout=5)
            reports.append(ledger.run_next("actor-A", created.job_id, executor))

        threads = [threading.Thread(target=run, args=(ledger,)) for ledger in ledgers]
        for thread in threads:
            thread.start()
        self.assertTrue(entered.wait(timeout=5))
        for thread in threads:
            thread.join(timeout=0.1)
        self.assertEqual(calls, [1])
        release.set()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(calls, [1])
        self.assertEqual(sorted(report.status for report in reports), ["completed", "no_work"])
        self.assertEqual(self.ledger.get_job("actor-A", created.job_id).positions[0].state, "published")

        second = self.ledger.run_next("actor-A", created.job_id, lambda p, _c: published_result())
        self.assertEqual(second.position.position, 2)

    def test_all_core_outcomes_are_persisted_with_fixed_reasons(self):
        results = [
            core.SaveResult(core.Outcome.PUBLISHED, "bytes_published"),
            core.SaveResult(core.Outcome.PUBLISHED_CLEANUP_PENDING, "published_cleanup_pending", cleanup_pending=True),
            core.SaveResult(core.Outcome.CONFLICT, "destination_conflict", cleanup_pending=True),
            core.SaveResult(core.Outcome.CANCELLED, "cancelled_before_publish"),
            core.SaveResult(core.Outcome.REJECTED, "invalid_target"),
            core.SaveResult(core.Outcome.FAILED, "staging_failed"),
            core.SaveResult(core.Outcome.COMMIT_UNKNOWN, "commit_unknown", cleanup_pending=True),
        ]
        outcomes = []
        for index, result in enumerate(results):
            created = self.ledger.create_job("actor-A", f"outcome-{index}", one_entry_manifest())
            report = self.ledger.run_next("actor-A", created.job_id, lambda _p, _c, result=result: result)
            outcomes.append(report.position.outcome)
            saved = self.ledger.get_job("actor-A", created.job_id)
            self.assertEqual(saved.positions[0].reason, result.reason)
        self.assertEqual(outcomes, [item.value for item in core.Outcome])
        frozen_job = self.ledger.create_job("actor-A", "unknown-freeze", manifest())
        self.ledger.run_next(
            "actor-A", frozen_job.job_id,
            lambda _p, _c: core.SaveResult(core.Outcome.COMMIT_UNKNOWN, "commit_unknown", cleanup_pending=True),
        )
        self.assertEqual(self.ledger.run_next("actor-A", frozen_job.job_id, lambda *_args: published_result()).status, "no_work")
        self.assertEqual(self.ledger.get_job("actor-A", frozen_job.job_id).positions[1].state, "queued")

    def test_invalid_executor_result_and_exception_become_commit_unknown_without_details(self):
        created = self.ledger.create_job("actor-A", "bad-result", manifest())
        first = self.ledger.run_next("actor-A", created.job_id, lambda *_args: object())
        self.assertEqual(first.status, "commit_unknown")
        self.assertEqual(first.position.reason, "commit_unknown")
        self.assertEqual(self.ledger.run_next("actor-A", created.job_id, lambda *_args: published_result()).status, "no_work")

        created2 = self.ledger.create_job("actor-A", "raised", one_entry_manifest())

        def explode(_position, _cancel):
            raise RuntimeError("SECRET_PATH_AUDIO_BODY")

        report = self.ledger.run_next("actor-A", created2.job_id, explode)
        self.assertEqual(report.status, "commit_unknown")
        self.assertEqual(report.result.reason, "commit_unknown")
        self.assertNotIn("SECRET_PATH_AUDIO_BODY", str(self.db_rows("SELECT reason FROM positions")))
        self.assertNotIn("SECRET_PATH_AUDIO_BODY", str(self.db_rows("SELECT manifest_json FROM jobs")))

    def test_completion_write_failure_returns_completion_unknown_and_never_reexecutes(self):
        created = self.ledger.create_job("actor-A", "completion-failure", one_entry_manifest())
        executions = []

        def executor(_position, _cancel):
            executions.append(1)
            connection = sqlite3.connect(self.database_path)
            try:
                connection.execute("""CREATE TRIGGER fail_completion BEFORE UPDATE OF state ON positions
                    WHEN OLD.state = 'running' BEGIN SELECT RAISE(ABORT, 'SECRET trigger text'); END""")
                connection.commit()
            finally:
                connection.close()
            return published_result()

        report = self.ledger.run_next("actor-A", created.job_id, executor)
        self.assertEqual(report.status, "completion_unknown")
        self.assertEqual(report.result.reason, "commit_unknown")
        self.assertEqual(executions, [1])
        self.assertEqual(self.ledger.get_job("actor-A", created.job_id).positions[0].state, "running")
        self.assertEqual(self.ledger.run_next("actor-A", created.job_id, executor).status, "no_work")
        self.assertEqual(executions, [1])

    def test_completion_connection_and_wrapper_failures_are_unknown_without_retry(self):
        cases = (
            (
                "sqlite_connect_operational_error",
                lambda: mock.patch("sqlite3.connect", side_effect=sqlite3.OperationalError("private detail")),
            ),
            (
                "wrapped_corrupt_ledger_error",
                lambda ledger: mock.patch.object(
                    ledger, "_connect", side_effect=jobs.CorruptLedgerError("private wrapper detail"),
                ),
            ),
        )
        for index, (label, patch_factory) in enumerate(cases):
            with self.subTest(failure=label):
                created = self.ledger.create_job("actor-A", f"connect-failure-{index}", one_entry_manifest())
                executions = []
                patchers = []

                def executor(_position, _cancel):
                    executions.append(1)
                    patcher = patch_factory() if index == 0 else patch_factory(self.ledger)
                    patcher.start()
                    patchers.append(patcher)
                    return published_result()

                try:
                    report = self.ledger.run_next("actor-A", created.job_id, executor)
                finally:
                    for patcher in patchers:
                        patcher.stop()

                self.assertEqual(report.status, "completion_unknown")
                self.assertEqual(report.result.reason, "commit_unknown")
                self.assertEqual(executions, [1])
                self.assertEqual(self.ledger.get_job("actor-A", created.job_id).positions[0].state, "running")
                self.assertEqual(
                    self.ledger.run_next("actor-A", created.job_id, executor).status,
                    "no_work",
                )
                self.assertEqual(executions, [1])

    def test_explicit_recovery_freezes_dispatch_and_rejects_late_completion(self):
        created = self.ledger.create_job("actor-A", "recovery", manifest())
        complete = jobs.ManualSaveJobsLedger._complete

        def recover_before_completion(ledger, actor_scope, job_id, position, claim_token, result):
            # The synchronous executor has returned. Simulate explicit recovery
            # winning immediately before its delayed completion compare-and-set.
            self.assertEqual(job_id, created.job_id)
            snapshot = ledger.recover_stale_running(actor_scope, job_id, worker_stopped=True)
            self.assertEqual(snapshot.positions[0].state, "commit_unknown")
            return complete(ledger, actor_scope, job_id, position, claim_token, result)

        with mock.patch.object(jobs.ManualSaveJobsLedger, "_complete", recover_before_completion):
            report = self.ledger.run_next("actor-A", created.job_id, lambda _p, _c: published_result())
        self.assertEqual(report.status, "stale_completion")
        snapshot = self.ledger.get_job("actor-A", created.job_id)
        self.assertEqual(snapshot.positions[0].state, "commit_unknown")
        self.assertEqual(snapshot.positions[0].reason, "commit_unknown")
        self.assertEqual(snapshot.positions[1].state, "queued")
        self.assertEqual(snapshot.state, "reconciliation_required")
        self.assertEqual(self.ledger.run_next("actor-A", created.job_id, lambda *_args: published_result()).status, "no_work")
        with self.assertRaises(jobs.ValidationError) as caught:
            self.ledger.recover_stale_running("actor-A", created.job_id, worker_stopped=False)
        self.assertEqual(caught.exception.code, "worker_stopped_assertion_required")

    def test_cancel_before_claim_during_execution_and_after_publish_preserves_result(self):
        before = self.ledger.create_job("actor-A", "cancel-before", manifest())
        cancelled = self.ledger.cancel_job("actor-A", before.job_id)
        self.assertEqual([item.state for item in cancelled.positions], ["cancelled", "cancelled", "cancelled"])
        calls = []
        self.assertEqual(self.ledger.run_next(
            "actor-A", before.job_id, lambda *_args: calls.append(1) or published_result(),
        ).status, "no_work")
        self.assertEqual(calls, [])

        during = self.ledger.create_job("actor-A", "cancel-during", manifest())

        def cancel_during_execution(position, cancellation_check):
            self.assertFalse(cancellation_check())
            self.ledger.cancel_job("actor-A", during.job_id)
            self.assertTrue(cancellation_check())
            return published_result()

        report = self.ledger.run_next("actor-A", during.job_id, cancel_during_execution)
        self.assertEqual(report.position.outcome, core.Outcome.PUBLISHED.value)
        during_after = self.ledger.get_job("actor-A", during.job_id)
        self.assertTrue(during_after.cancel_requested)
        self.assertEqual(during_after.positions[0].state, "published")
        self.assertEqual([p.state for p in during_after.positions[1:]], ["cancelled", "cancelled"])

        after = self.ledger.create_job("actor-A", "cancel-after", one_entry_manifest())
        self.ledger.run_next("actor-A", after.job_id, lambda *_args: published_result())
        after_cancel = self.ledger.cancel_job("actor-A", after.job_id)
        self.assertEqual(after_cancel.positions[0].state, "published")
        self.assertTrue(after_cancel.cancel_requested)

    def test_reopen_does_not_execute_queued_work_and_summaries_reconcile(self):
        created = self.ledger.create_job("actor-A", "reopen-queue", manifest())
        calls = []
        reopened = self.new_ledger()
        self.assertEqual(reopened.get_job("actor-A", created.job_id).counts["queued"], 3)
        self.assertEqual(calls, [])

        reopened.run_next("actor-A", created.job_id, lambda *_args: published_result())
        reopened.run_next(
            "actor-A", created.job_id,
            lambda *_args: core.SaveResult(core.Outcome.REJECTED, "invalid_target"),
        )
        summary = reopened.get_job("actor-A", created.job_id)
        self.assertEqual(sum(summary.counts.values()), len(summary.positions))
        self.assertEqual(summary.counts["published"], 1)
        self.assertEqual(summary.counts["rejected"], 1)
        self.assertEqual(summary.counts["queued"], 1)
        self.assertEqual(summary.state, "queued")
        self.assertFalse(summary.audio_verified)
        self.assertFalse(summary.production_ready)
        self.assertFalse(summary.import_ready)

        reopened.run_next(
            "actor-A", created.job_id,
            lambda *_args: core.SaveResult(core.Outcome.FAILED, "staging_failed"),
        )
        final = reopened.get_job("actor-A", created.job_id)
        self.assertEqual(sum(final.counts.values()), len(final.positions))
        self.assertEqual(final.state, "partial")
        self.assertFalse(final.audio_verified)
        self.assertFalse(final.production_ready)
        self.assertFalse(final.import_ready)

    def test_absolute_path_required_and_no_implicit_default_exists(self):
        with self.assertRaises(jobs.ValidationError) as caught:
            jobs.ManualSaveJobsLedger("relative-ledger.sqlite3")
        self.assertEqual(caught.exception.code, "absolute_database_path_required")
        self.assertFalse(os.path.exists("relative-ledger.sqlite3"))


if __name__ == "__main__":
    unittest.main()
