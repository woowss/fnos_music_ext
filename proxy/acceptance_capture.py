"""P1 验收证据「保存时采集」边界：生产者骨架（纯 stdlib、默认不启用）。

**边界（设计见 docs/ACCEPTANCE_CAPTURE_DESIGN_20261107.md）**

- 只做「只读抽取 → 受控证据」：不改变保存/发布语义，不写曲库，不接流水线
  （``PIPELINE_INTEGRATION_ALLOWED=False`` 期间只用于离线与隔离验收）。
- 采集失败或未采集 ⇒ 对应维度 ``unknown``/``blocked``，**绝不产出 pass**。
- 不持久化 URL / token / 完整响应 / 绝对路径；只保留白名单字段与摘要。
- 本模块不 import ``proxy.app``，因此可被离线测试与采集脚本独立使用。

字段语义与 JSON Schema 见 ``docs/ACCEPTANCE_EVIDENCE_SCHEMA_20261107.md``
（Rev-3 PASS）与 ``scripts/verify_acceptance_evidence_semantics.py``（G-X3）。
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "fnmusic.p1.acceptance-evidence"
SCHEMA_VERSION = "0.1-draft-20261107"

#: provider-reported 边界（S2）：任何 pass 也不得外推到「档位等价/母带」。
CLAIM_SCOPE = "provider_reported_not_independently_verified"

DIMENSIONS = ("bytes", "quality", "tags", "cover", "lyric", "duplicate", "index_play")

#: 采集侧敏感模式：出现即视为泄漏（证据里只允许摘要/白名单字段）。
SENSITIVE_PATTERNS = (
    "http://",
    "https://",
    "sk-",
    "token=",
    "Authorization",
    "/vol1/",
    "/repo/",
    "/data/",
    "/var/apps",
)

#: 维度/整体 verdict 的传播优先级（越高越严重）。
_VERDICT_RANK = {"pass": 0, "unknown": 1, "blocked": 2, "fail": 3}


class CaptureError(ValueError):
    """构造证据时的编程错误（不是运行时采集失败）。"""


def sha256_hex(payload: bytes) -> str:
    """返回 payload 的 64 位十六进制 SHA-256。"""
    return hashlib.sha256(payload).hexdigest()


def canonical_json(document: Mapping[str, Any]) -> bytes:
    """规范化 JSON 字节：键排序、紧凑分隔、UTF-8、不转义非 ASCII。"""
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def utc_now_iso() -> str:
    """当前时间的 ISO-8601 UTC 字符串（用于 captured_at）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def evidence_ref(
    *,
    kind: str,
    payload: bytes,
    captured_at: str,
    capture_method: str = "",
    custody: str = "captured",
) -> dict[str, Any]:
    """构造 evidence_ref（sha256/bytes 必须对实际原始字节重算）。"""
    if not isinstance(payload, (bytes, bytearray)):
        raise CaptureError("payload must be bytes")
    raw = bytes(payload)
    ref_id = "ev_" + hashlib.sha256(kind.encode("ascii") + b"\x00" + raw).hexdigest()[:32]
    return {
        "ref_id": ref_id,
        "kind": kind,
        "sha256": sha256_hex(raw),
        "bytes": len(raw),
        "captured_at": captured_at,
        "capture_method": capture_method[:128],
        "custody": custody,
    }


def recompute_record_sha256(position: Mapping[str, Any]) -> str:
    """对 position 记录（剔除 record_sha256 后）重算规范化摘要。"""
    body = {key: value for key, value in position.items() if key != "record_sha256"}
    return sha256_hex(canonical_json(body))


def _seal_position(position: dict[str, Any]) -> dict[str, Any]:
    position["record_sha256"] = recompute_record_sha256(position)
    return position


def source_evidence_uncaptured() -> dict[str, Any]:
    """未采集时的 claim 层：全部显式 unknown/空，不留可被误读的默认值。"""
    return {
        "quality": {
            "tier_requested": "",
            "reported_level": "",
            "reported_status": "unknown",
            "level_in_ladder": "unknown",
            "claim_scope": CLAIM_SCOPE,
            "fallback": False,
            "claim_refs": [],
        },
        "container_claim": {"api_type": "", "status": "unknown"},
        "metadata": {
            "title": "",
            "artist": "",
            "album": "",
            "track": None,
            "disc": None,
            "lyricist": "",
            "composer": "",
            "status": "unknown",
        },
        "cover": {"status": "not_attempted", "error": ""},
        "lyric": {"status": "not_attempted"},
        "integrity": {"status": "unknown"},
    }


def byte_scan_uncaptured(endpoint: str = "no_independent_view") -> dict[str, Any]:
    """未做独立字节/位置对应时的保守记录（不得伪装成 unique_match）。"""
    return {
        "profile": "none",
        "status": "not_run",
        "match_count": 0,
        "limitations": [endpoint],
    }


def index_play_uncaptured() -> dict[str, Any]:
    """索引/播放未观测：显式 not_run + 固定限制声明。"""
    return {
        "index": {"status": "not_run", "method": "machine_api", "machine_readable": False},
        "playback": {"status": "not_run", "method": "machine_api", "machine_readable": False},
        "limitations": ["cannot_prove_audible_output"],
    }


def verdict_unknown(*, fixed_reasons: Sequence[str] = ("not_executed",),
                    note: str = "") -> dict[str, Any]:
    """未采集位置的统一 verdict：unknown（INV-23 不允许 mismatch 用 blocked），无 pass 维度。"""
    reasons = list(fixed_reasons) or ["not_executed"]
    return {
        "verdict": "unknown",
        "dimensions": {name: "unknown" for name in DIMENSIONS},
        "duplicate_resolution": "unknown",
        "fixed_reasons": reasons,
        "notes": [note] if note else [],
    }


def build_position_uncaptured(
    *,
    position: int,
    captured_at: str,
    source_id: str | None = None,
    duplicate_of_position: int | None = None,
    expected_outcome: str = "published",
    fixed_reasons: Sequence[str] = (),
    note: str = "",
) -> dict[str, Any]:
    """构造「该位置证据尚未采集」的 PositionRecord（诚实 unknown/blocked）。"""
    if not 1 <= int(position) <= 1000:
        raise CaptureError("position must be within 1..1000")
    if expected_outcome not in ("published", "conflict", "failure", "cancelled"):
        raise CaptureError("invalid expected_outcome")
    record: dict[str, Any] = {
        "position": int(position),
        "source_id": source_id,
        "duplicate_of_position": duplicate_of_position,
        "state": "not_executed",
        "expected_outcome": expected_outcome,
        "reason": None,
        "reason_message": None,
        "source_evidence": source_evidence_uncaptured(),
        "delivered_evidence": None,
        "failure": None,
        "byte_scan": byte_scan_uncaptured(),
        "index_play": index_play_uncaptured(),
        "verdict": verdict_unknown(fixed_reasons=fixed_reasons, note=note),
        "timestamps": {"captured_at": captured_at, "verified_at": None},
    }
    return _seal_position(record)


def build_environment(
    *,
    device_ref: str,
    fnos_version: str,
    kernel: str,
    fstype: str,
    mount_options_digest: str,
    library_ref: str,
    staging_ref: str,
    deployed_version: str,
    provenance_status: str,
    package_sha256: str | None = None,
    git_head: str | None = None,
    tool_hashes: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """环境绑定（逻辑引用，不写绝对路径）。

    ``provenance_status='observed'`` 时 INV-22 要求同时给出 package_sha256(64hex)
    与 git_head(40hex)；缺少其一必须降级为 ``partial``/``unknown``，不得声称 observed。
    """
    if provenance_status == "observed" and not (package_sha256 and git_head):
        raise CaptureError("observed provenance requires package_sha256 and git_head")
    if package_sha256 is not None and (
        len(package_sha256) != 64 or any(ch not in "0123456789abcdef" for ch in package_sha256)
    ):
        raise CaptureError("package_sha256 must be 64-hex")
    if git_head is not None and len(git_head) != 40:
        raise CaptureError("git_head must be 40-hex")
    for name, value in (("mount_options_digest", mount_options_digest),):
        if len(str(value)) != 64 or any(ch not in "0123456789abcdef" for ch in str(value)):
            raise CaptureError(f"{name} must be 64-hex")
    document: dict[str, Any] = {
        "device_ref": device_ref,
        "fnos_version": fnos_version,
        "kernel": kernel,
        "filesystem": {"fstype": fstype, "mount_options_digest": mount_options_digest},
        "library_ref": library_ref,
        "staging_ref": staging_ref,
        "deployed": {
            "version": deployed_version,
            "provenance_status": provenance_status,
            "package_sha256": package_sha256,
            "git_head": git_head,
        },
    }
    if tool_hashes:
        document["tool_hashes"] = dict(tool_hashes)
    return document


def build_job(
    *,
    job_id: str,
    manifest_sha256: str,
    positions_total: int,
    source_fingerprint: str | None = None,
    source_fingerprint_origin: str = "unknown",
    duplicate_positions: Iterable[int] = (),
) -> dict[str, Any]:
    """账本绑定（manifest 摘要必须是实际重算值）。"""
    if len(manifest_sha256) != 64:
        raise CaptureError("manifest_sha256 must be 64-hex")
    if source_fingerprint_origin not in (
        "snapshot_response", "collection_version_manifest", "unknown",
    ):
        raise CaptureError("invalid source_fingerprint_origin")
    return {
        "job_id": job_id,
        "manifest_sha256": manifest_sha256,
        "source_fingerprint": source_fingerprint,
        "source_fingerprint_origin": source_fingerprint_origin,
        "positions_total": int(positions_total),
        "duplicate_positions": sorted(int(value) for value in duplicate_positions),
    }


def _overall_verdict(positions: Sequence[Mapping[str, Any]]) -> tuple[str, dict[str, int]]:
    counts = {"pass": 0, "fail": 0, "unknown": 0, "blocked": 0}
    for position in positions:
        verdict = (position.get("verdict") or {}).get("verdict")
        if verdict not in counts:
            raise CaptureError(f"invalid position verdict: {verdict!r}")
        counts[verdict] += 1
    present = [name for name, count in counts.items() if count > 0]
    overall = max(present, key=lambda name: _VERDICT_RANK[name])
    return overall, counts


def build_evidence_set(
    *,
    environment: Mapping[str, Any],
    job: Mapping[str, Any],
    positions: Sequence[Mapping[str, Any]],
    evidence_set_id: str | None = None,
    created_at: str | None = None,
    created_by: str = "capture-boundary-skeleton",
) -> dict[str, Any]:
    """组装 EvidenceSet；计数与整体结论由 position 派生，不接受调用方声称值。"""
    ordered = list(positions)
    if not ordered:
        raise CaptureError("positions must be non-empty")
    numbers = [int(record.get("position", 0)) for record in ordered]
    if any(not 1 <= number <= 1000 for number in numbers):
        raise CaptureError("position out of range")
    if len(set(numbers)) != len(numbers):
        raise CaptureError("duplicate position numbers")
    ordered.sort(key=lambda record: int(record["position"]))

    job_positions_total = int(job.get("positions_total", -1))
    if job_positions_total != len(ordered):
        raise CaptureError("job.positions_total does not match positions length")

    for record in ordered:
        body = {key: value for key, value in record.items() if key != "record_sha256"}
        if record.get("record_sha256") != sha256_hex(canonical_json(body)):
            raise CaptureError(
                f"record_sha256 mismatch at position {record.get('position')}"
            )

    overall, counts = _overall_verdict(ordered)
    stamp = created_at or utc_now_iso()
    document: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "evidence_set_id": evidence_set_id or ("ev_" + sha256_hex(canonical_json({
            "job": job.get("job_id"), "created_at": stamp,
        }))[:32]),
        "created_at": stamp,
        "created_by": created_by,
        "environment": dict(environment),
        "job": dict(job),
        "positions": ordered,
        "summary": {
            "positions_total": len(ordered),
            "pass": counts["pass"],
            "fail": counts["fail"],
            "unknown": counts["unknown"],
            "blocked": counts["blocked"],
            "overall_verdict": overall,
            "blocked_reasons": sorted({
                reason
                for record in ordered
                for reason in (record.get("verdict") or {}).get("fixed_reasons") or []
            }),
        },
    }
    return document


QUALITY_LADDER = ("jymaster", "hires", "lossless", "exhigh")

#: provider 响应里允许抽取的字段（白名单；其余一律不落证据）。
PROVIDER_QUALITY_KEYS = (
    "id", "code", "level", "type", "size", "md5", "freeTrialInfo", "url", "time",
)
METADATA_SOURCE_KEYS = ("name", "ar", "al", "dt", "no", "pop")

_COVER_STATUSES = (
    "preserved", "embedded", "unavailable", "unsupported", "error",
    "not_published", "not_attempted",
)
_COVER_ERRORS = (
    "", "host_untrusted", "url_invalid", "address_untrusted", "http_error",
    "payload_invalid", "decode_failed", "embed_failed", "unknown",
)
_LYRIC_STATUSES = ("published", "unavailable", "error", "conflict", "not_attempted", "unknown")
_INTEGRITY_STATUSES = ("match", "mismatch", "not_provided", "unknown")


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _digest(value: Any) -> str:
    """只接受 32 位十六进制 md5（小写归一）；其它一律视为缺失。"""
    if not isinstance(value, str):
        return ""
    candidate = value.strip().lower()
    if len(candidate) != 32 or any(ch not in "0123456789abcdef" for ch in candidate):
        return ""
    return candidate


def extract_provider_quality(payload: Mapping[str, Any]) -> dict[str, Any]:
    """白名单抽取 provider 质量响应；不复制 URL/完整响应。

    返回 ``{"extract": <白名单字段>, "reported_level": str, "reported_status": str,
    "level_in_ladder": bool|"unknown", "trial": bool, "digest": {"size","md5"},
    "raw_sha256": <白名单 JSON 的 sha256>}``。
    """
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    extract: dict[str, Any] = {}
    for key in PROVIDER_QUALITY_KEYS:
        if key not in data:
            continue
        value = data.get(key)
        if key == "url":
            extract["url_present"] = isinstance(value, str) and bool(value.strip())
            continue
        if key == "freeTrialInfo":
            extract["freeTrialInfo_present"] = value is not None
            continue
        if key in ("id", "code", "time"):
            extract[key] = value if isinstance(value, int) else None
            continue
        if key == "size":
            extract["size"] = value if isinstance(value, int) else None
            continue
        if key == "md5":
            extract["md5"] = _digest(value) or None
            continue
        extract[key] = _text(value)
    level = _text(data.get("level"))
    if level and level in QUALITY_LADDER:
        in_ladder: bool | str = True
    elif level:
        in_ladder = False
    else:
        in_ladder = "unknown"
    return {
        "extract": extract,
        "reported_level": level,
        "reported_status": "reported" if level else "unknown",
        "level_in_ladder": in_ladder,
        "trial": bool(extract.get("freeTrialInfo_present")),
        "digest": {"size": extract.get("size"), "md5": extract.get("md5")},
        "raw_sha256": sha256_hex(canonical_json(extract)),
    }


def extract_container_claim(payload: Mapping[str, Any]) -> dict[str, Any]:
    """provider ``data.type`` 只作 claim（并不等于实际落盘格式）。"""
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else payload
    api_type = _text(data.get("type")).lower()
    if api_type not in ("flac", "mp3"):
        return {"api_type": "", "status": "unknown"}
    return {"api_type": api_type, "status": "reported"}


def _first_text(mapping: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, int):
            return str(value)
    return ""


def _names(value: Any) -> str:
    """酷狗/网易云风格 ``[{"name": ...}]`` → 单个展示名（多值用 / 连接）。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return _first_text(value, "name", "nickname")
    if isinstance(value, (list, tuple)):
        names = []
        for item in value:
            if isinstance(item, Mapping):
                name = _first_text(item, "name", "nickname")
            elif isinstance(item, str):
                name = item.strip()
            else:
                name = ""
            if name:
                names.append(name)
        return "/".join(names)
    return ""


def extract_metadata_claim(info_payload: Mapping[str, Any]) -> dict[str, Any]:
    """``/info`` 的白名单元数据快照（track/disc 当前未进 route 最终 data → 可能为空）。"""
    data = info_payload.get("songs") or info_payload.get("data") or info_payload
    if isinstance(data, (list, tuple)):
        data = data[0] if data else {}
    if not isinstance(data, Mapping):
        data = {}
    album = data.get("al") if isinstance(data.get("al"), Mapping) else data
    track_number = None
    for key in ("track", "trackNumber", "no"):
        value = album.get(key) if isinstance(album, Mapping) else None
        if isinstance(value, int) and value > 0:
            track_number = value
            break
    disc_number = data.get("disc")
    disc_value = str(disc_number) if isinstance(disc_number, int) and disc_number > 0 else None
    document = {
        "title": _first_text(data, "name", "title"),
        "artist": _names(data.get("ar") or data.get("artists") or data.get("artist")),
        "album": _names(album.get("name") if isinstance(album, Mapping) else data.get("album")),
        "track": str(track_number) if track_number else None,
        "disc": disc_value,
        "lyricist": _names(data.get("lyricist") or data.get("lyrics_by")),
        "composer": _names(data.get("composer") or data.get("composed_by")),
        "status": "reported",
    }
    if not any(document[key] for key in ("title", "artist", "album")):
        document["status"] = "unknown"
    return document


def extract_cover_claim(*, status: str, error: str = "") -> dict[str, Any]:
    """封面状态机快照（状态与固定错误码必须来自既有枚举）。"""
    if status not in _COVER_STATUSES:
        status = "error"
    if error not in _COVER_ERRORS:
        error = "unknown"
    if status != "error":
        error = ""
    return {"status": status, "error": error}


def extract_lyric_claim(*, status: str) -> dict[str, Any]:
    if status not in _LYRIC_STATUSES:
        status = "unknown"
    return {"status": status}


def extract_integrity_claim(
    *,
    api_size: Any = None,
    api_md5: Any = None,
    downloaded_size: Any = None,
    downloaded_md5: Any = None,
) -> dict[str, Any]:
    """下载完整性：只在两侧都有值时才可判定 match/mismatch，否则 not_provided。"""
    expected_digest = _digest(api_md5)
    actual_digest = _digest(downloaded_md5)
    has_size = isinstance(api_size, int) and api_size > 0 and isinstance(downloaded_size, int)
    if not has_size and not (expected_digest and actual_digest):
        return {"status": "not_provided"}
    mismatch = False
    compared = False
    if has_size:
        compared = True
        if int(api_size) != int(downloaded_size):
            mismatch = True
    if expected_digest and actual_digest:
        compared = True
        if expected_digest != actual_digest:
            mismatch = True
    if not compared:
        return {"status": "not_provided"}
    return {"status": "mismatch" if mismatch else "match"}


def build_source_evidence(
    *,
    provider_payload: Mapping[str, Any],
    captured_at: str,
    tier_requested: str = "",
    fallback: bool = False,
    info_payload: Mapping[str, Any] | None = None,
    cover_status: str = "not_attempted",
    cover_error: str = "",
    lyric_status: str = "not_attempted",
    api_size: Any = None,
    api_md5: Any = None,
    downloaded_size: Any = None,
    downloaded_md5: Any = None,
) -> dict[str, Any]:
    """把一次保存尝试的白名单事实组装成 schema §2.4.2 的 source_evidence。"""
    quality_extract = extract_provider_quality(provider_payload)
    claim_payload = canonical_json(quality_extract["extract"])
    claim_ref = evidence_ref(
        kind="provider_response_extract",
        payload=claim_payload,
        captured_at=captured_at,
        capture_method="whitelist_extract",
    )
    quality = {
        "tier_requested": tier_requested if tier_requested in QUALITY_LADDER else "",
        "reported_level": quality_extract["reported_level"],
        "reported_status": quality_extract["reported_status"],
        "level_in_ladder": quality_extract["level_in_ladder"],
        "claim_scope": CLAIM_SCOPE,
        "fallback": bool(fallback),
        "claim_refs": [claim_ref],
    }
    return {
        "quality": quality,
        "container_claim": extract_container_claim(provider_payload),
        "metadata": extract_metadata_claim(info_payload or {}),
        "cover": extract_cover_claim(status=cover_status, error=cover_error),
        "lyric": extract_lyric_claim(status=lyric_status),
        "integrity": extract_integrity_claim(
            api_size=api_size, api_md5=api_md5,
            downloaded_size=downloaded_size, downloaded_md5=downloaded_md5,
        ),
    }


def _walk_strings(node: Any, path: str = "$") -> Iterable[tuple[str, str]]:
    if isinstance(node, str):
        yield path, node
    elif isinstance(node, Mapping):
        for key, value in node.items():
            yield from _walk_strings(value, f"{path}.{key}")
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            yield from _walk_strings(value, f"{path}[{index}]")


def find_sensitive_leaks(document: Mapping[str, Any]) -> list[str]:
    """返回含敏感模式（URL/token/绝对路径）的 JSON 路径列表；空表示未发现。"""
    leaks: list[str] = []
    for path, text in _walk_strings(document):
        for pattern in SENSITIVE_PATTERNS:
            if pattern in text:
                leaks.append(f"{path}~{pattern}")
                break
    return leaks
