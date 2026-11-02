#!/usr/bin/env python3
"""只读观察脚本：整歌单保存接线前的 P3 硬门槛（合同 §4.7）。

本脚本只做**只读**观察，不保存、不转换、不写 NAS 目标/暂存目录、不改 `.env`、
不重启任何服务。它适合在 musicbox 容器内（或装有同一套模块与 SDK 的只读环境）
运行，用于在实施接线前确认：

1. artifact 冻结事实（HEAD、模块 sha256、SDK 版本与 dist-info、wheel 摘要常量）；
2. 两次独立读取一张测试歌单的成员快照，fingerprint 是否一致；
3. 是否出现音频 URL / 歌曲详情 / 歌词请求（禁止项，只统计次数，不打印行）；
4. Cookie 文件是否被写回（只记录 size/mtime/mode，**不读内容**）；
5. 目标/暂存目录是否被写入（只比较目录元数据，不读文件内容）；
6. 每次读取的耗时与运行期 flag 状态。

安全约定（与合同 §4.7/§8 一致）：
- 输出报告**只含**哈希、计数、布尔值、耗时与固定码；不含歌单名、曲目名、
  Cookie、签名 URL、本机路径或异常文本；
- 不读取 Cookie 文件内容；日志只统计禁止模式出现次数；
- 任何异常折叠为固定 `error` 字段，不打印 traceback；
- `--confirm-observe` 为必需确认位；未提供时只打印计划并退出 2；
- 运行期通过进程内环境变量打开 producer 只读开关，**不写任何配置文件**。

用法（在容器内举例）::

    python3 /tmp/p3/tools/netease_playlist_observe.py \
        --module-dir /tmp/p3/musicbox-service \
        --playlist-id 123456 \
        --cookie-path /root/.netease-musicbox/cookie \
        --target-dir /vol1/1000/fnmusic-test-20261031 \
        --staging-dir /vol1/1000/fnmusic-manual-save-staging \
        --musicbox-log /repo/logs/musicbox.log \
        --confirm-observe \
        --report /tmp/p3/observation.json

没有授权时只跑本地自检::

    python3 tools/netease_playlist_observe.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import stat as stat_module
import sys
import time
from typing import Any, Mapping

SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[1]
DEFAULT_MODULE_DIR = REPO_ROOT / "musicbox-service"

# 只读开关：进程内设置，不写 .env（合同 §4.7 执行前提）。
READONLY_ENV = "FNMUSIC_PLAYLIST_MANIFEST_READONLY"
# 保存侧开关：P3 前提是"保存侧全关"；任一为真则拒绝观察（fail-closed）。
SAVE_SWITCHES = ("FNMUSIC_MANUAL_SAVE_ENABLED", "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED")
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})
# 禁止出现在 musicbox 访问日志里的上游路径（只统计次数，不打印行）。
FORBIDDEN_LOG_PATTERNS = (
    "/api/song/enhance/player/url",
    "/weapi/v3/song/detail",
    "/lyric",
)
# 冻结 artifact 的源码文件（相对 module-dir / repo）。
ARTIFACT_FILES = (
    "netease_playlist_snapshot.py",
    "playlist_manifest.py",
    "netease_ext.py",
)
MAX_DIR_ENTRIES = 2000
MAX_LOG_BYTES = 32 * 1024 * 1024
REPORT_SCHEMA_VERSION = 1


class ObserveError(Exception):
    """固定码错误；不携带上游文本。"""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _is_true(value: Any) -> bool:
    return type(value) is str and value.strip().lower() in _TRUE_TOKENS


def _sha256_file(path: Path) -> "str | None":
    try:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def _stat_facts(path: "Path | None") -> dict:
    """只读 stat；绝不打开文件。缺失/无权访问时返回固定形状。"""
    if path is None:
        return {"provided": False, "exists": False}
    try:
        info = path.stat()
    except OSError:
        return {"provided": True, "exists": False}
    return {
        "provided": True,
        "exists": True,
        "size": int(info.st_size),
        "mtime_ns": int(info.st_mtime_ns),
        "mode": stat_module.S_IMODE(info.st_mode),
    }


def _stat_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> "bool | None":
    if not before.get("exists") or not after.get("exists"):
        return None
    return (
        before.get("size") != after.get("size")
        or before.get("mtime_ns") != after.get("mtime_ns")
        or before.get("mode") != after.get("mode")
    )


def _dir_changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> "bool | None":
    """目录是否变化；摘要被截断时返回 ``None``（绝不据此判"未变化"）。"""
    if not before.get("exists") or not after.get("exists"):
        return None
    if before.get("truncated") or after.get("truncated"):
        return None
    return before.get("digest") != after.get("digest")


def _log_delta(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict:
    """禁止路径的**增量**计数（避免历史日志里的旧条目误判本次观察）。"""
    before_counts = before.get("counts") if isinstance(before.get("counts"), Mapping) else {}
    after_counts = after.get("counts") if isinstance(after.get("counts"), Mapping) else {}
    delta = {}
    for pattern in FORBIDDEN_LOG_PATTERNS:
        old = before_counts.get(pattern, 0)
        new = after_counts.get(pattern, 0)
        delta[pattern] = max(0, int(new) - int(old)) if isinstance(old, int) and isinstance(new, int) else None
    return {
        "available": before.get("exists") is True and after.get("exists") is True,
        "delta": delta,
        "before": before_counts or None,
        "after": after_counts or None,
    }


def _load_snapshot_module(module_dir: Path):
    """按目录加载 producer 模块，并校验真实来源，避免 ``sys.modules`` 遮蔽。"""
    module_path = (module_dir / "netease_playlist_snapshot.py").resolve()
    if not module_path.is_file():
        raise ObserveError("module_dir_invalid")
    name = "netease_playlist_snapshot"
    inserted = False
    if str(module_dir) not in sys.path:
        sys.path.insert(0, str(module_dir))
        inserted = True
    try:
        sys.modules.pop(name, None)
        import importlib
        module = importlib.import_module(name)
        resolved = Path(getattr(module, "__file__", "") or "").resolve()
        if resolved != module_path:
            raise ObserveError("module_shadowed")
        return module
    finally:
        if inserted:
            try:
                sys.path.remove(str(module_dir))
            except ValueError:
                pass
        sys.modules.pop(name, None)
        sys.modules.pop("playlist_manifest", None)


def _dir_digest(path: "Path | None") -> dict:
    """目录元数据摘要：只含相对名/大小/mtime/mode 的哈希，用于判断"有没有变"。

    不读取文件内容；条目数超过 ``MAX_DIR_ENTRIES`` 时标记 ``truncated`` 并只取前 N。
    """
    if path is None:
        return {"provided": False, "exists": False}
    try:
        if not path.is_dir():
            return {"provided": True, "exists": False}
        rows: list[str] = []
        truncated = False
        for index, entry in enumerate(sorted(path.iterdir(), key=lambda item: item.name)):
            if index >= MAX_DIR_ENTRIES:
                truncated = True
                break
            try:
                info = entry.stat()
            except OSError:
                rows.append(entry.name + "|stat_error")
                continue
            kind = "d" if entry.is_dir() else "f"
            rows.append(
                "|".join((
                    entry.name, kind, str(int(info.st_size)),
                    str(int(info.st_mtime_ns)), str(stat_module.S_IMODE(info.st_mode)),
                ))
            )
        digest = hashlib.sha256("\n".join(rows).encode("utf-8", "surrogatepass")).hexdigest()
        return {
            "provided": True, "exists": True, "entries": len(rows),
            "truncated": truncated, "digest": digest,
        }
    except OSError:
        return {"provided": True, "exists": False}


def _count_forbidden_log_patterns(path: "Path | None") -> dict:
    """统计禁止路径出现次数；只回计数，绝不回日志行或大小写内容。"""
    if path is None:
        return {"provided": False}
    try:
        size = path.stat().st_size
    except OSError:
        return {"provided": True, "exists": False}
    counts = {pattern: 0 for pattern in FORBIDDEN_LOG_PATTERNS}
    try:
        with path.open("rb") as handle:
            if size > MAX_LOG_BYTES:
                handle.seek(size - MAX_LOG_BYTES)
            for raw in handle:
                line = raw.decode("utf-8", "replace")
                for pattern in FORBIDDEN_LOG_PATTERNS:
                    if pattern in line:
                        counts[pattern] += 1
    except OSError:
        return {"provided": True, "exists": False}
    return {"provided": True, "exists": True, "counts": counts}


def _artifact_facts(module_dir: Path, repo_root: Path) -> dict:
    """冻结 artifact：HEAD、源码哈希、SDK 版本与 dist-info、wheel 摘要常量。"""
    head = None
    head_file = repo_root / ".git" / "HEAD"
    try:
        raw_head = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        raw_head = ""
    if raw_head.startswith("ref: "):
        try:
            head = (repo_root / ".git" / raw_head[5:]).read_text(encoding="utf-8").strip()
        except OSError:
            head = None
    elif raw_head:
        head = raw_head

    files = {}
    for name in ARTIFACT_FILES:
        files[name] = _sha256_file(module_dir / name)
    requirements = None
    requirements_sha256 = None
    req_path = module_dir / "requirements.txt"
    try:
        raw = req_path.read_text(encoding="utf-8")
    except OSError:
        raw = ""
    if raw:
        requirements_sha256 = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith("netease-musicbox"):
                requirements = stripped
                break

    installed_version = None
    dist_info_present = False
    try:
        distribution = importlib.metadata.distribution("NetEase-MusicBox")
        raw_version = distribution.version
        installed_version = raw_version if isinstance(raw_version, str) and raw_version else None
        dist_info_present = True
    except Exception:  # noqa: BLE001 — 缺失/损坏都按"未安装"处理
        installed_version = None
        dist_info_present = False

    pinned_version = None
    wheel_sha256 = None
    try:
        module = _load_snapshot_module(module_dir)
        pinned_version = getattr(module, "PINNED_VERSION", None)
        wheel_sha256 = getattr(module, "WHEEL_SHA256", None)
    except Exception:  # noqa: BLE001 — 模块不可导入/被遮蔽时保持 None
        pinned_version = None
        wheel_sha256 = None

    return {
        "git_head": head,
        "files_sha256": files,
        "requirements_netease_line": requirements,
        "requirements_sha256": requirements_sha256,
        "installed_version": installed_version,
        "dist_info_present": dist_info_present,
        "pinned_version": pinned_version,
        "wheel_sha256": wheel_sha256,
        "version_matches_pinned": installed_version == pinned_version,
    }


def _observe_once(playlist_id: int, module_dir: Path) -> dict:
    """一次完整观测（内部是 2×account + 2×playlist 的独立读取）。"""
    try:
        snapshot_module = _load_snapshot_module(module_dir)
        fetch_account, fetch_playlist = snapshot_module.live_fetchers()
        started = time.monotonic()
        payload = snapshot_module.build_snapshot(playlist_id, fetch_account, fetch_playlist)
        elapsed = time.monotonic() - started
    except ObserveError:
        raise
    except Exception:  # noqa: BLE001 — 固定码，不回显异常文本
        raise ObserveError("snapshot_failed") from None
    if not isinstance(payload, Mapping):
        raise ObserveError("snapshot_malformed")
    artifact = payload.get("source_artifact")
    return {
        "status": payload.get("status"),
        "error_code": payload.get("error_code"),
        "artifact_error_code": payload.get("artifact_error_code"),
        "track_count": payload.get("track_count"),
        "position_count": payload.get("position_count"),
        "enumeration_complete": payload.get("enumeration_complete"),
        "source_fingerprint": payload.get("source_fingerprint"),
        "source_versions": payload.get("source_versions"),
        "artifact_version": artifact.get("version") if isinstance(artifact, Mapping) else None,
        "artifact_dist_info_present": (
            artifact.get("dist_info_present") if isinstance(artifact, Mapping) else None
        ),
        "elapsed_seconds": round(elapsed, 3),
    }


def _parse_args(argv: "list[str]") -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="整歌单保存 P3 只读观察（合同 §4.7）。只读，不写 NAS、不读 Cookie 内容。",
    )
    parser.add_argument("--module-dir", type=Path, default=DEFAULT_MODULE_DIR,
                        help="musicbox-service 模块目录（默认仓库内）")
    parser.add_argument("--playlist-id", type=int, default=0,
                        help="测试歌单 ID（正整数）；--dry-run 时不需要")
    parser.add_argument("--confirm-observe", action="store_true",
                        help="确认已获授权并处于维护窗口；缺少时只打印计划并退出 2")
    parser.add_argument("--dry-run", action="store_true",
                        help="只做本地自检：打印计划与 artifact 事实，不访问网络")
    parser.add_argument("--cookie-path", type=Path, default=None,
                        help="Cookie 文件路径（只 stat，不读内容）")
    parser.add_argument("--target-dir", type=Path, default=None,
                        help="保存目标目录（只比较目录元数据）")
    parser.add_argument("--staging-dir", type=Path, default=None,
                        help="暂存目录（只比较目录元数据）")
    parser.add_argument("--musicbox-log", type=Path, default=None,
                        help="musicbox 访问日志（只统计禁止路径出现次数）")
    parser.add_argument("--report", type=Path, default=None,
                        help="报告输出路径（JSON；内容已脱敏）")
    parser.add_argument("--show-playlist-id", action="store_true",
                        help="在报告里保留原始 playlist_id（默认脱敏为 sha256 前缀）")
    return parser.parse_args(argv)


def _plan(args: argparse.Namespace) -> dict:
    return {
        "script": "netease_playlist_observe",
        "contract": "FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01 §4.7",
        "would": [
            "记录 artifact 事实（HEAD/源码 sha256/SDK 版本/dist-info/requirements）",
            "stat Cookie 文件（不读内容）与目标/暂存目录元数据（写前基线）",
            "两次独立读取测试歌单成员快照，比较 source_fingerprint",
            "再次 stat Cookie/目录，统计禁止日志路径出现次数",
            "输出脱敏 JSON 报告",
        ],
        "playlist_id_required": not args.dry_run,
        "switches_expected": {
            "readonly_flag_in_process": READONLY_ENV,
            "save_switches_must_be_off": list(SAVE_SWITCHES),
        },
    }


def _build_report(args: argparse.Namespace, artifact: dict, dry_run: bool) -> dict:
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "mode": "dry-run" if dry_run else "observe",
        "artifact": artifact,
        "environment": {name: _is_true(os.environ.get(name)) for name in SAVE_SWITCHES},
        "playlist_id": None,
        "reads": [],
        "side_effects": {},
        "ok": False,
        "stop_reasons": [],
        "not_proven": (
            "provider 成员列表的完整性/实时性/授权、Cookie 未被服务端轮换、"
            "NAS 写入能力、音频有效性"
        ),
    }
    if args.playlist_id and not args.show_playlist_id:
        raw = str(args.playlist_id).encode("utf-8")
        report["playlist_id"] = "sha256:" + hashlib.sha256(raw).hexdigest()[:16]
    else:
        report["playlist_id"] = args.playlist_id or None
    return report


def _sandbox_guard(report: dict) -> None:
    """fail-closed：保存侧开关必须全关，否则拒绝观察。"""
    for name in SAVE_SWITCHES:
        if _is_true(os.environ.get(name)):
            report["stop_reasons"].append("save_switch_enabled")
            return


def _observe(args: argparse.Namespace, report: dict) -> None:
    cookie_path = args.cookie_path
    target_dir = args.target_dir
    staging_dir = args.staging_dir
    log_path = args.musicbox_log
    module_dir = args.module_dir

    cookie_before = _stat_facts(cookie_path)
    target_before = _dir_digest(target_dir)
    staging_before = _dir_digest(staging_dir)
    log_before = _count_forbidden_log_patterns(log_path)

    first = _observe_once(args.playlist_id, module_dir)
    report["reads"].append(first)
    time.sleep(0.2)
    second = _observe_once(args.playlist_id, module_dir)
    report["reads"].append(second)

    cookie_after = _stat_facts(cookie_path)
    target_after = _dir_digest(target_dir)
    staging_after = _dir_digest(staging_dir)
    log_after = _count_forbidden_log_patterns(log_path)

    reasons = report["stop_reasons"]
    for label, before, after in (
        ("target", target_before, target_after),
        ("staging", staging_before, staging_after),
    ):
        if before.get("truncated") or after.get("truncated"):
            # 摘要被截断时不能断言"没有写入"（fail-closed）。
            reasons.append(label + "_dir_digest_truncated")

    report["side_effects"] = {
        "cookie_changed": _stat_changed(cookie_before, cookie_after),
        "cookie_before": cookie_before,
        "cookie_after": cookie_after,
        "target_dir_changed": _dir_changed(target_before, target_after),
        "target_dir_before": {k: v for k, v in target_before.items() if k != "digest"},
        "target_dir_after": {k: v for k, v in target_after.items() if k != "digest"},
        "staging_dir_changed": _dir_changed(staging_before, staging_after),
        "staging_dir_before": {k: v for k, v in staging_before.items() if k != "digest"},
        "staging_dir_after": {k: v for k, v in staging_after.items() if k != "digest"},
        "musicbox_forbidden_requests": _log_delta(log_before, log_after),
    }

    first_fp = first.get("source_fingerprint")
    second_fp = second.get("source_fingerprint")
    if first.get("status") != "complete" or second.get("status") != "complete":
        reasons.append("status_not_complete")
    if not first_fp:
        reasons.append("missing_fingerprint")
    if first_fp != second_fp:
        reasons.append("fingerprint_mismatch")
    if first.get("track_count") != first.get("position_count"):
        reasons.append("count_mismatch")
    if first.get("artifact_error_code"):
        reasons.append("artifact_error")
    logging_result = report["side_effects"]["musicbox_forbidden_requests"]
    delta = logging_result.get("delta") or {}
    if any(isinstance(value, int) and value > 0 for value in delta.values()):
        reasons.append("forbidden_upstream_requests")
    if report["side_effects"]["target_dir_changed"]:
        reasons.append("target_dir_written")
    if report["side_effects"]["staging_dir_changed"]:
        reasons.append("staging_dir_written")
    report["ok"] = not reasons


def main(argv: "list[str] | None" = None) -> int:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    module_dir = args.module_dir
    if not (module_dir / "netease_playlist_snapshot.py").is_file():
        print(json.dumps({"ok": False, "error": "module_dir_invalid"}, ensure_ascii=False))
        return 2
    global DEFAULT_MODULE_DIR
    DEFAULT_MODULE_DIR = module_dir

    try:
        artifact = _artifact_facts(module_dir, REPO_ROOT)
    except Exception:  # noqa: BLE001 — 固定码
        artifact = {"error": "artifact_probe_failed"}
    report = _build_report(args, artifact, dry_run=args.dry_run)

    if args.dry_run:
        report["plan"] = _plan(args)
        # dry-run 的 ok 要求 SDS 版本/元数据三件齐，避免 None==None 假通过。
        report["ok"] = (
            artifact.get("pinned_version") == "0.5.3"
            and artifact.get("installed_version") == artifact.get("pinned_version")
            and artifact.get("dist_info_present") is True
        )
        _emit(report, args.report)
        return 0
    if not args.confirm_observe:
        report["plan"] = _plan(args)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print("缺少 --confirm-observe：未获授权，只打印计划。", file=sys.stderr)
        return 2
    if args.playlist_id <= 0:
        report["stop_reasons"].append("invalid_playlist_id")
        _emit(report, args.report)
        return 2

    # 进程内打开 producer 只读开关；不写 .env、不重启任何服务。
    os.environ[READONLY_ENV] = "1"
    _sandbox_guard(report)
    if not report["stop_reasons"]:
        try:
            _observe(args, report)
        except ObserveError as error:
            report["stop_reasons"].append(error.code)
        except Exception:  # noqa: BLE001 — 固定码
            report["stop_reasons"].append("observe_failed")
    report["ok"] = bool(report["ok"]) and not report["stop_reasons"]
    _emit(report, args.report)
    return 0 if report["ok"] else 1


def _emit(report: dict, report_path: "Path | None") -> None:
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    if report_path is None:
        return
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(text + "\n", encoding="utf-8")
    except OSError:
        print("报告写入失败（结果仍已在标准输出）。", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
