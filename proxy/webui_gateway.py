#!/usr/bin/env python3
"""把飞牛桌面网关的 Unix socket 原样转到本机 WebUI。

应用中心按 ui/config 的 gatewaySocket 连接
<应用目录>/fnmusic-ext.sock，把 https://<主机>:<桌面端口>/app/fnmusic-ext/
转到这个 socket。WebUI 自己监听 127.0.0.1:8774，并认识 /app/fnmusic-ext 前缀。

本模块还处理以下例外端点（都只在桌面网关链路里存在，直连 8774 不经过这里，
都要求网关注入的 X-Trim-Isadmin: true；其余请求一律原样转发，不动字节）：
- POST /app/fnmusic-ext/api/host-file——浏览器把 NAS 上选中的 .js 源脚本路径
  发来，由宿主侧（本进程，root）代读文件内容。
- POST /app/fnmusic-ext/api/fs-check——浏览器把储存目录（缓存/下载目录）路径
  发来，由宿主侧按真实落盘视角（root proxy）探测目录存在性与可写性。
- GET  /app/fnmusic-ext/api/manual-save/status——手动保存开关与目录/音质阶梯状态。
- POST /app/fnmusic-ext/api/manual-save/track——按 song_id 手动保存单曲；两部分都
  要求管理员声明与 x-fnmusic-save: 1，同一时刻只跑一个保存任务。
- GET  /app/fnmusic-ext/api/manual-save/search——按关键词搜索网易云曲目，
  供管理台「保存到 NAS」页使用（同样要求管理员声明与 x-fnmusic-save: 1）。
- /app/fnmusic-ext/api/manual-save/playlist/{playlists,snapshot,jobs,job,
  advance,cancel}——管理页整歌单保存：固定路径 + 查询/请求体参数，同一套
  严格 head/L4/开关校验；账本在 gateway 侧，推进每次只跑一首，执行锁与
  单曲端点串行（见 docs/MANUAL_SAVE_PLAYLIST_JOB.md）。
- /api/maintenance：仅转发已确认的清理/任务查询到固定私有 Unix socket，
  不持有业务状态，也不把未知维护请求交给普通 WebUI。

可信入口（G0-FNMEXT-TRUSTED-INGRESS）分六层，任何一层都不得替代另一层：
L1 socket 权限（默认 0600/0660，启动时重断言）→ L2 内核 peer uid 允许名单
（accept 后立即校验，失败不读字节/不起线程）→ L3 已读 head 的严格解析
（重复或畸形 identity 头、Content-Length/Transfer-Encoding 冲突、超限 →
400 固定码）→ L4 手动保存的单条规范 admin 声明 + userid 声明 → L5 既有
CSRF/enabled/body 上限 → L6 副作用。环境变量 FNMUSIC_GATEWAY_PEER_ALLOW /
FNMUSIC_GATEWAY_SOCKET_MODE 非法时拒绝启动（exit 2），不得静默退化。
"""
from __future__ import annotations

import json
import errno
import os
import re
import http.client
import signal
import socket
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from urllib.parse import unquote, urlsplit
from pathlib import Path

if __package__:
    from . import gateway_identity, gateway_observation, manual_save_playlist_jobs
    from . import manual_save_route
else:  # direct execution from the proxy source directory
    import gateway_identity
    import gateway_observation
    import manual_save_playlist_jobs
    import manual_save_route

UPSTREAM = ("127.0.0.1", 8774)
SOCK_NAME = "fnmusic-ext.sock"
PID_FILE = Path("/run/fnmusic-ext/webui-gateway.pid")
# create_connection 的 timeout 会留在套接字上，之后的 recv 也受它限制。
# 洛雪源校验要几十秒才有响应；沿用 5 秒会让桌面网关中途拆连接，nginx 回 502。
CONNECT_TIMEOUT = 5
RELAY_TIMEOUT = 180

# Trusted-channel policy (G0-FNMEXT-TRUSTED-INGRESS). Both variables are read
# once per startup; an invalid value refuses startup instead of degrading the
# socket to a world-writable mode. See docs/G0_TRUSTED_INGRESS.md.
GATEWAY_PEER_ALLOW_ENV = "FNMUSIC_GATEWAY_PEER_ALLOW"
GATEWAY_SOCKET_MODE_ENV = "FNMUSIC_GATEWAY_SOCKET_MODE"
CHANNEL_REJECT_LOG_INTERVAL = 60.0
_CHANNEL_REJECT_LOG_PREFIX = "fnmusic-webui-gateway channel-rejected"

HOST_FILE_PATHS = ("/app/fnmusic-ext/api/host-file", "/api/host-file")
HOST_FILE_MAX_BODY = 1 << 20          # 请求体上限 1MB（只装一个路径字符串）
HOST_FILE_MAX_BYTES = 9_000_000       # 与 lxmusic SCRIPT_MAX_BYTES 对齐
FS_CHECK_PATHS = ("/app/fnmusic-ext/api/fs-check", "/api/fs-check")
FS_CHECK_MAX_BODY = 1 << 20           # 同 host-file：只装一个路径字符串
MAINTENANCE_SOCKET = "/run/fnmusic-ext/maintenance.sock"
MAINTENANCE_ROOT = "/api/maintenance"
MAINTENANCE_MAX_HEADER = 16 * 1024
MAINTENANCE_MAX_BODY = 4096
MAINTENANCE_TIMEOUT = 35
ORGANIZE_PATH = MAINTENANCE_ROOT + "/organize"
ORGANIZE_LAYOUTS = frozenset(("flat", "artist", "album", "artist_album"))

MANUAL_SAVE_STATUS_PATHS = (
    "/app/fnmusic-ext/api/manual-save/status",
    "/api/manual-save/status",
)
MANUAL_SAVE_TRACK_PATHS = (
    "/app/fnmusic-ext/api/manual-save/track",
    "/api/manual-save/track",
)
MANUAL_SAVE_SEARCH_PATHS = (
    "/app/fnmusic-ext/api/manual-save/search",
    "/api/manual-save/search",
)
MANUAL_SAVE_MAX_HEADER = 16 * 1024
MANUAL_SAVE_MAX_BODY = 4096
MANUAL_SAVE_MAX_QUERY = 2048
MANUAL_SAVE_MAX_KEYWORD_CHARS = 120
MANUAL_SAVE_CSRF_HEADER = "x-fnmusic-save"
MANUAL_SAVE_CSRF_VALUE = "1"
# L4 authorization switch: require a syntactically valid X-Trim-Userid claim
# in addition to the canonical admin claim. Kept as a module constant so a
# future evidence round can change the requirement in one place.
MANUAL_SAVE_REQUIRE_USER_ID = True
# The engine's worst case (musicbox API + the 580 s download attempt budget +
# probe + full decode + tag) can exceed 600 s. The gateway must not return 504
# before the engine has spent its own fail-closed budget, so keep this above it.
MANUAL_SAVE_TIMEOUT = 1200
MANUAL_SAVE_SEARCH_TIMEOUT = 30.0
MANUAL_SAVE_BODY_TIMEOUT = 15
_MANUAL_SAVE_CRLF = bytes((13, 10))
_MANUAL_SAVE_HEAD_END = _MANUAL_SAVE_CRLF * 2
_MANUAL_SAVE_HEADER_NAME_RE = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
_MANUAL_SAVE_GATEWAY_MESSAGES = {
    "ok": "",
    "forbidden": "仅管理员可执行手动保存",
    "invalid_request": "请求无效",
    "invalid_keyword": "搜索关键词必须是 1-120 字符",
    "invalid_song_id": "song_id 必须是正整数",
    "disabled": "手动保存未启用",
    "busy": "已有保存任务进行中",
    "timeout": "保存超时",
    "quality_unverified": "音质响应无法核验",
    "quality_identity_mismatch": "无法确认音质响应所属曲目",
    "trial_only": "仅取得试听片段，未保存",
    "download_target_untrusted": "下载目标未通过安全校验",
    "internal": "内部错误",
}
_MANUAL_SAVE_HTTP_STATUS = {
    "conflict": 409,
    "busy": 409,
    "disabled": 403,
    "invalid_song_id": 400,
    "invalid_keyword": 400,
    "service_unavailable": 503,
    "staging_unavailable": 503,
    "publish_failed": 500,
    "internal": 500,
    "config_invalid": 500,
    "no_quality_available": 502,
    "quality_unverified": 502,
    "quality_identity_mismatch": 502,
    "trial_only": 502,
    "download_target_untrusted": 502,
    "download_failed": 502,
    "integrity_mismatch": 502,
    "invalid_audio": 502,
    "tag_failed": 500,
}
# Same server-token shape as the engine: a delivered quality is only ever the
# raw lower-case token the provider reported, never a translated label.
_MANUAL_SAVE_QUALITY_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
# Fixed redacted cover-error classes; only meaningful while cover_status == error.
_MANUAL_SAVE_COVER_ERRORS = frozenset({
    "host_untrusted",
    "url_invalid",
    "address_untrusted",
    "http_error",
    "payload_invalid",
    "decode_failed",
    "embed_failed",
    "unknown",
})

# ---------------------------------------------------------------------------
# Playlist save endpoints (contract FNMEXT-MANUAL-SAVE-PLAYLIST-JOB-20261101-01 §3)
# ---------------------------------------------------------------------------
# Fixed paths with query/body parameters (deliberately no /jobs/{id} template):
# the reviewed strict head parser matches exact targets only, so keeping the
# job id in a query/body parameter reuses it without relaxing any rule (§3.1).
MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/playlists",
    "/api/manual-save/playlist/playlists",
)
MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/snapshot",
    "/api/manual-save/playlist/snapshot",
)
MANUAL_SAVE_PLAYLIST_JOBS_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/jobs",
    "/api/manual-save/playlist/jobs",
)
MANUAL_SAVE_PLAYLIST_JOB_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/job",
    "/api/manual-save/playlist/job",
)
MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/advance",
    "/api/manual-save/playlist/advance",
)
MANUAL_SAVE_PLAYLIST_CANCEL_PATHS = (
    "/app/fnmusic-ext/api/manual-save/playlist/cancel",
    "/api/manual-save/playlist/cancel",
)
# One advance is one track; §7.3 forbids any budget above the single-track one.
MANUAL_SAVE_PLAYLIST_TIMEOUT = MANUAL_SAVE_TIMEOUT
# Bounded read budget for the producer/list calls (§8.3 item 2).
MANUAL_SAVE_PLAYLIST_READ_TIMEOUT = 120.0
# Query/cancel are ledger-only; they get a short worker budget.
MANUAL_SAVE_PLAYLIST_QUERY_TIMEOUT = 30.0
MANUAL_SAVE_PLAYLIST_MAX_POSITIONS = 1000
MANUAL_SAVE_PLAYLIST_MAX_PLAYLIST_ID = 0xFFFFFFFF
MANUAL_SAVE_PLAYLIST_MAX_LIST_ROWS = 100
MANUAL_SAVE_PLAYLIST_MAX_NAME_CHARS = 256
MANUAL_SAVE_PLAYLIST_MAX_JSON_BYTES = 1 << 20
MANUAL_SAVE_PLAYLIST_MAX_BASE_URL_CHARS = 2048
MANUAL_SAVE_PLAYLIST_JOB_ID_RE = re.compile(r"^[0-9a-f]{48}$")
MANUAL_SAVE_PLAYLIST_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
MANUAL_SAVE_PLAYLIST_IDEMPOTENCY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
MANUAL_SAVE_PLAYLIST_DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,9}$")
MANUAL_SAVE_PLAYLIST_REASON_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MANUAL_SAVE_PLAYLIST_SOURCE_ID_RE = re.compile(r"^[0-9]{1,128}$")
MANUAL_SAVE_PLAYLIST_MANIFEST_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
MANUAL_SAVE_PLAYLIST_ENABLED_KEY = "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED"
MANUAL_SAVE_JOBS_DB_KEY = "FNMUSIC_MANUAL_SAVE_JOBS_DB"
# §4.1 fixed artifact provenance (producer-side assertion; the gateway only
# checks the producer's reported version/flag, never the wheel digest).
MANUAL_SAVE_PLAYLIST_ARTIFACT_PROVIDER = "netease"
MANUAL_SAVE_PLAYLIST_ARTIFACT_PACKAGE = "NetEase-MusicBox"
MANUAL_SAVE_PLAYLIST_ARTIFACT_VERSION = "0.5.3"
MANUAL_SAVE_PLAYLIST_MANIFEST_SCHEMA_VERSION = 1
# §7.1 gateway-wide execution lock: the single-track endpoint and the playlist
# advance endpoint serialize on this exact object (acquired before any ledger
# claim or engine call, released in the worker's finally). Acquisition order is
# always gateway lock -> ledger/engine, never the reverse.
MANUAL_SAVE_PLAYLIST_LOCK = threading.Lock()
# §3.9.2 whitelist of producer error codes that may be echoed; anything else is
# dropped to "" (never echoed as free text).
MANUAL_SAVE_PLAYLIST_SOURCE_ERROR_CODES = frozenset({
    "SOURCE_READ_FAILED",
    "PLAYLIST_ID_MISMATCH",
    "POSITION_LIMIT_EXCEEDED",
    "SOURCE_LIMIT_REACHED",
    "TRACK_COUNT_MISMATCH",
    "INVALID_TRACK_ID",
    "SOURCE_SCHEMA_CHANGED",
    "ACCOUNT_CHANGED",
    "PLAYLIST_CHANGED",
    "SOURCE_VERSION_CHANGED",
    "TRACK_SEQUENCE_CHANGED",
    "UNSUPPORTED_SOURCE",
    "SOURCE_CHANGED_OR_RECHECK_FAILED",
})
# §6.2 position states (closed set; unknown persisted state renders as unknown).
MANUAL_SAVE_PLAYLIST_POSITION_STATES = frozenset({
    "queued",
    "running",
    "published",
    "published_cleanup_pending",
    "conflict",
    "cancelled",
    "rejected",
    "failed",
    "commit_unknown",
})
# §6.1 job states (closed set; unknown persisted state renders as unknown).
MANUAL_SAVE_PLAYLIST_TASK_STATES = frozenset({
    "queued",
    "running",
    "cancelling",
    "cancelled",
    "partial",
    "completed_with_issues",
    "completed_bytes_reported",
    "reconciliation_required",
})
_MANUAL_SAVE_PLAYLIST_REASON_FALLBACK = "未保存（原因未登记）"
# §6.2 position reason -> fixed Chinese text. Texts mirror the route message
# table verbatim; this table is the only renderer and never interpolates input.
_MANUAL_SAVE_PLAYLIST_REASON_MESSAGES = {
    "bytes_published": "已发布",
    "destination_conflict": "目标已存在同名文件，未覆盖",
    "cancelled_before_publish": "已取消（未执行）",
    "commit_unknown": "执行结果未知，需要人工对账",
    "stage_unavailable": "暂存不可用",
    "staging_failed": "暂存不可用",
    "cancel_check_failed": "取消检查失败（按已取消处理）",
    "disabled": "手动保存未启用",
    # §6.2 requires every overlapping text to match the route table verbatim;
    # the route (authoritative existing code) says "手动保存配置无效" for this
    # code, so the route text wins over the §6.2 table cell transcription.
    "config_invalid": "手动保存配置无效",
    "busy": "已有保存任务进行中",
    "service_unavailable": "音乐服务不可用",
    "no_quality_available": "没有可用的音质",
    "quality_unverified": "音质响应无法核验",
    "quality_identity_mismatch": "无法确认音质响应所属曲目",
    "trial_only": "仅取得试听片段，未保存",
    "download_target_untrusted": "下载目标未通过安全校验",
    "download_failed": "下载失败",
    "integrity_mismatch": "文件校验不一致",
    "invalid_audio": "音频文件无效",
    "tag_failed": "写入标签失败",
    "invalid_target": "输入无效，未执行",
}
# §3.9.1 fixed code -> fixed, non-interpolated Chinese message. An unregistered
# code is never passed through: both helpers fall back to ``internal``.
_MANUAL_SAVE_PLAYLIST_MESSAGES = {
    "ok": "",
    "invalid_request": "请求无效",
    "invalid_playlist_id": "歌单 ID 必须是正整数",
    "invalid_fingerprint": "快照指纹无效",
    "invalid_idempotency_key": "幂等键无效",
    "not_found": "任务不存在",
    "forbidden": "仅管理员可执行手动保存",
    "disabled": "手动保存未启用",
    "playlist_disabled": "整歌单保存未启用",
    "not_logged_in": "网易云账号未登录",
    "config_invalid": "手动保存配置无效",
    "playlist_unavailable": "音乐服务不可用",
    "playlist_unsupported": "该歌单不支持可验证快照",
    "playlist_too_large": "歌单成员超过 1000 个",
    "manifest_incomplete": "歌单成员清单不完整",
    "source_changed": "歌单在确认后发生变化，请重新读取",
    "source_artifact_mismatch": "音乐服务版本与固定证据不一致",
    "idempotency_conflict": "幂等键已用于不同清单",
    "capacity_exceeded": "任务账本容量已满",
    "ledger_unavailable": "任务账本不可用",
    "busy": "已有保存任务进行中",
    "completion_unknown": "任务结果写入失败，需人工核对",
    "stale_completion": "迟到的执行结果未覆盖记录",
    "timeout": "保存超时",
    "internal": "内部错误",
}
_MANUAL_SAVE_PLAYLIST_HTTP_STATUS = {
    "ok": 200,
    "invalid_request": 400,
    "invalid_playlist_id": 400,
    "invalid_fingerprint": 400,
    "invalid_idempotency_key": 400,
    "not_found": 404,
    "forbidden": 403,
    "disabled": 403,
    "playlist_disabled": 403,
    "not_logged_in": 403,
    "config_invalid": 500,
    "playlist_unavailable": 502,
    "playlist_unsupported": 409,
    "playlist_too_large": 409,
    "manifest_incomplete": 409,
    "source_changed": 409,
    "source_artifact_mismatch": 500,
    "idempotency_conflict": 409,
    "capacity_exceeded": 409,
    "ledger_unavailable": 503,
    "busy": 409,
    "completion_unknown": 500,
    "stale_completion": 409,
    "timeout": 504,
    "internal": 500,
}

# L3 boundary: fixed rejection classes for an ambiguous request head. Only the
# stable class name is logged or returned; header names/values are never echoed.
HEAD_REJECT_FRAMING = "invalid_framing"
HEAD_REJECT_IDENTITY = "invalid_identity_claim"
_HEAD_REJECT_MESSAGES = {
    HEAD_REJECT_FRAMING: "请求框架无效",
    HEAD_REJECT_IDENTITY: "身份声明无效",
}
# parse_untrusted_identity() reason codes that describe framing or general head
# validity; every other parsing code is an identity-claim problem.
_HEAD_FRAMING_REASON_CODES = frozenset({
    "INVALID_HEADER_INPUT",
    "INVALID_HEADER_NAME",
    "INVALID_HEADER_VALUE",
    "TOO_MANY_HEADERS",
    "HEADER_BYTES_LIMIT",
    "DUPLICATE_CONTENT_LENGTH",
    "INVALID_CONTENT_LENGTH",
    "DUPLICATE_TRANSFER_ENCODING",
    "CONTENT_LENGTH_TRANSFER_ENCODING_CONFLICT",
})
# Peer-credential failures are logged as one of two documented classes only.
_PEER_CREDENTIAL_UNSUPPORTED_CODES = frozenset({
    "UNSUPPORTED_PLATFORM",
    "UNSUPPORTED_SOCKET_FAMILY",
    "UNSUPPORTED_SOCKET_TYPE",
    "PEER_CREDENTIALS_UNSUPPORTED",
})


def socket_path_for(base: Path) -> Path:
    """fpk 布局是 <应用目录>/repo。socket 必须放在应用目录下，桌面网关才找得到。"""
    parent = base.resolve().parent
    if (parent / "ui").is_dir() and base.name == "repo":
        return parent / SOCK_NAME
    run = Path("/run/fnmusic-ext")
    run.mkdir(parents=True, exist_ok=True)
    return run / SOCK_NAME


def _relay(src: socket.socket, dst: socket.socket, prefix: bytes = b"") -> None:
    try:
        if prefix:
            dst.sendall(prefix)
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for sock in (src, dst):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _read_request_head(client: socket.socket) -> "tuple[bytes, dict[str, str]] | None":
    """读到 HTTP 头部结束；返回 (头部原始字节, 解析出的头)。超时/格式异常返回 None。"""
    buf = b""
    deadline = time.monotonic() + CONNECT_TIMEOUT
    try:
        while b"\r\n\r\n" not in buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            client.settimeout(remaining)
            if len(buf) > 65536:
                return None
            chunk = client.recv(8192)
            if not chunk:
                return None
            buf += chunk
    except OSError:
        return None
    head, _, rest = buf.partition(b"\r\n\r\n")
    headers: dict[str, str] = {}
    lines = head.decode("latin-1", "replace").split("\r\n")
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()
    if rest:
        headers["__rest__"] = rest.decode("latin-1", "replace")
    headers["__raw__"] = head.decode("latin-1", "replace")
    return head + b"\r\n\r\n" + rest, headers


def _header_pairs_from_raw_head(head_raw: bytes) -> "list[tuple[bytes, bytes]] | None":
    """Split an already-read head into raw (name, value) pairs for strict parsing.

    A missing head terminator, a header line without a colon, or an empty
    request line yields ``None``. Values keep their original OWS so the shared
    parser, not this splitter, owns every trimming rule.
    """
    header, separator, _body = head_raw.partition(b"\r\n\r\n")
    if not separator:
        return None
    lines = header.split(b"\r\n")
    if not lines or not lines[0]:
        return None
    pairs: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        if not colon:
            return None
        pairs.append((name, value))
    return pairs


def _identity_claims_from_raw_head(head_raw: bytes):
    """Parse the strict identity/framing claims of an already-read head.

    Returns ``None`` when the head is not a well-formed strict request head;
    a successful result is still only an untrusted claim set.
    """
    pairs = _header_pairs_from_raw_head(head_raw)
    if pairs is None:
        return None
    try:
        return gateway_identity.parse_untrusted_identity(pairs)
    except gateway_identity.HeaderClaimsError:
        return None


def _validate_head(head_raw: bytes) -> "str | None":
    """L3 boundary check for the already-read request head.

    Returns a fixed rejection class (``invalid_framing`` or
    ``invalid_identity_claim``) and never inspects the body. It runs on the
    forwarded path before ``create_connection``: local privileged handlers are
    dispatched earlier and own their documented strict-parse responses, but
    they reject every ambiguous head they share with this check before any
    body, engine, filesystem, or upstream work.
    """
    pairs = _header_pairs_from_raw_head(head_raw)
    if pairs is None:
        return HEAD_REJECT_FRAMING
    try:
        gateway_identity.parse_untrusted_identity(pairs)
    except gateway_identity.HeaderClaimsError as exc:
        if exc.reason_code in _HEAD_FRAMING_REASON_CODES:
            return HEAD_REJECT_FRAMING
        return HEAD_REJECT_IDENTITY
    return None


def _reject_head(client: socket.socket, code: str) -> None:
    """Send the fixed 400 for a rejected head and close the connection."""
    try:
        client.sendall(_json_response(400, {
            "ok": False,
            "code": code,
            "message": _HEAD_REJECT_MESSAGES[code],
        }))
    except OSError:
        pass
    finally:
        _close_quietly(client)


def _read_full_body(client: socket.socket, headers: dict[str, str], head_raw: bytes,
                    max_body: int = HOST_FILE_MAX_BODY) -> "bytes | None":
    """按 Content-Length 收齐 POST 体（小请求：仅路径字符串）。"""
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        return None
    if length < 0 or length > max_body:
        return None
    head_end = head_raw.find(b"\r\n\r\n") + 4
    body = head_raw[head_end:]
    while len(body) < length:
        chunk = client.recv(65536)
        if not chunk:
            return None
        body += chunk
    return body[:length]


def _http_response(status: int, reason: str, payload: bytes, content_type: str) -> bytes:
    head = (
        f"HTTP/1.1 {status} {reason}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(payload)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("latin-1")
    return head + payload


def _json_response(status: int, obj: dict) -> bytes:
    return _http_response(status, "OK" if status == 200 else "Error",
                          json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")


def _admin_claim_from_raw_head(head_raw: bytes) -> bool:
    """Return whether one canonical admin claim exists in a well-formed head.

    The shared parser deliberately returns an untrusted claim, not an
    authorization decision. This only removes duplicate/malformed-header
    order ambiguity before these two privileged gateway handlers act.
    """
    claims = _identity_claims_from_raw_head(head_raw)
    return claims is not None and claims.admin_claim is True


def handle_host_file(client: socket.socket, headers: dict[str, str], head_raw: bytes) -> None:
    """POST /api/host-file：校验 admin + .js 路径，读文件内容回 JSON {script}。"""
    try:
        if not _admin_claim_from_raw_head(head_raw):
            client.sendall(_json_response(403, {"ok": False, "error": "仅管理员可读取主机文件"}))
            return
        body = _read_full_body(client, headers, head_raw)
        if body is None:
            client.sendall(_json_response(400, {"ok": False, "error": "请求体无效"}))
            return
        try:
            req = json.loads(body.decode("utf-8"))
            path = str(req.get("path") or "")
        except Exception:  # noqa: BLE001
            client.sendall(_json_response(400, {"ok": False, "error": "请求体必须是 JSON"}))
            return
        target = Path(path)
        if not path.startswith("/") or ".." in target.parts:
            client.sendall(_json_response(400, {"ok": False, "error": "路径必须是绝对路径且不含 .."}))
            return
        if not path.lower().endswith(".js"):
            client.sendall(_json_response(400, {"ok": False, "error": "只支持读取 .js 后缀文件"}))
            return
        try:
            st = target.stat()
        except OSError:
            client.sendall(_json_response(404, {"ok": False, "error": f"文件不存在: {path}"}))
            return
        if not os.path.isfile(str(target)) or st.st_size > HOST_FILE_MAX_BYTES:
            client.sendall(_json_response(400, {"ok": False, "error": "不是常规文件或超过 9MB 上限"}))
            return
        try:
            script = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            client.sendall(_json_response(400, {"ok": False, "error": f"读取失败: {exc}"}))
            return
        client.sendall(_json_response(200, {"ok": True, "script": script}))
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass


def _probe_dir_writable(path: str) -> bool:
    """写入试探：os.access 对只读挂载等场景可能误报，落一个临时文件再删才算数。"""
    probe = os.path.join(path, f".fnmusic-fscheck-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    fd = None
    created = False
    try:
        fd = os.open(probe, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
        created = True
        close_fd, fd = fd, None
        os.close(close_fd)
        return True
    except OSError:
        return False
    finally:
        if fd is not None:
            close_fd, fd = fd, None
            try:
                os.close(close_fd)
            except OSError:
                pass
        if created:
            try:
                os.unlink(probe)
            except OSError:
                pass


def handle_fs_check(client: socket.socket, headers: dict[str, str], head_raw: bytes) -> None:
    """POST /api/fs-check：校验 admin + 目录路径，按宿主 root 视角（真实落盘方是
    root 跑的 proxy）探测目录存在性与可写性。无副作用：不创建目录，临时探测
    文件即落即删。目录不存在时用最近存在祖先目录的可写性代表"能否创建"。"""
    try:
        if not _admin_claim_from_raw_head(head_raw):
            client.sendall(_json_response(403, {"ok": False, "error": "仅管理员可校验目录"}))
            return
        body = _read_full_body(client, headers, head_raw, FS_CHECK_MAX_BODY)
        if body is None:
            client.sendall(_json_response(400, {"ok": False, "error": "请求体无效"}))
            return
        try:
            req = json.loads(body.decode("utf-8"))
            path = str(req.get("path") or "")
        except Exception:  # noqa: BLE001
            client.sendall(_json_response(400, {"ok": False, "error": "请求体必须是 JSON"}))
            return
        if any(ch in path for ch in "\r\n\x00"):
            client.sendall(_json_response(400, {"ok": False, "error": "路径不能包含换行或空字符"}))
            return
        target = Path(path)
        if not path.startswith("/") or ".." in target.parts:
            client.sendall(_json_response(400, {"ok": False, "error": "路径必须是绝对路径且不含 .."}))
            return
        exists = writable = False
        ancestor = target
        while True:
            try:
                ancestor.stat()
            except FileNotFoundError:
                # 只能跳过真正缺失的路径；悬空链接本身占位，mkdir 无法替换它。
                if ancestor.is_symlink() or ancestor.parent == ancestor:
                    break
                ancestor = ancestor.parent
                continue
            except OSError:
                # ENOTDIR（文件祖先）、EACCES 等不能当作缺失后继续向上放行。
                break
            if ancestor.is_dir():
                exists = ancestor == target
                writable = _probe_dir_writable(str(ancestor))
            # 首个存在节点不是目录时同样停止，不能跳过普通文件。
            break
        client.sendall(_json_response(200, {
            "ok": True, "path": path, "exists": exists, "writable": writable,
        }))
    except OSError:
        pass
    finally:
        try:
            client.close()
        except OSError:
            pass


def _manual_save_message(code: str) -> str:
    return _MANUAL_SAVE_GATEWAY_MESSAGES.get(code, _MANUAL_SAVE_GATEWAY_MESSAGES["internal"])


def _manual_save_error(code: str, message: str | None = None) -> dict:
    return {
        "ok": False,
        "code": code,
        "message": _manual_save_message(code) if message is None else message,
        "data": {},
    }


def _request_method_and_target(head_raw: bytes) -> "tuple[str, str] | None":
    try:
        request_line = head_raw.split(_MANUAL_SAVE_CRLF, 1)[0].decode("latin-1", "replace")
    except Exception:  # noqa: BLE001
        return None
    parts = request_line.split(" ")
    if len(parts) != 3:
        return None
    return parts[0], parts[1]


def _is_manual_save_status_request(head_raw: bytes) -> bool:
    pair = _request_method_and_target(head_raw)
    return pair is not None and pair[0] == "GET" and pair[1] in MANUAL_SAVE_STATUS_PATHS


def _is_manual_save_track_request(head_raw: bytes) -> bool:
    pair = _request_method_and_target(head_raw)
    return pair is not None and pair[0] == "POST" and pair[1] in MANUAL_SAVE_TRACK_PATHS


def _is_manual_save_search_request(head_raw: bytes) -> bool:
    pair = _request_method_and_target(head_raw)
    if pair is None or pair[0] != "GET":
        return False
    return pair[1].partition("?")[0] in MANUAL_SAVE_SEARCH_PATHS


def _parse_manual_save_head(head_raw: bytes, *, method: str,
                            targets: "tuple[str, ...]", allow_query: bool = False):
    """Strict single-request-line/header parsing for the manual-save endpoints."""
    head, separator, rest = head_raw.partition(_MANUAL_SAVE_HEAD_END)
    if not separator or len(head) + 4 > MANUAL_SAVE_MAX_HEADER:
        return None
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        return None
    lines = text.split(_MANUAL_SAVE_CRLF.decode("ascii"))
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    target = parts[1]
    path = target.partition("?")[0] if allow_query else target
    if parts[0] != method or path not in targets:
        return None
    if parts[2] not in ("HTTP/1.0", "HTTP/1.1"):
        return None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            return None
        name, colon, value = line.partition(":")
        if not colon or _MANUAL_SAVE_HEADER_NAME_RE.fullmatch(name) is None:
            return None
        key = name.lower()
        if key in headers:
            return None
        headers[key] = value.strip()
    if "transfer-encoding" in headers:
        return None
    content_length = headers.get("content-length")
    if content_length is not None and re.fullmatch(r"[0-9]{1,10}", content_length) is None:
        return None
    return headers, rest, target


def _manual_save_authorized(head_raw: bytes, headers: dict[str, str]) -> bool:
    """L4: CSRF header + canonical admin claim (+ user-id claim when required).

    The admin and user-id values remain gateway-injected claims, never an
    authentication proof; this only enforces one canonical claim of each kind
    and the explicit user-id presence switch below.
    """
    if headers.get(MANUAL_SAVE_CSRF_HEADER) != MANUAL_SAVE_CSRF_VALUE:
        return False
    claims = _identity_claims_from_raw_head(head_raw)
    if claims is None or claims.admin_claim is not True:
        return False
    if not MANUAL_SAVE_REQUIRE_USER_ID:
        return True
    try:
        gateway_identity.require_user_id(claims)
    except gateway_identity.TrustedChannelError:
        return False
    return True


def _read_manual_save_body(client: socket.socket, headers: dict[str, str],
                           rest: bytes) -> "bytes | None":
    raw_length = headers.get("content-length")
    length = int(raw_length) if raw_length is not None else 0
    if length > MANUAL_SAVE_MAX_BODY:
        return None
    body = bytes(rest[:length])
    deadline = time.monotonic() + MANUAL_SAVE_BODY_TIMEOUT
    try:
        while len(body) < length:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            client.settimeout(remaining)
            chunk = client.recv(length - len(body))
            if not chunk:
                return None
            body += chunk
    except OSError:
        return None
    return body


def _engine_status(engine) -> "dict | None":
    try:
        result = engine.status()
    except Exception:  # noqa: BLE001 — fixed JSON below, never the exception text
        return None
    return result if isinstance(result, dict) else None


def _public_status_data(data: object) -> dict:
    source = data if isinstance(data, dict) else {}
    target_dir = source.get("target_dir")
    staging_dir = source.get("staging_dir")
    quality_order = source.get("quality_order")
    max_bytes = source.get("max_bytes")
    return {
        "enabled": source.get("enabled") is True,
        "target_dir": target_dir if type(target_dir) is str else "",
        "staging_dir": staging_dir if type(staging_dir) is str else "",
        "quality_order": (
            [item for item in quality_order if type(item) is str]
            if isinstance(quality_order, list) else []
        ),
        "max_bytes": (
            max_bytes
            if type(max_bytes) is int and not isinstance(max_bytes, bool) else 0
        ),
    }


def _manual_save_int(value: object) -> int:
    return value if type(value) is int and not isinstance(value, bool) else 0


def _sanitize_manual_save_data(data: object) -> dict:
    """Whitelist the documented result fields; unknown keys never reach the wire."""
    source = data if isinstance(data, dict) else {}
    text_fields = (
        "title", "artist", "album", "quality_requested", "quality_actual",
        "quality_actual_status", "source_type", "format", "basename", "target_dir",
        "cover_error",
    )
    sanitized = {
        name: (source.get(name) if type(source.get(name)) is str else "")
        for name in text_fields
    }
    quality_actual_status = sanitized["quality_actual_status"]
    if quality_actual_status not in ("reported", "unknown"):
        quality_actual_status = "unknown"
    if (
        quality_actual_status == "reported"
        and _MANUAL_SAVE_QUALITY_TOKEN_RE.fullmatch(sanitized["quality_actual"]) is None
    ):
        # "reported" without a legal server token is not a verified label.
        quality_actual_status = "unknown"
    if quality_actual_status == "unknown":
        sanitized["quality_actual"] = ""
    sanitized["quality_actual_status"] = quality_actual_status
    sanitized["quality_fallback"] = source.get("quality_fallback") is True
    status_value = source.get("status")
    sanitized["status"] = status_value if status_value in ("published", "conflict") else ""
    sanitized["song_id"] = _manual_save_int(source.get("song_id"))
    sanitized["size"] = _manual_save_int(source.get("size"))
    md5 = source.get("md5")
    sanitized["md5"] = (
        md5 if type(md5) is str and re.fullmatch(r"[0-9a-fA-F]{32}", md5) else ""
    )
    sanitized["published_size"] = _manual_save_int(source.get("published_size"))
    published_md5 = source.get("published_md5")
    sanitized["published_md5"] = (
        published_md5
        if type(published_md5) is str
        and re.fullmatch(r"[0-9a-fA-F]{32}", published_md5)
        else ""
    )
    sanitized["tagged"] = source.get("tagged") is True
    sanitized["cover_embedded"] = source.get("cover_embedded") is True
    for name in ("lyricist", "composer"):
        value = source.get(name)
        sanitized[name] = value[:512] if type(value) is str else ""
    status_values = {
        "title_status": {"tagged", "unavailable"},
        "artist_status": {"tagged", "unavailable"},
        "album_status": {"tagged", "unavailable"},
        "lyricist_status": {"tagged", "unavailable"},
        "composer_status": {"tagged", "unavailable"},
        "cover_status": {"embedded", "preserved", "unavailable", "unsupported", "error", "not_published"},
        "lyric_status": {"published", "unavailable", "error", "conflict", "not_attempted", "unknown"},
    }
    for name, allowed in status_values.items():
        value = source.get(name)
        sanitized[name] = value if type(value) is str and value in allowed else "unavailable"
    cover_error = sanitized["cover_error"]
    if sanitized["cover_status"] != "error":
        cover_error = ""
    elif cover_error not in _MANUAL_SAVE_COVER_ERRORS:
        cover_error = "unknown"
    sanitized["cover_error"] = cover_error
    return sanitized


def _sanitize_manual_save_result(result: object) -> dict:
    if not isinstance(result, dict):
        return _manual_save_error("internal")
    ok = result.get("ok") is True
    code = result.get("code")
    if type(code) is not str or not code:
        code = "ok" if ok else "internal"
    message = result.get("message")
    if type(message) is not str:
        message = _manual_save_message(code)
    return {
        "ok": ok,
        "code": code,
        "message": message,
        "data": _sanitize_manual_save_data(result.get("data")),
    }


def _manual_save_http_status(code: str, ok: bool) -> int:
    if ok:
        return 200
    return _MANUAL_SAVE_HTTP_STATUS.get(code, 500)


def _run_manual_save(engine, song_id: int, timeout: float):
    """Run one track save in a worker thread under the gateway execution lock.

    Contract §7.1: the single-track worker must acquire the same module-level
    ``MANUAL_SAVE_PLAYLIST_LOCK`` as the playlist advance endpoint (non-blocking,
    before any engine call). A lost race returns ``busy`` (409) without calling
    the engine, so the playlist endpoint can never claim a position while a
    single-track save is in flight.
    """
    holder: dict[str, object] = {}

    def worker() -> None:
        if not MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False):
            holder["value"] = _manual_save_error("busy")
            return
        try:
            try:
                holder["value"] = engine.save_track(song_id)
            except BaseException:  # noqa: BLE001 — worker errors become fixed JSON
                holder["value"] = _manual_save_error("internal")
        finally:
            MANUAL_SAVE_PLAYLIST_LOCK.release()

    thread = threading.Thread(target=worker, name="fnmusic-manual-save", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    return holder.get("value")


def _close_quietly(client: socket.socket) -> None:
    try:
        client.close()
    except OSError:
        pass


def handle_manual_save_status(client: socket.socket, head_raw: bytes, *, engine=None) -> None:
    """GET manual-save status: strict admin claim + x-fnmusic-save: 1."""
    try:
        active_engine = manual_save_route if engine is None else engine
        parsed = _parse_manual_save_head(
            head_raw, method="GET", targets=MANUAL_SAVE_STATUS_PATHS,
        )
        if parsed is None:
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        headers, rest, _target = parsed
        if rest or headers.get("content-length", "0") != "0":
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        if not _manual_save_authorized(head_raw, headers):
            client.sendall(_json_response(403, _manual_save_error("forbidden")))
            return
        engine_result = _engine_status(active_engine)
        if engine_result is None:
            client.sendall(_json_response(500, _manual_save_error("internal")))
            return
        if engine_result.get("ok") is not True:
            client.sendall(_json_response(403, {
                "ok": False,
                "code": "disabled",
                "message": _manual_save_message("disabled"),
                "data": {"enabled": False},
            }))
            return
        client.sendall(_json_response(200, {
            "ok": True,
            "code": "ok",
            "message": "",
            "data": _public_status_data(engine_result.get("data")),
        }))
    except OSError:
        pass
    finally:
        _close_quietly(client)


def handle_manual_save_track(client: socket.socket, head_raw: bytes, *,
                             engine=None, timeout: float | None = None) -> None:
    """POST manual-save track: strict parsing, CSRF header, worker-thread timeout."""
    try:
        active_engine = manual_save_route if engine is None else engine
        parsed = _parse_manual_save_head(
            head_raw, method="POST", targets=MANUAL_SAVE_TRACK_PATHS,
        )
        if parsed is None:
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        headers, rest, _target = parsed
        if not _manual_save_authorized(head_raw, headers):
            client.sendall(_json_response(403, _manual_save_error("forbidden")))
            return
        engine_result = _engine_status(active_engine)
        if engine_result is None:
            client.sendall(_json_response(500, _manual_save_error("internal")))
            return
        if engine_result.get("ok") is not True:
            client.sendall(_json_response(403, {
                "ok": False,
                "code": "disabled",
                "message": _manual_save_message("disabled"),
                "data": {"enabled": False},
            }))
            return
        if "content-length" not in headers:
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        body = _read_manual_save_body(client, headers, rest)
        if body is None:
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        try:
            request = json.loads(body.decode("utf-8"))
        except (UnicodeError, ValueError):
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        if not isinstance(request, dict):
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        song_id = request.get("song_id")
        if type(song_id) is not int or isinstance(song_id, bool) or song_id <= 0:
            client.sendall(_json_response(400, _manual_save_error("invalid_song_id")))
            return
        result = _run_manual_save(
            active_engine, song_id,
            MANUAL_SAVE_TIMEOUT if timeout is None else timeout,
        )
        if result is None:
            client.sendall(_json_response(504, _manual_save_error("timeout")))
            return
        payload = _sanitize_manual_save_result(result)
        client.sendall(_json_response(
            _manual_save_http_status(payload["code"], payload["ok"]), payload,
        ))
    except OSError:
        pass
    finally:
        _close_quietly(client)


def _parse_manual_save_search_keyword(target: str) -> "tuple[str, str]":
    """Return (keyword, error_code) after strict single-keyword query parsing."""
    _path, separator, query = target.partition("?")
    if not separator or not query:
        return "", "invalid_keyword"
    if len(query) > MANUAL_SAVE_MAX_QUERY:
        return "", "invalid_request"
    pairs: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            return "", "invalid_request"
        name, equals, value = part.partition("=")
        if not equals:
            return "", "invalid_request"
        try:
            name = unquote(name, errors="strict")
            value = unquote(value, errors="strict")
        except (UnicodeError, ValueError):
            return "", "invalid_request"
        pairs.append((name, value))
    if any(name != "keyword" for name, _value in pairs):
        return "", "invalid_request"
    if len(pairs) != 1:
        return "", "invalid_request"
    keyword = pairs[0][1]
    if not keyword.strip() or len(keyword) > MANUAL_SAVE_MAX_KEYWORD_CHARS:
        return "", "invalid_keyword"
    return keyword, ""


def _sanitize_manual_save_search_items(items: object) -> list[dict]:
    sanitized: list[dict] = []
    if not isinstance(items, list):
        return sanitized
    for item in items:
        if not isinstance(item, dict):
            continue
        song_id = item.get("song_id")
        if type(song_id) is not int or isinstance(song_id, bool) or song_id <= 0:
            continue
        sanitized_item = {"song_id": song_id}
        for name in ("title", "artist", "album", "quality"):
            value = item.get(name)
            sanitized_item[name] = value if type(value) is str else ""
        duration = item.get("duration")
        sanitized_item["duration"] = (
            duration
            if type(duration) is int and not isinstance(duration, bool) and duration >= 0
            else 0
        )
        sanitized.append(sanitized_item)
    return sanitized


def _sanitize_manual_save_search_result(result: object) -> dict:
    if not isinstance(result, dict):
        return _manual_save_error("internal")
    ok = result.get("ok") is True
    code = result.get("code")
    if type(code) is not str or not code:
        code = "ok" if ok else "internal"
    message = result.get("message")
    if type(message) is not str:
        message = _manual_save_message(code)
    if not ok:
        return {"ok": False, "code": code, "message": message, "data": {}}
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    keyword = data.get("keyword")
    limit = data.get("limit")
    return {
        "ok": True,
        "code": code,
        "message": message,
        "data": {
            "items": _sanitize_manual_save_search_items(data.get("items")),
            "keyword": keyword if type(keyword) is str else "",
            "limit": limit if type(limit) is int and not isinstance(limit, bool) else 0,
        },
    }


def _run_manual_save_search(engine, keyword: str, timeout: float):
    holder: dict[str, object] = {}

    def worker() -> None:
        try:
            holder["value"] = engine.search_tracks(keyword)
        except BaseException:  # noqa: BLE001 — worker errors become fixed JSON
            holder["value"] = _manual_save_error("internal")

    thread = threading.Thread(target=worker, name="fnmusic-manual-save-search", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    return holder.get("value")


def handle_manual_save_search(client: socket.socket, head_raw: bytes, *,
                              engine=None, timeout: float | None = None) -> None:
    """GET manual-save search: strict parsing, CSRF header, worker-thread timeout."""
    try:
        active_engine = manual_save_route if engine is None else engine
        parsed = _parse_manual_save_head(
            head_raw, method="GET", targets=MANUAL_SAVE_SEARCH_PATHS, allow_query=True,
        )
        if parsed is None:
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        headers, rest, target = parsed
        if rest or headers.get("content-length", "0") != "0":
            client.sendall(_json_response(400, _manual_save_error("invalid_request")))
            return
        if not _manual_save_authorized(head_raw, headers):
            client.sendall(_json_response(403, _manual_save_error("forbidden")))
            return
        engine_result = _engine_status(active_engine)
        if engine_result is None:
            client.sendall(_json_response(500, _manual_save_error("internal")))
            return
        if engine_result.get("ok") is not True:
            client.sendall(_json_response(403, {
                "ok": False,
                "code": "disabled",
                "message": _manual_save_message("disabled"),
                "data": {"enabled": False},
            }))
            return
        keyword, query_error = _parse_manual_save_search_keyword(target)
        if query_error:
            client.sendall(_json_response(400, _manual_save_error(query_error)))
            return
        result = _run_manual_save_search(
            active_engine, keyword,
            MANUAL_SAVE_SEARCH_TIMEOUT if timeout is None else timeout,
        )
        if result is None:
            client.sendall(_json_response(504, _manual_save_error("timeout")))
            return
        payload = _sanitize_manual_save_search_result(result)
        client.sendall(_json_response(
            _manual_save_http_status(payload["code"], payload["ok"]), payload,
        ))
    except OSError:
        pass
    finally:
        _close_quietly(client)


# ---------------------------------------------------------------------------
# Playlist save helpers (contract §3, §4.3.1, §7.1)
# ---------------------------------------------------------------------------

MANUAL_SAVE_PLAYLIST_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
# Producer/transport/logic exception code -> reviewed endpoint code (§3.9.1).
# Anything absent from this table and from the HTTP status table collapses to
# ``internal``; no exception text or unknown code is ever echoed (§3.9.1).
_MANUAL_SAVE_PLAYLIST_EXCEPTION_CODE_ALIASES = {
    "producer_not_loopback": "config_invalid",
    "producer_config_invalid": "config_invalid",
    "producer_timeout": "timeout",
    "producer_unreachable": "playlist_unavailable",
    "producer_http_status": "playlist_unavailable",
    "producer_response_too_large": "playlist_unavailable",
    "producer_malformed_response": "playlist_unavailable",
    "producer_unexpected_url": "playlist_unavailable",
    "manifest_disabled": "config_invalid",
    # A gateway-built manifest that the ledger rejects is a programming error:
    # the 500 path never exposes the validation detail (§3.4 step 9).
    "invalid_request": "internal",
    "invalid_actor": "forbidden",
}


class _ManualSavePlaylistDeps:
    """Resolved playlist services; injected in tests, built from .env in prod."""

    __slots__ = ("service", "producer", "enabled")

    def __init__(self, service=None, producer=None, enabled=True):
        self.service = service
        self.producer = producer
        self.enabled = enabled is True


def _manual_save_playlist_message(code: object) -> str:
    if type(code) is str and code in _MANUAL_SAVE_PLAYLIST_MESSAGES:
        return _MANUAL_SAVE_PLAYLIST_MESSAGES[code]
    return _MANUAL_SAVE_PLAYLIST_MESSAGES["internal"]


def _manual_save_playlist_reason_message(reason: object) -> str:
    """Fixed Chinese text for one persisted ledger reason (§6.2)."""
    if reason is None:
        return ""
    if type(reason) is str and reason in _MANUAL_SAVE_PLAYLIST_REASON_MESSAGES:
        return _MANUAL_SAVE_PLAYLIST_REASON_MESSAGES[reason]
    return _MANUAL_SAVE_PLAYLIST_REASON_FALLBACK


def _manual_save_playlist_error(code: str, data: "dict | None" = None) -> dict:
    return {
        "ok": False,
        "code": code,
        "message": _manual_save_playlist_message(code),
        "data": {} if data is None else data,
    }


def _manual_save_playlist_result(data: dict) -> dict:
    return {"ok": True, "code": "ok", "message": "", "data": data}


def _manual_save_playlist_http_status(code: str, ok: bool) -> int:
    if ok:
        return 200
    return _MANUAL_SAVE_PLAYLIST_HTTP_STATUS.get(code, 500)


def _send_manual_save_playlist(client: socket.socket, status: int, payload: dict) -> None:
    try:
        client.sendall(_json_response(status, payload))
    except OSError:
        pass


def _manual_save_playlist_send_result(client: socket.socket, result: object) -> None:
    """Send one fixed envelope; unregistered codes/keys can never reach the wire."""
    if not isinstance(result, Mapping):
        result = _manual_save_playlist_error("internal")
    ok = result.get("ok") is True
    code = result.get("code")
    if ok and code != "ok":
        # An "ok" result must carry the fixed success code.
        ok, code = False, "internal"
    if not ok and (type(code) is not str or code not in _MANUAL_SAVE_PLAYLIST_HTTP_STATUS):
        code = "internal"
    if not ok and code == "ok":
        code = "internal"
    data = result.get("data")
    if not isinstance(data, dict):
        data = {}
    _send_manual_save_playlist(client, _manual_save_playlist_http_status(code, ok), {
        "ok": ok,
        "code": code,
        "message": _manual_save_playlist_message(code),
        "data": data,
    })


def _manual_save_playlist_code_for_exception(exc: BaseException) -> str:
    code = getattr(exc, "code", None)
    if type(code) is not str:
        return "internal"
    alias = _MANUAL_SAVE_PLAYLIST_EXCEPTION_CODE_ALIASES.get(code)
    if alias is not None:
        return alias
    if code in _MANUAL_SAVE_PLAYLIST_HTTP_STATUS:
        return code
    return "internal"


def _run_manual_save_playlist_worker(operation, timeout: float):
    """Run one fixed-code operation in a worker; ``None`` means the join timed out.

    Contract §7.3: a 504 is not a cancellation. The worker keeps its execution
    lock (when it took one) until the operation really finishes, exactly like
    the single-track worker.
    """
    holder: dict[str, object] = {}

    def worker() -> None:
        try:
            holder["value"] = ("ok", operation())
        except BaseException as exc:  # noqa: BLE001 — fixed code only, no text
            holder["value"] = ("error", _manual_save_playlist_code_for_exception(exc))

    thread = threading.Thread(
        target=worker, name="fnmusic-manual-save-playlist", daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return None
    return holder.get("value")


def _manual_save_playlist_run(client: socket.socket, operation, timeout: float) -> None:
    outcome = _run_manual_save_playlist_worker(operation, timeout)
    if outcome is None:
        _send_manual_save_playlist(
            client, 504, _manual_save_playlist_error("timeout"))
        return
    kind, value = outcome
    if kind == "error":
        code = value
        _send_manual_save_playlist(
            client,
            _manual_save_playlist_http_status(code, False),
            _manual_save_playlist_error(code),
        )
        return
    _manual_save_playlist_send_result(client, value)


def _manual_save_playlist_actor_scope(head_raw: bytes, headers: dict) -> "str | None":
    """L4 authorization + actor scope from the canonical, already-checked claim.

    Contract §2.4/§8.3: the scope is derived only from the L4-validated user-id
    claim. No request body/query field can influence it (callers use closed key
    sets, so an identity field there is ``invalid_request``).
    """
    if not _manual_save_authorized(head_raw, headers):
        return None
    claims = _identity_claims_from_raw_head(head_raw)
    if claims is None or claims.user_id_claim is None:
        return None
    try:
        claim_text = claims.user_id_claim.decode("ascii")
    except UnicodeDecodeError:
        return None
    try:
        return manual_save_playlist_jobs.derive_actor_scope(claim_text)
    except manual_save_playlist_jobs.PlaylistJobError:
        return None


def _manual_save_playlist_deps(deps: object) -> "_ManualSavePlaylistDeps":
    if deps is not None:
        return deps
    return _manual_save_playlist_resolve_deps()


def _manual_save_playlist_environ() -> dict:
    """Merge ``.env`` (deployment source) with process environment (fallback).

    ``fnmusic-ext.service`` has no ``EnvironmentFile=``, so the reviewed
    ``FNMUSIC_MANUAL_SAVE_*`` keys live in ``<repo>/.env``. Process values only
    fill keys the file omits; a missing file is treated as "all unset".
    """
    try:
        values = manual_save_route.read_env_file()
    except Exception:  # noqa: BLE001 — a missing/unreadable .env means all unset
        values = {}
    keys = (
        manual_save_playlist_jobs.PLAYLIST_ENABLED_ENV,
        manual_save_playlist_jobs.JOBS_DB_ENV,
    )
    # Keep only the two reviewed keys: never retain the rest of ``.env`` in memory.
    environ: dict = {}
    if isinstance(values, Mapping):
        for key in keys:
            if key in values:
                environ[key] = values[key]
    for key in keys:
        if key not in environ and key in os.environ:
            environ[key] = os.environ[key]
    return environ


def _manual_save_playlist_musicbox_url() -> str:
    """Loopback musicbox base URL from the reviewed route config (default included)."""
    config = manual_save_route.load_config()
    url = getattr(config, "musicbox_url", None)
    if type(url) is not str or not url:
        raise manual_save_playlist_jobs.ServiceError("config_invalid")
    return url


def _manual_save_playlist_resolve_deps() -> "_ManualSavePlaylistDeps":
    """Build the production service layer or raise one fixed endpoint code.

    Order: playlist switch (403 ``playlist_disabled``) -> ledger path
    (500 ``config_invalid``) -> producer client (loopback-checked). Nothing here
    opens the ledger or the network by itself.
    """
    try:
        config = manual_save_playlist_jobs.load_service_config(
            _manual_save_playlist_environ())
    except Exception:  # noqa: BLE001 — invalid config fails closed, fixed code
        raise manual_save_playlist_jobs.ServiceError("config_invalid") from None
    if not config.enabled:
        raise manual_save_playlist_jobs.ServiceError("playlist_disabled")
    if config.db_path is None:
        raise manual_save_playlist_jobs.ServiceError("config_invalid")
    try:
        base_url = _manual_save_playlist_musicbox_url()
        producer = manual_save_playlist_jobs.PlaylistSnapshotClient(
            base_url, timeout=MANUAL_SAVE_PLAYLIST_READ_TIMEOUT)
    except manual_save_playlist_jobs.PlaylistJobError as exc:
        if exc.code in (
            manual_save_playlist_jobs.PRODUCER_NOT_LOOPBACK,
            manual_save_playlist_jobs.PRODUCER_CONFIG_INVALID,
        ):
            raise manual_save_playlist_jobs.ServiceError("config_invalid") from None
        raise
    except Exception:  # noqa: BLE001 — invalid musicbox URL/config, fixed code
        raise manual_save_playlist_jobs.ServiceError("config_invalid") from None
    service = manual_save_playlist_jobs.PlaylistJobService(config, producer)
    return _ManualSavePlaylistDeps(service=service, producer=producer, enabled=True)


def _manual_save_playlist_total_switch(client: socket.socket, engine) -> bool:
    """L5 switch: the existing single-track master switch gates every endpoint.

    A closed master switch is 403 ``disabled``; only when it is open does the
    playlist switch (resolved in the deps layer) produce ``playlist_disabled``.
    """
    engine_result = _engine_status(manual_save_route if engine is None else engine)
    if engine_result is None:
        _send_manual_save_playlist(
            client, 500, _manual_save_playlist_error("internal"))
        return False
    if engine_result.get("ok") is not True:
        _send_manual_save_playlist(client, 403, {
            "ok": False,
            "code": "disabled",
            "message": _manual_save_playlist_message("disabled"),
            "data": {"enabled": False},
        })
        return False
    return True


def _manual_save_playlist_gate(client: socket.socket, engine, deps: object):
    """Parse-independent gate: master switch -> playlist switch -> deps.

    Returns ``(deps, failed)``; on failure the fixed error has already been sent.
    Runs before any body/query validation so a closed switch has no side effects.
    """
    if not _manual_save_playlist_total_switch(client, engine):
        return None, True
    try:
        active = _manual_save_playlist_deps(deps)
    except Exception as exc:  # noqa: BLE001 — fixed code, never exception text
        code = _manual_save_playlist_code_for_exception(exc)
        _send_manual_save_playlist(
            client,
            _manual_save_playlist_http_status(code, False),
            _manual_save_playlist_error(code),
        )
        return None, True
    if not active.enabled:
        _send_manual_save_playlist(
            client, 403, _manual_save_playlist_error("playlist_disabled"))
        return None, True
    return active, False


def _manual_save_playlist_get_head(client: socket.socket, head_raw: bytes, *,
                                   targets: "tuple[str, ...]",
                                   allow_query: bool = False):
    parsed = _parse_manual_save_head(
        head_raw, method="GET", targets=targets, allow_query=allow_query)
    if parsed is None:
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    headers, rest, target = parsed
    if rest or headers.get("content-length", "0") != "0":
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    return headers, target


def _manual_save_playlist_post_head(client: socket.socket, head_raw: bytes, *,
                                    targets: "tuple[str, ...]"):
    """Strict POST head parse; returns (headers, rest) before body reading."""
    parsed = _parse_manual_save_head(
        head_raw, method="POST", targets=targets)
    if parsed is None:
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    headers, rest, _target = parsed
    return headers, rest


def _manual_save_playlist_json_request(client: socket.socket, headers: dict,
                                       rest: bytes) -> "dict | None":
    if "content-length" not in headers:
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    body = _read_manual_save_body(client, headers, rest)
    if body is None:
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    try:
        request = json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    if not isinstance(request, dict):
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return None
    return request


def _manual_save_playlist_query_value(target: str, key: str) -> "tuple[str, str]":
    """Strict single-key query parsing (same discipline as the search endpoint)."""
    _path, separator, query = target.partition("?")
    if not separator or not query:
        return "", "invalid_request"
    if len(query) > MANUAL_SAVE_MAX_QUERY:
        return "", "invalid_request"
    pairs: list[tuple[str, str]] = []
    for part in query.split("&"):
        if not part:
            return "", "invalid_request"
        name, equals, value = part.partition("=")
        if not equals:
            return "", "invalid_request"
        try:
            name = unquote(name, errors="strict")
            value = unquote(value, errors="strict")
        except (UnicodeError, ValueError):
            return "", "invalid_request"
        pairs.append((name, value))
    if len(pairs) != 1 or pairs[0][0] != key:
        return "", "invalid_request"
    return pairs[0][1], ""


def _manual_save_playlist_parse_playlist_id(value: object) -> "int | None":
    if type(value) is not str or MANUAL_SAVE_PLAYLIST_DECIMAL_RE.fullmatch(value) is None:
        return None
    playlist_id = int(value, 10)
    if not 1 <= playlist_id <= MANUAL_SAVE_PLAYLIST_MAX_PLAYLIST_ID:
        return None
    return playlist_id


def _manual_save_playlist_parse_job_id(value: object) -> "str | None":
    if type(value) is not str or MANUAL_SAVE_PLAYLIST_JOB_ID_RE.fullmatch(value) is None:
        return None
    return value


def _manual_save_playlist_job_id_request(request: dict) -> "tuple[str | None, str]":
    """Advance/cancel body: exactly one key (``job_id``); bad id -> not_found."""
    if set(request) != {"job_id"}:
        return None, "invalid_request"
    job_id = _manual_save_playlist_parse_job_id(request.get("job_id"))
    if job_id is None:
        # §3.5/§3.6: malformed/absent/foreign ids share the fixed not_found code.
        return None, "not_found"
    return job_id, ""


def _manual_save_playlist_create_request(request: dict):
    """Create body allow-list; any identity/extra field is ``invalid_request``."""
    allowed = {"playlist_id", "source_fingerprint", "idempotency_key"}
    if set(request) - allowed:
        return None, "invalid_request"
    playlist_id = request.get("playlist_id")
    if (
        type(playlist_id) is not int
        or isinstance(playlist_id, bool)
        or not 1 <= playlist_id <= MANUAL_SAVE_PLAYLIST_MAX_PLAYLIST_ID
    ):
        return None, "invalid_playlist_id"
    fingerprint = request.get("source_fingerprint")
    if (
        type(fingerprint) is not str
        or MANUAL_SAVE_PLAYLIST_FINGERPRINT_RE.fullmatch(fingerprint) is None
    ):
        return None, "invalid_fingerprint"
    idempotency_key = None
    if "idempotency_key" in request:
        idempotency_key = request.get("idempotency_key")
        if (
            type(idempotency_key) is not str
            or MANUAL_SAVE_PLAYLIST_IDEMPOTENCY_RE.fullmatch(idempotency_key) is None
        ):
            return None, "invalid_idempotency_key"
    return (playlist_id, fingerprint, idempotency_key), ""


def _manual_save_playlist_derived_key(playlist_id: int, fingerprint: str) -> str:
    """§3.4 step 7: ``"p" + str(playlist_id) + "-" + source_fingerprint``."""
    return "p" + str(playlist_id) + "-" + fingerprint


def _manual_save_playlist_manifest_fingerprint(manifest: object) -> str:
    if not isinstance(manifest, Mapping):
        return ""
    version = manifest.get("collection_version")
    prefix = manual_save_playlist_jobs.COLLECTION_VERSION_PREFIX
    if type(version) is not str or not version.startswith(prefix):
        return ""
    fingerprint = version[len(prefix):]
    if MANUAL_SAVE_PLAYLIST_FINGERPRINT_RE.fullmatch(fingerprint) is None:
        return ""
    return fingerprint


def _manual_save_playlist_artifact(payload: Mapping) -> dict:
    artifact = payload.get("source_artifact")
    source = artifact if isinstance(artifact, Mapping) else {}

    def text(name: str) -> str:
        value = source.get(name)
        return value if type(value) is str else ""

    return {
        "provider": text("provider"),
        "package": text("package"),
        "version": text("version"),
        "wheel_sha256": text("wheel_sha256"),
    }


def _manual_save_playlist_artifact_valid(payload: Mapping) -> bool:
    """§3.4 step 5: version/dist-info/error-code recheck (no wheel digest claim).

    The producer already asserts the installed ``NetEase-MusicBox`` version and
    ``dist-info`` presence; the gateway re-checks the reported fields instead of
    trusting the snapshot verdict. It never recomputes/validates the wheel
    SHA-256 at runtime (§4.1 item 3).
    """
    artifact = payload.get("source_artifact")
    if not isinstance(artifact, Mapping):
        return False
    if payload.get("artifact_error_code") is not None:
        return False
    return (
        artifact.get("provider") == MANUAL_SAVE_PLAYLIST_ARTIFACT_PROVIDER
        and artifact.get("package") == MANUAL_SAVE_PLAYLIST_ARTIFACT_PACKAGE
        and artifact.get("version") == MANUAL_SAVE_PLAYLIST_ARTIFACT_VERSION
        and artifact.get("dist_info_present") is True
        and type(artifact.get("wheel_sha256")) is str
    )


def _manual_save_playlist_snapshot_failure_data(playlist_id: int,
                                                payload: object) -> dict:
    """§3.3 failure data; only the reviewed fields and the code whitelist."""
    source = payload if isinstance(payload, Mapping) else {}

    def count(name: str) -> int:
        value = source.get(name)
        return value if type(value) is int and not isinstance(value, bool) and value >= 0 else 0

    error_code = source.get("error_code")
    whitelisted = (
        error_code
        if type(error_code) is str
        and error_code in MANUAL_SAVE_PLAYLIST_SOURCE_ERROR_CODES
        else ""
    )
    return {
        "playlist_id": playlist_id,
        "track_count": count("track_count"),
        "position_count": count("position_count"),
        "enumeration_complete": False,
        "source_error_code": whitelisted,
    }


def _manual_save_playlist_snapshot_result(active: "_ManualSavePlaylistDeps",
                                          playlist_id: int) -> dict:
    """§3.3 precheck: one producer read, then the gateway re-checks the structure.

    §4.3.1's seven re-checks are executed by ``build_manifest`` (wired here, not
    trusted from the producer): positions list length == position_count ==
    track_count, contiguous 1..N positions, positive non-bool track ids with no
    error_code, 1..1000 tracks, 64-hex fingerprint, fixed provider/schema, and
    no silent truncate/pad on a count mismatch. The artifact fields are checked
    separately (also on the complete branch).
    """
    payload = active.producer.fetch_snapshot(playlist_id)
    if not isinstance(payload, Mapping):
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_MALFORMED_RESPONSE)
    failure = _manual_save_playlist_snapshot_failure_data(playlist_id, payload)
    try:
        manifest = manual_save_playlist_jobs.build_manifest(playlist_id, payload)
    except manual_save_playlist_jobs.PlaylistJobError as exc:
        return _manual_save_playlist_error(
            _manual_save_playlist_code_for_exception(exc), failure)
    if not _manual_save_playlist_artifact_valid(payload):
        return _manual_save_playlist_error("source_artifact_mismatch", failure)
    if manifest.get("enumeration_complete") is not True:
        return _manual_save_playlist_error("manifest_incomplete", failure)
    fingerprint = _manual_save_playlist_manifest_fingerprint(manifest)
    entries = manifest.get("entries")
    if not fingerprint or not isinstance(entries, list):
        return _manual_save_playlist_error("manifest_incomplete", failure)
    source_ids = [
        entry.get("source_id")
        for entry in entries
        if isinstance(entry, Mapping) and entry.get("source_status") == "ok"
    ]
    duplicate_count = len(source_ids) - len({value for value in source_ids if type(value) is str})
    return _manual_save_playlist_result({
        "playlist_id": playlist_id,
        "track_count": len(entries),
        "position_count": len(entries),
        "duplicate_position_count": duplicate_count,
        "enumeration_complete": True,
        "source_fingerprint": fingerprint,
        "source_artifact": _manual_save_playlist_artifact(payload),
        "manifest_schema_version": MANUAL_SAVE_PLAYLIST_MANIFEST_SCHEMA_VERSION,
    })


def _manual_save_playlist_create_result(active: "_ManualSavePlaylistDeps",
                                        actor_scope: str, playlist_id: int,
                                        fingerprint: str,
                                        idempotency_key: "str | None") -> dict:
    """§3.4 fixed order: re-read snapshot -> bind fingerprint -> recheck -> create.

    No ledger write happens before the fingerprint binding succeeds, so a
    playlist that changed after the precheck is rejected with 409
    ``source_changed`` instead of creating a job for an unconfirmed manifest.
    """
    payload = active.producer.fetch_snapshot(playlist_id)
    if not isinstance(payload, Mapping):
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_MALFORMED_RESPONSE)
    if payload.get("status") != "complete":
        # A non-complete snapshot never reaches the ledger: build_manifest maps
        # its status to the reviewed fixed code and raises.
        manual_save_playlist_jobs.build_manifest(playlist_id, payload)
        raise manual_save_playlist_jobs.ManifestError(
            manual_save_playlist_jobs.MANIFEST_INCOMPLETE)
    if payload.get("source_fingerprint") != fingerprint:
        raise manual_save_playlist_jobs.ManifestError(
            manual_save_playlist_jobs.SOURCE_CHANGED)
    manifest = manual_save_playlist_jobs.build_manifest(playlist_id, payload)
    if not _manual_save_playlist_artifact_valid(payload):
        raise manual_save_playlist_jobs.ManifestError(
            manual_save_playlist_jobs.SOURCE_ARTIFACT_MISMATCH)
    if manifest.get("enumeration_complete") is not True:
        raise manual_save_playlist_jobs.ManifestError(
            manual_save_playlist_jobs.MANIFEST_INCOMPLETE)
    key = (
        idempotency_key
        if idempotency_key is not None
        else _manual_save_playlist_derived_key(playlist_id, fingerprint)
    )
    job = active.service.create_job(
        actor_scope, playlist_id, payload, idempotency_key=key)
    manifest_sha256 = getattr(job, "manifest_sha256", None)
    return _manual_save_playlist_result({
        "job": _manual_save_playlist_job_summary(job),
        "manifest_sha256": (
            manifest_sha256
            if type(manifest_sha256) is str
            and MANUAL_SAVE_PLAYLIST_MANIFEST_SHA_RE.fullmatch(manifest_sha256)
            else ""
        ),
        "manifest": _manual_save_playlist_manifest_summary(job),
    })


def _manual_save_playlist_manifest_summary(job: object) -> dict:
    manifest = getattr(job, "manifest", None)
    source = manifest if isinstance(manifest, Mapping) else {}

    def text(name: str) -> str:
        value = source.get(name)
        return value if type(value) is str else ""

    entries = source.get("entries")
    return {
        "kind": text("kind"),
        "target_id": text("target_id"),
        "provider_id": text("provider_id"),
        "collection_id": text("collection_id"),
        "collection_version": text("collection_version"),
        "enumeration_complete": source.get("enumeration_complete") is True,
        "positions_total": len(entries) if isinstance(entries, list) else 0,
    }


def _manual_save_playlist_job_summary(job: object) -> dict:
    """§3.7.1 ``JobSummary``: fixed fields only, readiness flags always false."""
    job_id = getattr(job, "job_id", None)
    state = getattr(job, "state", None)
    manifest_sha256 = getattr(job, "manifest_sha256", None)
    positions = getattr(job, "positions", ())
    counts_source = getattr(job, "counts", None)
    counts: dict[str, int] = {}
    if isinstance(counts_source, Mapping):
        for key, value in counts_source.items():
            if (
                type(key) is str
                and key in MANUAL_SAVE_PLAYLIST_POSITION_STATES
                and type(value) is int
                and not isinstance(value, bool)
                and value >= 0
            ):
                counts[key] = value
    return {
        "job_id": (
            job_id
            if type(job_id) is str
            and MANUAL_SAVE_PLAYLIST_JOB_ID_RE.fullmatch(job_id)
            else ""
        ),
        "state": (
            state
            if type(state) is str and state in MANUAL_SAVE_PLAYLIST_TASK_STATES
            else "unknown"
        ),
        "cancel_requested": getattr(job, "cancel_requested", False) is True,
        "counts": counts,
        "positions_total": len(positions) if isinstance(positions, (tuple, list)) else 0,
        "manifest_sha256": (
            manifest_sha256
            if type(manifest_sha256) is str
            and MANUAL_SAVE_PLAYLIST_MANIFEST_SHA_RE.fullmatch(manifest_sha256)
            else ""
        ),
        # Ledger invariants: these three flags are always false (§3.7.1/§6.1).
        "audio_verified": False,
        "production_ready": False,
        "import_ready": False,
    }


def _manual_save_playlist_positions(job: object) -> list:
    """§3.5 positions: manifest order, sanitized fields + fixed reason text."""
    positions = getattr(job, "positions", ())
    rows: list[dict] = []
    if isinstance(positions, (tuple, list)):
        for item in positions:
            position = getattr(item, "position", None)
            if (
                type(position) is not int
                or isinstance(position, bool)
                or position < 1
            ):
                continue
            source_id = getattr(item, "source_id", None)
            state = getattr(item, "state", None)
            reason = getattr(item, "reason", None)
            safe_reason = (
                reason
                if type(reason) is str
                and MANUAL_SAVE_PLAYLIST_REASON_RE.fullmatch(reason) is not None
                else None
            )
            rows.append({
                "position": position,
                "source_id": (
                    source_id
                    if type(source_id) is str
                    and MANUAL_SAVE_PLAYLIST_SOURCE_ID_RE.fullmatch(source_id) is not None
                    else None
                ),
                "state": (
                    state
                    if type(state) is str
                    and state in MANUAL_SAVE_PLAYLIST_POSITION_STATES
                    else "unknown"
                ),
                "reason": safe_reason,
                "reason_message": _manual_save_playlist_reason_message(safe_reason),
                "cleanup_pending": getattr(item, "cleanup_pending", None) is True,
            })
    return rows


def _manual_save_playlist_query_result(active: "_ManualSavePlaylistDeps",
                                       actor_scope: str, job_id: str) -> dict:
    job = active.service.get_job(actor_scope, job_id)
    return _manual_save_playlist_result({
        "job": _manual_save_playlist_job_summary(job),
        "positions": _manual_save_playlist_positions(job),
    })


def _manual_save_playlist_cancel_result(active: "_ManualSavePlaylistDeps",
                                        actor_scope: str, job_id: str) -> dict:
    job = active.service.cancel_job(actor_scope, job_id)
    return _manual_save_playlist_result({
        "job": _manual_save_playlist_job_summary(job),
    })


def _manual_save_playlist_run_block(report: object) -> "dict | None":
    """§3.6 run block: five statuses only, disposition fixed by the contract table."""
    status = getattr(report, "status", None)
    position = getattr(report, "position", None)
    position_number = getattr(position, "position", None)
    if (
        type(position_number) is not int
        or isinstance(position_number, bool)
        or position_number < 1
    ):
        position_number = None
    outcome = getattr(position, "outcome", None) if position_number is not None else None
    if type(outcome) is not str or outcome not in MANUAL_SAVE_PLAYLIST_POSITION_STATES:
        outcome = None
    reason = getattr(position, "reason", None) if position_number is not None else None
    if type(reason) is not str or MANUAL_SAVE_PLAYLIST_REASON_RE.fullmatch(reason) is None:
        reason = None
    if status == "completed":
        disposition = getattr(report, "disposition", None)
        if disposition not in (
            manual_save_playlist_jobs.DISPOSITION_CONTINUE,
            manual_save_playlist_jobs.DISPOSITION_ABORT,
            manual_save_playlist_jobs.DISPOSITION_FREEZE,
        ):
            disposition = manual_save_playlist_jobs.DISPOSITION_FREEZE
    elif status == "commit_unknown":
        disposition = manual_save_playlist_jobs.DISPOSITION_FREEZE
        outcome, reason = "commit_unknown", "commit_unknown"
    elif status == "no_work":
        # §3.6 table: no_work -> 200 + continue (refresh/recheck the job).
        disposition = manual_save_playlist_jobs.DISPOSITION_CONTINUE
        position_number, outcome, reason = None, None, None
    elif status == "stale_completion":
        disposition = manual_save_playlist_jobs.DISPOSITION_FREEZE
    elif status == "completion_unknown":
        disposition = manual_save_playlist_jobs.DISPOSITION_FREEZE
        outcome, reason = "commit_unknown", "commit_unknown"
    else:
        return None
    return {
        "status": status,
        "disposition": disposition,
        "position": position_number,
        "outcome": outcome,
        "reason": reason,
        # §3.6 detail gap (P5 item 6): the P4 executor returns only a
        # SaveResult and never the raw route data, so P5 must not invent a
        # detail payload from untrusted input. The field stays null until a
        # later phase persists/reconstructs a sanitized detail from a fixed
        # whitelist. Never echo the raw save_track response here.
        "detail": None,
    }


def _manual_save_playlist_advance_result(active: "_ManualSavePlaylistDeps",
                                         actor_scope: str, job_id: str) -> dict:
    """One ``run_next`` attempt under the gateway lock, one track at most.

    The lock is acquired inside the worker and **before** any ledger claim; a
    lost race returns 409 ``busy`` without consuming a position (§7.1). The same
    lock object is taken by the single-track worker, so the route's internal
    save lock can no longer be contended inside this process.
    """
    if not MANUAL_SAVE_PLAYLIST_LOCK.acquire(blocking=False):
        return _manual_save_playlist_error("busy")
    try:
        report = active.service.advance_once(actor_scope, job_id)
        status = getattr(report, "status", None)
        if status == "busy":
            return _manual_save_playlist_error("busy")
        if status == "playlist_disabled":
            return _manual_save_playlist_error("playlist_disabled")
        job = active.service.get_job(actor_scope, job_id)
        run = _manual_save_playlist_run_block(report)
        if run is None:
            return _manual_save_playlist_error("internal")
        data = {"run": run, "job": _manual_save_playlist_job_summary(job)}
        if status in ("completed", "commit_unknown", "no_work"):
            return _manual_save_playlist_result(data)
        if status == "stale_completion":
            return _manual_save_playlist_error("stale_completion", data)
        return _manual_save_playlist_error("completion_unknown", data)
    finally:
        MANUAL_SAVE_PLAYLIST_LOCK.release()


def _manual_save_playlist_loopback_target(base_url: object) -> "tuple[str, int]":
    """§8.3: http + loopback host only, no userinfo/path/query/fragment."""
    if (
        type(base_url) is not str
        or not base_url
        or len(base_url) > MANUAL_SAVE_PLAYLIST_MAX_BASE_URL_CHARS
        or any(character in base_url for character in (chr(0), chr(13), chr(10)))
    ):
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_CONFIG_INVALID)
    try:
        parts = urlsplit(base_url)
        port = parts.port if parts.port is not None else 80
    except ValueError:
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_CONFIG_INVALID) from None
    if (
        parts.scheme != "http"
        or parts.hostname not in MANUAL_SAVE_PLAYLIST_LOOPBACK_HOSTS
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in ("", "/")
    ):
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_NOT_LOOPBACK)
    return parts.hostname, port


def _manual_save_playlist_default_list_fetcher(timeout: float) -> object:
    """GET musicbox ``/api/v1/user/playlists?limit=100`` over bounded loopback HTTP."""
    host, port = _manual_save_playlist_loopback_target(
        _manual_save_playlist_musicbox_url())
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        try:
            connection.request(
                "GET",
                "/api/v1/user/playlists?limit="
                + str(MANUAL_SAVE_PLAYLIST_MAX_LIST_ROWS),
            )
            response = connection.getresponse()
            status = response.status
            body = response.read(MANUAL_SAVE_PLAYLIST_MAX_JSON_BYTES + 1)
        except (socket.timeout, TimeoutError):
            raise manual_save_playlist_jobs.ProducerError(
                manual_save_playlist_jobs.PRODUCER_TIMEOUT) from None
        except (OSError, http.client.HTTPException):
            raise manual_save_playlist_jobs.ProducerError(
                manual_save_playlist_jobs.PRODUCER_UNREACHABLE) from None
    finally:
        try:
            connection.close()
        except OSError:
            pass
    if len(body) > MANUAL_SAVE_PLAYLIST_MAX_JSON_BYTES:
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_RESPONSE_TOO_LARGE)
    if status != 200:
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_HTTP_STATUS)
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise manual_save_playlist_jobs.ProducerError(
            manual_save_playlist_jobs.PRODUCER_MALFORMED_RESPONSE) from None


def _manual_save_playlist_list_data(payload: object) -> dict:
    """§3.2 list whitelist: rebuild rows field by field, drop everything else."""
    if not isinstance(payload, Mapping):
        raise manual_save_playlist_jobs.ServiceError("playlist_unavailable")
    if payload.get("ok") is not True:
        error = payload.get("error")
        if error == "not_logged_in" or payload.get("logged_in") is False:
            raise manual_save_playlist_jobs.ServiceError(
                manual_save_playlist_jobs.NOT_LOGGED_IN)
        raise manual_save_playlist_jobs.ServiceError("playlist_unavailable")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise manual_save_playlist_jobs.ServiceError("playlist_unavailable")
    account_uid = payload.get("account_uid")
    if type(account_uid) is not int or isinstance(account_uid, bool) or account_uid < 0:
        account_uid = 0
    playlists: list[dict] = []
    for row in rows:
        if len(playlists) >= MANUAL_SAVE_PLAYLIST_MAX_LIST_ROWS:
            break
        if not isinstance(row, Mapping):
            continue
        playlist_id = row.get("playlist_id")
        if (
            type(playlist_id) is not int
            or isinstance(playlist_id, bool)
            or playlist_id <= 0
        ):
            continue
        track_count = row.get("track_count")
        if (
            type(track_count) is not int
            or isinstance(track_count, bool)
            or track_count < 0
        ):
            continue
        name = row.get("name")
        if type(name) is not str:
            name = ""
        name = name[:MANUAL_SAVE_PLAYLIST_MAX_NAME_CHARS]
        updated_at = row.get("updated_at")
        if (
            type(updated_at) is not int
            or isinstance(updated_at, bool)
            or updated_at < 0
        ):
            updated_at = 0
        user_id = row.get("user_id")
        if type(user_id) is not int or isinstance(user_id, bool):
            user_id = 0
        playlists.append({
            "playlist_id": playlist_id,
            "name": name,
            "track_count": track_count,
            "updated_at": updated_at,
            "owned": account_uid > 0 and user_id == account_uid,
        })
    return {"account_uid": account_uid, "playlists": playlists}


def _manual_save_playlist_list_result(fetcher, timeout: float) -> dict:
    return _manual_save_playlist_result(_manual_save_playlist_list_data(fetcher(timeout)))


def handle_manual_save_playlist_playlists(client: socket.socket, head_raw: bytes, *,
                                          deps=None, engine=None,
                                          list_fetcher=None, timeout=None) -> None:
    """GET .../playlist/playlists: account playlist list (sanitized whitelist)."""
    try:
        parsed = _manual_save_playlist_get_head(
            client, head_raw, targets=MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS)
        if parsed is None:
            return
        headers, _target = parsed
        if _manual_save_playlist_actor_scope(head_raw, headers) is None:
            _send_manual_save_playlist(
                client, 403, _manual_save_playlist_error("forbidden"))
            return
        active, failed = _manual_save_playlist_gate(client, engine, deps)
        if failed:
            return
        budget = (
            MANUAL_SAVE_PLAYLIST_READ_TIMEOUT if timeout is None else timeout
        )
        fetcher = (
            _manual_save_playlist_default_list_fetcher
            if list_fetcher is None
            else list_fetcher
        )
        _manual_save_playlist_run(
            client,
            lambda: _manual_save_playlist_list_result(fetcher, budget),
            budget,
        )
    except OSError:
        pass
    finally:
        _close_quietly(client)


def handle_manual_save_playlist_snapshot(client: socket.socket, head_raw: bytes, *,
                                         deps=None, engine=None,
                                         timeout=None) -> None:
    """GET .../playlist/snapshot?playlist_id=N: read-only precheck (§3.3)."""
    try:
        parsed = _manual_save_playlist_get_head(
            client, head_raw,
            targets=MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS, allow_query=True)
        if parsed is None:
            return
        headers, target = parsed
        if _manual_save_playlist_actor_scope(head_raw, headers) is None:
            _send_manual_save_playlist(
                client, 403, _manual_save_playlist_error("forbidden"))
            return
        active, failed = _manual_save_playlist_gate(client, engine, deps)
        if failed:
            return
        value, query_error = _manual_save_playlist_query_value(target, "playlist_id")
        if query_error:
            _send_manual_save_playlist(
                client, 400, _manual_save_playlist_error(query_error))
            return
        playlist_id = _manual_save_playlist_parse_playlist_id(value)
        if playlist_id is None:
            _send_manual_save_playlist(
                client, 400, _manual_save_playlist_error("invalid_playlist_id"))
            return
        budget = (
            MANUAL_SAVE_PLAYLIST_READ_TIMEOUT if timeout is None else timeout
        )
        _manual_save_playlist_run(
            client,
            lambda: _manual_save_playlist_snapshot_result(active, playlist_id),
            budget,
        )
    except OSError:
        pass
    finally:
        _close_quietly(client)


def handle_manual_save_playlist_create(client: socket.socket, head_raw: bytes, *,
                                       deps=None, engine=None,
                                       timeout=None) -> None:
    """POST .../playlist/jobs: bind fingerprint, build manifest, create job (§3.4)."""
    try:
        parsed = _manual_save_playlist_post_head(
            client, head_raw, targets=MANUAL_SAVE_PLAYLIST_JOBS_PATHS)
        if parsed is None:
            return
        headers, rest = parsed
        actor_scope = _manual_save_playlist_actor_scope(head_raw, headers)
        if actor_scope is None:
            _send_manual_save_playlist(
                client, 403, _manual_save_playlist_error("forbidden"))
            return
        active, failed = _manual_save_playlist_gate(client, engine, deps)
        if failed:
            return
        request = _manual_save_playlist_json_request(client, headers, rest)
        if request is None:
            return
        parsed_request, request_error = _manual_save_playlist_create_request(request)
        if request_error:
            _send_manual_save_playlist(
                client,
                _manual_save_playlist_http_status(request_error, False),
                _manual_save_playlist_error(request_error),
            )
            return
        playlist_id, fingerprint, idempotency_key = parsed_request
        budget = (
            MANUAL_SAVE_PLAYLIST_READ_TIMEOUT if timeout is None else timeout
        )
        _manual_save_playlist_run(
            client,
            lambda: _manual_save_playlist_create_result(
                active, actor_scope, playlist_id, fingerprint, idempotency_key),
            budget,
        )
    except OSError:
        pass
    finally:
        _close_quietly(client)


def handle_manual_save_playlist_job(client: socket.socket, head_raw: bytes, *,
                                    deps=None, engine=None, timeout=None) -> None:
    """GET .../playlist/job?job_id=<48hex>: job summary + position list (§3.5)."""
    try:
        parsed = _manual_save_playlist_get_head(
            client, head_raw,
            targets=MANUAL_SAVE_PLAYLIST_JOB_PATHS, allow_query=True)
        if parsed is None:
            return
        headers, target = parsed
        scope = _manual_save_playlist_actor_scope(head_raw, headers)
        if scope is None:
            _send_manual_save_playlist(
                client, 403, _manual_save_playlist_error("forbidden"))
            return
        active, failed = _manual_save_playlist_gate(client, engine, deps)
        if failed:
            return
        value, query_error = _manual_save_playlist_query_value(target, "job_id")
        if query_error:
            _send_manual_save_playlist(
                client, 400, _manual_save_playlist_error(query_error))
            return
        job_id = _manual_save_playlist_parse_job_id(value)
        if job_id is None:
            # §3.5: malformed/unknown/other-scope ids share the fixed not_found.
            _send_manual_save_playlist(
                client, 404, _manual_save_playlist_error("not_found"))
            return
        budget = (
            MANUAL_SAVE_PLAYLIST_QUERY_TIMEOUT if timeout is None else timeout
        )
        _manual_save_playlist_run(
            client,
            lambda: _manual_save_playlist_query_result(active, scope, job_id),
            budget,
        )
    except OSError:
        pass
    finally:
        _close_quietly(client)


def handle_manual_save_playlist_advance(client: socket.socket, head_raw: bytes, *,
                                        deps=None, engine=None,
                                        timeout=None) -> None:
    """POST .../playlist/advance: exactly one position attempt per request (§3.6)."""
    try:
        parsed = _manual_save_playlist_post_head(
            client, head_raw, targets=MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS)
        if parsed is None:
            return
        headers, rest = parsed
        actor_scope = _manual_save_playlist_actor_scope(head_raw, headers)
        if actor_scope is None:
            _send_manual_save_playlist(
                client, 403, _manual_save_playlist_error("forbidden"))
            return
        active, failed = _manual_save_playlist_gate(client, engine, deps)
        if failed:
            return
        request = _manual_save_playlist_json_request(client, headers, rest)
        if request is None:
            return
        job_id, request_error = _manual_save_playlist_job_id_request(request)
        if request_error:
            _send_manual_save_playlist(
                client,
                _manual_save_playlist_http_status(request_error, False),
                _manual_save_playlist_error(request_error),
            )
            return
        budget = (
            MANUAL_SAVE_PLAYLIST_TIMEOUT if timeout is None else timeout
        )
        _manual_save_playlist_run(
            client,
            lambda: _manual_save_playlist_advance_result(
                active, actor_scope, job_id),
            budget,
        )
    except OSError:
        pass
    finally:
        _close_quietly(client)


def handle_manual_save_playlist_cancel(client: socket.socket, head_raw: bytes, *,
                                       deps=None, engine=None,
                                       timeout=None) -> None:
    """POST .../playlist/cancel: request cancellation (idempotent, no lock, §3.7)."""
    try:
        parsed = _manual_save_playlist_post_head(
            client, head_raw, targets=MANUAL_SAVE_PLAYLIST_CANCEL_PATHS)
        if parsed is None:
            return
        headers, rest = parsed
        actor_scope = _manual_save_playlist_actor_scope(head_raw, headers)
        if actor_scope is None:
            _send_manual_save_playlist(
                client, 403, _manual_save_playlist_error("forbidden"))
            return
        active, failed = _manual_save_playlist_gate(client, engine, deps)
        if failed:
            return
        request = _manual_save_playlist_json_request(client, headers, rest)
        if request is None:
            return
        job_id, request_error = _manual_save_playlist_job_id_request(request)
        if request_error:
            _send_manual_save_playlist(
                client,
                _manual_save_playlist_http_status(request_error, False),
                _manual_save_playlist_error(request_error),
            )
            return
        budget = (
            MANUAL_SAVE_PLAYLIST_QUERY_TIMEOUT if timeout is None else timeout
        )
        _manual_save_playlist_run(
            client,
            lambda: _manual_save_playlist_cancel_result(
                active, actor_scope, job_id),
            budget,
        )
    except OSError:
        pass
    finally:
        _close_quietly(client)


_MANUAL_SAVE_PLAYLIST_ROUTES = (
    ("playlists", "GET", MANUAL_SAVE_PLAYLIST_PLAYLISTS_PATHS),
    ("snapshot", "GET", MANUAL_SAVE_PLAYLIST_SNAPSHOT_PATHS),
    ("create", "POST", MANUAL_SAVE_PLAYLIST_JOBS_PATHS),
    ("job", "GET", MANUAL_SAVE_PLAYLIST_JOB_PATHS),
    ("advance", "POST", MANUAL_SAVE_PLAYLIST_ADVANCE_PATHS),
    ("cancel", "POST", MANUAL_SAVE_PLAYLIST_CANCEL_PATHS),
)
_MANUAL_SAVE_PLAYLIST_HANDLERS = {
    "playlists": handle_manual_save_playlist_playlists,
    "snapshot": handle_manual_save_playlist_snapshot,
    "create": handle_manual_save_playlist_create,
    "job": handle_manual_save_playlist_job,
    "advance": handle_manual_save_playlist_advance,
    "cancel": handle_manual_save_playlist_cancel,
}


def _manual_save_playlist_request_kind(head_raw: bytes) -> "str | None":
    """Exact target+method match for the six playlist endpoints.

    A method mismatch never enters the new handlers: it falls through to the
    forwarding path and the WebUI's 501 (contract §3.0 / §3.8 behaviour).
    """
    pair = _request_method_and_target(head_raw)
    if pair is None:
        return None
    method, target = pair
    path = target.partition("?")[0]
    for kind, expected_method, paths in _MANUAL_SAVE_PLAYLIST_ROUTES:
        if method == expected_method and path in paths:
            return kind
    return None


def handle_manual_save_playlist(client: socket.socket, head_raw: bytes,
                                kind: str) -> None:
    """Dispatch one already-identified playlist request to its fixed handler."""
    handler = _MANUAL_SAVE_PLAYLIST_HANDLERS.get(kind)
    if handler is None:
        _send_manual_save_playlist(
            client, 400, _manual_save_playlist_error("invalid_request"))
        return
    handler(client, head_raw)


def _channel_policy(environ: "Mapping[str, str] | None" = None) -> "tuple[frozenset[int], int]":
    """Resolve (peer uid allow-list, socket mode) from the startup environment.

    Raises ``gateway_identity.TrustedChannelError`` with a fixed code for any
    invalid value; callers must refuse startup instead of falling back to a
    permissive mode.
    """
    source = os.environ if environ is None else environ
    allow_uids = gateway_identity.parse_peer_allow_list(
        source.get(GATEWAY_PEER_ALLOW_ENV))
    socket_mode = gateway_identity.parse_socket_mode(
        source.get(GATEWAY_SOCKET_MODE_ENV), allow_uids)
    return allow_uids, socket_mode


def _policy_or_exit(environ: "Mapping[str, str] | None" = None) -> "tuple[frozenset[int], int]":
    """Return the channel policy, or print the fixed code and exit with 2."""
    try:
        return _channel_policy(environ)
    except gateway_identity.TrustedChannelError as exc:
        try:
            print(
                f"{_CHANNEL_REJECT_LOG_PREFIX} startup-refused code={exc.code}",
                file=sys.stderr,
                flush=True,
            )
        except Exception:  # noqa: BLE001 — a broken stderr must still refuse startup
            pass
        raise SystemExit(2) from None


# Fixed-code, throttled rejection log: at most one line per code per interval,
# and only the integer pid/uid that the kernel reported. No header value, URL,
# cookie, or exception text is ever formatted into this stream.
_channel_reject_log_state: dict[str, float] = {}
_channel_reject_log_lock = threading.Lock()


def _log_channel_rejection(code: str,
                           peer: "gateway_identity.PeerCredentialsMetadata | None" = None) -> None:
    """Record one fixed channel-rejection code without leaking peer input."""
    try:
        now = time.monotonic()
        with _channel_reject_log_lock:
            previous = _channel_reject_log_state.get(code)
            if previous is not None and now - previous < CHANNEL_REJECT_LOG_INTERVAL:
                return
            _channel_reject_log_state[code] = now
        fields = [f"code={code}"]
        if peer is not None:
            fields.append(f"pid={int(peer.pid)}")
            fields.append(f"uid={int(peer.uid)}")
        print(f"{_CHANNEL_REJECT_LOG_PREFIX} " + " ".join(fields),
              file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001 — logging must never change service behavior
        pass


def _peer_credential_rejection_code(reason_code: str) -> str:
    """Map a peer-credential failure to one documented fixed log class."""
    if reason_code in _PEER_CREDENTIAL_UNSUPPORTED_CODES:
        return "peer_credentials_unsupported"
    return "peer_credentials_invalid"


def _admit_peer(client: socket.socket, allow_uids: frozenset[int]) -> bool:
    """L2 admission at accept time: kernel peer metadata before any read.

    Must run before a worker thread exists: on rejection the connection is
    closed without reading a byte, without a thread, and without an upstream
    connection.
    """
    try:
        peer = gateway_identity.read_peer_credentials(client)
    except gateway_identity.PeerCredentialsError as exc:
        _log_channel_rejection(_peer_credential_rejection_code(exc.reason_code))
        _close_quietly(client)
        return False
    try:
        gateway_identity.evaluate_channel(peer, allow_uids)
    except gateway_identity.TrustedChannelError as exc:
        _log_channel_rejection(exc.code, peer)
        _close_quietly(client)
        return False
    return True


def serve(sock_path: Path, upstream: tuple[str, int] = UPSTREAM,
          observer: "gateway_observation.GatewayObserver | None" = None, *,
          policy: "tuple[frozenset[int], int] | None" = None,
          _stop_event: "threading.Event | None" = None,
          _max_accepts: int | None = None) -> None:
    """Accept desktop-gateway connections under the trusted-ingress policy.

    ``policy`` and the leading-underscore parameters are test-only injection
    points with production defaults: when ``policy`` is None the startup
    environment is resolved exactly as ``main()`` does, and ``_stop_event`` /
    ``_max_accepts`` let a finite test drive the accept loop without changing
    the default infinite service loop.
    """
    allow_uids, socket_mode = _policy_or_exit() if policy is None else policy
    server = None
    try:
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        # Re-assert the policy mode on every start; never inherit a stale 0666.
        os.chmod(sock_path, socket_mode)
        server.listen(64)
        if observer is not None:
            try:
                observer.start()
            except Exception:
                # Observation must never change the gateway's service behavior.
                pass
        accepts = 0
        while True:
            if _stop_event is not None and _stop_event.is_set():
                break
            try:
                client, _addr = server.accept()
            except OSError:
                # Socket closed during shutdown: leave the loop cleanly.
                break
            except Exception:  # noqa: BLE001 - transient error (e.g. resource limit)
                time.sleep(0.1)
                continue
            accepts += 1
            if not _admit_peer(client, allow_uids):
                if _max_accepts is not None and accepts >= _max_accepts:
                    break
                continue
            try:
                if observer is None:
                    thread = threading.Thread(target=_handle, args=(client, upstream), daemon=True)
                else:
                    thread = threading.Thread(
                        target=_handle, args=(client, upstream, observer), daemon=True,
                    )
                thread.start()
            except Exception:  # noqa: BLE001 - transient dispatch failure must not kill serve()
                _close_quietly(client)
                time.sleep(0.1)
                continue
            if _max_accepts is not None and accepts >= _max_accepts:
                break
    finally:
        if observer is not None:
            try:
                observer.stop()
            except Exception:
                pass
        if server is not None:
            server.close()


def _post_request_path(head_raw: bytes) -> "str | None":
    """取请求行的 POST 路径；非 POST 或解析失败返回 None。"""
    try:
        request_line = head_raw.split(b"\r\n", 1)[0].decode("latin-1", "replace")
    except Exception:  # noqa: BLE001
        return None
    parts = request_line.split()
    if len(parts) < 2 or parts[0].upper() != "POST":
        return None
    return parts[1]


def _is_host_file_request(head_raw: bytes) -> bool:
    return _post_request_path(head_raw) in HOST_FILE_PATHS


def _is_fs_check_request(head_raw: bytes) -> bool:
    return _post_request_path(head_raw) in FS_CHECK_PATHS


def _maintenance_target(head_raw: bytes):
    parts = head_raw.split(b"\r\n", 1)[0].decode("latin-1").split()
    if len(parts) < 2:
        return None
    target = parts[1]
    prefix = "/app/fnmusic-ext"
    if target.startswith(prefix + "/"):
        target = target[len(prefix):]
    # Reserve the entire namespace, including malformed/unknown maintenance paths.
    decoded = unquote(target)
    if decoded.startswith(prefix + "/"):
        decoded = decoded[len(prefix):]
    if target.startswith(MAINTENANCE_ROOT) or decoded.startswith(MAINTENANCE_ROOT):
        return parts[0], target
    return None


def handle_maintenance(client: socket.socket, head_raw: bytes, method: str, target: str) -> None:
    remote = None
    try:
        client.settimeout(CONNECT_TIMEOUT)
        head = head_raw.split(b"\r\n\r\n", 1)[0]
        if len(head) + 4 > MAINTENANCE_MAX_HEADER:
            raise ValueError("请求头过大")
        lines = head.decode("ascii").split("\r\n")
        if len(lines[0].split(" ")) != 3 or lines[0].split(" ")[2] not in ("HTTP/1.0", "HTTP/1.1"):
            raise ValueError("请求行无效")
        headers = {}
        for line in lines[1:]:
            key, value = line.split(":", 1)
            if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", key):
                raise ValueError("请求头无效")
            key = key.lower()
            if key in headers:
                raise ValueError("重复请求头")
            headers[key] = value.strip()
        if "transfer-encoding" in headers:
            raise ValueError("不支持 Transfer-Encoding")
        length = headers.get("content-length", "0")
        if not re.fullmatch(r"[0-9]{1,10}", length) or int(length) > MAINTENANCE_MAX_BODY:
            raise ValueError("Content-Length 无效")
        # Keep strict raw-claim parsing for privileged gateway handlers. The
        # claim remains untrusted and is not an authentication proof.
        if not _admin_claim_from_raw_head(head_raw):
            client.sendall(_json_response(403, {"ok": False, "error": "仅管理员可执行维护"}))
            return
        cleanup = MAINTENANCE_ROOT + "/cleanup"
        cleanup_route = ((method == "POST" and target == cleanup) or
                         (method == "GET" and re.fullmatch(cleanup + r"/[0-9a-fA-F]{32}", target)))
        organize_route = ((method == "POST" and target == ORGANIZE_PATH) or
                          (method == "GET" and (target == ORGANIZE_PATH or
                           re.fullmatch(ORGANIZE_PATH + r"/[0-9a-fA-F]{32}", target) is not None)))
        # 音乐用户列表只读，走同一条管理员维护通道（GET，无请求体）
        users_route = method == "GET" and target == MAINTENANCE_ROOT + "/music-users"
        if not (cleanup_route or organize_route or users_route):
            client.sendall(_json_response(404, {"ok": False, "error": "未知维护接口"}))
            return
        body = head_raw.split(b"\r\n\r\n", 1)[1][:int(length)]
        deadline = time.monotonic() + CONNECT_TIMEOUT
        while len(body) < int(length):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("请求体超时")
            client.settimeout(remaining)
            chunk = client.recv(int(length) - len(body))
            if not chunk:
                raise ValueError("请求体不完整")
            body += chunk
        if method == "POST":
            req = json.loads(body)
            if target == cleanup:
                if req != {"confirm_beta": True} or type(req.get("confirm_beta")) is not bool:
                    raise ValueError("仅允许 confirm_beta: true 确认字段")
                body = b'{"confirm_beta":true}'
            else:
                if (not isinstance(req, dict) or
                        set(req) != {"confirm_beta", "root", "layout"} or
                        type(req.get("confirm_beta")) is not bool or
                        req.get("confirm_beta") is not True or
                        not isinstance(req.get("layout"), str) or
                        req.get("layout") not in ORGANIZE_LAYOUTS or
                        not isinstance(req.get("root"), str)):
                    raise ValueError("整理确认参数无效")
                root = req["root"]
                if (not root.startswith("/") or ".." in root.split("/") or
                        any(char in root for char in "\r\n\x00")):
                    raise ValueError("整理目录无效")
                body = json.dumps({"confirm_beta": True, "root": root,
                                   "layout": req["layout"]}, separators=(",", ":")).encode("utf-8")
        elif body:
            raise ValueError("查询不允许请求体")
        remote = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        remote.settimeout(MAINTENANCE_TIMEOUT)
        remote.connect(MAINTENANCE_SOCKET)
        remote.sendall((f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n"
                        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body)
        response = http.client.HTTPResponse(remote)
        response.begin()
        data = response.read(1 << 20)
        if len(data) >= 1 << 20:
            raise OSError("Maintenance response too large")
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise OSError("Invalid maintenance response")
        client.sendall(_json_response(response.status, payload))
    except (ValueError, UnicodeError) as exc:
        client.sendall(_json_response(400 if remote is None else 503,
                                     {"ok": False, "error": str(exc) if remote is None else "维护服务不可用"}))
    except (OSError, http.client.HTTPException):
        try:
            client.sendall(_json_response(503, {"ok": False, "error": "维护服务不可用"}))
        except OSError:
            pass
    finally:
        if remote is not None:
            remote.close()
        client.close()


def _handle(client: socket.socket, upstream: tuple[str, int],
            observer: "gateway_observation.GatewayObserver | None" = None) -> None:
    client.settimeout(CONNECT_TIMEOUT)
    head = _read_request_head(client)
    if head is None:
        client.close()
        return
    head_raw, headers = head
    if observer is not None:
        try:
            # Inspect only the already-read request head. Any diagnostic failure
            # is isolated from the original branch and forwarding bytes below.
            observer.observe(client, head_raw)
        except Exception:
            pass
    maintenance = _maintenance_target(head_raw)
    if maintenance is not None:
        handle_maintenance(client, head_raw, *maintenance)
        return
    if _is_manual_save_status_request(head_raw):
        handle_manual_save_status(client, head_raw)
        return
    if _is_manual_save_track_request(head_raw):
        handle_manual_save_track(client, head_raw)
        return
    if _is_manual_save_search_request(head_raw):
        handle_manual_save_search(client, head_raw)
        return
    # Playlist save endpoints (contract §3) are matched before `_validate_head`
    # exactly like the other local privileged handlers above: an ambiguous head
    # is rejected by this handler's own strict parse with its documented fixed
    # responses, before any body, ledger, engine, or upstream work.
    playlist_kind = _manual_save_playlist_request_kind(head_raw)
    if playlist_kind is not None:
        handle_manual_save_playlist(client, head_raw, playlist_kind)
        return
    client.settimeout(None)
    if _is_host_file_request(head_raw):
        handle_host_file(client, headers, head_raw)
        return
    if _is_fs_check_request(head_raw):
        handle_fs_check(client, headers, head_raw)
        return
    # L3 boundary for the forwarded path: an ambiguous identity/framing head is
    # rejected here, before any upstream connection exists, and the body is
    # never read. Local privileged handlers above keep their own strict parse
    # (they reject the same heads with their documented fixed responses before
    # any body/engine/filesystem work).
    rejection = _validate_head(head_raw)
    if rejection is not None:
        _reject_head(client, rejection)
        return
    try:
        remote = socket.create_connection(upstream, timeout=CONNECT_TIMEOUT)
        remote.settimeout(RELAY_TIMEOUT)
    except OSError:
        client.close()
        return
    left = threading.Thread(target=_relay, args=(client, remote, head_raw), daemon=True)
    right = threading.Thread(target=_relay, args=(remote, client), daemon=True)
    left.start()
    right.start()
    left.join()
    right.join()
    client.close()
    remote.close()


def _stop_previous() -> None:
    if not PID_FILE.is_file():
        return
    try:
        pid = int(PID_FILE.read_text().strip())
    except ValueError:
        return
    if pid == os.getpid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass


def _daemonize() -> None:
    if os.fork() > 0:
        os._exit(0)
    os.setsid()
    if os.fork() > 0:
        os._exit(0)
    os.chdir("/")
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        try:
            os.dup2(devnull, fd)
        except OSError:
            pass


def _parse_main_args(args: list[str] | None) -> tuple[Path, gateway_observation.ObservationOptions | None] | None:
    values = list(sys.argv[1:] if args is None else args)
    positional: list[str] = []
    marker: str | None = None
    seconds: int | None = None
    index = 0
    while index < len(values):
        value = values[index]
        if value in ("--g0-observe-marker", "--g0-observe-seconds"):
            if index + 1 >= len(values):
                return None
            parameter = values[index + 1]
            if value == "--g0-observe-marker":
                if marker is not None:
                    return None
                marker = parameter
            else:
                if seconds is not None or not parameter or not parameter.isascii() or not parameter.isdecimal():
                    return None
                try:
                    seconds = int(parameter, 10)
                except ValueError:
                    return None
            index += 2
            continue
        if value.startswith("--"):
            return None
        positional.append(value)
        index += 1

    if len(positional) > 1 or (seconds is not None and marker is None):
        return None
    if marker is None:
        if seconds is not None:
            return None
        options = None
    else:
        try:
            options = gateway_observation.validate_options(marker, seconds if seconds is not None else 60)
        except gateway_observation.ObservationSetupError:
            return None
    base = Path(positional[0]) if positional else Path(__file__).resolve().parent.parent
    return base, options


def _diagnostic_previous_pid_is_live() -> bool:
    """Return whether a readable PID file still names a live process.

    Malformed or inaccessible records fail closed with a stable startup error.
    This diagnostic-only check never signals a process or changes a socket.
    """
    try:
        raw_pid = PID_FILE.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        return False
    except UnicodeError:
        raise gateway_observation.ObservationSetupError("PID_FILE_INVALID") from None
    except OSError:
        raise gateway_observation.ObservationSetupError("PID_FILE_READ_FAILED") from None
    if not raw_pid or not raw_pid.isascii() or not raw_pid.isdecimal():
        raise gateway_observation.ObservationSetupError("PID_FILE_INVALID")
    try:
        pid = int(raw_pid, 10)
    except ValueError:
        raise gateway_observation.ObservationSetupError("PID_FILE_INVALID") from None
    if pid <= 0:
        raise gateway_observation.ObservationSetupError("PID_FILE_INVALID")
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return False
        if exc.errno == errno.EPERM:
            return True
        raise gateway_observation.ObservationSetupError("PID_CHECK_FAILED") from None
    except OverflowError:
        raise gateway_observation.ObservationSetupError("PID_FILE_INVALID") from None
    return True


def main(argv: list[str] | None = None) -> int:
    parsed = _parse_main_args(argv)
    if parsed is None:
        return 2
    # Refuse an invalid channel policy before any process/socket side effect.
    # serve() resolves the same environment again for its own startup path.
    _policy_or_exit()
    base, options = parsed
    observer = None
    if options is not None:
        output_fd = None
        try:
            if _diagnostic_previous_pid_is_live():
                return 3
            output_fd = gateway_observation.prepare_output_fd(options.marker)
            observer = gateway_observation.GatewayObserver(
                options.marker, options.seconds, output_fd,
            )
            output_fd = None
        except gateway_observation.ObservationSetupError:
            if output_fd is not None:
                try:
                    os.close(output_fd)
                except OSError:
                    pass
            return 3
        except Exception:
            if output_fd is not None:
                try:
                    os.close(output_fd)
                except OSError:
                    pass
            return 3
    try:
        path = socket_path_for(base)
        if options is None:
            _stop_previous()
        _daemonize()
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))
    except Exception:
        if observer is not None:
            observer.stop()
        return 4
    try:
        if observer is None:
            serve(path)
        else:
            serve(path, observer=observer)
    finally:
        if observer is None:
            try:
                PID_FILE.unlink()
            except OSError:
                pass
        else:
            try:
                observer.stop()
            except Exception:
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
