"""Tests for the G-X3 schema-external INV semantic validator.

The fixtures are synthetic EvidenceSet documents (no device, no network, no
filesystem writes beyond pytest's tmp_path). Each invariant group has at least
one positive and two negative cases; failures must be fail-closed (exit 2).
"""
from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import verify_acceptance_evidence_semantics as validator  # noqa: E402


_HEX64 = "a" * 64
_HEX40 = "b" * 40
_HEX48 = "c" * 48
_NOW = "2026-11-07T00:00:00Z"


# ---------------------------------------------------------------------------
# Fixture builders (synthetic only; schema-plausible but not claimed schema-valid)
# ---------------------------------------------------------------------------


def _ref(kind: str = "provider_response_extract") -> dict:
    return {
        "ref_id": "ev_" + "d" * 32,
        "kind": kind,
        "sha256": _HEX64,
        "bytes": 1,
        "captured_at": _NOW,
    }


def _quality() -> dict:
    return {
        "tier_requested": "jymaster",
        "reported_level": "",
        "reported_status": "unknown",
        "level_in_ladder": "unknown",
        "claim_scope": "provider_reported_not_independently_verified",
        "fallback": False,
        "claim_refs": [_ref()],
    }


def _source_evidence() -> dict:
    return {
        "quality": _quality(),
        "container_claim": {"api_type": "flac", "status": "reported", "ref": _ref()},
        "metadata": {
            "title": "title", "artist": "artist", "album": "album",
            "track": 1, "disc": 1, "lyricist": None, "composer": None,
            "status": "reported", "ref": _ref(),
        },
        "cover": {"status": "embedded", "error": "", "source": None},
        "lyric": {"status": "unavailable", "source": None},
        "integrity": {
            "api_size": 10, "api_md5": "e" * 32, "downloaded_size": 10,
            "downloaded_md5": "e" * 32, "status": "match", "ref": _ref(),
        },
    }


def _delivered() -> dict:
    return {
        "basename": "track.flac",
        "basename_ref": _ref("stat_snapshot"),
        "stat": {"mode": 420, "uid": 1000, "gid": 1001, "size": 10, "mtime_ns": 1},
        "sha256": _HEX64,
        "size": 10,
        "md5": "f" * 32,
        "audio": {
            "format_name": "flac", "codec_name": "flac", "sample_rate": 44100,
            "channels": 2, "sample_fmt": "s32", "bits_per_raw_sample": 24,
            "duration_seconds": 1.0, "bit_rate_bps": 1000, "stream_count": 1,
        },
        "decode": {"ok": True, "exit_code": 0, "ref": _ref("ffmpeg_log")},
        "tags": {
            "title": "title", "artist": "artist", "album": "album",
            "track": 1, "disc": 1, "lyricist": None, "composer": None,
            "raw_keys": [], "ref": _ref("ffprobe_json"),
        },
        "cover": {
            "embedded": True, "stream_index": 1, "codec": "mjpeg",
            "mime": "image/jpeg", "width": 1, "height": 1,
            "bytes_sha256": _HEX64, "decode_ok": True, "sidecar": None,
            "ref": _ref("cover_bytes"),
        },
        "lyric": {
            "kind": "none", "basename": None, "encoding": "unknown", "bytes": 0,
            "line_count": 0, "text_sha256": _HEX64, "ref": None,
        },
        "mode_owner": {"mode": 420, "uid": 1000, "gid": 1001},
    }


def _byte_scan() -> dict:
    return {
        "profile": "snapshot",
        "status": "unique_match",
        "match_count": 1,
        "view": {
            "kind": "snapshot",
            "identity_ref": _ref(),
            "provenance_ref": _ref(),
            "tree_uuid": "e" * 32,
            "parent_uuid": None,
            "generation_start": 1,
            "generation_end": 1,
            "ro_enforcement": "verified",
            "view_refs": [_ref()],
            "cleanup_ref": None,
        },
        "scanner": {
            "name": "verify_position_file_evidence",
            "version": "0.1",
            "sha256": _HEX64,
            "ingress": "local",
            "evidence_ref": _ref(),
        },
        "limitations": ["scanner_generation_gate_unvalidated"],
    }


def _index_play() -> dict:
    return {
        "index": {
            "status": "visible",
            "method": "machine_api",
            "machine_readable": True,
            "entry_ref": _ref(),
            "observed_fields": {
                "title": "present", "artist": "present", "album": "present",
                "cover": "present", "lyric": "absent",
            },
            "refs": [_ref()],
        },
        "playback": {
            "status": "advanced",
            "method": "machine_api",
            "machine_readable": True,
            "parsed_duration_seconds": 1.0,
            "ui_duration_seconds": 1.0,
            "duration_match": "match",
            "position_advanced_seconds": 0.5,
            "error_event": None,
            "refs": [_ref()],
        },
        "limitations": ["cannot_prove_audible_output"],
    }


def _verdict(**overrides) -> dict:
    verdict = {
        "verdict": "pass",
        "dimensions": {
            "bytes": "pass", "quality": "pass", "tags": "pass",
            "cover": "pass", "lyric": "pass",
            "duplicate": "not_applicable", "index_play": "pass",
        },
        "duplicate_resolution": "not_applicable",
        "fixed_reasons": [],
        "notes": [],
    }
    verdict.update(overrides)
    return verdict


def _position(**overrides) -> dict:
    position = {
        "position": 1,
        "source_id": "1",
        "duplicate_of_position": None,
        "state": "published",
        "expected_outcome": "published",
        "reason": None,
        "reason_message": None,
        "source_evidence": _source_evidence(),
        "delivered_evidence": _delivered(),
        "byte_scan": _byte_scan(),
        "index_play": _index_play(),
        "verdict": _verdict(),
        "failure": None,
        "timestamps": {"captured_at": _NOW, "verified_at": None},
        "record_sha256": _HEX64,
    }
    position.update(overrides)
    return position


def _summary(positions: list[dict], overall: str | None = None) -> dict:
    counts = {"pass": 0, "fail": 0, "unknown": 0, "blocked": 0}
    for position in positions:
        counts[position["verdict"]["verdict"]] += 1
    if overall is None:
        if counts["fail"]:
            overall = "fail"
        elif counts["blocked"]:
            overall = "blocked"
        elif counts["unknown"]:
            overall = "unknown"
        else:
            overall = "pass"
    return {
        "positions_total": len(positions),
        **counts,
        "overall_verdict": overall,
        "blocked_reasons": [],
    }


def _job(total: int, **overrides) -> dict:
    job = {
        "job_id": _HEX48,
        "manifest_sha256": _HEX64,
        "source_fingerprint": None,
        "source_fingerprint_origin": "unknown",
        "positions_total": total,
    }
    job.update(overrides)
    return job


def _environment(**deployed_overrides) -> dict:
    deployed = {
        "version": "2.10.2",
        "package_sha256": None,
        "git_head": None,
        "provenance_status": "partial",
    }
    deployed.update(deployed_overrides)
    return {
        "device_ref": "kotomi",
        "fnos_version": "1.2.0903",
        "kernel": "6.18.18.c1168-trim",
        "filesystem": {
            "fstype": "btrfs",
            "mount_options_digest": _HEX64,
            "mount_options_class": "trimacl",
        },
        "library_ref": "library",
        "staging_ref": "staging",
        "deployed": deployed,
        "tool_hashes": {"scanner": _HEX64},
    }


def _document(positions: list[dict] | None = None, *, summary: dict | None = None,
              job: dict | None = None, environment: dict | None = None) -> dict:
    positions = [_position()] if positions is None else positions
    return {
        "schema": "fnmusic.p1.acceptance-evidence",
        "schema_version": "0.1-draft-20261107",
        "evidence_set_id": _HEX48,
        "created_at": _NOW,
        "created_by": {"kind": "tool"},
        "environment": _environment() if environment is None else environment,
        "job": _job(len(positions)) if job is None else job,
        "scope": {
            "contract": "contract",
            "runbook_sha256": _HEX64,
            "acceptance_items": ["A4", "A5", "A6", "A7"],
            "excluded": [],
        },
        "positions": positions,
        "summary": _summary(positions) if summary is None else summary,
        "review": {"required": False, "verdict": "not_reviewed"},
        "redaction": {
            "policy_version": "v1",
            "no_urls": True,
            "no_tokens": True,
            "no_absolute_paths": True,
            "raw_provider_response_retained": False,
            "canonicalization_version": "utf8-sorted-json-v1",
        },
    }


def _check(result: dict, check_id: str) -> dict:
    for item in result["checks"]:
        if item["id"] == check_id:
            return item
    raise AssertionError("missing check %r in %r" % (check_id, [i["id"] for i in result["checks"]]))


def _assert_check(result: dict, check_id: str, status: str, reason: str | None = None) -> None:
    item = _check(result, check_id)
    assert item["status"] == status, item
    assert item["reason"] == reason, item


def _run(document: dict) -> dict:
    result = validator.verify_document(document)
    _assert_contract(result)
    return result


def _assert_contract(result: dict) -> None:
    assert result["tool"] == validator.TOOL
    assert result["fail_closed"] is (result["result"] != "pass")
    assert result["exit_code"] == (0 if result["result"] == "pass" else 2)
    counts = {"pass": 0, "fail": 0, "unknown": 0}
    for item in result["checks"]:
        assert item["status"] in {"pass", "fail", "unknown"}, item
        counts[item["status"]] += 1
        if item["status"] == "pass":
            assert item["reason"] is None, item
        else:
            assert item["reason"] in validator._REASON_CODES, item
    assert result["counts"] == {"checks": len(result["checks"]), **counts}
    assert len(result["violations"]) == counts["fail"]
    assert len(result["unknowns"]) == counts["unknown"]
    assert isinstance(result["not_checked"], list) and result["not_checked"]
    for item in result["not_checked"]:
        assert isinstance(item["item"], str) and isinstance(item["reason"], str)


# ---------------------------------------------------------------------------
# Positive baselines (one per invariant group where applicable)
# ---------------------------------------------------------------------------


def test_valid_baseline_passes_all_checks():
    result = _run(_document())
    assert result["result"] == "pass"
    assert result["exit_code"] == 0
    assert result["violations"] == []
    assert result["unknowns"] == []
    assert result["counts"]["checks"] >= 20
    assert all(item["status"] == "pass" for item in result["checks"])
    assert result["input"]["schema"] == "fnmusic.p1.acceptance-evidence"
    assert result["input"]["schema_version"] == "0.1-draft-20261107"
    assert result["input"]["kind"] == "memory"
    assert "sha256" not in result["input"]  # no raw bytes for in-memory documents


def test_expected_failure_confirmed_is_pass():
    position = _position()
    position.update({
        "state": "rejected",
        "expected_outcome": "failure",
        "delivered_evidence": None,
        "failure": {
            "stage": "quality",
            "code": "no_quality_available",
            "fatal": True,
            "reason_message": "no quality",
            "refs": [_ref()],
        },
        "verdict": _verdict(
            dimensions={
                "bytes": "not_applicable", "quality": "not_applicable",
                "tags": "not_applicable", "cover": "not_applicable",
                "lyric": "not_applicable", "duplicate": "not_applicable",
                "index_play": "not_applicable",
            },
        ),
    })
    result = _run(_document([position]))
    assert result["result"] == "pass"
    assert _check(result, "INV-2.expected_failure_pass_confirmation")["status"] == "pass"
    assert _check(result, "INV-17.failure_states_require_failure")["status"] == "pass"


def test_expected_conflict_confirmed_is_pass():
    position = _position()
    position.update({
        "state": "conflict",
        "expected_outcome": "conflict",
        "delivered_evidence": None,
        "verdict": _verdict(
            dimensions={
                "bytes": "unknown", "quality": "unknown", "tags": "unknown",
                "cover": "not_applicable", "lyric": "not_applicable",
                "duplicate": "pass", "index_play": "not_applicable",
            },
            duplicate_resolution="conflict_expected",
        ),
    })
    result = _run(_document([position]))
    assert result["result"] == "pass"
    assert _check(result, "INV-17.conflict_resolution")["status"] == "pass"


def test_snapshot_fingerprint_with_64hex_is_consistent():
    document = _document(job=_job(1, source_fingerprint=_HEX64,
                                  source_fingerprint_origin="snapshot_response"))
    result = _run(document)
    assert result["result"] == "pass"
    assert _check(result, "INV-22.fingerprint_origin")["status"] == "pass"


def test_fallback_attempts_cover_higher_tiers_is_pass():
    position = _position()
    position["source_evidence"]["quality"].update({
        "tier_requested": "exhigh",
        "reported_level": "exhigh",
        "reported_status": "reported",
        "fallback": True,
        "attempts": [
            {"tier": "jymaster", "outcome": "unavailable", "ref": _ref()},
            {"tier": "hires", "outcome": "unavailable", "ref": _ref()},
            {"tier": "lossless", "outcome": "unavailable", "ref": _ref()},
        ],
    })
    result = _run(_document([position]))
    assert result["result"] == "pass"
    assert _check(result, "INV-18.fallback_tier_coverage")["status"] == "pass"


# ---------------------------------------------------------------------------
# INV-15: position uniqueness / order / totals (one positive + >=2 negatives)
# ---------------------------------------------------------------------------


def test_inv15_multiple_ascending_positions_pass():
    first = _position(position=1)
    second = _position(position=2)
    result = _run(_document([first, second]))
    assert result["result"] == "pass"
    assert _check(result, "INV-15.positions_unique")["status"] == "pass"
    proxy = _check(result, "INV-15.manifest_order_ascending_proxy")
    assert proxy["status"] == "pass"
    # G-5: the check is explicitly marked as a proxy, never as verified order.
    assert proxy["details"]["verification"] == "ascending_proxy_only"
    assert proxy["details"]["manifest_order_available"] is False


def test_inv15_duplicate_position_fails():
    first = _position(position=1)
    second = _position(position=1)
    result = _run(_document([first, second]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-15.positions_unique", "fail", "positions_duplicate")


def test_inv15_out_of_range_position_fails():
    position = _position(position=0)
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-15.positions_unique", "fail", "position_out_of_range")


def test_inv15_positions_total_mismatch_fails():
    result = _run(_document([_position()], job=_job(2)))
    assert result["result"] == "fail"
    _assert_check(result, "INV-15.positions_total", "fail", "positions_total_mismatch")


def test_inv15_reversed_manifest_order_is_unknown_fail_closed():
    # G-6: reversed positions (2,1) -> ascending proxy cannot pass; the manifest
    # itself is not in the EvidenceSet, so the proxy is unknown and exit is 2.
    first = _position(position=2)
    second = _position(position=1)
    result = _run(_document([first, second]))
    assert result["result"] == "unknown"
    assert result["exit_code"] == 2
    assert _check(result, "INV-15.manifest_order_ascending_proxy")["status"] == "unknown"
    _assert_check(result, "INV-15.manifest_order_ascending_proxy", "unknown",
                  "manifest_order_unavailable")


# ---------------------------------------------------------------------------
# INV-16: summary counts / overall derivation (one positive + >=2 negatives)
# ---------------------------------------------------------------------------


def test_inv16_counters_follow_position_verdicts():
    fail = _position(position=2)
    fail["verdict"] = _verdict(verdict="fail", fixed_reasons=["integrity_mismatch"],
                               dimensions={**fail["verdict"]["dimensions"], "bytes": "fail"})
    result = _run(_document([_position(), fail]))
    assert result["result"] == "pass"  # a consistently failed job is still consistent evidence
    assert _check(result, "INV-16.summary_counts")["status"] == "pass"
    assert _check(result, "INV-16.overall_verdict")["status"] == "pass"
    assert result["input"]["schema"] == "fnmusic.p1.acceptance-evidence"


def test_inv16_summary_count_mismatch_fails():
    position = _position()
    summary = _summary([position])
    summary["pass"] = 2
    result = _run(_document([position], summary=summary))
    assert result["result"] == "fail"
    _assert_check(result, "INV-16.summary_counts", "fail", "summary_count_mismatch")


def test_inv16_overall_verdict_mismatch_fails():
    position = _position()
    summary = _summary([position])
    summary["overall_verdict"] = "fail"
    result = _run(_document([position], summary=summary))
    assert result["result"] == "fail"
    _assert_check(result, "INV-16.overall_verdict", "fail", "overall_verdict_mismatch")


def test_inv16_declared_total_mismatch_fails():
    position = _position()
    summary = _summary([position])
    summary["positions_total"] = 2
    result = _run(_document([position], summary=summary))
    assert result["result"] == "fail"
    _assert_check(result, "INV-16.summary_counts", "fail", "summary_total_mismatch")


# ---------------------------------------------------------------------------
# INV-17: state <-> failure/delivered matrix (one positive + >=2 negatives)
# ---------------------------------------------------------------------------


def test_inv17_published_requires_null_failure():
    position = _position()
    result = _run(_document([position]))
    assert _check(result, "INV-17.published_failure_null")["status"] == "pass"
    assert _check(result, "INV-17.published_requires_delivered")["status"] == "pass"


def test_inv17_published_with_failure_fails():
    position = _position()
    position["failure"] = {
        "stage": "publish", "code": "publish_failed", "fatal": True,
        "reason_message": "publish failed", "refs": [_ref()],
    }
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-17.published_failure_null", "fail", "published_failure_present")


def test_inv17_failure_state_without_failure_fails():
    position = _position()
    position.update({
        "state": "rejected",
        "expected_outcome": "failure",
        "delivered_evidence": None,
        "failure": None,
        "verdict": _verdict(verdict="unknown"),
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-17.failure_states_require_failure", "fail", "failure_missing")


def test_inv17_failure_code_not_in_fixed_set_fails():
    position = _position()
    position.update({
        "state": "failed",
        "expected_outcome": "failure",
        "delivered_evidence": None,
        "failure": {
            "stage": "quality", "code": "free-form code with /private/token=secret",
            "fatal": True, "reason_message": "x", "refs": [],
        },
        "verdict": _verdict(verdict="unknown"),
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-17.failure_code_fixed", "fail", "failure_code_not_fixed")
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_inv17_conflict_with_delivered_evidence_fails():
    position = _position()
    position.update({
        "state": "conflict",
        "expected_outcome": "conflict",
        "delivered_evidence": _delivered(),
        "verdict": _verdict(verdict="unknown",
                            duplicate_resolution="conflict_expected"),
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-17.conflict_delivered_null", "fail", "conflict_delivered_present")


def test_inv17_cancelled_with_delivered_evidence_fails():
    position = _position()
    position.update({
        "state": "cancelled",
        "expected_outcome": "cancelled",
        "delivered_evidence": _delivered(),
        "verdict": _verdict(verdict="unknown"),
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-17.non_published_delivered_null", "fail",
                  "non_published_delivered_present")


# ---------------------------------------------------------------------------
# INV-2 / INV-3 / INV-23: expected_outcome <-> verdict (positive + negatives)
# ---------------------------------------------------------------------------


def test_inv2_published_pass_requires_gate_dimensions():
    position = _position()
    position["verdict"]["dimensions"]["quality"] = "unknown"
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-2.expected_published_pass_gates", "fail",
                  "expected_published_pass_gates_unmet")


def test_inv2_commit_unknown_must_not_be_pass():
    position = _position()
    position.update({
        "state": "commit_unknown",
        "expected_outcome": "failure",
        "delivered_evidence": None,
        "failure": {
            "stage": "publish", "code": "commit_unknown", "fatal": True,
            "reason_message": "commit unknown", "refs": [],
        },
        "verdict": _verdict(verdict="pass"),
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-2.commit_unknown_verdict", "fail", "commit_unknown_verdict_invalid")


def test_inv23_unexpected_published_silent_pass_fails():
    position = _position()  # state=published, delivered present
    position["expected_outcome"] = "failure"
    position["failure"] = None
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-23.expected_actual_mismatch", "fail",
                  "expected_actual_mismatch_silent_pass")
    _assert_check(result, "INV-2.expected_failure_pass_confirmation", "fail",
                  "expected_failure_confirmation_invalid")


def test_inv23_mismatch_with_unknown_verdict_requires_fixed_reasons():
    position = _position()
    position.update({
        "state": "rejected",
        "expected_outcome": "published",
        "delivered_evidence": None,
        "verdict": _verdict(verdict="unknown"),
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-23.expected_actual_mismatch", "fail",
                  "expected_actual_mismatch_fixed_reasons_missing")


def test_inv3_fail_verdict_requires_fixed_reasons():
    position = _position()
    position["verdict"] = _verdict(
        verdict="fail", fixed_reasons=[],
        dimensions={**position["verdict"]["dimensions"], "bytes": "fail"},
    )
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-3.fail_verdict_without_fixed_reasons", "fail",
                  "fail_verdict_without_fixed_reasons")


def test_inv3_fail_verdict_must_be_justified():
    position = _position()
    position["verdict"] = _verdict(verdict="fail", fixed_reasons=["internal"])
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-3.fail_verdict_unjustified", "fail", "fail_verdict_unjustified")


def test_inv19_fixed_reasons_must_be_fixed_codes():
    position = _position()
    position["verdict"] = _verdict(
        verdict="fail",
        fixed_reasons=["free form /private/token=secret"],
        dimensions={**position["verdict"]["dimensions"], "bytes": "fail"},
    )
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-19.fixed_reasons_codes", "fail", "fixed_reason_code_not_fixed")
    assert "private" not in json.dumps(result)
    assert "secret" not in json.dumps(result)


def test_inv3_fail_verdict_with_dimension_fail_is_acceptable():
    position = _position()
    position["verdict"] = _verdict(
        verdict="fail", fixed_reasons=["integrity_mismatch"],
        dimensions={**position["verdict"]["dimensions"], "bytes": "fail"},
    )
    result = _run(_document([position]))
    assert result["result"] == "pass"
    assert _check(result, "INV-3.fail_verdict_unjustified")["status"] == "pass"
    assert _check(result, "INV-19.fixed_reasons_codes")["status"] == "pass"


# ---------------------------------------------------------------------------
# INV-22: binding availability (positive + >=2 negatives)
# ---------------------------------------------------------------------------


def test_inv22_observed_provenance_requires_hashes():
    environment = _environment(provenance_status="observed",
                               package_sha256=None, git_head=None)
    result = _run(_document(environment=environment))
    assert result["result"] == "fail"
    _assert_check(result, "INV-22.deployed_provenance", "fail",
                  "deployed_observed_hashes_missing")


def test_inv22_snapshot_response_requires_64hex_fingerprint():
    document = _document(job=_job(1, source_fingerprint=None,
                                  source_fingerprint_origin="snapshot_response"))
    result = _run(document)
    assert result["result"] == "fail"
    _assert_check(result, "INV-22.fingerprint_origin", "fail",
                  "fingerprint_snapshot_not_64hex")


def test_inv22_unknown_origin_requires_null_fingerprint():
    document = _document(job=_job(1, source_fingerprint=_HEX64,
                                  source_fingerprint_origin="unknown"))
    result = _run(document)
    assert result["result"] == "fail"
    _assert_check(result, "INV-22.fingerprint_origin", "fail",
                  "fingerprint_origin_unknown_not_null")


# ---------------------------------------------------------------------------
# INV-18: fallback -> attempts (positive + >=2 negatives)
# ---------------------------------------------------------------------------


def test_inv18_fallback_requires_attempts():
    position = _position()
    position["source_evidence"]["quality"].update({"fallback": True})
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-18.fallback_attempts_present", "fail",
                  "fallback_attempts_missing")


def test_inv18_fallback_requires_higher_tier_coverage():
    position = _position()
    position["source_evidence"]["quality"].update({
        "tier_requested": "exhigh",
        "fallback": True,
        "attempts": [{"tier": "lossless", "outcome": "unavailable", "ref": _ref()}],
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-18.fallback_tier_coverage", "fail",
                  "fallback_attempts_tier_coverage")


def test_inv18_reported_level_diff_requires_fallback_true():
    position = _position()
    position["source_evidence"]["quality"].update({
        "tier_requested": "jymaster",
        "reported_level": "lossless",
        "reported_status": "reported",
        "fallback": False,
    })
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-18.reported_diff_requires_fallback", "fail",
                  "reported_diff_requires_fallback")


# ---------------------------------------------------------------------------
# Rev-2 review fixes (G-2/G-3/G-4) and extra boundary tests (G-6)
# ---------------------------------------------------------------------------


def test_g2_malicious_schema_version_is_not_echoed_and_fails_closed():
    document = _document()
    document["schema_version"] = "https://secret.example/token=SECRET"
    result = _run(document)
    assert result["result"] == "fail"
    assert result["exit_code"] == 2
    assert result["input"]["schema_version"] == "<invalid>"
    rendered = json.dumps(result, ensure_ascii=False)
    assert "secret" not in rendered.casefold()
    assert "example.test" not in rendered
    _assert_check(result, "input.schema_version", "fail", "schema_version_not_fixed")
    _assert_check(result, "structure.preconditions", "fail", "evidence_schema_invalid")


def test_g2_malicious_schema_version_cli_stdout_has_no_secret(tmp_path, capsys):
    document = _document()
    document["schema_version"] = "https://secret.example/token=SECRET"
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    exit_code = validator.main([str(path)])
    captured = capsys.readouterr().out
    output = json.loads(captured)
    assert exit_code == 2
    assert output["result"] == "fail"
    assert output["input"]["schema_version"] == "<invalid>"
    assert "secret" not in captured.casefold()
    assert "example.test" not in captured


def test_g2_non_fixed_schema_identifier_is_not_echoed():
    document = _document()
    document["schema"] = "https://secret.example/token=SECRET"
    result = _run(document)
    assert result["result"] == "fail"
    assert result["input"]["schema"] == "<unexpected>"
    assert "secret" not in json.dumps(result).casefold()


def test_g3_empty_requested_tier_with_reported_level_is_unknown():
    position = _position()
    position["source_evidence"]["quality"].update({
        "tier_requested": "",
        "reported_level": "lossless",
        "reported_status": "reported",
        "fallback": False,
    })
    result = _run(_document([position]))
    assert result["result"] == "unknown"
    assert result["exit_code"] == 2
    _assert_check(result, "INV-18.reported_diff_requires_fallback", "unknown",
                  "requested_tier_unknown_for_reported")


def test_g4_collection_version_manifest_null_is_allowed():
    document = _document(job=_job(1, source_fingerprint=None,
                                  source_fingerprint_origin="collection_version_manifest"))
    result = _run(document)
    assert result["result"] == "pass"
    assert _check(result, "INV-22.fingerprint_origin")["status"] == "pass"


def test_g4_collection_version_manifest_non_null_must_be_64hex():
    document = _document(job=_job(1, source_fingerprint="not-a-64hex-hash",
                                  source_fingerprint_origin="collection_version_manifest"))
    result = _run(document)
    assert result["result"] == "fail"
    _assert_check(result, "INV-22.fingerprint_origin", "fail",
                  "fingerprint_declared_not_64hex")


def test_inv19_rejects_non_fixed_code_on_pass_verdict():
    # G-6: second explicit INV-19 negative; the check applies regardless of the
    # position verdict, and the raw reason string must not be echoed.
    position = _position()
    position["verdict"]["fixed_reasons"] = ["free form /private/token=secret"]
    result = _run(_document([position]))
    assert result["result"] == "fail"
    _assert_check(result, "INV-19.fixed_reasons_codes", "fail",
                  "fixed_reason_code_not_fixed")
    rendered = json.dumps(result)
    assert "secret" not in rendered.casefold()
    assert "private" not in rendered.casefold()


# ---------------------------------------------------------------------------
# Fail-closed input handling and CLI contract
# ---------------------------------------------------------------------------


def test_missing_required_shape_fails_closed():
    document = _document()
    del document["summary"]
    result = _run(document)
    assert result["result"] == "fail"
    _assert_check(result, "structure.preconditions", "fail", "evidence_schema_invalid")


def test_non_object_json_fails_closed():
    result = _run([])
    assert result["result"] == "fail"
    _assert_check(result, "structure.preconditions", "fail", "evidence_schema_invalid")


def test_cli_file_input_exit_zero_and_json(tmp_path, capsys):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(_document()), encoding="utf-8")
    exit_code = validator.main([str(path)])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["result"] == "pass"
    assert output["input"]["kind"] == "file"
    assert len(output["input"]["sha256"]) == 64


def test_cli_stdin_input_exit_zero(monkeypatch, capsys):
    monkeypatch.setattr(validator.sys, "stdin", io.StringIO(json.dumps(_document())))
    exit_code = validator.main([])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert output["result"] == "pass"
    assert output["input"]["kind"] == "stdin"


def test_cli_invalid_json_exits_two(tmp_path, capsys):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    exit_code = validator.main([str(path)])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["result"] == "fail"
    assert output["fail_closed"] is True
    _assert_check(output, "input.readable", "fail", "evidence_input_invalid_json")


def test_cli_missing_file_exits_two(tmp_path, capsys):
    exit_code = validator.main([str(tmp_path / "absent.json")])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    _assert_check(output, "input.readable", "fail", "input_unreadable")


def test_cli_semantic_failure_exits_two(tmp_path, capsys):
    document = _document()
    document["positions"][0]["state"] = "published"
    document["positions"][0]["failure"] = {
        "stage": "publish", "code": "publish_failed", "fatal": True,
        "reason_message": "x", "refs": [],
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    exit_code = validator.main([str(path)])
    output = json.loads(capsys.readouterr().out)
    assert exit_code == 2
    assert output["result"] == "fail"
    assert output["violations"]


# ---------------------------------------------------------------------------
# Output safety / fixed reason codes
# ---------------------------------------------------------------------------


def test_output_does_not_echo_untrusted_strings():
    position = _position()
    position["delivered_evidence"]["basename"] = "ｃｏｏｋｉｅ：secret=hidden.flac"
    position["source_evidence"]["quality"]["reported_level"] = "token=secret"
    position["failure"] = {
        "stage": "publish", "code": "url=https://example.test/token=secret",
        "fatal": True, "reason_message": "secret", "refs": [],
    }
    result = _run(_document([position]))
    rendered = json.dumps(result, ensure_ascii=False)
    assert "secret" not in rendered.casefold()
    assert "example.test" not in rendered
    assert "cookie" not in rendered.casefold()


def test_every_emitted_reason_code_is_fixed():
    documents = [
        _document(),
        _document([_position(position=1), _position(position=1)]),
        _document([_position()], job=_job(2)),
    ]
    for document in documents:
        result = validator.verify_document(document)
        _assert_contract(result)


def test_summary_overall_unknown_is_semantically_consistent():
    # A declared overall=unknown that matches the per-position verdicts is a
    # consistent evidence set; it is not a semantic contradiction (the evidence
    # simply has unknown data). Exit 0 means "semantically consistent", not
    # "accepted".
    position = _position()
    position["verdict"] = _verdict(verdict="unknown")
    result = _run(_document([position]))
    assert result["result"] == "pass"
    assert result["counts"]["unknown"] == 0  # no schema-external 'unknown' checks
    assert _check(result, "INV-16.overall_verdict")["status"] == "pass"
