"""Synthetic tests for the read-only test-library content verifier."""
from __future__ import annotations

import errno
import hashlib
import io
import json
import os
import platform
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_position_file_evidence as verifier  # noqa: E402


_LINUX = sys.platform == "linux"
_HAS_SECURE_WALK = _LINUX and platform.machine() == "x86_64" and hasattr(os, "O_NOFOLLOW")
_GENERATION = 100


def _row(position: int, data: bytes) -> dict:
    return {"position": position, "published_sha256": hashlib.sha256(data).hexdigest(), "published_size": len(data)}


def _readonly_snapshot(treeid=256, generation=100, *, readonly=True, uuid=b"s" * 16, parent_uuid=b"p" * 16):
    # Independent Linux UAPI fixture: GET_SUBVOL_INFO returns root_item.flags,
    # where BTRFS_ROOT_SUBVOL_RDONLY is the literal bit 0 (0x1).
    flags = 0x1 if readonly else 0
    return (treeid, generation, flags, uuid, parent_uuid)


def _legacy_unknown(positions: list[dict]) -> dict:
    return {"scope": "read_only_test_tree", "complete": False, "positions": positions}


def _expected_evidence(
    *,
    profile: str = "btrfs-ro-snapshot",
    read_mode: str = "o_noatime",
    root_scope: str = "subtree",
    ro_semantics: str = "metadata_ro_bit_only",
    generation_bracket: str = "unknown",
    identity: str = "unknown",
    content_hash: str = "not_attempted",
    pre: str = "done",
    post: str = "not_attempted",
    mechanism: str | None = "syncfs",
    generation_before_pre_sync: int | None = _GENERATION,
    generation_start: int | None = _GENERATION,
    generation_before_post_sync: int | None = None,
    generation_end: int | None = None,
    max_entries: int | None = None,
    entries_seen: int = 0,
    limit_exceeded: bool = False,
    scanner_unprivileged: str = "ok",
) -> dict:
    return {
        "evidence_profile": profile,
        "read_mode": read_mode,
        "root_scope": root_scope,
        "sync_barrier": {
            "pre": pre,
            "post": post,
            "mechanism": mechanism,
            "generation_before_pre_sync": generation_before_pre_sync,
            "generation_start": generation_start,
            "generation_before_post_sync": generation_before_post_sync,
            "generation_end": generation_end,
        },
        "limits": {
            "max_entries": verifier.MAX_ENTRIES if max_entries is None else max_entries,
            "entries_seen": entries_seen,
            "limit_exceeded": limit_exceeded,
        },
        "checks": {
            "ro_semantics": ro_semantics,
            "generation_bracket": generation_bracket,
            "scanner_unprivileged": scanner_unprivileged,
            "identity": identity,
            "content_hash": content_hash,
        },
    }


def _expected_report(*, complete: bool, unknown_reason: str | None, positions: list[dict], **overrides) -> dict:
    report = {
        "scope": "read_only_test_tree",
        "complete": complete,
        "unknown_reason": unknown_reason,
        "positions": positions,
    }
    report.update(_expected_evidence(**overrides))
    return report


def _stable_scan_evidence(**overrides) -> dict:
    """Evidence for a completed btrfs-ro-snapshot scan with both barriers stable."""
    values = {
        "generation_bracket": "stable",
        "identity": "ok",
        "content_hash": "performed",
        "post": "done",
        "generation_before_post_sync": _GENERATION,
        "generation_end": _GENERATION,
    }
    values.update(overrides)
    return values


def _allow_synthetic_snapshot_traversal(monkeypatch):
    # Linux CI temp directories are not immutable Btrfs snapshots. Mock the
    # process, native-evidence hard gates, subvolume gates and the sync barrier
    # only to exercise traversal. This is not native snapshot, generation or
    # barrier-stability evidence.
    monkeypatch.setattr(verifier, "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED", True)
    monkeypatch.setattr(verifier, "_POINT_IN_TIME_SCAN_VALIDATED", True)
    monkeypatch.setattr(verifier, "_require_unprivileged_process", lambda: None)
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: _readonly_snapshot())
    monkeypatch.setattr(verifier, "_sync_filesystem", lambda _fd: "syncfs")
    # Never label a synthetic temp directory as a Btrfs subvolume root.
    monkeypatch.setattr(verifier, "_BTRFS_FIRST_FREE_OBJECTID", -1)


# ---------------------------------------------------------------------------
# Input, capability and contract validation
# ---------------------------------------------------------------------------


def test_input_is_exact_and_bounded():
    valid = {"positions": [_row(1, b"audio") ]}
    assert verifier._validate_positions(valid) == valid["positions"]
    with pytest.raises(verifier.EvidenceInputError):
        verifier._validate_positions({"positions": [{**_row(1, b"x"), "path": "/private"}]})
    with pytest.raises(verifier.EvidenceInputError):
        verifier._validate_positions({"positions": [{**_row(1, b"x"), "position": True}]})
    with pytest.raises(verifier.EvidenceInputError):
        verifier._validate_positions({"positions": [_row(1, b"x"), _row(1, b"y")]})


def test_process_capability_parser_fails_closed():
    assert verifier._parse_process_capabilities("CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n") == (0, 0)
    with pytest.raises(verifier.EvidenceInputError):
        verifier._parse_process_capabilities("CapEff:\t0\n")
    with pytest.raises(verifier.EvidenceInputError):
        verifier._parse_process_capabilities("CapEff:\tinvalid\nCapPrm:\t0\n")
    assert verifier._parse_process_capabilities("CapEff:\t1\nCapPrm:\t1\n") != (0, 0)


def test_get_subvol_info_readonly_flag_uses_root_item_abi_bit_zero(monkeypatch):
    # Keep the returned ABI values literal and independent of the production
    # constant so a regression to BTRFS_SUBVOL_RDONLY (0x2) fails this test.
    root_item_readonly_flag = 0x1
    getflags_readonly_flag = 0x2
    fixture = (256, 100, root_item_readonly_flag, b"s" * 16, b"p" * 16)
    assert verifier._BTRFS_ROOT_SUBVOL_RDONLY == root_item_readonly_flag
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: fixture)
    assert verifier._require_readonly_snapshot(17) == fixture

    wrong_ioctl_flag_fixture = (256, 100, getflags_readonly_flag, b"s" * 16, b"p" * 16)
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: wrong_ioctl_flag_fixture)
    with pytest.raises(verifier.UnsafeTreeError):
        verifier._require_readonly_snapshot(17)

    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: _readonly_snapshot(readonly=False))
    with pytest.raises(verifier.UnsafeTreeError):
        verifier._require_readonly_snapshot(17)
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: _readonly_snapshot(parent_uuid=bytes(16)))
    with pytest.raises(verifier.UnsafeTreeError):
        verifier._require_readonly_snapshot(17)


def test_same_snapshot_gate_rejects_subvolume_and_generation_changes(monkeypatch):
    expected = _readonly_snapshot()
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: expected)
    assert verifier._require_same_snapshot(17, expected) == expected
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: _readonly_snapshot(treeid=257))
    with pytest.raises(verifier.UnsafeTreeError):
        verifier._require_same_snapshot(17, expected)
    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", lambda _fd: _readonly_snapshot(generation=101))
    with pytest.raises(verifier.UnsafeTreeError):
        verifier._require_same_snapshot(17, expected)


def test_capability_gates_stay_false():
    # The pre-existing hard gate and both new capability gates must stay False
    # until separate native/independent evidence and review exist.
    assert verifier._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED is False
    assert verifier._POINT_IN_TIME_SCAN_VALIDATED is False
    assert verifier._INDEPENDENT_COPY_VIEW_VALIDATED is False
    assert verifier.MAX_ENTRIES == 100_000


def test_evidence_profile_and_entry_limit_validation():
    assert verifier._validate_evidence_profile("btrfs-ro-snapshot") == "btrfs-ro-snapshot"
    assert verifier._validate_evidence_profile("independent-copy") == "independent-copy"
    for bad in ("copy", "", None, 1):
        with pytest.raises(verifier.EvidenceInputError):
            verifier._validate_evidence_profile(bad)
    assert verifier._validate_max_entries(1) == 1
    assert verifier._validate_max_entries(verifier.MAX_ENTRIES) == verifier.MAX_ENTRIES
    for bad in (0, -1, True, "100", None, 1.5):
        with pytest.raises(verifier.EvidenceInputError):
            verifier._validate_max_entries(bad)


def test_invalid_profile_or_limit_returns_legacy_unknown(tmp_path):
    row = [_row(1, b"x")]
    for kwargs in ({"evidence_profile": "bogus"}, {"max_entries": 0}):
        assert verifier.scan_tree(str(tmp_path), row, **kwargs) == _legacy_unknown(
            [{"position": 1, "status": "unknown"}]
        )


def test_scan_fails_closed_without_native_generation_stability_evidence(tmp_path, monkeypatch):
    assert verifier._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED is False

    def forbidden_open(_path):
        pytest.fail("unverified generation guard must stop before opening the scan root")

    monkeypatch.setattr(verifier, "_open_root", forbidden_open)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"candidate")])
    assert report == {
        "scope": "read_only_test_tree", "complete": False,
        "positions": [{"position": 1, "status": "unknown"}],
    }


def test_point_in_time_gate_blocks_scan_even_when_legacy_gate_is_patched(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED", True)

    def forbidden_open(_path):
        pytest.fail("an unvalidated point-in-time gate must stop before opening the scan root")

    monkeypatch.setattr(verifier, "_open_root", forbidden_open)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"candidate")])
    assert report == {
        "scope": "read_only_test_tree", "complete": False,
        "positions": [{"position": 1, "status": "unknown"}],
    }


def test_unprivileged_process_gate_failure_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setattr(verifier, "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED", True)
    monkeypatch.setattr(verifier, "_POINT_IN_TIME_SCAN_VALIDATED", True)

    def privileged():
        raise verifier.UnsafeTreeError(errno.EPERM, "privileged scanner process")

    monkeypatch.setattr(verifier, "_require_unprivileged_process", privileged)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report == _expected_report(
        complete=False,
        unknown_reason="permission_unverifiable",
        positions=[{"position": 1, "status": "unknown"}],
        ro_semantics="unknown",
        root_scope="unknown",
        pre="not_attempted",
        post="not_attempted",
        mechanism=None,
        generation_before_pre_sync=None,
        generation_start=None,
        scanner_unprivileged="unknown",
    )


def test_cli_returns_nonzero_when_native_generation_gate_blocks(tmp_path, monkeypatch, capsys):
    payload = {"positions": [_row(1, b"candidate")]}
    monkeypatch.setattr(verifier.sys, "stdin", io.StringIO(json.dumps(payload)))

    exit_code = verifier.main(["--root", str(tmp_path)])

    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "scope": "read_only_test_tree", "complete": False,
        "positions": [{"position": 1, "status": "unknown"}],
    }


def test_root_scope_classification_uses_btrfs_first_free_objectid(tmp_path, monkeypatch):
    root_stat = os.stat(tmp_path)
    monkeypatch.setattr(verifier, "_BTRFS_FIRST_FREE_OBJECTID", root_stat.st_ino)
    assert verifier._classify_root_scope(root_stat) == "subvolume_root"
    monkeypatch.setattr(verifier, "_BTRFS_FIRST_FREE_OBJECTID", root_stat.st_ino + 1)
    assert verifier._classify_root_scope(root_stat) == "subtree"


def test_read_opens_request_o_noatime(monkeypatch):
    sentinel = getattr(os, "O_NOATIME", 0o1000000)
    monkeypatch.setattr(verifier, "_O_NOATIME", sentinel)
    assert verifier._noatime_flag() == sentinel
    flags = verifier._directory_open_flags()
    if hasattr(os, "O_DIRECTORY"):
        assert flags & os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        assert flags & os.O_NOFOLLOW
    monkeypatch.setattr(verifier, "_O_NOATIME", 0)
    with pytest.raises(verifier.NoAtimeUnavailableError):
        verifier._noatime_flag()


# ---------------------------------------------------------------------------
# Synthetic traversal contract (Linux x86_64 openat2 required)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_unique_and_no_match_are_read_only_and_do_not_return_paths(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "subdir").mkdir()
    payload = b"synthetic FLAC bytes"
    sensitive_name = "ｃｏｏｋｉｅ：secret=hidden.flac"
    audio = tmp_path / "subdir" / sensitive_name
    audio.write_bytes(payload)
    altered = tmp_path / "subdir" / "altered.flac"
    altered.write_bytes(b"x" * len(payload))
    before = audio.read_bytes()
    report = verifier.scan_tree(str(tmp_path), [_row(1, payload), _row(2, b"altered")])
    assert report == _expected_report(
        complete=True,
        unknown_reason=None,
        positions=[
            {"position": 1, "status": "unique_match", "match_count": 1},
            {"position": 2, "status": "no_match", "match_count": 0},
        ],
        entries_seen=3,
        **_stable_scan_evidence(),
    )
    assert audio.read_bytes() == before
    assert "subdir" not in str(report)
    assert sensitive_name not in str(report)
    assert "cookie" not in str(report).casefold()


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_duplicate_identical_files_are_ambiguous_for_every_matching_position(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    payload = b"same bytes"
    (tmp_path / "a.flac").write_bytes(payload)
    (tmp_path / "b.flac").write_bytes(payload)
    report = verifier.scan_tree(str(tmp_path), [_row(1, payload), _row(2, payload)])
    assert report == _expected_report(
        complete=True,
        unknown_reason=None,
        positions=[
            {"position": 1, "status": "ambiguous", "match_count": 2},
            {"position": 2, "status": "ambiguous", "match_count": 2},
        ],
        entries_seen=2,
        **_stable_scan_evidence(),
    )


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_every_read_and_directory_open_requests_o_noatime(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    assert verifier._O_NOATIME, "O_NOATIME must exist on this platform"
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "track.flac").write_bytes(b"audio")
    captured: list[tuple[str, int]] = []
    real_openat2 = verifier._openat2

    def spy_openat2(root_fd, relative_path, flags):
        captured.append((relative_path, flags))
        return real_openat2(root_fd, relative_path, flags)

    monkeypatch.setattr(verifier, "_openat2", spy_openat2)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is True
    assert captured, "scan must open descendants through openat2"
    assert all(flags & verifier._O_NOATIME for _path, flags in captured)
    assert {path for path, _flags in captured} == {"nested", "nested/track.flac"}


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_missing_o_noatime_fails_closed(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")
    monkeypatch.setattr(verifier, "_O_NOATIME", 0)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["unknown_reason"] == "noatime_unavailable"
    assert report["read_mode"] == "unavailable"
    assert report["positions"] == [{"position": 1, "status": "unknown"}]
    assert report["sync_barrier"]["pre"] == "not_attempted"


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_o_noatime_permission_denial_fails_closed(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")
    real_openat2 = verifier._openat2

    def refuse_file_open(root_fd, relative_path, flags):
        if relative_path.endswith("track.flac"):
            raise verifier.UnsafeTreeError(errno.EPERM, "Operation not permitted")
        return real_openat2(root_fd, relative_path, flags)

    monkeypatch.setattr(verifier, "_openat2", refuse_file_open)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["unknown_reason"] == "noatime_permission_denied"
    assert report["read_mode"] == "o_noatime_denied"
    assert report["sync_barrier"]["pre"] == "done"
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_subtree_root_is_accepted_and_confined(tmp_path, monkeypatch):
    # The root is a library directory inside a snapshot, not the subvolume
    # root. A same-bytes sibling outside the root must not be traversed.
    _allow_synthetic_snapshot_traversal(monkeypatch)
    library = tmp_path / "snapshot" / "library"
    library.mkdir(parents=True)
    payload = b"subtree candidate"
    (library / "track.flac").write_bytes(payload)
    (tmp_path / "snapshot" / "sibling.flac").write_bytes(payload)
    report = verifier.scan_tree(str(library), [_row(1, payload)])
    assert report["complete"] is True
    assert report["root_scope"] == "subtree"
    assert report["positions"] == [{"position": 1, "status": "unique_match", "match_count": 1}]
    assert report["limits"]["entries_seen"] == 1
    assert library.as_posix() not in json.dumps(report)


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_subvolume_root_is_labelled_as_subvolume_root(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")
    monkeypatch.setattr(verifier, "_BTRFS_FIRST_FREE_OBJECTID", os.stat(tmp_path).st_ino)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is True
    assert report["root_scope"] == "subvolume_root"


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 syncfs required")
def test_filesystem_sync_barrier_really_commits_on_linux(tmp_path):
    # Real kernel path (no mock): an unprivileged process must be able to call
    # the filesystem-level barrier used by the scanner on a directory fd.
    fd = os.open(tmp_path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        mechanism = verifier._sync_filesystem(fd)
    finally:
        os.close(fd)
    assert mechanism in ("syncfs", "sync")


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_sync_barrier_runs_before_and_after_the_scan(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")
    calls: list[int] = []

    def counting_sync(fd):
        calls.append(fd)
        return "syncfs"

    monkeypatch.setattr(verifier, "_sync_filesystem", counting_sync)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is True
    assert len(calls) == 2
    assert report["sync_barrier"] == {
        "pre": "done",
        "post": "done",
        "mechanism": "syncfs",
        "generation_before_pre_sync": _GENERATION,
        "generation_start": _GENERATION,
        "generation_before_post_sync": _GENERATION,
        "generation_end": _GENERATION,
    }


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_pre_barrier_failure_is_fail_closed(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")

    def failing_sync(_fd):
        raise PermissionError("syncfs denied")

    monkeypatch.setattr(verifier, "_sync_filesystem", failing_sync)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["unknown_reason"] == "sync_barrier_failed"
    assert report["sync_barrier"]["pre"] == "failed"
    assert report["sync_barrier"]["post"] == "not_attempted"
    assert report["sync_barrier"]["generation_before_pre_sync"] == _GENERATION
    assert report["sync_barrier"]["generation_start"] is None
    assert report["positions"] == [{"position": 1, "status": "unknown"}]
    assert str(tmp_path) not in json.dumps(report)


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_post_barrier_failure_is_fail_closed(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")
    calls: list[int] = []

    def second_call_fails(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise PermissionError("syncfs denied")
        return "syncfs"

    monkeypatch.setattr(verifier, "_sync_filesystem", second_call_fails)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["unknown_reason"] == "sync_barrier_failed"
    assert len(calls) == 2
    assert report["sync_barrier"]["pre"] == "done"
    assert report["sync_barrier"]["post"] == "failed"
    assert report["sync_barrier"]["generation_before_post_sync"] == _GENERATION
    assert report["sync_barrier"]["generation_end"] is None


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_generation_change_committed_by_final_barrier_is_unknown(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"audio")
    state = {"calls": 0, "post": False}

    def sync(fd):
        state["calls"] += 1
        if state["calls"] == 2:
            state["post"] = True
        return "syncfs"

    monkeypatch.setattr(verifier, "_sync_filesystem", sync)
    monkeypatch.setattr(
        verifier, "_read_btrfs_subvol_info",
        lambda _fd: _readonly_snapshot(generation=101 if state["post"] else _GENERATION),
    )
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["unknown_reason"] == "view_mutated"
    assert report["checks"]["generation_bracket"] == "changed"
    assert report["sync_barrier"]["pre"] == "done"
    assert report["sync_barrier"]["post"] == "done"
    assert report["sync_barrier"]["generation_before_post_sync"] == _GENERATION
    assert report["sync_barrier"]["generation_end"] == 101


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_entry_limit_override_is_recorded_and_exceeded_limit_is_unknown(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    payload = b"one candidate"
    for name in ("a.flac", "b.flac", "c.flac"):
        (tmp_path / name).write_bytes(payload)
    report = verifier.scan_tree(str(tmp_path), [_row(1, payload)], max_entries=2)
    assert report["complete"] is False
    assert report["unknown_reason"] == "limits_exceeded"
    assert report["limits"] == {"max_entries": 2, "entries_seen": 3, "limit_exceeded": True}
    assert report["positions"] == [{"position": 1, "status": "unknown"}]

    default_report = verifier.scan_tree(str(tmp_path), [_row(1, payload)])
    assert default_report["complete"] is True
    assert default_report["limits"] == {
        "max_entries": verifier.MAX_ENTRIES,
        "entries_seen": 3,
        "limit_exceeded": False,
    }
    assert default_report["positions"] == [{"position": 1, "status": "ambiguous", "match_count": 3}]


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_independent_copy_profile_scans_but_stays_unknown(tmp_path, monkeypatch):
    # M4 plain copy / M2 received tree: no Btrfs RO ioctl, no generation
    # bracket, but the unprivileged, O_NOATIME, identity, hash and barrier
    # checks still run; the report must never claim a complete result.
    monkeypatch.setattr(verifier, "_require_unprivileged_process", lambda: None)
    sync_calls: list[int] = []
    monkeypatch.setattr(verifier, "_sync_filesystem", lambda fd: sync_calls.append(fd) or "syncfs")

    def forbidden_subvol(_fd):
        pytest.fail("independent-copy must not pretend to have Btrfs RO semantics")

    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", forbidden_subvol)
    (tmp_path / "track.flac").write_bytes(b"audio")
    report = verifier.scan_tree(
        str(tmp_path), [_row(1, b"audio")], evidence_profile="independent-copy"
    )
    assert report["complete"] is False
    assert report["unknown_reason"] == "ro_semantics_unavailable"
    assert report["positions"] == [{"position": 1, "status": "unknown"}]
    assert report["evidence_profile"] == "independent-copy"
    assert report["checks"]["ro_semantics"] == "unavailable"
    assert report["checks"]["generation_bracket"] == "not_applicable"
    assert report["checks"]["content_hash"] == "performed"
    assert report["checks"]["identity"] == "ok"
    assert report["checks"]["scanner_unprivileged"] == "ok"
    assert report["sync_barrier"]["pre"] == "done"
    assert report["sync_barrier"]["post"] == "done"
    assert report["sync_barrier"]["generation_before_pre_sync"] is None
    assert report["sync_barrier"]["generation_start"] is None
    assert report["sync_barrier"]["generation_end"] is None
    assert report["root_scope"] == "unknown"
    assert report["limits"]["entries_seen"] == 1
    assert len(sync_calls) == 2
    assert str(tmp_path) not in json.dumps(report)


# ---------------------------------------------------------------------------
# CLI contract
# ---------------------------------------------------------------------------


def test_cli_forwards_evidence_profile_and_entry_limit(tmp_path, monkeypatch, capsys):
    captured: dict = {}

    def fake_scan(root, positions, **kwargs):
        captured["root"] = root
        captured.update(kwargs)
        return _legacy_unknown([{"position": 1, "status": "unknown"}])

    monkeypatch.setattr(verifier, "scan_tree", fake_scan)
    monkeypatch.setattr(verifier.sys, "stdin", io.StringIO(json.dumps({"positions": [_row(1, b"audio")]})))
    exit_code = verifier.main([
        "--root", str(tmp_path), "--evidence-profile", "independent-copy", "--max-entries", "7",
    ])
    assert exit_code == 2
    assert captured == {
        "root": str(tmp_path),
        "evidence_profile": "independent-copy",
        "max_entries": 7,
    }
    assert json.loads(capsys.readouterr().out)["complete"] is False


def test_cli_rejects_invalid_entry_limit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(verifier.sys, "stdin", io.StringIO(json.dumps({"positions": [_row(1, b"audio")]})))
    exit_code = verifier.main(["--root", str(tmp_path), "--max-entries", "0"])
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out) == {
        "scope": "read_only_test_tree", "complete": False, "positions": [],
    }


def test_cli_rejects_unknown_evidence_profile(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        verifier.main(["--root", str(tmp_path), "--evidence-profile", "unknown"])
    assert excinfo.value.code == 2


# ---------------------------------------------------------------------------
# Original traversal failure paths (updated to the additive evidence schema)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_symlink_in_tree_returns_unknown_without_following_target(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    outside = tmp_path.parent / (tmp_path.name + "-outside.flac")
    outside.write_bytes(b"outside")
    try:
        (tmp_path / "link.flac").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"outside")])
    assert report == _expected_report(
        complete=False,
        unknown_reason="symlink_in_tree",
        positions=[{"position": 1, "status": "unknown"}],
        entries_seen=1,
    )
    assert outside.read_bytes() == b"outside"


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_child_mount_crossing_or_path_escape_is_rejected(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "child").mkdir()
    real_openat2 = verifier._openat2

    def reject_child_mount(root_fd, relative_path, flags):
        if relative_path == "child":
            raise verifier.UnsafeTreeError(errno.EXDEV, "cross-device")
        return real_openat2(root_fd, relative_path, flags)

    monkeypatch.setattr(verifier, "_openat2", reject_child_mount)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report == _expected_report(
        complete=False,
        unknown_reason="cross_device",
        positions=[{"position": 1, "status": "unknown"}],
        entries_seen=1,
    )


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_read_failure_returns_unknown_without_paths_or_exception_text(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"synthetic audio")

    def fail_read(*_args, **_kwargs):
        raise PermissionError("/private/music/track.flac token=secret")

    monkeypatch.setattr(verifier, "_hash_candidate", fail_read)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"synthetic audio")])
    assert report == _expected_report(
        complete=False,
        unknown_reason="scan_failed",
        positions=[{"position": 1, "status": "unknown"}],
        entries_seen=1,
        content_hash="attempted",
    )
    assert "private" not in str(report)
    assert "secret" not in str(report)


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_writable_btrfs_subvolume_returns_unknown(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "track.flac").write_bytes(b"candidate")
    writable_checks = []

    def writable_subvolume(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            writable_checks.append(True)
            return _readonly_snapshot(readonly=False)
        return _readonly_snapshot()

    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", writable_subvolume)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"candidate")])
    assert report == _expected_report(
        complete=False,
        unknown_reason="ro_semantics_unavailable",
        positions=[{"position": 1, "status": "unknown"}],
        entries_seen=1,
        content_hash="attempted",
    )
    assert writable_checks, "scan must inspect the file's subvolume state"


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_nested_readonly_subvolume_makes_whole_scan_unknown(tmp_path, monkeypatch):
    _allow_synthetic_snapshot_traversal(monkeypatch)
    (tmp_path / "unrelated.bin").write_bytes(b"different size")
    nested_checks = []

    def nested_subvolume(fd):
        if stat.S_ISREG(os.fstat(fd).st_mode):
            nested_checks.append(True)
            return _readonly_snapshot(treeid=257, uuid=b"n" * 16)
        return _readonly_snapshot()

    monkeypatch.setattr(verifier, "_read_btrfs_subvol_info", nested_subvolume)
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["unknown_reason"] == "view_mutated"
    assert report["positions"] == [{"position": 1, "status": "unknown"}]
    assert nested_checks, "scan must inspect file ancestry and reject nested subvolume identity"


@pytest.mark.skipif(not _HAS_SECURE_WALK, reason="Linux x86_64 openat2 required")
def test_temporary_readwrite_relock_with_new_generation_is_unknown(tmp_path, monkeypatch):
    # Synthetic only: inject a generation change while a privileged manager
    # clears readonly, rewrites the same inode, then restores readonly. This
    # proves the scanner rejects changed generation evidence; it does not prove
    # that a native Btrfs kernel always changes this field for that sequence.
    _allow_synthetic_snapshot_traversal(monkeypatch)
    original = b"original audio bytes"
    replacement = b"tampered audio bytes"
    assert len(original) == len(replacement)
    audio = tmp_path / "same-name.flac"
    audio.write_bytes(original)
    directory_mtime = tmp_path.stat().st_mtime_ns
    inode = audio.stat().st_ino
    real_read = os.read
    changed = False

    def rewrite_after_read(fd, amount):
        nonlocal changed
        chunk = real_read(fd, amount)
        if not changed and os.fstat(fd).st_ino == inode and chunk:
            with audio.open("r+b") as stream:
                stream.write(replacement)
                stream.flush()
            changed = True
        return chunk

    monkeypatch.setattr(verifier.os, "read", rewrite_after_read)
    monkeypatch.setattr(
        verifier, "_read_btrfs_subvol_info",
        lambda _fd: _readonly_snapshot(generation=101 if changed else _GENERATION),
    )
    report = verifier.scan_tree(str(tmp_path), [_row(1, original)])
    assert changed is True
    assert tmp_path.stat().st_mtime_ns == directory_mtime
    assert audio.read_bytes() == replacement
    assert report == _expected_report(
        complete=False,
        unknown_reason="view_mutated",
        positions=[{"position": 1, "status": "unknown"}],
        entries_seen=1,
        content_hash="attempted",
        generation_bracket="changed",
    )


def test_invalid_scan_input_never_echoes_untrusted_position(tmp_path, monkeypatch):
    report = verifier.scan_tree(str(tmp_path), [{"position": "https://example.test/private"}])
    assert report == {"scope": "read_only_test_tree", "complete": False, "positions": []}


def test_unsupported_platform_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(verifier.sys, "platform", "win32")
    report = verifier.scan_tree(str(tmp_path), [_row(1, b"audio")])
    assert report["complete"] is False
    assert report["positions"] == [{"position": 1, "status": "unknown"}]
