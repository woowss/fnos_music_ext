"""Synthetic-only tests for the pure playlist manifest candidate."""
from __future__ import annotations

import builtins
import copy
import importlib.util
import io
import os
import socket
import subprocess
import sys
import tempfile
import urllib.request
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[2] / "musicbox-service"
_MANIFEST_MODULE = None
MAX_SOURCE_POSITIONS = 100_000


def _responses(
    track_ids,
    *,
    account_uid=9001,
    playlist_id=42,
    track_count=None,
    versions=None,
    response_code=200,
):
    if versions is None:
        versions = {"updateTime": 7}
    return (
        {"code": response_code, "account": {"id": account_uid}},
        {
            "code": response_code,
            "playlist": {
                "id": playlist_id,
                "trackCount": len(track_ids) if track_count is None else track_count,
                "trackIds": copy.deepcopy(track_ids),
                **copy.deepcopy(versions),
            },
        },
    )


def _snapshot(track_ids, **kwargs):
    account, playlist = _responses(track_ids, **kwargs)
    module = _current_manifest_module()
    result = module.snapshot_from_responses(account, playlist, kwargs.get("playlist_id", 42))
    assert isinstance(result, module.SourceSnapshot)
    return result


def _current_manifest_module():
    assert _MANIFEST_MODULE is not None, "manifest module must be loaded under sentinels"
    return _MANIFEST_MODULE


def _account_response(*, uid=9001, code=200):
    return {"code": code, "account": {"id": uid}}


def _playlist_response(*, code=200, **playlist_fields):
    return {"code": code, "playlist": playlist_fields}


def test_stable_empty_playlist_is_enumerated_but_never_import_ready():
    before = _snapshot([])
    after = _snapshot([])

    result = _current_manifest_module().compare_snapshots(before, after)

    assert result.status == "complete"
    assert result.enumeration_complete is True
    assert result.metadata_complete is False
    assert result.import_ready is False
    assert result.production_ready is False
    assert result.provider_verified is False
    assert result.items == ()
    assert result.track_count == result.position_count == 0
    assert result.source_fingerprint == _current_manifest_module().snapshot_fingerprint(after)


def test_1001_positions_preserve_exact_order_and_duplicate_positions():
    ids = [*range(1, 1000), 7, 1001]
    result = _current_manifest_module().compare_snapshots(_snapshot(ids), _snapshot(ids))

    assert result.status == "complete"
    assert result.position_count == 1001
    assert [row.position for row in result.items] == list(range(1, 1002))
    assert [row.track_id for row in result.items] == ids
    assert [row.metadata_status for row in result.items] == ["not_requested"] * 1001
    assert all(row.song is None for row in result.items)
    assert result.metadata_complete is False
    assert result.import_ready is result.production_ready is result.provider_verified is False


def test_provider_shaped_track_rows_are_normalized_without_deduplicating():
    ids = [{"id": 11}, 12, {"id": 11}]
    result = _current_manifest_module().compare_snapshots(_snapshot(ids), _snapshot(ids))

    assert result.status == "complete"
    assert [(row.position, row.track_id) for row in result.items] == [
        (1, 11), (2, 12), (3, 11),
    ]


@pytest.mark.parametrize(
    ("before_options", "after_options", "expected_error"),
    [
        ({}, {"account_uid": 9002}, "ACCOUNT_CHANGED"),
        ({}, {"playlist_id": 43}, "PLAYLIST_CHANGED"),
        ({}, {"versions": {"updateTime": 8}}, "SOURCE_VERSION_CHANGED"),
        (
            {"versions": {"updateTime": 7}},
            {"versions": {"trackUpdateTime": 7}},
            "SOURCE_VERSION_CHANGED",
        ),
        ({}, {"track_count": 4}, "TRACK_COUNT_CHANGED"),
        ({"track_ids": [11, 12, 13]}, {"track_ids": [11, 13, 12]}, "TRACK_SEQUENCE_CHANGED"),
    ],
)
def test_any_source_identity_version_count_or_order_change_is_not_complete(
    before_options, after_options, expected_error,
):
    before_ids = before_options.pop("track_ids", [11, 12, 13])
    after_ids = after_options.pop("track_ids", [11, 12, 13])
    before = _snapshot(before_ids, **before_options)
    after = _snapshot(after_ids, **after_options)

    result = _current_manifest_module().compare_snapshots(before, after)

    assert result.status == "source_changed"
    assert result.error_code == expected_error
    assert result.enumeration_complete is False


@pytest.mark.parametrize(
    ("track_ids", "declared_count", "expected_error"),
    [([11, 12], 1, "TRACK_COUNT_MISMATCH"), ([11], 2, "TRACK_COUNT_MISMATCH")],
)
def test_declared_count_mismatch_is_incomplete_and_keeps_all_positions(
    track_ids, declared_count, expected_error,
):
    before = _snapshot(track_ids, track_count=declared_count)
    after = _snapshot(track_ids, track_count=declared_count)

    result = _current_manifest_module().compare_snapshots(before, after)

    assert result.status == "incomplete"
    assert result.error_code == expected_error
    assert result.position_count == len(track_ids)
    assert [row.track_id for row in result.items] == track_ids


@pytest.mark.parametrize("bad_id", [True, False, "11", "0", 0, -1, None, {"unexpected": 11}])
def test_invalid_ids_keep_positions_and_never_complete(bad_id):
    ids = [11, bad_id, 12]
    result = _current_manifest_module().compare_snapshots(_snapshot(ids), _snapshot(ids))

    assert result.status == "incomplete"
    assert result.error_code == "INVALID_TRACK_ID"
    assert result.position_count == 3
    assert [(row.position, row.track_id, row.error_code) for row in result.items] == [
        (1, 11, None), (2, None, "INVALID_TRACK_ID"), (3, 12, None),
    ]


@pytest.mark.parametrize(
    ("account", "playlist", "expected_status", "expected_error"),
    [
        (
            {"account": {"id": 9001}},
            _playlist_response(id=42, trackCount=0, trackIds=[], updateTime=7),
            "upstream_error", "SOURCE_READ_FAILED",
        ),
        (
            _account_response(code=301),
            _playlist_response(id=42, trackCount=0, trackIds=[], updateTime=7),
            "upstream_error", "SOURCE_READ_FAILED",
        ),
        (
            _account_response(code=200.0),
            _playlist_response(id=42, trackCount=0, trackIds=[], updateTime=7),
            "upstream_error", "SOURCE_READ_FAILED",
        ),
        (
            _account_response(code=True),
            _playlist_response(id=42, trackCount=0, trackIds=[], updateTime=7),
            "upstream_error", "SOURCE_READ_FAILED",
        ),
        (
            _account_response(uid=True),
            _playlist_response(id=42, trackCount=0, trackIds=[], updateTime=7),
            "unsupported", "UNSUPPORTED_SOURCE",
        ),
        (
            _account_response(),
            _playlist_response(id=42, trackCount=0, trackIds=[]),
            "unsupported", "UNSUPPORTED_SOURCE",
        ),
        (
            _account_response(),
            _playlist_response(id=42, trackCount=True, trackIds=[], updateTime=7),
            "unsupported", "UNSUPPORTED_SOURCE",
        ),
        (
            _account_response(),
            _playlist_response(id=42, trackCount=-1, trackIds=[], updateTime=7),
            "unsupported", "UNSUPPORTED_SOURCE",
        ),
        (
            _account_response(),
            _playlist_response(id=42, trackCount=0, updateTime=7),
            "unsupported", "UNSUPPORTED_SOURCE",
        ),
    ],
)
def test_missing_or_malformed_responses_never_default_to_empty_success(
    account, playlist, expected_status, expected_error,
):
    snapshot = _current_manifest_module().snapshot_from_responses(account, playlist, 42)

    assert isinstance(snapshot, _current_manifest_module().SourceFailure)
    result = _current_manifest_module().compare_snapshots(snapshot, snapshot)
    assert result.status == expected_status
    assert result.error_code == expected_error
    assert result.enumeration_complete is False


@pytest.mark.parametrize(
    "versions",
    [
        {},
        {"updateTime": 0},
        {"updateTime": True},
        {"updateTime": 7, "trackUpdateTime": -1},
        {"updateTime": 7, "trackNumberUpdateTime": "9"},
    ],
)
def test_missing_or_invalid_version_fields_are_unsupported(versions):
    account, playlist = _responses([], versions=versions)
    result = _current_manifest_module().snapshot_from_responses(account, playlist, 42)

    assert isinstance(result, _current_manifest_module().SourceFailure)
    assert result.status == "unsupported"
    assert result.error_code == "UNSUPPORTED_SOURCE"


def test_playlist_id_must_match_requested_id_and_booleans_are_not_ids():
    account, playlist = _responses([], playlist_id=43)
    mismatch = _current_manifest_module().snapshot_from_responses(account, playlist, 42)
    invalid_expected = _current_manifest_module().snapshot_from_responses(account, playlist, True)

    failure_type = _current_manifest_module().SourceFailure
    assert mismatch == failure_type("unsupported", "PLAYLIST_ID_MISMATCH")
    assert invalid_expected == failure_type("unsupported", "UNSUPPORTED_SOURCE")


@pytest.mark.parametrize("count", [99999, 100000, 100001])
def test_source_capacity_boundary_is_conservative(count):
    if count < MAX_SOURCE_POSITIONS:
        ids = list(range(1, count + 1))
        account, playlist = _responses(ids, track_count=count)
        result = _current_manifest_module().snapshot_from_responses(account, playlist, 42)
        assert isinstance(result, _current_manifest_module().SourceSnapshot)
        assert len(result.positions) == count
    else:
        account, playlist = _responses([], track_count=count)
        result = _current_manifest_module().snapshot_from_responses(account, playlist, 42)
        assert result == _current_manifest_module().SourceFailure(
            "unsupported",
            "SOURCE_LIMIT_REACHED" if count == MAX_SOURCE_POSITIONS else "POSITION_LIMIT_EXCEEDED",
        )


def test_returned_position_count_at_capacity_is_also_unsupported():
    ids = list(range(1, MAX_SOURCE_POSITIONS + 1))
    account, playlist = _responses(ids, track_count=MAX_SOURCE_POSITIONS)

    result = _current_manifest_module().snapshot_from_responses(account, playlist, 42)

    assert result == _current_manifest_module().SourceFailure("unsupported", "SOURCE_LIMIT_REACHED")


def test_returned_position_count_over_capacity_is_unsupported():
    ids = list(range(1, MAX_SOURCE_POSITIONS + 2))
    account, playlist = _responses(ids, track_count=1)

    result = _current_manifest_module().snapshot_from_responses(account, playlist, 42)

    assert result == _current_manifest_module().SourceFailure("unsupported", "POSITION_LIMIT_EXCEEDED")


def test_normalized_failure_cause_is_preserved():
    failure = _current_manifest_module().SourceFailure(
        status="upstream_error", error_code="ACCOUNT_SESSION_EXPIRED",
    )

    result_before = _current_manifest_module().compare_snapshots(failure, _snapshot([11]))
    result_after = _current_manifest_module().compare_snapshots(_snapshot([11]), failure)

    assert (result_before.status, result_before.error_code) == (failure.status, failure.error_code)
    assert (result_after.status, result_after.error_code) == (failure.status, failure.error_code)


def test_snapshot_and_manifest_are_isolated_from_later_input_mutation():
    ids = [{"id": 11}, 12]
    account, playlist = _responses(ids)
    module = _current_manifest_module()
    before = module.snapshot_from_responses(account, playlist, 42)
    after = module.snapshot_from_responses(copy.deepcopy(account), copy.deepcopy(playlist), 42)
    assert isinstance(before, module.SourceSnapshot)
    assert isinstance(after, module.SourceSnapshot)
    manifest = module.compare_snapshots(before, after)
    original_digest = module.snapshot_fingerprint(before)
    original_items = manifest.items

    account["account"]["id"] = 9002
    playlist["playlist"]["trackIds"][0]["id"] = 999
    playlist["playlist"]["trackIds"].reverse()
    playlist["playlist"]["updateTime"] = 999

    assert before.account_uid == after.account_uid == 9001
    assert [position.track_id for position in before.positions] == [11, 12]
    assert [position.track_id for position in after.positions] == [11, 12]
    assert before.versions == after.versions == (("updateTime", 7),)
    assert module.snapshot_fingerprint(before) == original_digest
    assert manifest.items == original_items
    assert [row.track_id for row in manifest.items] == [11, 12]
    assert manifest.source_fingerprint == original_digest
    with pytest.raises(FrozenInstanceError):
        before.track_count = 999
    with pytest.raises(FrozenInstanceError):
        manifest.items[0].track_id = 999

    assert module.snapshot_fingerprint(_snapshot([12, 11])) != original_digest


def _install_side_effect_sentinels(monkeypatch, attempts):
    def blocked(name):
        def trip(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"forbidden side effect attempted: {name}")
        return trip

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name.startswith((
            "NEMbox", "requests", "httpx", "runner", "playback", "netease_ext",
            "manual_save", "proxy.app",
        )):
            return blocked(f"import:{name}")()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(builtins, "open", blocked("builtins.open"))
    monkeypatch.setattr(io, "open", blocked("io.open"))
    monkeypatch.setattr(Path, "open", blocked("Path.open"))
    monkeypatch.setattr(Path, "write_text", blocked("Path.write_text"))
    monkeypatch.setattr(Path, "write_bytes", blocked("Path.write_bytes"))
    for name in ("open", "system", "popen", "remove", "unlink", "replace", "rename", "mkdir", "makedirs", "rmdir"):
        monkeypatch.setattr(os, name, blocked(f"os.{name}"))
    for name in ("run", "Popen", "call", "check_call", "check_output"):
        monkeypatch.setattr(subprocess, name, blocked(f"subprocess.{name}"))
    for name in ("mkstemp", "NamedTemporaryFile", "TemporaryDirectory"):
        monkeypatch.setattr(tempfile, name, blocked(f"tempfile.{name}"))
    monkeypatch.setattr(socket, "create_connection", blocked("socket.create_connection"))
    monkeypatch.setattr(socket.socket, "connect", blocked("socket.socket.connect"))
    monkeypatch.setattr(urllib.request, "urlopen", blocked("urllib.request.urlopen"))


@pytest.fixture(autouse=True)
def _load_fresh_manifest_only_after_sentinels(monkeypatch):
    """Load each fresh candidate only after fail-closed sentinels are active."""
    attempts: list[str] = []
    _install_side_effect_sentinels(monkeypatch, attempts)

    module_name = "playlist_manifest_guarded_test_instance"
    spec = importlib.util.spec_from_file_location(
        module_name,
        SERVICE_DIR / "playlist_manifest.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)

    global _MANIFEST_MODULE
    previous_module = _MANIFEST_MODULE
    try:
        spec.loader.exec_module(module)
        _MANIFEST_MODULE = module
        yield module
    finally:
        captured_attempts = tuple(attempts)
        _MANIFEST_MODULE = previous_module
        assert not captured_attempts, (
            f"captured prohibited calls during guarded module load or use: {captured_attempts}"
        )


def test_manifest_core_has_no_network_sdk_process_or_file_side_effects():
    module = _current_manifest_module()
    before = module.snapshot_from_responses(
        _account_response(),
        _playlist_response(id=42, trackCount=2, trackIds=[11, 12], updateTime=7),
        42,
    )
    after = module.snapshot_from_responses(
        _account_response(),
        _playlist_response(id=42, trackCount=2, trackIds=[11, 12], updateTime=7),
        42,
    )
    manifest = module.compare_snapshots(before, after)

    assert manifest.enumeration_complete is True


@pytest.mark.parametrize(
    ("before_ids", "after_ids", "expected_error"),
    [
        ([11, 12, 13, 14, 15], [15, 14, 13, 12, 11], "TRACK_SEQUENCE_CHANGED"),
        ([11, 12, 13, 14, 15], [11, 13, 12, 14, 15], "TRACK_SEQUENCE_CHANGED"),
    ],
)
def test_full_reversal_and_interior_swap_change_order(
    before_ids, after_ids, expected_error,
):
    before = _snapshot(before_ids)
    after = _snapshot(after_ids)

    result = _current_manifest_module().compare_snapshots(before, after)

    assert result.status == "source_changed"
    assert result.error_code == expected_error
    assert result.enumeration_complete is False
    if before_ids[0] == after_ids[0] and before_ids[-1] == after_ids[-1]:
        assert before_ids[0] == after_ids[0]
        assert before_ids[-1] == after_ids[-1]
