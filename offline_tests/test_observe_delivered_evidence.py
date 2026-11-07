"""`delivered_evidence` 独立只读观测器的离线合同测试。

覆盖：
- 摘要/大小/stat 对实际字节重算（与 capture.evidence_ref 算法一致）；
- 侧车歌词解码与行数、编码识别；
- 侧车封面发现；
- 目标目录 mode/owner 采样；
- 缺 ffprobe/ffmpeg 时显式失败（不产出「看似有效」的 unknown 证据）；
- 有 ffprobe/ffmpeg 时（真机）产出 schema 形状合法的 delivered_evidence，
  且结构校验器对该块不报违规（其余块由更上层 EvidenceSet 负责）。
"""

from __future__ import annotations

import importlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "observe_delivered_evidence", ROOT / "scripts" / "observe_delivered_evidence.py",
)
observer = importlib.util.module_from_spec(_MODULE_SPEC)
assert _MODULE_SPEC.loader is not None
_MODULE_SPEC.loader.exec_module(observer)

_VALIDATOR_SPEC = importlib.util.spec_from_file_location(
    "verify_acceptance_evidence_semantics",
    ROOT / "scripts" / "verify_acceptance_evidence_semantics.py",
)
VALIDATOR = importlib.util.module_from_spec(_VALIDATOR_SPEC)
assert _VALIDATOR_SPEC.loader is not None
_VALIDATOR_SPEC.loader.exec_module(VALIDATOR)

capture = importlib.import_module("proxy.acceptance_capture")

FFPROBE = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
FFMPEG = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")

_TS = "2026-11-07T00:00:00Z"


def _write_wav(path: Path, seconds: float = 0.2, rate: int = 8000) -> None:
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * frames)


class PureHelperTests(unittest.TestCase):
    def test_format_mode_and_encoding_detection(self):
        self.assertEqual(observer.format_mode(0o640), "0640")
        self.assertEqual(observer.detect_lyric_encoding(b"plain utf8"), "utf-8")
        self.assertEqual(observer.detect_lyric_encoding(b"\xef\xbb\xbfbom"), "utf-8-bom")
        self.assertEqual(observer.detect_lyric_encoding(b"\xff\xfe\x00"), "other")

    def test_lyric_line_count_and_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "track.wav"
            audio.write_bytes(b"RIFF")
            (Path(tmp) / "track.lrc").write_bytes("第一行\n第二行\n".encode("utf-8"))
            facts = observer.observe_lyric(audio)
            payload = (Path(tmp) / "track.lrc").read_bytes()
            self.assertEqual(facts["kind"], "sidecar")
            self.assertEqual(facts["encoding"], "utf-8")
            self.assertEqual(facts["bytes"], len(payload))
            self.assertEqual(facts["line_count"], 2)
            self.assertEqual(facts["text_sha256"], observer.sha256_hex(payload))

    def test_lyric_without_newline_counts_one_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "t.wav"
            audio.write_bytes(b"x")
            (Path(tmp) / "t.lrc").write_bytes("无换行".encode("utf-8"))
            facts = observer.observe_lyric(audio)
            self.assertEqual(facts["line_count"], 1)

    def test_missing_lyric_is_none_kind(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "t.wav"
            audio.write_bytes(b"x")
            self.assertEqual(observer.observe_lyric(audio)["kind"], "none")

    def test_sidecar_cover_detection_prefers_jpeg_then_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "cover-test.wav"
            audio.write_bytes(b"x")
            self.assertIsNone(observer.find_sidecar_cover(audio))
            (Path(tmp) / "cover-test.png").write_bytes(b"png")
            self.assertEqual(observer.find_sidecar_cover(audio)["mime"], "image/png")

    def test_mode_owner_sample_reads_target_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(3):
                (root / f"existing{index}.flac").write_bytes(b"x")
            sample = observer.sample_mode_owner(root)
            self.assertGreaterEqual(sample["sample_count"], 3)
            self.assertIsNotNone(sample["majority"])

    def test_hash_file_streams_and_matches_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "blob.bin"
            payload = b"delivered-evidence-fixture" * 1000
            target.write_bytes(payload)
            digest, md5, size = observer.hash_file(target)
            self.assertEqual(digest, observer.sha256_hex(payload))
            self.assertEqual(size, len(payload))
            self.assertEqual(len(md5), 32)

    def test_symlink_rejected_when_supported(self):
        if not hasattr(observer.os, "O_NOFOLLOW") or sys.platform == "win32":
            self.skipTest("O_NOFOLLOW not available")
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp) / "real.bin"
            real.write_bytes(b"x")
            link = Path(tmp) / "link.bin"
            try:
                link.symlink_to(real)
            except OSError:
                self.skipTest("cannot create symlink")
            with self.assertRaises(observer.ObserverError) as caught:
                observer.hash_file(link)
            self.assertIn(caught.exception.code, ("symlink_rejected", "file_open_failed"))

    def test_evidence_ref_matches_capture_module(self):
        payload = b"fixture-bytes"
        ours = observer.evidence_ref("file_bytes", payload, _TS)
        theirs = capture.evidence_ref(kind="file_bytes", payload=payload, captured_at=_TS)
        self.assertEqual(ours["ref_id"], theirs["ref_id"])
        self.assertEqual(ours["sha256"], theirs["sha256"])
        self.assertEqual(ours["bytes"], theirs["bytes"])


class ToolRequirementTests(unittest.TestCase):
    def test_missing_tools_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "a.wav"
            _write_wav(audio)
            with self.assertRaises(observer.ObserverError) as caught:
                observer.observe(
                    file_path=audio,
                    evidence_dir=Path(tmp) / "evidence",
                    ffprobe="definitely-not-a-real-ffprobe",
                    ffmpeg="definitely-not-a-real-ffmpeg",
                    captured_at=_TS,
                )
            self.assertIn(caught.exception.code, ("ffprobe_missing", "ffmpeg_missing"))

    def test_relative_or_missing_file_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(observer.ObserverError):
                observer.observe(
                    file_path=Path("relative.wav"), evidence_dir=Path(tmp) / "e",
                    ffprobe="x", ffmpeg="x", captured_at=_TS,
                )


@unittest.skipUnless(FFPROBE and FFMPEG, "requires ffprobe and ffmpeg")
class EndToEndObserverTests(unittest.TestCase):
    def test_observes_wav_and_produces_schema_shaped_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "曲目A.wav"
            _write_wav(audio)
            (root / "曲目A.lrc").write_text("line1\nline2\nline3\n", encoding="utf-8")
            (root / "曲目A.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
            evidence_dir = root / "evidence"
            result = observer.observe(
                file_path=audio,
                evidence_dir=evidence_dir,
                target_dir=root,
                captured_at=_TS,
            )
            delivered = result["delivered_evidence"]
            self.assertEqual(delivered["basename"], "曲目A.wav")
            self.assertEqual(delivered["size"], audio.stat().st_size)
            self.assertEqual(len(delivered["sha256"]), 64)
            self.assertRegex(delivered["stat"]["mode"], r"^0[0-7]{3}$")
            self.assertEqual(delivered["lyric"]["kind"], "sidecar")
            self.assertEqual(delivered["lyric"]["line_count"], 3)
            self.assertEqual(delivered["audio"]["codec_name"], "pcm_s16le")
            self.assertTrue(delivered["decode"]["ok"])
            self.assertFalse(delivered["cover"]["embedded"])
            self.assertEqual(delivered["cover"]["mime"], "image/png")
            self.assertEqual(delivered["cover"]["bytes_sha256"],
                             observer.sha256_hex(b"\x89PNG\r\n\x1a\nfake"))
            names = {item["name"] for item in result["evidence_files"]}
            self.assertIn("basename.txt", names)
            self.assertIn("ffprobe.json", names)
            self.assertIn("decode.log", names)
            self.assertIn("lyric.lrc", names)
            for item in result["evidence_files"]:
                stored = evidence_dir / item["name"]
                self.assertTrue(stored.is_file(), item["name"])
                self.assertEqual(item["bytes"], stored.stat().st_size)
                self.assertEqual(item["sha256"], observer.sha256_hex(stored.read_bytes()))

    def test_delivered_block_has_no_structural_violations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio = root / "track.wav"
            _write_wav(audio)
            result = observer.observe(
                file_path=audio, evidence_dir=root / "e", captured_at=_TS)
            position = capture.build_position_uncaptured(
                position=1, captured_at=_TS, source_id="1")
            position["state"] = "published"
            position["delivered_evidence"] = result["delivered_evidence"]
            position["record_sha256"] = capture.recompute_record_sha256(position)
            document = capture.build_evidence_set(
                environment=capture.build_environment(
                    device_ref="d", fnos_version="1.2.0903", kernel="k", fstype="ext4",
                    mount_options_digest="0" * 64, library_ref="l", staging_ref="s",
                    deployed_version="2.12.0-8", provenance_status="unknown",
                ),
                job=capture.build_job(job_id="j", manifest_sha256="1" * 64, positions_total=1),
                positions=[position],
                created_at=_TS,
            )
            issues = VALIDATOR._structure_issues(document)
            delivered_issues = [
                issue for issue in issues if "delivered_evidence" in issue.get("path", "")
            ]
            self.assertEqual(delivered_issues, [], delivered_issues)


if __name__ == "__main__":
    unittest.main()
