"""Synthetic-only tests for the read-only playlist member snapshot producer.

Every test injects response objects.  No test opens a socket, reads a cookie,
runs a subprocess, or writes to the repository; module import is also guarded
so an accidental network/disk side effect fails the suite.
"""
from __future__ import annotations

import builtins
import contextlib
import copy
import importlib.util
import io
import json
import os
import re
import socket
import subprocess
import sys
import types
import urllib.request
from pathlib import Path
from unittest import mock

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[2] / "musicbox-service"
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
_SNAPSHOT_MODULE = None


# —— Guarded module loading ————————————————————————————————————————————————————
def _blocked(label, attempts):
    def _raise(*_args, **_kwargs):
        attempts.append(label)
        raise AssertionError(f"prohibited side effect: {label}")

    return _raise


@contextlib.contextmanager
def _side_effect_sentinels(attempts):
    """Fail closed on file/network/process side effects inside the block."""
    previous_bytecode = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(builtins, "open", _blocked("builtins.open", attempts)))
            stack.enter_context(mock.patch.object(io, "open", _blocked("io.open", attempts)))
            stack.enter_context(mock.patch.object(Path, "open", _blocked("Path.open", attempts)))
            stack.enter_context(mock.patch.object(Path, "write_text", _blocked("Path.write_text", attempts)))
            stack.enter_context(mock.patch.object(Path, "write_bytes", _blocked("Path.write_bytes", attempts)))
            for name in ("system", "popen", "remove", "unlink", "replace", "rename", "mkdir", "makedirs", "rmdir"):
                stack.enter_context(mock.patch.object(os, name, _blocked(f"os.{name}", attempts)))
            for name in ("run", "Popen", "call", "check_call", "check_output"):
                stack.enter_context(mock.patch.object(subprocess, name, _blocked(f"subprocess.{name}", attempts)))
            stack.enter_context(mock.patch.object(socket, "create_connection", _blocked("socket.create_connection", attempts)))
            stack.enter_context(mock.patch.object(socket.socket, "connect", _blocked("socket.socket.connect", attempts)))
            stack.enter_context(mock.patch.object(urllib.request, "urlopen", _blocked("urllib.request.urlopen", attempts)))
            yield
    finally:
        sys.dont_write_bytecode = previous_bytecode


@pytest.fixture(autouse=True)
def _snapshot_module(monkeypatch):
    """Load a fresh producer only after fail-closed sentinels are active."""
    global _SNAPSHOT_MODULE
    attempts: list[str] = []
    monkeypatch.syspath_prepend(str(SERVICE_DIR))

    module_name = "netease_playlist_snapshot_guarded_test_instance"
    spec = importlib.util.spec_from_file_location(module_name, SERVICE_DIR / "netease_playlist_snapshot.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, module_name, module)
    with _side_effect_sentinels(attempts):
        spec.loader.exec_module(module)

    previous = _SNAPSHOT_MODULE
    _SNAPSHOT_MODULE = module
    try:
        assert not attempts, f"producer import caused side effects: {attempts}"
        yield module
    finally:
        _SNAPSHOT_MODULE = previous


def _module():
    assert _SNAPSHOT_MODULE is not None, "producer module must be loaded under sentinels"
    return _SNAPSHOT_MODULE


def _ok_probe():
    return ("0.5.3", True)


def _artifact_ok(payload):
    artifact = payload["source_artifact"]
    return (
        payload["artifact_error_code"] is None
        and artifact["installed_version"] == "0.5.3"
        and artifact["dist_info_present"] is True
    )


# —— Fake injected responses ——————————————————————————————————————————————————
def _responses(
    track_ids,
    *,
    account_uid=9001,
    playlist_id=42,
    track_count=None,
    versions=None,
    account_code=200,
    playlist_code=200,
):
    if versions is None:
        versions = {"updateTime": 7}
    account = {"code": account_code, "account": {"id": account_uid}}
    playlist = {
        "code": playlist_code,
        "playlist": {
            "id": playlist_id,
            "trackCount": len(track_ids) if track_count is None else track_count,
            "trackIds": copy.deepcopy(track_ids),
            **copy.deepcopy(versions),
        },
    }
    return account, playlist


class _Fetchers:
    """One queued ``(account, playlist)`` pair per observation, never reused."""

    def __init__(self, accounts, playlists):
        self._accounts = list(accounts)
        self._playlists = list(playlists)
        self.account_calls = 0
        self.playlist_calls = 0
        self.account_objects: list[int] = []
        self.playlist_objects: list[int] = []

    def fetch_account(self):
        index = self.account_calls
        self.account_calls += 1
        if index >= len(self._accounts):
            raise AssertionError("unexpected extra account read")
        response = copy.deepcopy(self._accounts[index])
        self.account_objects.append(id(response))
        return response

    def fetch_playlist(self, playlist_id):
        index = self.playlist_calls
        self.playlist_calls += 1
        if index >= len(self._playlists):
            raise AssertionError("unexpected extra playlist read")
        response = copy.deepcopy(self._playlists[index])
        self.playlist_objects.append(id(response))
        return response


def _fetchers_for(before_ids, after_ids=None, *, playlist_id=42, before_kwargs=None, after_kwargs=None):
    if after_ids is None:
        after_ids = before_ids
    account_a, playlist_a = _responses(before_ids, playlist_id=playlist_id, **(before_kwargs or {}))
    account_b, playlist_b = _responses(after_ids, playlist_id=playlist_id, **(after_kwargs or {}))
    return _Fetchers([account_a, account_b], [playlist_a, playlist_b])


def _build(fetchers, *, playlist_id=42, probe=_ok_probe):
    return _module().build_snapshot(
        playlist_id,
        fetchers.fetch_account,
        fetchers.fetch_playlist,
        artifact_probe=probe,
    )


def _never_called_fetchers():
    def _raise(*_args, **_kwargs):
        raise AssertionError("the upstream must not be read")

    return _raise, _raise


# —— Complete / consistent snapshot ————————————————————————————————————————————
def test_complete_consistent_snapshot_payload():
    track_ids = list(range(101, 113))
    fetchers = _fetchers_for(track_ids)

    payload = _build(fetchers)

    assert payload["ok"] is True
    assert payload["status"] == "complete"
    assert payload["error_code"] is None
    assert payload["schema_version"] == 1
    assert payload["provider"] == "netease"
    assert payload["account_uid"] == 9001
    assert payload["playlist_id"] == 42
    assert payload["track_count"] == 12
    assert payload["position_count"] == 12
    assert payload["enumeration_complete"] is True
    assert _FINGERPRINT_RE.fullmatch(payload["source_fingerprint"])
    assert payload["source_versions"] == [["updateTime", 7]]
    assert [row["position"] for row in payload["positions"]] == list(range(1, 13))
    assert [row["track_id"] for row in payload["positions"]] == track_ids
    assert all(row["error_code"] is None for row in payload["positions"])
    assert {tuple(sorted(row)) for row in payload["positions"]} == {("error_code", "position", "track_id")}
    assert payload["metadata_status"] == "not_requested"
    assert payload["metadata_complete"] is False
    assert payload["import_ready"] is False
    assert payload["production_ready"] is False
    assert payload["provider_verified"] is False
    assert payload["source_artifact"] == {
        "provider": "netease",
        "package": "NetEase-MusicBox",
        "version": "0.5.3",
        "wheel_sha256": "7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12",
        "installed_version": "0.5.3",
        "dist_info_present": True,
    }
    assert _artifact_ok(payload)


def test_two_observations_read_account_and_playlist_independently():
    fetchers = _fetchers_for([11, 12, 13])

    payload = _build(fetchers)

    assert payload["status"] == "complete"
    # Fixed upstream budget: 2 account reads + 2 playlist reads (§4.2 item 4).
    assert fetchers.account_calls == 2
    assert fetchers.playlist_calls == 2
    # Each observation holds its own response objects; nothing is reused.
    assert len(set(fetchers.account_objects)) == 2
    assert len(set(fetchers.playlist_objects)) == 2


def test_requested_position_count_is_1001_not_preview_1000():
    module = _module()
    assert module.PLAYLIST_DETAIL_POSITION_REQUEST == 1001
    assert module.MAX_LEDGER_POSITIONS == 1000
    assert module.PINNED_VERSION == "0.5.3"
    assert module.WHEEL_SHA256 == "7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12"


# —— Count / order / duplicate / invalid ——————————————————————————————————————
def test_track_count_mismatch_is_incomplete_not_silently_fixed():
    fetchers = _fetchers_for([11, 12, 13], before_kwargs={"track_count": 4}, after_kwargs={"track_count": 4})

    payload = _build(fetchers)

    assert payload["ok"] is True
    assert payload["status"] == "incomplete"
    assert payload["error_code"] == "TRACK_COUNT_MISMATCH"
    assert payload["enumeration_complete"] is False
    assert payload["track_count"] == 4
    assert payload["position_count"] == 3
    assert len(payload["positions"]) == 3
    assert _FINGERPRINT_RE.fullmatch(payload["source_fingerprint"])


def test_sequence_change_between_observations_is_source_changed():
    fetchers = _fetchers_for([11, 12, 13], [13, 12, 11])

    payload = _build(fetchers)

    assert payload["ok"] is True
    assert payload["status"] == "source_changed"
    assert payload["error_code"] == "TRACK_SEQUENCE_CHANGED"
    assert payload["enumeration_complete"] is False
    # Positions come from the second observation, in provider order.
    assert [row["track_id"] for row in payload["positions"]] == [13, 12, 11]


def test_duplicate_track_ids_are_retained_at_every_position():
    fetchers = _fetchers_for([11, 12, 11, 13])

    payload = _build(fetchers)

    assert payload["status"] == "complete"
    assert payload["enumeration_complete"] is True
    assert payload["track_count"] == 4
    assert payload["position_count"] == 4
    assert [row["track_id"] for row in payload["positions"]] == [11, 12, 11, 13]


def test_invalid_track_id_is_retained_and_blocks_completeness():
    fetchers = _fetchers_for([11, "12", 13])

    payload = _build(fetchers)

    assert payload["ok"] is True
    assert payload["status"] == "incomplete"
    assert payload["error_code"] == "INVALID_TRACK_ID"
    assert payload["enumeration_complete"] is False
    assert payload["positions"][1] == {"position": 2, "track_id": None, "error_code": "INVALID_TRACK_ID"}
    assert payload["positions"][0]["error_code"] is None
    assert payload["positions"][2]["error_code"] is None


def test_playlist_id_mismatch_is_reported_not_coerced():
    account_a, playlist_a = _responses([11, 12], playlist_id=77)
    account_b, playlist_b = _responses([11, 12], playlist_id=77)
    fetchers = _Fetchers([account_a, account_b], [playlist_a, playlist_b])

    payload = _build(fetchers, playlist_id=42)

    assert payload["status"] == "unsupported"
    assert payload["error_code"] == "PLAYLIST_ID_MISMATCH"
    assert payload["enumeration_complete"] is False
    assert payload["positions"] == []


def test_missing_track_count_is_unsupported_not_zeroed():
    account, playlist = _responses([11, 12])
    del playlist["playlist"]["trackCount"]
    fetchers = _Fetchers([account, copy.deepcopy(account)], [playlist, copy.deepcopy(playlist)])

    payload = _build(fetchers)

    assert payload["status"] == "unsupported"
    assert payload["error_code"] == "UNSUPPORTED_SOURCE"
    assert payload["track_count"] is None
    assert payload["position_count"] == 0


# —— Position boundaries (1000 / 1001 / 100000 / 100001) ————————————————————————
def test_1000_positions_are_complete_and_prove_no_truncation():
    fetchers = _fetchers_for(list(range(1, 1001)))

    payload = _build(fetchers)

    assert payload["status"] == "complete"
    assert payload["enumeration_complete"] is True
    assert payload["track_count"] == 1000
    assert payload["position_count"] == 1000


def test_1001_positions_returned_verbatim_for_gateway_to_reject():
    """§4.4: the gateway rejects track_count > 1000; the producer never clamps."""
    fetchers = _fetchers_for(list(range(1, 1002)))

    payload = _build(fetchers)

    assert payload["status"] == "complete"
    assert payload["track_count"] == 1001
    assert payload["position_count"] == 1001


@pytest.mark.parametrize(
    ("position_count", "expected_error"),
    [
        (100_000, "SOURCE_LIMIT_REACHED"),
        (100_001, "POSITION_LIMIT_EXCEEDED"),
    ],
)
def test_source_position_limits_fold_to_unsupported(position_count, expected_error):
    fetchers = _fetchers_for(list(range(1, position_count + 1)))

    payload = _build(fetchers)

    assert payload["status"] == "unsupported"
    assert payload["error_code"] == expected_error
    assert payload["enumeration_complete"] is False
    assert payload["positions"] == []
    assert payload["track_count"] is None


# —— Artifact assertion (§4.1) —————————————————————————————————————————————————
def test_artifact_version_mismatch_fails_closed_before_any_read():
    fetch_account, fetch_playlist = _never_called_fetchers()

    payload = _module().build_snapshot(
        42, fetch_account, fetch_playlist, artifact_probe=lambda: ("0.5.4", True),
    )

    # Envelope flag stays true: only the fixed `status`/`error_code` carry the
    # failure (the adapter only understands `ok=false` + a known `error`).
    assert payload["ok"] is True
    assert payload["status"] == "unsupported"
    assert payload["error_code"] == "UNSUPPORTED_SOURCE"
    assert payload["artifact_error_code"] == "source_artifact_mismatch"
    assert payload["source_artifact"]["version"] == "0.5.3"
    assert payload["source_artifact"]["installed_version"] == "0.5.4"
    assert payload["source_artifact"]["dist_info_present"] is True
    assert payload["enumeration_complete"] is False
    assert payload["account_uid"] is None
    assert payload["playlist_id"] == 42
    assert payload["positions"] == []


def test_artifact_metadata_missing_fails_closed():
    fetch_account, fetch_playlist = _never_called_fetchers()

    payload = _module().build_snapshot(
        42, fetch_account, fetch_playlist, artifact_probe=lambda: ("0.5.3", False),
    )

    assert payload["status"] == "unsupported"
    assert payload["artifact_error_code"] == "source_artifact_mismatch"
    assert payload["source_artifact"]["installed_version"] == "0.5.3"
    assert payload["source_artifact"]["dist_info_present"] is False


def test_artifact_probe_failure_fails_closed():
    fetch_account, fetch_playlist = _never_called_fetchers()

    def _boom():
        raise RuntimeError("metadata unavailable")

    payload = _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_boom)

    assert payload["status"] == "unsupported"
    assert payload["error_code"] == "UNSUPPORTED_SOURCE"
    assert payload["artifact_error_code"] == "source_artifact_mismatch"
    assert payload["source_artifact"]["installed_version"] is None
    assert payload["source_artifact"]["dist_info_present"] is False
    assert payload["source_artifact"]["wheel_sha256"] == _module().WHEEL_SHA256


def test_artifact_probe_with_unknown_shape_fails_closed():
    fetch_account, fetch_playlist = _never_called_fetchers()

    payload = _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=lambda: None)

    assert payload["status"] == "unsupported"
    assert payload["artifact_error_code"] == "source_artifact_mismatch"


def test_default_artifact_probe_shape_and_fail_closed_when_absent():
    module = _module()
    version, metadata_present = module._default_artifact_probe()  # noqa: SLF001  (contract §4.1 item 3)

    assert version is None or isinstance(version, str)
    assert isinstance(metadata_present, bool)
    if version == module.PINNED_VERSION and metadata_present:
        pytest.skip("NetEase-MusicBox 0.5.3 is installed in this test environment")

    fetch_account, fetch_playlist = _never_called_fetchers()
    payload = module.build_snapshot(42, fetch_account, fetch_playlist)

    assert payload["status"] == "unsupported"
    assert payload["artifact_error_code"] == "source_artifact_mismatch"


# —— Upstream failure folding ——————————————————————————————————————————————————
def test_account_read_exception_folds_to_upstream_error():
    attempts = {"account": 0, "playlist": 0}

    def fetch_account():
        attempts["account"] += 1
        raise RuntimeError("upstream exploded with cookie=SID=secret")

    def fetch_playlist(_playlist_id):
        attempts["playlist"] += 1
        raise AssertionError("playlist must not be read after a failed account read")

    payload = _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)

    # Failure status, but the envelope flag stays true (see _payload docstring).
    assert payload["ok"] is True
    assert payload["status"] == "upstream_error"
    assert payload["error_code"] == "SOURCE_READ_FAILED"
    assert payload["enumeration_complete"] is False
    assert payload["positions"] == []
    assert attempts == {"account": 2, "playlist": 0}


def test_playlist_read_exception_folds_to_upstream_error():
    attempts = {"account": 0, "playlist": 0}

    def fetch_account():
        attempts["account"] += 1
        return {"code": 200, "account": {"id": 9001}}

    def fetch_playlist(_playlist_id):
        attempts["playlist"] += 1
        raise TimeoutError("read timed out")

    payload = _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)

    assert payload["status"] == "upstream_error"
    assert payload["error_code"] == "SOURCE_READ_FAILED"
    assert attempts == {"account": 2, "playlist": 2}


@pytest.mark.parametrize(
    "account_response",
    [None, {"code": 301}, {"code": 200}],
    ids=["none-response", "code-301", "no-account-or-profile"],
)
def test_not_logged_in_shapes_fold_to_fixed_status(account_response):
    attempts = {"account": 0, "playlist": 0}

    def fetch_account():
        attempts["account"] += 1
        return copy.deepcopy(account_response)

    def fetch_playlist(_playlist_id):
        attempts["playlist"] += 1
        raise AssertionError("playlist must not be read while logged out")

    payload = _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)

    assert payload["ok"] is True
    assert payload["status"] == "not_logged_in"
    assert payload["error_code"] == "NOT_LOGGED_IN"
    assert payload["enumeration_complete"] is False
    assert attempts == {"account": 2, "playlist": 0}


def test_upstream_response_code_failure_folds_to_upstream_error():
    fetchers = _fetchers_for([11, 12], before_kwargs={"playlist_code": 500}, after_kwargs={"playlist_code": 500})

    payload = _build(fetchers)

    assert payload["status"] == "upstream_error"
    assert payload["error_code"] == "SOURCE_READ_FAILED"


def test_payload_never_echoes_urls_cookies_or_exception_text():
    def fetch_account():
        raise RuntimeError("https://music.163.com/api?cookie=MUSIC_U%3DSECRET-VALUE")

    def fetch_playlist(_playlist_id):
        raise AssertionError("playlist must not be read")

    payload = _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)
    encoded = json.dumps(payload, sort_keys=True).lower()

    assert "http" not in encoded
    assert "cookie" not in encoded
    assert "secret" not in encoded
    assert "music.163" not in encoded
    assert "traceback" not in encoded


# —— Defensive input handling ——————————————————————————————————————————————————
@pytest.mark.parametrize("playlist_id", [0, -1, True, "42", None], ids=["zero", "negative", "bool", "string", "none"])
def test_invalid_playlist_id_never_reads_upstream(playlist_id):
    fetch_account, fetch_playlist = _never_called_fetchers()

    payload = _module().build_snapshot(playlist_id, fetch_account, fetch_playlist, artifact_probe=_ok_probe)

    assert payload["status"] == "unsupported"
    assert payload["error_code"] == "UNSUPPORTED_SOURCE"
    assert payload["playlist_id"] is None
    assert payload["artifact_error_code"] is None


# —— Read-only switch (§4.2; independent of the save switches) —————————————————
@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, False),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "true"}, True),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": " TRUE "}, True),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "1"}, True),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "yes"}, True),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "on"}, True),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "false"}, False),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "0"}, False),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": ""}, False),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "no"}, False),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": 1}, False),
        ({"FNMUSIC_PLAYLIST_MANIFEST_READONLY": None}, False),
    ],
)
def test_producer_readonly_switch_values(environ, expected):
    module = _module()
    assert module.PLAYLIST_MANIFEST_READONLY_ENV == "FNMUSIC_PLAYLIST_MANIFEST_READONLY"
    assert module.producer_enabled(environ) is expected


def test_save_switches_do_not_affect_producer_enabled():
    module = _module()
    save_on = {
        "FNMUSIC_MANUAL_SAVE_ENABLED": "true",
        "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED": "true",
    }
    save_off = {
        "FNMUSIC_MANUAL_SAVE_ENABLED": "false",
        "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED": "false",
    }
    readonly = {"FNMUSIC_PLAYLIST_MANIFEST_READONLY": "true"}
    # Save switches alone never enable the read-only producer.
    assert module.producer_enabled(save_off) is False
    assert module.producer_enabled(save_on) is False
    # §4.7 read-only window: saving fully off, observation on.
    assert module.producer_enabled({**save_off, **readonly}) is True
    assert module.producer_enabled({**save_on, **readonly}) is True


# —— Import/pure-call side effects ————————————————————————————————————————————
def test_pure_build_has_no_file_network_or_process_side_effects():
    fetchers = _fetchers_for([11, 12, 13])
    attempts: list[str] = []

    with _side_effect_sentinels(attempts):
        payload = _build(fetchers)

    assert payload["status"] == "complete"
    assert attempts == []


# —— Live binding request shape (P2-5) ————————————————————————————————————————
class _FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeNetEaseApi:
    def __init__(self, playlist_id=42, track_ids=(11, 12)):
        self._playlist_id = playlist_id
        self._track_ids = list(track_ids)
        self.account_calls = 0
        self.playlist_calls: "list[tuple[str, dict]]" = []

    def get_account_info(self):
        self.account_calls += 1
        return {"code": 200, "account": {"id": 9001}}

    def eapi_request(self, path, params):
        self.playlist_calls.append((path, dict(params)))
        return {
            "code": 200,
            "playlist": {
                "id": self._playlist_id,
                "trackCount": len(self._track_ids),
                "trackIds": list(self._track_ids),
                "updateTime": 7,
            },
        }


def test_live_fetchers_request_n_1001_and_no_offset(monkeypatch):
    """Stubbed SDK seam: both playlist reads use n=1001, s=0 (not preview's 1000)."""
    module = _module()
    api = _FakeNetEaseApi()
    fake_netease_ext = types.SimpleNamespace(
        _api_lock=_FakeLock(),
        _get_api_locked=lambda: api,
    )
    monkeypatch.setitem(sys.modules, "netease_ext", fake_netease_ext)

    fetch_account, fetch_playlist = module.live_fetchers()
    payload = module.build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)

    assert payload["status"] == "complete"
    assert api.account_calls == 2
    assert api.playlist_calls == [
        ("/api/v6/playlist/detail", {"id": 42, "n": 1001, "s": 0}),
        ("/api/v6/playlist/detail", {"id": 42, "n": 1001, "s": 0}),
    ]
    # n=1001 (not 1000) is what keeps "exactly 1000" distinguishable from truncation.
    assert module.PLAYLIST_DETAIL_POSITION_REQUEST == 1001


# —— Cross-module: gateway adapter accepts the producer envelope (P0) ——————————
_GATEWAY_ADAPTER = None


def _gateway_adapter():
    """Load ``proxy/manual_save_playlist_jobs.py`` by path (its own import style)."""
    global _GATEWAY_ADAPTER
    if _GATEWAY_ADAPTER is not None:
        return _GATEWAY_ADAPTER
    proxy_dir = SERVICE_DIR.parent / "proxy"
    if str(proxy_dir) not in sys.path:
        sys.path.insert(0, str(proxy_dir))
    spec = importlib.util.spec_from_file_location(
        "manual_save_playlist_jobs_crosscheck",
        proxy_dir / "manual_save_playlist_jobs.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    _GATEWAY_ADAPTER = module
    return module


def _upstream_error_payload():
    def fetch_account():
        raise RuntimeError("read failed")

    def fetch_playlist(_playlist_id):
        raise AssertionError("playlist must not be read")

    return _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)


def _not_logged_in_payload():
    def fetch_account():
        return None

    def fetch_playlist(_playlist_id):
        raise AssertionError("playlist must not be read")

    return _module().build_snapshot(42, fetch_account, fetch_playlist, artifact_probe=_ok_probe)


def _artifact_mismatch_payload():
    fetch_account, fetch_playlist = _never_called_fetchers()
    return _module().build_snapshot(
        42, fetch_account, fetch_playlist, artifact_probe=lambda: ("0.5.4", True),
    )


def test_gateway_adapter_accepts_complete_envelope():
    adapter = _gateway_adapter()
    payload = _build(_fetchers_for([11, 12, 13]))

    assert adapter._validate_snapshot_envelope(payload, 42) is payload  # noqa: SLF001
    manifest = adapter.build_manifest(42, payload)
    assert manifest["enumeration_complete"] is True
    assert [entry["source_id"] for entry in manifest["entries"]] == ["11", "12", "13"]


@pytest.mark.parametrize(
    ("payload_factory", "expected_code"),
    [
        (_artifact_mismatch_payload, "source_artifact_mismatch"),
        (_upstream_error_payload, "playlist_unavailable"),
        (_not_logged_in_payload, "not_logged_in"),
    ],
    ids=["artifact-mismatch", "upstream-error", "not-logged-in"],
)
def test_gateway_adapter_maps_producer_failures_without_malformed_fold(payload_factory, expected_code):
    """P0 regression: failure statuses keep ``ok=true`` and reach the §3.3 mapping."""
    adapter = _gateway_adapter()
    payload = payload_factory()

    assert payload["ok"] is True
    # Structurally accepted: status/provider/schema_version/playlist_id all present.
    assert adapter._validate_snapshot_envelope(payload, 42) is payload  # noqa: SLF001
    with pytest.raises(adapter.PlaylistJobError) as excinfo:
        adapter.build_manifest(42, payload)
    assert excinfo.value.code == expected_code
    assert excinfo.value.code != adapter.PRODUCER_MALFORMED_RESPONSE
    # Every reached code must be registered in the fixed message table (§3.9).
    assert expected_code in adapter._MESSAGES  # noqa: SLF001


def test_gateway_adapter_maps_source_position_limit_to_playlist_too_large():
    """§4.4: the producer's >MAX_SOURCE_POSITIONS unsupported maps to 409 too_large."""
    adapter = _gateway_adapter()
    payload = _build(_fetchers_for(list(range(1, 100_001))))

    assert payload["status"] == "unsupported"
    assert payload["error_code"] == "SOURCE_LIMIT_REACHED"
    with pytest.raises(adapter.PlaylistJobError) as excinfo:
        adapter.build_manifest(42, payload)
    assert excinfo.value.code == "playlist_too_large"


def test_gateway_adapter_still_rejects_ok_false_without_fixed_error():
    adapter = _gateway_adapter()
    complete = _build(_fetchers_for([11, 12]))

    # Regression guard: the old `ok=false + status` shape is what got folded.
    legacy = dict(complete)
    legacy["ok"] = False
    with pytest.raises(adapter.ProducerError) as excinfo:
        adapter._validate_snapshot_envelope(legacy, 42)  # noqa: SLF001
    assert excinfo.value.code == adapter.PRODUCER_MALFORMED_RESPONSE

    # The endpoint-level 503 disabled body is the only accepted ok=false shape.
    assert adapter._producer_error_from_body(  # noqa: SLF001
        b'{"ok": false, "error": "manifest_disabled"}', 65536,
    ) == "manifest_disabled"
