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
- /api/maintenance：仅转发已确认的清理/任务查询到固定私有 Unix socket，
  不持有业务状态，也不把未知维护请求交给普通 WebUI。
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
from urllib.parse import unquote
from pathlib import Path

if __package__:
    from . import gateway_identity, gateway_observation, manual_save_route
else:  # direct execution from the proxy source directory
    import gateway_identity
    import gateway_observation
    import manual_save_route

UPSTREAM = ("127.0.0.1", 8774)
SOCK_NAME = "fnmusic-ext.sock"
PID_FILE = Path("/run/fnmusic-ext/webui-gateway.pid")
# create_connection 的 timeout 会留在套接字上，之后的 recv 也受它限制。
# 洛雪源校验要几十秒才有响应；沿用 5 秒会让桌面网关中途拆连接，nginx 回 502。
CONNECT_TIMEOUT = 5
RELAY_TIMEOUT = 180

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
    "timeout": "保存超时",
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
    "download_failed": 502,
    "integrity_mismatch": 502,
    "invalid_audio": 502,
    "tag_failed": 500,
}


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
    header, separator, _body = head_raw.partition(b"\r\n\r\n")
    if not separator:
        return False
    lines = header.split(b"\r\n")
    if not lines:
        return False
    pairs: list[tuple[bytes, bytes]] = []
    for line in lines[1:]:
        name, colon, value = line.partition(b":")
        if not colon:
            return False
        pairs.append((name, value))
    try:
        claims = gateway_identity.parse_untrusted_identity(pairs)
    except gateway_identity.HeaderClaimsError:
        return False
    return claims.admin_claim is True


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
    if headers.get(MANUAL_SAVE_CSRF_HEADER) != MANUAL_SAVE_CSRF_VALUE:
        return False
    return _admin_claim_from_raw_head(head_raw)


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
        "source_type", "format", "basename", "target_dir",
    )
    sanitized = {
        name: (source.get(name) if type(source.get(name)) is str else "")
        for name in text_fields
    }
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
    holder: dict[str, object] = {}

    def worker() -> None:
        try:
            holder["value"] = engine.save_track(song_id)
        except BaseException:  # noqa: BLE001 — worker errors become fixed JSON
            holder["value"] = _manual_save_error("internal")

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


def serve(sock_path: Path, upstream: tuple[str, int] = UPSTREAM,
          observer: "gateway_observation.GatewayObserver | None" = None) -> None:
    server = None
    try:
        sock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            sock_path.unlink()
        except FileNotFoundError:
            pass
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(sock_path))
        os.chmod(sock_path, 0o666)
        server.listen(64)
        if observer is not None:
            try:
                observer.start()
            except Exception:
                # Observation must never change the gateway's service behavior.
                pass
        while True:
            client, _addr = server.accept()
            if observer is None:
                thread = threading.Thread(target=_handle, args=(client, upstream), daemon=True)
            else:
                thread = threading.Thread(
                    target=_handle, args=(client, upstream, observer), daemon=True,
                )
            thread.start()
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
        if not ((method == "POST" and target == cleanup) or
                (method == "GET" and re.fullmatch(cleanup + r"/[0-9a-fA-F]{32}", target))):
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
            if req != {"confirm_beta": True} or type(req.get("confirm_beta")) is not bool:
                raise ValueError("仅允许 confirm_beta: true 确认字段")
            body = b'{"confirm_beta":true}'
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
    client.settimeout(None)
    if _is_host_file_request(head_raw):
        handle_host_file(client, headers, head_raw)
        return
    if _is_fs_check_request(head_raw):
        handle_fs_check(client, headers, head_raw)
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
