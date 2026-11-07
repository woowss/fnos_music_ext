"""Offline tests for ``proxy.manual_save_playlist_jobs`` (contract P4).

Every case here is fully offline: the producer HTTP seam is a fake returning
bytes, the ledger is a real ``ManualSaveJobsLedger`` in ``tmp_path``, and the
single-track engine is a fake ``save_track``. No network, no NAS, no probe
scripts, no ffmpeg, no real media path.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from urllib import error as urllib_error

import pytest

from proxy import manual_save_core
from proxy import manual_save_jobs
from proxy import manual_save_playlist as batch_engine
from proxy import manual_save_playlist_jobs as adapter
from proxy import manual_save_route as route

PROXY_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROXY_DIR.parent
SERVICE_DIR = REPO_ROOT / "musicbox-service"
PRODUCER_MODULE_PATH = SERVICE_DIR / "netease_playlist_snapshot.py"


# ---------------------------------------------------------------------------
# Envelope / payload helpers (mirror the real shapes exactly)
# ---------------------------------------------------------------------------

def _envelope(ok, code, data=None):
    return {"ok": ok, "code": code, "message": "", "data": {} if data is None else data}


def _published():
    return _envelope(True, "ok", {"status": "published"})


def _conflict():
    return _envelope(False, "conflict", {"status": "conflict"})


def _failure(code):
    return _envelope(False, code)


def _snapshot_payload(
    playlist_id=987654321,
    track_ids=(1901371647,),
    *,
    fingerprint="a" * 64,
    enumeration_complete=True,
    status="complete",
    provider="netease",
    schema_version=1,
    track_count=None,
    position_count=None,
    positions=None,
    ok=True,
    error_code=None,
    artifact_error_code=None,
):
    if positions is None:
        positions = [
            {"position": index, "track_id": track_id, "error_code": None}
            for index, track_id in enumerate(track_ids, start=1)
        ]
    return {
        "ok": ok,
        "status": status,
        "error_code": error_code,
        "schema_version": schema_version,
        "provider": provider,
        "account_uid": 123456789,
        "playlist_id": playlist_id,
        "track_count": len(positions) if track_count is None else track_count,
        "position_count": len(positions) if position_count is None else position_count,
        "enumeration_complete": enumeration_complete,
        "source_fingerprint": fingerprint,
        "source_versions": [["updateTime", 1730000000]],
        "positions": positions,
        "metadata_status": "not_requested",
        "metadata_complete": False,
        "import_ready": False,
        "production_ready": False,
        "provider_verified": False,
        "artifact_error_code": artifact_error_code,
    }


def _json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class RecordingHttpGet:
    """Injectable HTTP seam: records calls, returns a fixed ``(status, body)``."""

    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, timeout, max_bytes):
        self.calls.append((url, timeout, max_bytes))
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def _client_with_response(response, **kwargs):
    fake = RecordingHttpGet(response)
    client = adapter.PlaylistSnapshotClient(
        "http://127.0.0.1:8770", http_get=fake, **kwargs
    )
    return client, fake


def _client_with_body(body, status=200, **kwargs):
    return _client_with_response((status, body), **kwargs)


class FakeEngine:
    """Fake single-track engine keyed by song id."""

    def __init__(self, results):
        self.results = dict(results)
        self.calls = []

    def save_track(self, song_id):
        self.calls.append(song_id)
        return self.results[song_id]


def _service(tmp_path, engine, payload, *, name="jobs.db"):
    database = tmp_path / name
    config = adapter.PlaylistJobServiceConfig(db_path=str(database), enabled=True)
    fake_http = RecordingHttpGet((200, _json_bytes(payload)))
    producer = adapter.PlaylistSnapshotClient("http://127.0.0.1:8770", http_get=fake_http)
    service = adapter.PlaylistJobService(config, producer, engine=engine)
    return service, fake_http


SCOPE_A = adapter.derive_actor_scope("10001")
SCOPE_B = adapter.derive_actor_scope("10002")


# ---------------------------------------------------------------------------
# §5.2 audit mapping: rows 1-19, then rows 20-21
# ---------------------------------------------------------------------------

_AUDIT_CASES = (
    ("row1-published", _published(), manual_save_core.Outcome.PUBLISHED,
     "bytes_published", adapter.DISPOSITION_CONTINUE),
    ("row2-conflict", _conflict(), manual_save_core.Outcome.CONFLICT,
     "destination_conflict", adapter.DISPOSITION_CONTINUE),
    ("row3-service_unavailable", _failure("service_unavailable"),
     manual_save_core.Outcome.REJECTED, "service_unavailable", adapter.DISPOSITION_CONTINUE),
    ("row4-no_quality_available", _failure("no_quality_available"),
     manual_save_core.Outcome.REJECTED, "no_quality_available", adapter.DISPOSITION_CONTINUE),
    ("row5-quality_unverified", _failure("quality_unverified"),
     manual_save_core.Outcome.REJECTED, "quality_unverified", adapter.DISPOSITION_CONTINUE),
    ("row6-quality_identity_mismatch", _failure("quality_identity_mismatch"),
     manual_save_core.Outcome.REJECTED, "quality_identity_mismatch", adapter.DISPOSITION_CONTINUE),
    ("row7-trial_only", _failure("trial_only"),
     manual_save_core.Outcome.REJECTED, "trial_only", adapter.DISPOSITION_CONTINUE),
    ("row8-download_target_untrusted", _failure("download_target_untrusted"),
     manual_save_core.Outcome.REJECTED, "download_target_untrusted", adapter.DISPOSITION_CONTINUE),
    ("row9-download_failed", _failure("download_failed"),
     manual_save_core.Outcome.REJECTED, "download_failed", adapter.DISPOSITION_CONTINUE),
    ("row10-integrity_mismatch", _failure("integrity_mismatch"),
     manual_save_core.Outcome.REJECTED, "integrity_mismatch", adapter.DISPOSITION_CONTINUE),
    ("row11-invalid_audio", _failure("invalid_audio"),
     manual_save_core.Outcome.REJECTED, "invalid_audio", adapter.DISPOSITION_CONTINUE),
    ("row12-tag_failed", _failure("tag_failed"),
     manual_save_core.Outcome.REJECTED, "tag_failed", adapter.DISPOSITION_CONTINUE),
    ("row13-disabled", _failure("disabled"),
     manual_save_core.Outcome.FAILED, "disabled", adapter.DISPOSITION_ABORT),
    ("row14-config_invalid", _failure("config_invalid"),
     manual_save_core.Outcome.FAILED, "config_invalid", adapter.DISPOSITION_ABORT),
    ("row15-busy", _failure("busy"),
     manual_save_core.Outcome.FAILED, "busy", adapter.DISPOSITION_ABORT),
    # §5.2 row 16 literal cell says "staging_unavailable" but the row cites the
    # existing ledger reason from manual_save_jobs.py:50, which is
    # "stage_unavailable" (see deviation test below).
    ("row16-staging_unavailable", _failure("staging_unavailable"),
     manual_save_core.Outcome.FAILED, "stage_unavailable", adapter.DISPOSITION_ABORT),
    ("row17-publish_failed", _failure("publish_failed"),
     manual_save_core.Outcome.COMMIT_UNKNOWN, "commit_unknown", adapter.DISPOSITION_FREEZE),
    ("row18-internal", _failure("internal"),
     manual_save_core.Outcome.COMMIT_UNKNOWN, "commit_unknown", adapter.DISPOSITION_FREEZE),
    ("row19-invalid_song_id", _failure("invalid_song_id"),
     manual_save_core.Outcome.REJECTED, "invalid_target", adapter.DISPOSITION_ABORT),
)


@pytest.mark.parametrize(
    "case_id,result,outcome,reason,disposition",
    _AUDIT_CASES,
    ids=[case[0] for case in _AUDIT_CASES],
)
def test_audit_mapping_rows_1_to_19(case_id, result, outcome, reason, disposition):
    mapped = adapter.map_save_result(result)
    assert mapped == (outcome, reason, disposition)
    assert mapped[2] in adapter.DISPOSITIONS


def test_row1_requires_published_status_not_just_ok_flag():
    assert adapter.map_save_result(
        _envelope(True, "ok", {"status": "conflict"})
    ) == (manual_save_core.Outcome.COMMIT_UNKNOWN, "commit_unknown",
          adapter.DISPOSITION_FREEZE)
    assert adapter.map_save_result(
        _envelope(True, "ok", {"status": "published_cleanup_pending"})
    )[0] is manual_save_core.Outcome.COMMIT_UNKNOWN


@pytest.mark.parametrize(
    "result",
    [
        pytest.param(None, id="none"),
        pytest.param("ok", id="string"),
        pytest.param([], id="list"),
        pytest.param(42, id="int"),
        pytest.param(object(), id="object"),
        pytest.param({}, id="empty-dict"),
        pytest.param({"ok": 1, "code": "ok", "data": {"status": "published"}}, id="ok-int"),
        pytest.param({"ok": True, "code": 1, "data": {"status": "published"}}, id="code-int"),
        pytest.param({"ok": True, "code": "ok", "data": None}, id="data-none"),
        pytest.param({"ok": True, "code": "ok", "data": []}, id="data-list"),
        pytest.param({"ok": False, "code": "no_such_code", "data": {}}, id="unknown-code"),
        pytest.param({"ok": True, "code": "conflict", "data": {"status": "conflict"}},
                     id="ok-true-with-failure-code"),
        pytest.param({"ok": False, "code": "ok", "data": {}}, id="ok-false-code-ok"),
    ],
)
def test_row20_and_row21_invalid_returns_map_to_commit_unknown(result):
    mapped = adapter.map_save_result(result)
    assert mapped == (manual_save_core.Outcome.COMMIT_UNKNOWN, "commit_unknown",
                      adapter.DISPOSITION_FREEZE)
    assert mapped[2] != adapter.DISPOSITION_CONTINUE
    assert mapped[2] != adapter.DISPOSITION_ABORT


def test_mapping_never_raises_for_a_hostile_mapping():
    class HostileMapping(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("hostile mapping")

    assert adapter.map_save_result(HostileMapping()) == (
        manual_save_core.Outcome.COMMIT_UNKNOWN, "commit_unknown",
        adapter.DISPOSITION_FREEZE,
    )


def test_continue_abort_classification_matches_batch_engine():
    """Guard against the two fixed code tables drifting apart (§5.2)."""
    assert adapter.ROUTE_CONTINUE_CODES == batch_engine.PLAYLIST_CONTINUE_CODES
    assert adapter.ROUTE_ABORT_CODES == batch_engine.PLAYLIST_ABORT_CODES
    for code in batch_engine.PLAYLIST_CONTINUE_CODES:
        assert adapter.map_save_result(_failure(code))[2] == adapter.DISPOSITION_CONTINUE
    for code in batch_engine.PLAYLIST_ABORT_CODES:
        disposition = adapter.map_save_result(_failure(code))[2]
        assert disposition in {adapter.DISPOSITION_ABORT, adapter.DISPOSITION_FREEZE}
        assert disposition != adapter.DISPOSITION_CONTINUE


def test_mapping_table_covers_every_route_failure_code():
    expected = (
        set(batch_engine.PLAYLIST_CONTINUE_CODES)
        | set(batch_engine.PLAYLIST_ABORT_CODES)
        | {"invalid_song_id"}
    )
    assert set(adapter._CODE_MAP) == expected
    route_codes = set(route._MESSAGES) - {"ok", "invalid_keyword"}
    assert route_codes == expected


def test_row16_reason_uses_existing_ledger_name_not_the_literal_cell():
    outcome, reason, disposition = adapter.map_save_result(_failure("staging_unavailable"))
    assert (outcome, reason, disposition) == (
        manual_save_core.Outcome.FAILED, "stage_unavailable", adapter.DISPOSITION_ABORT,
    )
    # The literal cell value is not (and will not be) in the ledger whitelist.
    assert "staging_unavailable" not in manual_save_jobs._OUTCOME_REASONS["failed"]


@pytest.mark.parametrize("code", sorted(adapter._CODE_MAP))
def test_every_mapped_result_is_persistable_by_the_current_ledger(code):
    """P1 ledger extension: every mapped (Outcome, reason) passes ledger validation."""
    outcome, reason, _disposition = adapter._CODE_MAP[code]
    result = adapter.build_save_result(outcome, reason)
    assert manual_save_jobs._validated_result(result) is not None
    assert result.audio_verified is False
    assert result.production_ready is False
    expected_cleanup = outcome is manual_save_core.Outcome.COMMIT_UNKNOWN
    assert result.cleanup_pending is expected_cleanup


def test_result_reason_extension_declaration_is_accurate():
    mapped_reasons = {
        outcome.value: {
            reason for mapped_outcome, reason, _ in adapter._CODE_MAP.values()
            if mapped_outcome.value == outcome.value
        }
        for outcome in (manual_save_core.Outcome.REJECTED, manual_save_core.Outcome.FAILED)
    }
    for outcome_value, reasons in adapter.RESULT_REASON_EXTENSION.items():
        # Declared extension reasons are all actually used by the mapping...
        assert set(reasons) <= mapped_reasons[outcome_value]
        # ...and the current ledger (P1) accepts them, so no silent freeze occurs.
        assert set(reasons) <= set(manual_save_jobs._OUTCOME_REASONS[outcome_value])


@pytest.mark.parametrize("reason", ["no_such_reason", "service_unavailable_v2", "disabled_v2"])
def test_unknown_ledger_reason_is_still_rejected(reason):
    assert manual_save_jobs._validated_result(
        manual_save_core.SaveResult(manual_save_core.Outcome.REJECTED, reason)
    ) is None
    assert manual_save_jobs._validated_result(
        manual_save_core.SaveResult(manual_save_core.Outcome.FAILED, reason)
    ) is None


# ---------------------------------------------------------------------------
# Executor adapter (§5.1)
# ---------------------------------------------------------------------------

def _position(source_id="1901371647", source_status="ok", position=1):
    return manual_save_jobs.PositionSnapshot(
        position=position,
        source_id=source_id,
        source_status=source_status,
        state="running",
        outcome=None,
        reason=None,
        cleanup_pending=None,
    )


def test_executor_passes_only_the_numeric_source_id_without_deps():
    engine = FakeEngine({1901371647: _published()})
    executor = adapter.make_executor(engine)
    result = executor(_position(), lambda: False)
    assert engine.calls == [1901371647]
    assert result == manual_save_core.SaveResult(
        manual_save_core.Outcome.PUBLISHED, "bytes_published",
        cleanup_pending=False, audio_verified=False, production_ready=False,
    )


@pytest.mark.parametrize("source_status", ["invalid_id", "unavailable", "missing"])
def test_executor_never_calls_engine_for_non_ok_source_status(source_status):
    engine = FakeEngine({1901371647: _published()})
    executor = adapter.make_executor(engine)
    result = executor(_position(source_status=source_status), lambda: False)
    assert engine.calls == []
    assert (result.outcome, result.reason) == (
        manual_save_core.Outcome.REJECTED, "invalid_target",
    )
    assert result.cleanup_pending is False


@pytest.mark.parametrize("source_id", ["abc", "", " 7", "1.0", "-1", "0", None])
def test_executor_never_calls_engine_for_invalid_source_id(source_id):
    engine = FakeEngine({1901371647: _published()})
    executor = adapter.make_executor(engine)
    result = executor(_position(source_id=source_id), lambda: False)
    assert engine.calls == []
    assert (result.outcome, result.reason) == (
        manual_save_core.Outcome.REJECTED, "invalid_target",
    )


def test_executor_ignores_cancellation_check():
    engine = FakeEngine({7: _published()})
    calls = []

    def cancellation_check():
        calls.append(1)
        raise AssertionError("executor must not consult cancellation_check")

    result = adapter.make_executor(engine)(_position(source_id="7"), cancellation_check)
    assert result.outcome is manual_save_core.Outcome.PUBLISHED
    assert calls == []


def test_executor_engine_exception_becomes_commit_unknown():
    class RaisingEngine:
        def save_track(self, song_id):
            raise RuntimeError("TOPSECRET engine text")

    result = adapter.make_executor(RaisingEngine())(_position(), lambda: False)
    assert (result.outcome, result.reason) == (
        manual_save_core.Outcome.COMMIT_UNKNOWN, "commit_unknown",
    )
    assert result.cleanup_pending is True
    assert "TOPSECRET" not in str(result)


def test_executor_never_retries_continue_or_abort_codes():
    calls = []

    class RetryTrapEngine:
        def save_track(self, song_id):
            calls.append(song_id)
            return _failure("download_failed")

    result = adapter.make_executor(RetryTrapEngine())(_position(), lambda: False)
    assert calls == [1901371647]
    assert (result.outcome, result.reason) == (
        manual_save_core.Outcome.REJECTED, "download_failed",
    )


def test_disposition_for_persisted_result_uses_same_table():
    for code, (outcome, reason, disposition) in adapter._CODE_MAP.items():
        result = adapter.build_save_result(outcome, reason)
        assert adapter.disposition_for_save_result(result) == disposition
    assert adapter.disposition_for_save_result(object()) == adapter.DISPOSITION_FREEZE


# ---------------------------------------------------------------------------
# Manifest construction (§4.3.2 - §4.6)
# ---------------------------------------------------------------------------

def _manifest_error_code(playlist_id, payload):
    with pytest.raises(adapter.ManifestError) as error:
        adapter.build_manifest(playlist_id, payload)
    return error.value.code


def test_build_manifest_happy_path_exact_shape():
    payload = _snapshot_payload(track_ids=(1901371647, 1901371648))
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert manifest == {
        "kind": "playlist",
        "target_id": "manual-save",
        "provider_id": "netease-0.5.3-7f695b35",
        "collection_id": "netease-playlist-987654321",
        "collection_version": "sha256-" + "a" * 64,
        "enumeration_complete": True,
        "entries": [
            {"position": 1, "source_id": "1901371647", "source_status": "ok"},
            {"position": 2, "source_id": "1901371648", "source_status": "ok"},
        ],
    }
    # The real ledger accepts exactly this manifest (no SQL involved).
    canonical, manifest_bytes, digest = manual_save_jobs._validate_manifest(manifest)
    assert canonical == manifest
    assert len(digest) == 64
    assert len(manifest_bytes) <= manual_save_jobs.MAX_MANIFEST_BYTES


def test_build_manifest_keeps_duplicate_positions():
    payload = _snapshot_payload(track_ids=(111, 222, 111))
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert [entry["source_id"] for entry in manifest["entries"]] == ["111", "222", "111"]
    assert manifest["enumeration_complete"] is True


@pytest.mark.parametrize("track_id", [0, -5, None, "123", True, 1.5])
def test_build_manifest_retains_invalid_track_ids(track_id):
    payload = _snapshot_payload(positions=[
        {"position": 1, "track_id": 1901371647, "error_code": None},
        {"position": 2, "track_id": track_id, "error_code": None},
    ])
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert manifest["enumeration_complete"] is False
    assert manifest["entries"][1] == {
        "position": 2, "source_id": None, "source_status": "invalid_id",
    }
    assert manifest["entries"][0]["source_status"] == "ok"


def test_build_manifest_retains_invalid_error_code_positions():
    payload = _snapshot_payload(positions=[
        {"position": 1, "track_id": 5, "error_code": "INVALID_TRACK_ID"},
    ])
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert manifest["enumeration_complete"] is False
    assert manifest["entries"][0]["source_status"] == "invalid_id"


def test_build_manifest_respects_producer_incomplete_flag():
    payload = _snapshot_payload(track_ids=(1, 2), enumeration_complete=False)
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert manifest["enumeration_complete"] is False
    assert [entry["source_status"] for entry in manifest["entries"]] == ["ok", "ok"]


def test_build_manifest_never_trusts_claimed_true_against_invalid_ids():
    payload = _snapshot_payload(
        positions=[
            {"position": 1, "track_id": 5, "error_code": None},
            {"position": 2, "track_id": 0, "error_code": None},
        ],
        enumeration_complete=True,
    )
    assert adapter.build_manifest(payload["playlist_id"], payload)["enumeration_complete"] is False


def test_build_manifest_accepts_exactly_1000_positions():
    payload = _snapshot_payload(track_ids=tuple(range(1, 1001)))
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert manifest["enumeration_complete"] is True
    assert len(manifest["entries"]) == 1000


def test_build_manifest_rejects_more_than_1000_positions():
    payload = _snapshot_payload(track_ids=tuple(range(1, 1002)))
    assert _manifest_error_code(payload["playlist_id"], payload) == "playlist_too_large"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"track_count": 3}, id="track-count-mismatch"),
        pytest.param({"position_count": 1}, id="position-count-mismatch"),
    ],
)
def test_build_manifest_rejects_count_mismatch_without_silent_repair(kwargs):
    payload = _snapshot_payload(track_ids=(1, 2), **kwargs)
    assert _manifest_error_code(payload["playlist_id"], payload) == "manifest_incomplete"


def test_build_manifest_rejects_non_contiguous_positions():
    payload = _snapshot_payload(positions=[
        {"position": 1, "track_id": 1, "error_code": None},
        {"position": 3, "track_id": 2, "error_code": None},
    ])
    assert _manifest_error_code(payload["playlist_id"], payload) == "manifest_incomplete"


@pytest.mark.parametrize(
    "fingerprint",
    ["a" * 63, "a" * 65, "A" * 64, "g" * 64, None, 123],
)
def test_build_manifest_rejects_bad_fingerprint(fingerprint):
    payload = _snapshot_payload(fingerprint=fingerprint)
    assert _manifest_error_code(payload["playlist_id"], payload) == "manifest_incomplete"


@pytest.mark.parametrize(
    "status,expected",
    [
        # Contract §3.3 / §3.9.1 fixed classification.
        ("incomplete", "manifest_incomplete"),
        ("source_changed", "source_changed"),
        ("unsupported", "playlist_unsupported"),
        ("upstream_error", "playlist_unavailable"),
        ("not_logged_in", "not_logged_in"),
        ("unknown_status", "manifest_incomplete"),
    ],
)
def test_build_manifest_maps_non_complete_status_to_fixed_code(status, expected):
    payload = _snapshot_payload(status=status)
    assert _manifest_error_code(payload["playlist_id"], payload) == expected


@pytest.mark.parametrize(
    "overrides,expected",
    [
        # Version/digest mismatch is the producer-local 500 token (§4.1/§3.3).
        ({"status": "unsupported", "artifact_error_code": "source_artifact_mismatch"},
         "source_artifact_mismatch"),
        # >100000 source positions are reported as unsupported and map to 409.
        ({"status": "unsupported", "error_code": "POSITION_LIMIT_EXCEEDED"},
         "playlist_too_large"),
        ({"status": "unsupported", "error_code": "SOURCE_LIMIT_REACHED"},
         "playlist_too_large"),
        # Any other unsupported reason stays the reviewed 409 playlist_unsupported.
        ({"status": "unsupported", "error_code": "UNSUPPORTED_SOURCE"},
         "playlist_unsupported"),
        ({"status": "unsupported", "artifact_error_code": "other"},
         "playlist_unsupported"),
    ],
)
def test_build_manifest_classifies_unsupported_snapshot_failures(overrides, expected):
    payload = _snapshot_payload(**overrides)
    assert _manifest_error_code(payload["playlist_id"], payload) == expected


def test_build_manifest_accepts_producer_failure_payload_with_ok_false():
    """Producer failures may carry ok=false plus status/error_code (§4.2)."""
    for overrides, expected in (
        ({"status": "upstream_error", "error_code": "SOURCE_READ_FAILED"},
         "playlist_unavailable"),
        ({"status": "not_logged_in", "error_code": "NOT_LOGGED_IN"},
         "not_logged_in"),
        ({"status": "incomplete", "error_code": "TRACK_COUNT_MISMATCH"},
         "manifest_incomplete"),
        ({"status": "source_changed", "error_code": "SOURCE_VERSION_CHANGED"},
         "source_changed"),
    ):
        payload = _snapshot_payload(ok=False, **overrides)
        assert _manifest_error_code(payload["playlist_id"], payload) == expected


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"provider": "qq"}, "source_artifact_mismatch"),
        ({"schema_version": 2}, "source_artifact_mismatch"),
        ({"playlist_id": 42}, "manifest_incomplete"),
        ({"ok": False}, "manifest_incomplete"),
        ({"track_count": 0, "positions": []}, "manifest_incomplete"),
        ({"enumeration_complete": "true"}, "manifest_incomplete"),
    ],
)
def test_build_manifest_rejects_broken_identity_fields(kwargs, expected):
    payload = _snapshot_payload(**kwargs)
    assert _manifest_error_code(987654321, payload) == expected


@pytest.mark.parametrize("playlist_id", [0, -1, "987654321", True, None, 1.5])
def test_build_manifest_rejects_invalid_playlist_id(playlist_id):
    payload = _snapshot_payload()
    assert _manifest_error_code(playlist_id, payload) == "invalid_playlist_id"


def test_build_manifest_rejects_unknown_position_error_code():
    payload = _snapshot_payload(positions=[
        {"position": 1, "track_id": 5, "error_code": "TRACK_GONE"},
    ])
    assert _manifest_error_code(payload["playlist_id"], payload) == "manifest_incomplete"


def test_build_manifest_rejects_non_mapping_payload():
    assert _manifest_error_code(987654321, ["not", "a", "mapping"]) == "manifest_incomplete"


# ---------------------------------------------------------------------------
# Producer client (§4.2 loopback-only, bounded, structure re-checked)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.2:8770",
        "http://example.com",
        "http://192.168.0.106:8770",
        "http://10.0.0.5:8770",
        "http://localhost.evil.example",
        "http://127.0.0.1.evil",
        "http://user:pass@127.0.0.1:8770",
        "ftp://127.0.0.1:8770",
        # Contract §8.3: http only, no TLS to a loopback producer.
        "https://127.0.0.1:8770",
        "https://localhost:8770",
        "https://[::1]:8770",
        "http://localhost.:8770",
        "http://127.0.0.1:8770/api",
        "http://127.0.0.1:8770?x=1",
        "http://127.0.0.1:8770#frag",
    ],
)
def test_producer_rejects_every_non_loopback_target(base_url):
    with pytest.raises(adapter.ProducerError) as error:
        adapter.PlaylistSnapshotClient(base_url, http_get=RecordingHttpGet((200, b"{}")))
    assert error.value.code == adapter.PRODUCER_NOT_LOOPBACK


@pytest.mark.parametrize(
    "base_url,expected_prefix",
    [
        ("http://127.0.0.1:8770", "http://127.0.0.1:8770"),
        ("http://localhost:8770/", "http://localhost:8770"),
        ("HTTP://LocalHost:8770", "http://localhost:8770"),
        # §8.3 explicitly allows the IPv6 loopback literal.
        ("http://[::1]:8770", "http://[::1]:8770"),
        ("http://[::1]:8770/", "http://[::1]:8770"),
    ],
)
def test_producer_accepts_loopback_and_builds_snapshot_url(base_url, expected_prefix):
    client = adapter.PlaylistSnapshotClient(base_url, http_get=RecordingHttpGet((200, b"{}")))
    assert client.snapshot_url(42) == (
        expected_prefix + "/api/v1/user/playlists/42/member-snapshot"
    )


@pytest.mark.parametrize("base_url", [None, 123, "", "http://127.0.0.1:8770\x00"])
def test_producer_rejects_malformed_base_url_configuration(base_url):
    with pytest.raises(adapter.ProducerError) as error:
        adapter.PlaylistSnapshotClient(base_url, http_get=RecordingHttpGet((200, b"{}")))
    assert error.value.code in {
        adapter.PRODUCER_CONFIG_INVALID, adapter.PRODUCER_NOT_LOOPBACK,
    }


def test_fetch_snapshot_happy_path_uses_injected_http_only():
    payload = _snapshot_payload(track_ids=(7, 8))
    client, fake = _client_with_body(_json_bytes(payload))
    assert client.fetch_snapshot(payload["playlist_id"]) == payload
    url, timeout, max_bytes = fake.calls[0]
    assert url == (
        "http://127.0.0.1:8770/api/v1/user/playlists/987654321/member-snapshot"
    )
    assert timeout == adapter.DEFAULT_PLAYLIST_READ_TIMEOUT
    assert max_bytes == adapter.DEFAULT_MAX_RESPONSE_BYTES
    assert adapter.DEFAULT_MAX_RESPONSE_BYTES == 1 << 20
    assert adapter.MAX_RESPONSE_BYTES_LIMIT == 1 << 20


@pytest.mark.parametrize("playlist_id", [0, -1, "42", True, None, 1.0])
def test_fetch_snapshot_rejects_invalid_playlist_id_without_network(playlist_id):
    client, fake = _client_with_body(b"{}")
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(playlist_id)
    assert error.value.code == adapter.INVALID_PLAYLIST_ID
    assert fake.calls == []


def test_fetch_snapshot_timeout_folds_to_fixed_code():
    client, _ = _client_with_response(TimeoutError("timed out"))
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == adapter.PRODUCER_TIMEOUT


def test_fetch_snapshot_unreachable_folds_to_fixed_code():
    client, _ = _client_with_response(ConnectionRefusedError("refused"))
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == adapter.PRODUCER_UNREACHABLE


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b"null",
        b'{"ok": "yes"}',
        b'{"ok": true}',
        b'{"ok": true, "status": "weird_status", "provider": "netease", '
        b'"schema_version": 1, "playlist_id": 42}',
        b'{"ok": true, "status": "complete", "provider": "netease", '
        b'"schema_version": 1, "playlist_id": 43}',
    ],
)
def test_fetch_snapshot_folds_malformed_responses(body):
    client, _ = _client_with_body(body)
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(42)
    assert error.value.code == adapter.PRODUCER_MALFORMED_RESPONSE


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"provider": "qq"}, "source_artifact_mismatch"),
        ({"schema_version": 2}, "source_artifact_mismatch"),
    ],
)
def test_fetch_snapshot_folds_artifact_mismatch(overrides, expected):
    payload = _snapshot_payload(playlist_id=42, **overrides)
    client, _ = _client_with_body(_json_bytes(payload))
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(42)
    assert error.value.code == expected


def test_fetch_snapshot_bounds_the_response_size():
    client, _ = _client_with_body(b"x" * 200, max_response_bytes=32)
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == adapter.PRODUCER_RESPONSE_TOO_LARGE


def test_fetch_snapshot_folds_503_manifest_disabled():
    body = json.dumps({"ok": False, "error": "manifest_disabled"}).encode()
    client, _ = _client_with_body(body, status=503)
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == "manifest_disabled"


def test_fetch_snapshot_folds_unknown_http_failure():
    client, _ = _client_with_body(b"gateway exploded", status=500)
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == adapter.PRODUCER_HTTP_STATUS


def test_fetch_snapshot_passes_through_fixed_200_error_body():
    body = json.dumps({"ok": False, "error": "invalid_request"}).encode()
    client, _ = _client_with_body(body)
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == "invalid_request"


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"timeout": 0}, id="zero-timeout"),
        pytest.param({"timeout": 1201}, id="over-gateway-budget"),
        pytest.param({"timeout": float("nan")}, id="nan-timeout"),
        pytest.param({"max_response_bytes": 0}, id="zero-bytes"),
        pytest.param({"max_response_bytes": (1 << 20) + 1}, id="over-1mib-bytes"),
        pytest.param({"max_response_bytes": True}, id="bool-bytes"),
    ],
)
def test_producer_client_config_validation(kwargs):
    with pytest.raises(adapter.ProducerError) as error:
        adapter.PlaylistSnapshotClient(
            "http://127.0.0.1:8770", http_get=RecordingHttpGet((200, b"{}")), **kwargs
        )
    assert error.value.code == adapter.PRODUCER_CONFIG_INVALID


def test_constructing_a_client_never_calls_the_default_http_seam(monkeypatch):
    calls = []
    monkeypatch.setattr(
        adapter, "_default_http_get", lambda *args: calls.append(args) or (200, b"{}")
    )
    adapter.PlaylistSnapshotClient("http://127.0.0.1:8770")
    assert calls == []


def test_producer_error_text_is_only_the_fixed_code():
    client, _ = _client_with_response(TimeoutError("http://secret-host/path"))
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert str(error.value) == adapter.PRODUCER_TIMEOUT
    assert "secret-host" not in str(error.value)


# ---------------------------------------------------------------------------
# §2.4 actor scope / config / fixed messages
# ---------------------------------------------------------------------------

def test_derive_actor_scope_is_deterministic_and_identifier_shaped():
    scope = adapter.derive_actor_scope("10001")
    assert scope == adapter.derive_actor_scope("10001")
    assert scope != adapter.derive_actor_scope("10002")
    assert len(scope) == 41 and scope.startswith("u")
    assert manual_save_jobs._IDENTIFIER_RE.fullmatch(scope)


@pytest.mark.parametrize(
    "claim", [None, 123, "", "\x00", "1\n2", "x" * 300],
)
def test_derive_actor_scope_rejects_invalid_claims(claim):
    with pytest.raises(adapter.ServiceError) as error:
        adapter.derive_actor_scope(claim)
    assert error.value.code == adapter.INVALID_ACTOR


def test_derive_actor_scope_never_embeds_the_raw_claim():
    claim = "TRUSTED-USER-10001"
    scope = adapter.derive_actor_scope(claim)
    assert claim not in scope


def test_load_service_config_defaults_off():
    config = adapter.load_service_config({})
    assert config.enabled is False
    assert config.db_path is None
    assert adapter.load_service_config().enabled is False


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("", False), ("off", False),
        ("maybe", False), (None, False),
    ],
)
def test_load_service_config_parses_enabled_flag(raw, expected):
    environ = {}
    if raw is not None:
        environ[adapter.PLAYLIST_ENABLED_ENV] = raw
    assert adapter.load_service_config(environ).enabled is expected


def test_load_service_config_reads_absolute_database_path(tmp_path):
    database = str(tmp_path / "jobs.db")
    config = adapter.load_service_config({
        adapter.JOBS_DB_ENV: database,
        adapter.PLAYLIST_ENABLED_ENV: "true",
    })
    assert config.enabled is True
    assert config.db_path == database


@pytest.mark.parametrize("relative", ["jobs.db", "", "bad\x00path", "..\\jobs.db"])
def test_load_service_config_rejects_relative_or_framed_paths(relative):
    config = adapter.load_service_config({
        adapter.JOBS_DB_ENV: relative,
        adapter.PLAYLIST_ENABLED_ENV: "true",
    })
    assert config.db_path is None


def test_service_config_repr_never_exposes_the_database_path(tmp_path):
    database = str(tmp_path / "very-secret.db")
    config = adapter.PlaylistJobServiceConfig(db_path=database, enabled=True)
    assert database not in repr(config)
    assert "very-secret" not in repr(config)


def test_fixed_messages_are_not_interpolated():
    for code, message in adapter._MESSAGES.items():
        assert "{" not in message
        assert "}" not in message
        assert "%" not in message
    assert adapter.fixed_message("no_such_code") == adapter._MESSAGES["internal"]
    assert adapter.fixed_message(123) == adapter._MESSAGES["internal"]


def test_every_public_fixed_code_has_a_message():
    codes = set(adapter._CODE_MAP) | {
        adapter.PRODUCER_NOT_LOOPBACK, adapter.PRODUCER_CONFIG_INVALID,
        adapter.PRODUCER_TIMEOUT, adapter.PRODUCER_UNREACHABLE,
        adapter.PRODUCER_HTTP_STATUS, adapter.PRODUCER_RESPONSE_TOO_LARGE,
        adapter.PRODUCER_MALFORMED_RESPONSE, adapter.PRODUCER_UNEXPECTED_URL,
        adapter.INVALID_PLAYLIST_ID,
        adapter.MANIFEST_INCOMPLETE, adapter.PLAYLIST_TOO_LARGE,
        adapter.PLAYLIST_UNSUPPORTED, adapter.PLAYLIST_UNAVAILABLE,
        adapter.SOURCE_CHANGED, adapter.NOT_LOGGED_IN,
        adapter.SOURCE_ARTIFACT_MISMATCH,
        adapter.DISABLED, adapter.PLAYLIST_DISABLED, adapter.CONFIG_INVALID,
        adapter.BUSY, adapter.INVALID_ACTOR,
        "invalid_song_id", "manifest_disabled",
        # Ledger reasons that may be rendered after a refresh (§5.3).
        "stage_unavailable", "staging_failed", "cancel_check_failed",
    }
    assert codes <= set(adapter._MESSAGES)


def test_ledger_reason_aliases_render_the_staging_message():
    assert adapter.fixed_message("stage_unavailable") == "暂存不可用"
    assert adapter.fixed_message("staging_failed") == "暂存不可用"
    assert adapter.fixed_message("staging_unavailable") == "暂存不可用"
    assert adapter.fixed_message("stage_unavailable") != adapter.fixed_message("internal")


def test_reconciliation_banner_is_fixed_text():
    assert adapter.RECONCILIATION_REQUIRED_BANNER == (
        "存在结果未知的位置，已停止推进。请核对 NAS 上是否已出现该文件后再人工处理。"
    )


# ---------------------------------------------------------------------------
# Thin service layer: disabled / config-invalid / idempotency / isolation
# ---------------------------------------------------------------------------

def test_disabled_service_never_opens_the_ledger(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("ledger must not be opened while disabled")

    monkeypatch.setattr(adapter, "ManualSaveJobsLedger", _boom)
    service = adapter.PlaylistJobService(
        adapter.PlaylistJobServiceConfig(enabled=False), object()
    )
    with pytest.raises(adapter.ServiceError) as error:
        service.create_job(SCOPE_A, 1, _snapshot_payload())
    assert error.value.code == adapter.PLAYLIST_DISABLED
    with pytest.raises(adapter.ServiceError) as error:
        service.get_job(SCOPE_A, "a" * 48)
    assert error.value.code == adapter.PLAYLIST_DISABLED
    assert service.advance_once(SCOPE_A, "a" * 48).status == adapter.PLAYLIST_DISABLED


def test_enabled_service_without_database_path_is_config_invalid():
    service = adapter.PlaylistJobService(
        adapter.PlaylistJobServiceConfig(enabled=True, db_path=None), object()
    )
    with pytest.raises(adapter.ServiceError) as error:
        service.create_job(SCOPE_A, 1, _snapshot_payload())
    assert error.value.code == adapter.CONFIG_INVALID
    with pytest.raises(adapter.ServiceError) as error:
        service.advance_once(SCOPE_A, "a" * 48)
    assert error.value.code == adapter.CONFIG_INVALID
    # The failed advance released the gateway lock.
    assert adapter.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    adapter.MANUAL_SAVE_PLAYLIST_LOCK.release()


def test_create_job_refuses_incomplete_manifest(tmp_path):
    payload = _snapshot_payload(track_ids=(1, 2), enumeration_complete=False)
    service, _ = _service(tmp_path, FakeEngine({}), payload)
    with pytest.raises(adapter.ServiceError) as error:
        service.create_job(SCOPE_A, payload["playlist_id"], payload)
    assert error.value.code == adapter.MANIFEST_INCOMPLETE


def test_create_from_producer_is_idempotent_by_derived_key(tmp_path):
    payload = _snapshot_payload(track_ids=(101, 102))
    service, fake = _service(tmp_path, FakeEngine({}), payload)
    first = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    second = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    assert first.job_id == second.job_id
    assert len(fake.calls) == 2
    assert first.manifest["collection_version"] == "sha256-" + "a" * 64


def test_different_fingerprint_creates_a_new_job(tmp_path):
    payload = _snapshot_payload(track_ids=(101,))
    service, fake = _service(tmp_path, FakeEngine({}), payload)
    first = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    changed = _snapshot_payload(track_ids=(101,), fingerprint="b" * 64)
    fake.response = (200, _json_bytes(changed))
    second = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    assert second.job_id != first.job_id


def test_explicit_idempotency_key_conflict_is_propagated(tmp_path):
    payload = _snapshot_payload(track_ids=(101,))
    service, fake = _service(tmp_path, FakeEngine({}), payload)
    first = service.create_job(
        SCOPE_A, payload["playlist_id"], payload, idempotency_key="k-1",
    )
    changed = _snapshot_payload(track_ids=(102,))
    with pytest.raises(manual_save_jobs.IdempotencyConflict):
        service.create_job(
            SCOPE_A, payload["playlist_id"], changed, idempotency_key="k-1",
        )
    assert service.get_job(SCOPE_A, first.job_id).positions[0].source_id == "101"


def test_actor_scope_isolation_blocks_read_cancel_and_advance(tmp_path):
    payload = _snapshot_payload(track_ids=(401,))
    engine = FakeEngine({401: _published()})
    service, _ = _service(tmp_path, engine, payload)
    job = service.create_from_producer(SCOPE_A, payload["playlist_id"])

    with pytest.raises(manual_save_jobs.NotFoundError):
        service.get_job(SCOPE_B, job.job_id)
    with pytest.raises(manual_save_jobs.NotFoundError):
        service.cancel_job(SCOPE_B, job.job_id)
    with pytest.raises(manual_save_jobs.NotFoundError):
        service.advance_once(SCOPE_B, job.job_id)
    assert engine.calls == []
    # The failed cross-scope advance released the gateway lock.
    assert adapter.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    adapter.MANUAL_SAVE_PLAYLIST_LOCK.release()
    # The owner still reads the untouched job.
    snapshot = service.get_job(SCOPE_A, job.job_id)
    assert snapshot.counts["queued"] == 1


def test_advance_busy_does_not_touch_the_ledger(tmp_path):
    payload = _snapshot_payload(track_ids=(501,))
    engine = FakeEngine({501: _published()})
    service, _ = _service(tmp_path, engine, payload)
    job = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    assert adapter.MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False)
    try:
        report = service.advance_once(SCOPE_A, job.job_id)
    finally:
        adapter.MANUAL_SAVE_PLAYLIST_LOCK.release()
    assert report.status == adapter.BUSY
    assert report.disposition is None
    assert engine.calls == []
    assert service.get_job(SCOPE_A, job.job_id).counts["queued"] == 1


# ---------------------------------------------------------------------------
# End-to-end: fake producer + real ledger in tmp_path + fake save_track
# ---------------------------------------------------------------------------

def test_end_to_end_three_positions_mixed_results(tmp_path):
    payload = _snapshot_payload(track_ids=(101, 102, 103))
    engine = FakeEngine({
        101: _published(),
        102: _conflict(),
        103: _failure("disabled"),
    })
    service, fake_http = _service(tmp_path, engine, payload)
    job = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    assert len(fake_http.calls) == 1
    assert job.state == "queued"

    first = service.advance_once(SCOPE_A, job.job_id)
    second = service.advance_once(SCOPE_A, job.job_id)
    third = service.advance_once(SCOPE_A, job.job_id)
    assert engine.calls == [101, 102, 103]
    assert (first.status, first.disposition) == (
        "completed", adapter.DISPOSITION_CONTINUE,
    )
    assert (second.status, second.disposition) == (
        "completed", adapter.DISPOSITION_CONTINUE,
    )
    assert (third.status, third.disposition) == (
        "completed", adapter.DISPOSITION_ABORT,
    )
    assert service.advance_once(SCOPE_A, job.job_id).status == "no_work"

    snapshot = service.get_job(SCOPE_A, job.job_id)
    # Ledger summary: at least one published byte report with other terminal
    # issues is "partial" (ok only when every position is published).
    assert snapshot.state == "partial"
    assert snapshot.state != "completed_bytes_reported"
    assert snapshot.counts["published"] == 1
    assert snapshot.counts["conflict"] == 1
    assert snapshot.counts["failed"] == 1
    assert snapshot.counts["queued"] == 0
    reasons = [(p.state, p.reason) for p in snapshot.positions]
    assert reasons == [
        ("published", "bytes_published"),
        ("conflict", "destination_conflict"),
        ("failed", "disabled"),
    ]


def test_ok_only_when_every_position_is_published(tmp_path):
    payload = _snapshot_payload(track_ids=(201, 202), fingerprint="c" * 64)
    engine = FakeEngine({201: _published(), 202: _published()})
    service, _ = _service(tmp_path, engine, payload)
    job = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    service.advance_once(SCOPE_A, job.job_id)
    service.advance_once(SCOPE_A, job.job_id)
    snapshot = service.get_job(SCOPE_A, job.job_id)
    assert snapshot.state == "completed_bytes_reported"
    assert snapshot.counts["published"] == 2


def test_commit_unknown_freezes_further_dispatch(tmp_path):
    payload = _snapshot_payload(track_ids=(301, 302, 303), fingerprint="d" * 64)
    engine = FakeEngine({
        301: _published(),
        302: _failure("publish_failed"),
        303: _published(),
    })
    service, _ = _service(tmp_path, engine, payload)
    job = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    first = service.advance_once(SCOPE_A, job.job_id)
    second = service.advance_once(SCOPE_A, job.job_id)
    assert first.status == "completed"
    assert (second.status, second.disposition) == (
        "commit_unknown", adapter.DISPOSITION_FREEZE,
    )
    # Frozen: the next advance claims nothing and never calls the engine again.
    assert service.advance_once(SCOPE_A, job.job_id).status == "no_work"
    assert engine.calls == [301, 302]
    snapshot = service.get_job(SCOPE_A, job.job_id)
    assert snapshot.state == "reconciliation_required"
    assert snapshot.positions[1].cleanup_pending is True
    assert snapshot.positions[2].state == "queued"
    assert adapter.RECONCILIATION_REQUIRED_BANNER


def test_cancel_only_affects_queued_positions(tmp_path):
    payload = _snapshot_payload(track_ids=(401, 402), fingerprint="e" * 64)
    engine = FakeEngine({401: _published(), 402: _published()})
    service, _ = _service(tmp_path, engine, payload)
    job = service.create_from_producer(SCOPE_A, payload["playlist_id"])
    cancelled = service.cancel_job(SCOPE_A, job.job_id)
    assert cancelled.counts["cancelled"] == 2
    assert service.advance_once(SCOPE_A, job.job_id).status == "no_work"
    assert engine.calls == []


# ---------------------------------------------------------------------------
# §8.3 outbound boundary: injectable bounded timeout + redirect-free seam
# ---------------------------------------------------------------------------

SNAPSHOT_TEST_URL = (
    "http://127.0.0.1:8770/api/v1/user/playlists/42/member-snapshot"
)


def test_load_read_timeout_defaults_to_reviewed_budget():
    assert adapter.DEFAULT_PLAYLIST_READ_TIMEOUT == 120.0
    assert adapter.MAX_PLAYLIST_READ_TIMEOUT <= 1200.0
    assert adapter.load_read_timeout({}) == 120.0
    assert adapter.load_read_timeout({adapter.PLAYLIST_READ_TIMEOUT_ENV: ""}) == 120.0
    assert adapter.load_read_timeout({adapter.PLAYLIST_READ_TIMEOUT_ENV: "30"}) == 30.0
    assert adapter.load_read_timeout({
        adapter.PLAYLIST_READ_TIMEOUT_ENV: "120.5",
    }) == 120.5
    assert adapter.load_read_timeout({adapter.PLAYLIST_READ_TIMEOUT_ENV: "1200"}) == 1200.0


@pytest.mark.parametrize("raw", ["0", "-1", "abc", "nan", "inf", "1200.1", "   ", "1e999"])
def test_load_read_timeout_invalid_value_fails_closed(raw):
    with pytest.raises(adapter.ProducerError) as error:
        adapter.load_read_timeout({adapter.PLAYLIST_READ_TIMEOUT_ENV: raw})
    assert error.value.code == adapter.PRODUCER_CONFIG_INVALID


def test_client_default_timeout_comes_from_the_contract_env(monkeypatch):
    monkeypatch.setenv(adapter.PLAYLIST_READ_TIMEOUT_ENV, "45")
    client, fake = _client_with_body(b"{}")
    with pytest.raises(adapter.ProducerError):
        client.fetch_snapshot(1)
    assert fake.calls[0][1] == 45.0


def test_client_explicit_timeout_overrides_the_env(monkeypatch):
    monkeypatch.setenv(adapter.PLAYLIST_READ_TIMEOUT_ENV, "45")
    client, fake = _client_with_body(b"{}", timeout=7)
    with pytest.raises(adapter.ProducerError):
        client.fetch_snapshot(1)
    assert fake.calls[0][1] == 7.0


class _FakeHttpResponse:
    """Minimal urllib response stand-in (status/geturl/read/context manager)."""

    def __init__(self, status, body, final_url):
        self.status = status
        self._body = body
        self._final_url = final_url

    def geturl(self):
        return self._final_url

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _FakeOpener:
    def __init__(self, result):
        self.result = result

    def open(self, request, timeout=None):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def _fake_http_error(url, code):
    return urllib_error.HTTPError(url, code, "redirect", {}, io.BytesIO(b""))


def test_default_http_get_rejects_3xx_redirect_to_external_address(monkeypatch):
    monkeypatch.setattr(
        adapter, "_get_http_opener",
        lambda: _FakeOpener(_fake_http_error(SNAPSHOT_TEST_URL, 302)),
    )
    with pytest.raises(adapter.ProducerError) as error:
        adapter._default_http_get(SNAPSHOT_TEST_URL, 5.0, 1024)
    assert error.value.code == adapter.PRODUCER_UNEXPECTED_URL


def test_default_http_get_rejects_3xx_redirect_to_same_loopback_address(monkeypatch):
    # Even a redirect that points back at loopback is refused: the exact
    # requested URL is the only acceptable final address.
    monkeypatch.setattr(
        adapter, "_get_http_opener",
        lambda: _FakeOpener(_fake_http_error(SNAPSHOT_TEST_URL, 307)),
    )
    with pytest.raises(adapter.ProducerError) as error:
        adapter._default_http_get(SNAPSHOT_TEST_URL, 5.0, 1024)
    assert error.value.code == adapter.PRODUCER_UNEXPECTED_URL


def test_default_http_get_rejects_unexpected_final_url(monkeypatch):
    response = _FakeHttpResponse(200, b"{}", "http://evil.example/snapshot")
    monkeypatch.setattr(adapter, "_get_http_opener", lambda: _FakeOpener(response))
    with pytest.raises(adapter.ProducerError) as error:
        adapter._default_http_get(SNAPSHOT_TEST_URL, 5.0, 1024)
    assert error.value.code == adapter.PRODUCER_UNEXPECTED_URL


def test_default_http_get_accepts_the_exact_requested_url(monkeypatch):
    response = _FakeHttpResponse(200, b"payload", SNAPSHOT_TEST_URL)
    monkeypatch.setattr(adapter, "_get_http_opener", lambda: _FakeOpener(response))
    assert adapter._default_http_get(SNAPSHOT_TEST_URL, 5.0, 1024) == (200, b"payload")


def test_default_opener_disables_the_redirect_handler(monkeypatch):
    monkeypatch.setattr(adapter, "_HTTP_OPENER", None)
    try:
        opener = adapter._get_http_opener()
        assert any(
            isinstance(handler, adapter._NoRedirectHandler) for handler in opener.handlers
        )
        handler = adapter._NoRedirectHandler()
        # Both an external and a same-loopback redirect target are refused.
        for target in ("http://evil.example/snapshot", SNAPSHOT_TEST_URL):
            assert handler.redirect_request(None, None, 302, "", {}, target) is None
    finally:
        monkeypatch.setattr(adapter, "_HTTP_OPENER", None)


def test_client_surfaces_unexpected_url_from_the_http_seam():
    def _raising_http_get(url, timeout, max_bytes):
        raise adapter.ProducerError(adapter.PRODUCER_UNEXPECTED_URL)

    client = adapter.PlaylistSnapshotClient(
        "http://127.0.0.1:8770", http_get=_raising_http_get,
    )
    with pytest.raises(adapter.ProducerError) as error:
        client.fetch_snapshot(1)
    assert error.value.code == adapter.PRODUCER_UNEXPECTED_URL


@pytest.mark.parametrize("ok", [False, True], ids=["ok-false", "ok-true-forward-compat"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "upstream_error", "error_code": "SOURCE_READ_FAILED"},
        {"status": "not_logged_in", "error_code": "NOT_LOGGED_IN"},
        {"status": "incomplete", "error_code": "TRACK_COUNT_MISMATCH"},
        {"status": "source_changed", "error_code": "SOURCE_VERSION_CHANGED"},
    ],
)
def test_fetch_snapshot_returns_producer_failure_payloads_without_malformed(ok, overrides):
    """Producer failure payloads carry status/error_code; ``ok`` may be either
    ``false`` (current) or always-``true`` (announced producer-side change)."""
    payload = _snapshot_payload(ok=ok, **overrides)
    client, _ = _client_with_body(_json_bytes(payload))
    assert client.fetch_snapshot(payload["playlist_id"]) == payload


# ---------------------------------------------------------------------------
# Cross-module interop: the real producer's payloads must classify correctly
# ---------------------------------------------------------------------------

def _load_producer_module(monkeypatch):
    assert PRODUCER_MODULE_PATH.is_file(), f"missing producer module: {PRODUCER_MODULE_PATH}"
    monkeypatch.syspath_prepend(str(SERVICE_DIR))
    spec = importlib.util.spec_from_file_location(
        "netease_playlist_snapshot_cross_module_test", PRODUCER_MODULE_PATH,
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _producer_observed_payload(producer, *, playlist_id=42, track_ids=(11, 12), **overrides):
    def fetch_account():
        return {"code": 200, "account": {"id": 9001}}

    def fetch_playlist(requested_playlist_id):
        return {
            "code": 200,
            "playlist": {
                "id": requested_playlist_id,
                "trackCount": overrides.get("track_count", len(track_ids)),
                "trackIds": list(track_ids),
                "updateTime": 7,
            },
        }

    return producer.build_snapshot(
        playlist_id,
        fetch_account,
        fetch_playlist,
        artifact_probe=lambda: ("0.5.3", True),
    )


def test_cross_module_complete_payload_is_accepted_end_to_end(monkeypatch):
    producer = _load_producer_module(monkeypatch)
    payload = _producer_observed_payload(producer)
    assert payload["status"] == "complete"
    assert adapter._validate_snapshot_envelope(payload, payload["playlist_id"]) is payload
    manifest = adapter.build_manifest(payload["playlist_id"], payload)
    assert manifest["enumeration_complete"] is True
    assert [entry["source_id"] for entry in manifest["entries"]] == ["11", "12"]
    # The same payload through the real client seam (bytes -> validated payload).
    client, _ = _client_with_body(_json_bytes(payload))
    assert client.fetch_snapshot(payload["playlist_id"]) == payload


def test_cross_module_snapshot_failures_never_become_malformed(monkeypatch):
    producer = _load_producer_module(monkeypatch)

    def _must_not_read():
        raise AssertionError("artifact mismatch must fail before any provider read")

    def _upstream_explodes():
        raise RuntimeError("raw upstream text must not leak")

    cases = [
        # Version mismatch: fail closed before reading the account (§4.1).
        (
            producer.build_snapshot(
                42, _must_not_read, _must_not_read,
                artifact_probe=lambda: ("0.5.2", True),
            ),
            "source_artifact_mismatch",
        ),
        # Upstream read failure -> 502 playlist_unavailable.
        (
            producer.build_snapshot(
                42, _upstream_explodes, _upstream_explodes,
                artifact_probe=lambda: ("0.5.3", True),
            ),
            "playlist_unavailable",
        ),
        # Not logged in -> 403 not_logged_in.
        (
            producer.build_snapshot(
                42, lambda: None, _must_not_read,
                artifact_probe=lambda: ("0.5.3", True),
            ),
            "not_logged_in",
        ),
    ]
    for payload, expected in cases:
        assert payload["status"] != "complete"
        # Forward compatibility: the producer announced ``ok`` becomes always
        # true and failures carry only status/error_code. Exercise both shapes.
        for variant in (payload, {**payload, "ok": True}):
            # The envelope re-check must accept the producer's failure shape...
            assert adapter._validate_snapshot_envelope(variant, 42) is variant
            # ...and the classification must be the reviewed fixed code, not malformed.
            with pytest.raises(adapter.ManifestError) as error:
                adapter.build_manifest(42, variant)
            assert error.value.code == expected
            assert error.value.code != adapter.PRODUCER_MALFORMED_RESPONSE
        # Same result through the client seam (bytes -> payload -> manifest).
        client, _ = _client_with_body(_json_bytes(payload))
        fetched = client.fetch_snapshot(42)
        with pytest.raises(adapter.ManifestError) as through_client:
            adapter.build_manifest(42, fetched)
        assert through_client.value.code == expected


def test_cross_module_observed_incomplete_and_source_changed_are_accepted(monkeypatch):
    producer = _load_producer_module(monkeypatch)

    incomplete = _producer_observed_payload(producer, track_count=3)
    assert incomplete["status"] == "incomplete"
    assert adapter._validate_snapshot_envelope(incomplete, 42) is incomplete
    with pytest.raises(adapter.ManifestError) as error:
        adapter.build_manifest(42, incomplete)
    assert error.value.code == "manifest_incomplete"

    counter = {"reads": 0}

    def fetch_account():
        return {"code": 200, "account": {"id": 9001}}

    def fetch_playlist(requested_playlist_id):
        counter["reads"] += 1
        return {
            "code": 200,
            "playlist": {
                "id": requested_playlist_id,
                "trackCount": 2,
                "trackIds": [11, 12],
                "updateTime": 7 + counter["reads"] % 2,
            },
        }

    changed = producer.build_snapshot(
        42, fetch_account, fetch_playlist, artifact_probe=lambda: ("0.5.3", True),
    )
    assert changed["status"] == "source_changed"
    assert adapter._validate_snapshot_envelope(changed, 42) is changed
    with pytest.raises(adapter.ManifestError) as error:
        adapter.build_manifest(42, changed)
    assert error.value.code == "source_changed"


def test_cross_module_unsupported_limit_payload_maps_to_playlist_too_large(monkeypatch):
    producer = _load_producer_module(monkeypatch)
    artifact = {
        "provider": "netease",
        "package": "NetEase-MusicBox",
        "version": "0.5.3",
        "wheel_sha256": "0" * 64,
        "installed_version": "0.5.3",
    }
    for error_code in ("POSITION_LIMIT_EXCEEDED", "SOURCE_LIMIT_REACHED"):
        payload = producer._payload(
            status="unsupported",
            error_code=error_code,
            playlist_id=42,
            artifact=artifact,
        )
        assert adapter._validate_snapshot_envelope(payload, 42) is payload
        with pytest.raises(adapter.ManifestError) as error:
            adapter.build_manifest(42, payload)
        assert error.value.code == adapter.PLAYLIST_TOO_LARGE


# ---------------------------------------------------------------------------
# Import-time boundary: no DB, no socket
# ---------------------------------------------------------------------------

def test_module_import_opens_no_database_and_no_socket():
    script = "\n".join([
        "import sqlite3, socket",
        "def _boom(*args, **kwargs):",
        "    raise AssertionError('side_effect')",
        "sqlite3.connect = _boom",
        "socket.create_connection = _boom",
        "import manual_save_playlist_jobs as adapter",
        "assert adapter.fixed_message('busy')",
        "print('ok')",
    ])
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROXY_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-B", "-c", script],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "ok"
