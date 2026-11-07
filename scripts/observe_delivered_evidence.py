#!/usr/bin/env python3
"""P1 验收证据：落盘后 `delivered_evidence` 独立只读观测器。

**边界**（设计见 docs/ACCEPTANCE_CAPTURE_DESIGN_20261107.md）：

- 只读：对目标文件只做 ``open(O_RDONLY|O_NOFOLLOW)``、``stat``、``sha256/md5``、
  ``ffprobe``/``ffmpeg`` 只读调用；**打印/写出任何内容都不改变保存语义**。
- 独立：必须在保存进程之外运行（与保存进程无共享 fd/内存），只依据磁盘事实。
- 诚实：缺少 ffprobe/ffmpeg 时**显式失败**（exit 3），不产出「看起来有效」的
  unknown 证据；证据写入受控目录，所有摘要对实际原始字节重算。
- 脱敏：只输出白名单字段；basename 只进受控证据文件与受控 JSON，不进公开报告。

用法::

    python3 scripts/observe_delivered_evidence.py \\
        --file /path/to/track.flac --evidence-dir /tmp/evidence \\
        [--target-dir /path/to/library] [--ffprobe /usr/bin/ffprobe] \\
        [--ffmpeg /usr/bin/ffmpeg] [--json-out /tmp/delivered.json]

退出码：0 成功；2 输入非法；3 依赖工具缺失。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

SCHEMA = "fnmusic.p1.acceptance-evidence"
SCHEMA_VERSION = "0.1-draft-20261107"

TAG_KEYS = ("title", "artist", "album", "track", "disc", "lyricist", "composer")
LYRIC_ENCODINGS = ("utf-8", "utf-8-bom", "other", "unknown")
SIDECAR_COVER_EXTS = (".jpg", ".jpeg", ".png", ".webp")
MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
TARGET_SAMPLE_LIMIT = 5


class ObserverError(RuntimeError):
    """可预期失败：以固定码报告，不打印路径/异常文本。"""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def evidence_ref(kind: str, payload: bytes, captured_at: str, capture_method: str = "") -> dict:
    """与 proxy/acceptance_capture.evidence_ref 相同算法（避免跨目录 import）。"""
    raw = bytes(payload)
    ref_id = "ev_" + hashlib.sha256(kind.encode("ascii") + b"\x00" + raw).hexdigest()[:32]
    return {
        "ref_id": ref_id,
        "kind": kind,
        "sha256": sha256_hex(raw),
        "bytes": len(raw),
        "captured_at": captured_at,
        "capture_method": capture_method[:128],
        "custody": "captured",
    }


def _read_regular_no_symlink(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ObserverError("file_open_failed" if exc.errno != 40 else "symlink_rejected") from None
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise ObserverError("not_regular_file")
    return fd, info


def hash_file(path: Path, *, with_md5: bool = True) -> tuple[str, str | None, int]:
    """单次流式读取同时算 sha256（与可选 md5）与字节数。"""
    fd, _info = _read_regular_no_symlink(path)
    sha = hashlib.sha256()
    md5 = hashlib.md5() if with_md5 else None
    total = 0
    try:
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            sha.update(block)
            if md5 is not None:
                md5.update(block)
            total += len(block)
    finally:
        os.close(fd)
    return sha.hexdigest(), (md5.hexdigest() if md5 is not None else None), total


def format_mode(mode: int) -> str:
    return "0" + format(stat.S_IMODE(mode), "o")


def detect_lyric_encoding(payload: bytes) -> str:
    if payload.startswith(b"\xef\xbb\xbf"):
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            return "other"
        return "utf-8-bom"
    try:
        payload.decode("utf-8")
    except UnicodeDecodeError:
        return "other"
    return "utf-8"


def observe_lyric(audio_path: Path) -> dict:
    """侧车歌词观测（只读）。"""
    lrc = audio_path.with_suffix(".lrc")
    if not lrc.is_file():
        return {"kind": "none"}
    try:
        payload = lrc.read_bytes()
    except OSError:
        return {"kind": "none"}
    encoding = detect_lyric_encoding(payload)
    text = payload.decode("utf-8", errors="replace")
    return {
        "kind": "sidecar",
        "basename": lrc.name,
        "encoding": encoding,
        "bytes": len(payload),
        "line_count": text.count("\n") + (0 if text.endswith("\n") or not text else 1),
        "text_sha256": sha256_hex(payload),
        "_payload": payload,
    }


def find_sidecar_cover(audio_path: Path) -> dict | None:
    for ext in SIDECAR_COVER_EXTS:
        candidate = audio_path.with_suffix(ext)
        if candidate.is_file():
            return {"path": candidate, "mime": MIME_BY_EXT[ext]}
    return None


def sample_mode_owner(target_dir: Path) -> dict:
    """对目标目录既有文件采样 mode/uid/gid（只读，最多 N 个）。"""
    counts: dict[tuple[int, int, int], int] = {}
    sampled = 0
    try:
        entries = sorted(target_dir.iterdir())
    except OSError:
        entries = []
    for entry in entries:
        if sampled >= TARGET_SAMPLE_LIMIT:
            break
        try:
            info = entry.lstat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode):
            continue
        key = (stat.S_IMODE(info.st_mode), info.st_uid, info.st_gid)
        counts[key] = counts.get(key, 0) + 1
        sampled += 1
    if not counts:
        return {"sample_count": 0, "majority": None}
    majority = max(counts.items(), key=lambda item: (item[1], item[0]))[0]
    return {
        "sample_count": sampled,
        "majority": {"mode": format_mode(majority[0]), "uid": majority[1], "gid": majority[2]},
    }


def _resolve_tool(name_or_path: str | None, default_name: str) -> str | None:
    """把「未提供」与「提供了但不存在的路径」都归一为 None（缺工具）。"""
    candidate = name_or_path or shutil.which(default_name)
    if not candidate:
        return None
    separators = [sep for sep in (os.path.sep, os.path.altsep) if sep]
    if any(sep in candidate for sep in separators):
        return candidate if Path(candidate).is_file() else None
    return shutil.which(candidate)


def _run_json(command: list[str], *, timeout: int = 60) -> tuple[dict, bytes]:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except OSError:
        raise ObserverError("ffprobe_missing") from None
    if result.returncode != 0:
        raise ObserverError("ffprobe_failed")
    try:
        return json.loads(result.stdout.decode("utf-8")), result.stdout
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ObserverError("ffprobe_output_invalid") from None


def _audio_facts(probe: dict) -> dict:
    streams = probe.get("streams") or []
    audio_streams = [item for item in streams if item.get("codec_type") == "audio"]
    if not audio_streams:
        raise ObserverError("no_audio_stream")
    audio = audio_streams[0]
    fmt = probe.get("format") or {}

    def _int_or_none(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _float_or_none(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return {
        "format_name": str(fmt.get("format_name") or "unknown"),
        "codec_name": str(audio.get("codec_name") or "unknown"),
        "sample_rate": _int_or_none(audio.get("sample_rate")) or 0,
        "channels": _int_or_none(audio.get("channels")) or 0,
        "sample_fmt": audio.get("sample_fmt"),
        "bits_per_raw_sample": _int_or_none(audio.get("bits_per_raw_sample")) or 0,
        "duration_seconds": _float_or_none(fmt.get("duration"))
        or _float_or_none(audio.get("duration")) or 0.0,
        "bit_rate_bps": _int_or_none(fmt.get("bit_rate")),
        "stream_count": len(streams),
    }


def _tag_facts(probe: dict) -> dict:
    fmt_tags = {str(k).lower(): v for k, v in ((probe.get("format") or {}).get("tags") or {}).items()}
    streams = probe.get("streams") or []
    stream_tags: dict[str, object] = {}
    for stream in streams:
        for key, value in (stream.get("tags") or {}).items():
            stream_tags.setdefault(str(key).lower(), value)
    merged = {**stream_tags, **fmt_tags}

    def _clean(key: str):
        value = merged.get(key)
        return None if value is None else str(value)

    return {
        **{key: _clean(key) for key in TAG_KEYS},
        "raw_keys": sorted(merged),
    }


def _cover_facts(probe: dict) -> dict:
    for index, stream in enumerate(probe.get("streams") or []):
        disposition = stream.get("disposition") or {}
        if int(disposition.get("attached_pic") or 0) == 1:
            try:
                width = int(stream.get("width") or 0) or None
                height = int(stream.get("height") or 0) or None
            except (TypeError, ValueError):
                width = height = None
            return {
                "embedded": True,
                "stream_index": index,
                "codec": stream.get("codec_name"),
                "mime": None,
                "width": width,
                "height": height,
                "bytes_sha256": None,
                "decode_ok": True,
            }
    return {
        "embedded": False, "stream_index": None, "codec": None, "mime": None,
        "width": None, "height": None, "bytes_sha256": None, "decode_ok": None,
    }


def _decode_facts(file_path: Path, ffmpeg: str) -> tuple[dict, bytes]:
    command = [ffmpeg, "-v", "error", "-xerror", "-i", str(file_path), "-f", "null", "-"]
    try:
        result = subprocess.run(command, capture_output=True, timeout=120, check=False)
    except OSError:
        raise ObserverError("ffmpeg_missing") from None
    log = (result.stderr or b"")[: 64 * 1024]
    return {"ok": result.returncode == 0, "exit_code": result.returncode}, log


def _write_evidence(evidence_dir: Path, name: str, payload: bytes) -> Path:
    target = evidence_dir / name
    target.write_bytes(payload)
    return target


def observe(
    *,
    file_path: Path,
    evidence_dir: Path,
    target_dir: Path | None = None,
    ffprobe: str | None = None,
    ffmpeg: str | None = None,
    captured_at: str,
) -> dict:
    """产出 delivered_evidence + 受控证据文件清单（不写任何业务路径）。"""
    file_path = Path(file_path)
    if not file_path.is_absolute():
        raise ObserverError("file_not_absolute")
    if str(file_path).startswith("-"):
        raise ObserverError("file_looks_like_option")
    if not file_path.is_file():
        raise ObserverError("file_missing")

    ffprobe = _resolve_tool(ffprobe, "ffprobe")
    ffmpeg = _resolve_tool(ffmpeg, "ffmpeg")
    if not ffprobe:
        raise ObserverError("ffprobe_missing")
    if not ffmpeg:
        raise ObserverError("ffmpeg_missing")

    evidence_dir = Path(evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    written: list[dict] = []

    fd, info = _read_regular_no_symlink(file_path)
    os.close(fd)

    digest, md5_digest, size = hash_file(file_path)
    base_payload = (file_path.name + "\n").encode("utf-8")
    _write_evidence(evidence_dir, "basename.txt", base_payload)
    base_ref = evidence_ref("stat_snapshot", base_payload, captured_at, "basename_record")
    written.append({"name": "basename.txt", **base_ref})

    probe, probe_bytes = _run_json([
        ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
        str(file_path),
    ])
    _write_evidence(evidence_dir, "ffprobe.json", probe_bytes)
    probe_ref = evidence_ref("ffprobe_json", probe_bytes, captured_at, "ffprobe_show_format_streams")
    written.append({"name": "ffprobe.json", **probe_ref})

    decode, decode_log = _decode_facts(file_path, ffmpeg)
    _write_evidence(evidence_dir, "decode.log", decode_log)
    decode_ref = evidence_ref("ffmpeg_log", decode_log, captured_at, "ffmpeg_xerror_null")
    written.append({"name": "decode.log", **decode_ref})

    audio = _audio_facts(probe)
    audio["ref"] = probe_ref
    tags = _tag_facts(probe)
    tags["ref"] = probe_ref
    decode_block = {"ok": decode["ok"], "exit_code": decode["exit_code"], "ref": decode_ref}

    cover = _cover_facts(probe)
    sidecar = find_sidecar_cover(file_path)
    if not cover["embedded"] and sidecar is not None:
        try:
            cover_bytes = sidecar["path"].read_bytes()
        except OSError:
            cover_bytes = b""
        if cover_bytes:
            _write_evidence(evidence_dir, "cover" + sidecar["path"].suffix, cover_bytes)
            cover_ref = evidence_ref(
                "cover_bytes", cover_bytes, captured_at, "sidecar_cover_read")
            written.append({"name": "cover" + sidecar["path"].suffix, **cover_ref})
            cover = {
                "embedded": False,
                "stream_index": None,
                "codec": None,
                "mime": sidecar["mime"],
                "width": None,
                "height": None,
                "bytes_sha256": sha256_hex(cover_bytes),
                "decode_ok": None,
                "ref": cover_ref,
            }
        else:
            cover["ref"] = None
    else:
        cover["ref"] = probe_ref if cover["embedded"] else None

    lyric = observe_lyric(file_path)
    lyric_payload = lyric.pop("_payload", None)
    if lyric["kind"] == "sidecar" and lyric_payload is not None:
        _write_evidence(evidence_dir, "lyric.lrc", lyric_payload)
        lyric_ref = evidence_ref("lrc_bytes", lyric_payload, captured_at, "sidecar_lyric_read")
        written.append({"name": "lyric.lrc", **lyric_ref})
        lyric["ref"] = lyric_ref
    else:
        lyric["ref"] = None

    mode_owner = None
    if target_dir is not None:
        mode_owner = {
            "mode": format_mode(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "target": sample_mode_owner(Path(target_dir)),
        }

    delivered = {
        "basename": file_path.name,
        "basename_ref": base_ref,
        "stat": {
            "mode": format_mode(info.st_mode),
            "uid": info.st_uid,
            "gid": info.st_gid,
            "size": int(info.st_size),
            "mtime_ns": int(info.st_mtime_ns),
        },
        "sha256": digest,
        "size": size,
        "md5": md5_digest,
        "audio": audio,
        "decode": decode_block,
        "tags": tags,
        "cover": cover,
        "lyric": lyric,
    }
    if mode_owner is not None:
        delivered["mode_owner"] = mode_owner
    return {"delivered_evidence": delivered, "evidence_files": written}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only delivered_evidence observer (P1 acceptance evidence).")
    parser.add_argument("--file", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--target-dir")
    parser.add_argument("--ffprobe")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--json-out")
    parser.add_argument("--captured-at", default="")
    args = parser.parse_args(argv)

    from datetime import datetime, timezone
    captured_at = args.captured_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        document = observe(
            file_path=Path(args.file),
            evidence_dir=Path(args.evidence_dir),
            target_dir=Path(args.target_dir) if args.target_dir else None,
            ffprobe=args.ffprobe,
            ffmpeg=args.ffmpeg,
            captured_at=captured_at,
        )
    except ObserverError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": exc.code}, ensure_ascii=False) + "\n")
        return 3 if exc.code.endswith("_missing") else 2

    payload = {"ok": True, "schema": SCHEMA, "schema_version": SCHEMA_VERSION, **document}
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
