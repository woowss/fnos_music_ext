"""P1 验收证据采集边界（生产者骨架）的离线合同测试。

覆盖：
- 产出的 EvidenceSet 能通过既有 G-X3 语义校验器（生产者/校验器契约一致）；
- 未采集/采集失败**永不产出 pass**（诚实 unknown/blocked）；
- summary 计数与整体结论由 position 派生，不接受调用方声称；
- record_sha256 规范化重算稳定且对任意字段变更敏感；
- 证据中不含 URL/token/绝对路径（脱敏边界）；
- 非法构造（重复位置、计数不符、越界）显式失败。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "verify_acceptance_evidence_semantics",
    ROOT / "scripts" / "verify_acceptance_evidence_semantics.py",
)
VALIDATOR = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(VALIDATOR)

capture = importlib.import_module("proxy.acceptance_capture")

_TS = "2026-11-07T00:00:00Z"


def _environment() -> dict:
    return capture.build_environment(
        device_ref="device-ref-test",
        fnos_version="1.2.0903",
        kernel="6.18.18.c1168-trim",
        fstype="btrfs",
        mount_options_digest="0" * 64,
        library_ref="library-ref-test",
        staging_ref="staging-ref-test",
        deployed_version="2.12.0-8",
        provenance_status="observed",
        package_sha256="2" * 64,
        git_head="3" * 40,
    )


def _job(total: int) -> dict:
    return capture.build_job(
        job_id="job-test-0001",
        manifest_sha256="1" * 64,
        positions_total=total,
        source_fingerprint=None,
        source_fingerprint_origin="unknown",
    )


def _document(positions=None) -> dict:
    records = positions if positions is not None else [
        capture.build_position_uncaptured(position=index, captured_at=_TS, source_id=str(index))
        for index in range(1, 4)
    ]
    return capture.build_evidence_set(
        environment=_environment(),
        job=_job(len(records)),
        positions=records,
        created_at=_TS,
    )


class SchemaContractTests(unittest.TestCase):
    def test_constants_match_validator(self):
        self.assertEqual(capture.SCHEMA, VALIDATOR.SCHEMA_CONST)
        self.assertEqual(capture.SCHEMA_VERSION, VALIDATOR.SCHEMA_VERSION_CONST)

    def test_document_passes_semantic_validator_and_stays_not_pass(self):
        document = _document()
        result = VALIDATOR.verify_document(document)
        self.assertEqual(result["result"], "pass", result.get("violations"))
        self.assertEqual(result["fail_closed"], False)
        # 语义校验通过 ≠ 验收通过：文档自身整体结论必须不是 pass。
        self.assertNotEqual(document["summary"]["overall_verdict"], "pass")
        self.assertNotEqual(document["summary"]["pass"], 1)

    def test_uncaptured_position_is_explicitly_unknown(self):
        position = capture.build_position_uncaptured(position=1, captured_at=_TS, source_id="42")
        self.assertEqual(position["state"], "not_executed")
        self.assertIsNone(position["delivered_evidence"])
        self.assertEqual(position["byte_scan"]["profile"], "none")
        self.assertEqual(position["byte_scan"]["status"], "not_run")
        self.assertEqual(position["index_play"]["index"]["status"], "not_run")
        self.assertEqual(position["index_play"]["playback"]["status"], "not_run")
        self.assertIn("cannot_prove_audible_output", position["index_play"]["limitations"])
        self.assertEqual(position["verdict"]["verdict"], "unknown")
        for name in capture.DIMENSIONS:
            self.assertEqual(position["verdict"]["dimensions"][name], "unknown", name)
        self.assertEqual(
            position["source_evidence"]["quality"]["claim_scope"],
            capture.CLAIM_SCOPE,
        )
        self.assertEqual(position["source_evidence"]["quality"]["claim_refs"], [])

    def test_capture_failure_never_yields_pass(self):
        position = capture.build_position_uncaptured(
            position=1,
            captured_at=_TS,
            fixed_reasons=("evidence_missing_quality_claim",),
            note="capture_unavailable",
        )
        self.assertEqual(position["verdict"]["verdict"], "unknown")
        self.assertIn("evidence_missing_quality_claim", position["verdict"]["fixed_reasons"])
        document = _document([position])
        self.assertEqual(document["summary"]["overall_verdict"], "unknown")
        self.assertEqual(document["summary"]["pass"], 0)


class SummaryDerivationTests(unittest.TestCase):
    def test_summary_counts_are_derived_from_positions(self):
        document = _document()
        summary = document["summary"]
        self.assertEqual(summary["positions_total"], 3)
        self.assertEqual(summary["unknown"], 3)
        self.assertEqual(summary["pass"] + summary["fail"] + summary["unknown"] + summary["blocked"], 3)
        self.assertEqual(summary["overall_verdict"], "unknown")

    def test_blocked_reasons_are_sorted_unique(self):
        positions = [
            capture.build_position_uncaptured(
                position=index, captured_at=_TS, fixed_reasons=("not_executed",),
            )
            for index in (1, 2)
        ]
        document = _document(positions)
        self.assertEqual(document["summary"]["blocked_reasons"], ["not_executed"])

    def test_single_fail_dominates_overall(self):
        blocked = capture.build_position_uncaptured(position=1, captured_at=_TS)
        failed = capture.build_position_uncaptured(position=2, captured_at=_TS)
        failed["verdict"]["verdict"] = "fail"
        failed["record_sha256"] = capture.recompute_record_sha256(failed)
        document = _document([blocked, failed])
        self.assertEqual(document["summary"]["fail"], 1)
        self.assertEqual(document["summary"]["overall_verdict"], "fail")


class RecordHashTests(unittest.TestCase):
    def test_recompute_is_deterministic(self):
        position = capture.build_position_uncaptured(position=1, captured_at=_TS)
        again = capture.recompute_record_sha256(position)
        self.assertEqual(position["record_sha256"], again)
        self.assertEqual(len(again), 64)

    def test_any_field_change_changes_hash(self):
        position = capture.build_position_uncaptured(position=1, captured_at=_TS)
        before = position["record_sha256"]
        position["reason_message"] = "changed"
        self.assertNotEqual(before, capture.recompute_record_sha256(position))

    def test_builder_rejects_tampered_record_hash(self):
        position = capture.build_position_uncaptured(position=1, captured_at=_TS)
        position["reason"] = "tampered"
        with self.assertRaises(capture.CaptureError):
            capture.build_evidence_set(
                environment=_environment(), job=_job(1), positions=[position], created_at=_TS,
            )


class RedactionTests(unittest.TestCase):
    def test_builder_output_has_no_sensitive_material(self):
        document = _document()
        self.assertEqual(capture.find_sensitive_leaks(document), [])

    def test_detects_url_token_and_absolute_path(self):
        document = _document()
        document["positions"][0]["reason_message"] = "see https://example.com/x?token=abc"
        leaks = capture.find_sensitive_leaks(document)
        self.assertTrue(any("https://" in leak for leak in leaks), leaks)

        document2 = _document()
        document2["environment"]["library_ref"] = "/vol1/1000/music"
        leaks2 = capture.find_sensitive_leaks(document2)
        self.assertTrue(any("/vol1/" in leak for leak in leaks2), leaks2)

    def test_evidence_ref_hashes_actual_bytes(self):
        payload = b"fixture-bytes"
        ref = capture.evidence_ref(kind="file_bytes", payload=payload, captured_at=_TS)
        self.assertEqual(ref["sha256"], capture.sha256_hex(payload))
        self.assertEqual(ref["bytes"], len(payload))
        self.assertTrue(ref["ref_id"].startswith("ev_"))
        self.assertEqual(len(ref["ref_id"]), 3 + 32)
        other = capture.evidence_ref(kind="file_bytes", payload=b"other", captured_at=_TS)
        self.assertNotEqual(ref["ref_id"], other["ref_id"])


class RejectionTests(unittest.TestCase):
    def test_empty_positions_rejected(self):
        with self.assertRaises(capture.CaptureError):
            capture.build_evidence_set(
                environment=_environment(), job=_job(0), positions=[], created_at=_TS,
            )

    def test_duplicate_positions_rejected(self):
        first = capture.build_position_uncaptured(position=1, captured_at=_TS)
        second = capture.build_position_uncaptured(position=1, captured_at=_TS)
        with self.assertRaises(capture.CaptureError):
            capture.build_evidence_set(
                environment=_environment(), job=_job(2), positions=[first, second],
                created_at=_TS,
            )

    def test_positions_total_mismatch_rejected(self):
        with self.assertRaises(capture.CaptureError):
            capture.build_evidence_set(
                environment=_environment(),
                job=_job(5),
                positions=[capture.build_position_uncaptured(position=1, captured_at=_TS)],
                created_at=_TS,
            )

    def test_position_out_of_range_rejected(self):
        with self.assertRaises(capture.CaptureError):
            capture.build_position_uncaptured(position=0, captured_at=_TS)
        with self.assertRaises(capture.CaptureError):
            capture.build_position_uncaptured(position=1001, captured_at=_TS)

    def test_bad_manifest_digest_rejected(self):
        with self.assertRaises(capture.CaptureError):
            capture.build_job(job_id="j", manifest_sha256="nothex", positions_total=1)


if __name__ == "__main__":
    unittest.main()
