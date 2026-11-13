"""Manual single-track save engine: config, musicbox ladder, ffmpeg tags, anonymous publish."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import quote, urlsplit, urlunsplit

if __package__:
    from . import manual_save_core as core
else:  # direct execution from the proxy source directory
    import manual_save_core as core


QUALITY_ORDER = ("jymaster", "hires", "lossless", "exhigh")
MAX_BYTES = 1 << 30
DOWNLOAD_CHUNK_BYTES = 1 << 20
MAX_JSON_BYTES = 1 << 20
MAX_COVER_BYTES = 10 * 1024 * 1024
MAX_COVER_WIDTH = 8192
MAX_COVER_HEIGHT = 8192
MAX_COVER_PIXELS = 16_777_216
MAX_COVER_DECODE_ALLOC = 96 * 1024 * 1024
MAX_TAGGED_EXTRA_BYTES = MAX_COVER_BYTES + (1 << 20)
MAX_LYRIC_BYTES = 1 << 20
API_TIMEOUT = 15.0
# Metadata delivery (FNMEXT-METADATA-DELIVERY-20261101): the same musicbox
# service was observed returning /lyric after 27-42 s, so lyric and info get
# their own bounded retry budgets. The worst case stays under the gateway's
# 1200 s save timeout: info <= 15 + 0.5 + 15 = 30.5 s, lyric <= 30 + 1 + 30 = 61 s.
INFO_TIMEOUT = API_TIMEOUT
INFO_MAX_ATTEMPTS = 2
INFO_RETRY_DELAY = 0.5
INFO_TOTAL_BUDGET = 31.0
LYRIC_TIMEOUT = 30.0
LYRIC_MAX_ATTEMPTS = 2
LYRIC_RETRY_DELAY = 1.0
LYRIC_TOTAL_BUDGET = 62.0
COVER_TIMEOUT = 8.0
COVER_DECODE_TIMEOUT = 10.0
# The NetEase CDN intermittently resets long transfers, and curl --retry does
# NOT resume inside one invocation: continue-at = "-" only takes effect when the
# output file already exists at curl start, so --retry restarts at byte 0.
# _default_download therefore runs up to DOWNLOAD_MAX_ATTEMPTS Python-level
# attempts that each keep continue-at = "-": once an attempt left a partial
# output behind, the next attempt sends Range: bytes=<partial>- and resumes
# (measured on the NAS with curl 7.88.1: partial file -> Range/206, full
# 126,459,367-byte jymaster FLAC in 114.9 s with retries, 58.1 s without resets).
# DOWNLOAD_TIMEOUT is the per-attempt curl --max-time; DOWNLOAD_SUBPROCESS_TIMEOUT
# is the total attempt budget, kept below the gateway's save timeout. Every
# exhausted path fails closed with download_failed.
DOWNLOAD_TIMEOUT = 180.0
DOWNLOAD_SUBPROCESS_TIMEOUT = 580.0
DOWNLOAD_MAX_ATTEMPTS = 4
# Delivery guard (FNMEXT-DELIVERY-GUARD part 2): a signed media URL is fetched
# only after every hop passed URL policy, address policy, and a bounded probe.
# The probe phase has its own budget; the transfer phase gets a fresh one.
DOWNLOAD_MAX_REDIRECTS = 3
DOWNLOAD_PROBE_TIMEOUT = 15.0
DOWNLOAD_MAX_MEDIA_PROBE_ATTEMPTS = 4
DOWNLOAD_MAX_MEDIA_ADDRESSES = 32
DOWNLOAD_MAX_MEDIA_URL_CHARS = 4096
PROBE_TIMEOUT = 30.0
DECODE_TIMEOUT = 300.0
TAG_TIMEOUT = 120.0
TEMP_ROOT = "/tmp"
TEMP_PREFIX = "fnmusic-manual-save-"
TARGET_ID = "manual-save"
ENV_FILE_NAME = ".env"
DEFAULT_MUSICBOX_URL = "http://127.0.0.1:8770"
ENABLED_KEY = "FNMUSIC_MANUAL_SAVE_ENABLED"
TARGET_KEY = "FNMUSIC_MANUAL_SAVE_TARGET"
STAGING_KEY = "FNMUSIC_MANUAL_SAVE_STAGING"
MUSICBOX_KEY = "FNMUSIC_MUSICBOX_URL"
QUALITY_KEY = "FNMUSIC_MANUAL_SAVE_QUALITY"
COVER_ENABLED_KEY = "FNMUSIC_MANUAL_SAVE_COVER_ENABLED"
LYRIC_ENABLED_KEY = "FNMUSIC_MANUAL_SAVE_LYRIC_ENABLED"
FFMPEG_BIN = "/usr/bin/ffmpeg"
FFPROBE_BIN = "/usr/bin/ffprobe"
CURL_BIN = "/usr/bin/curl"
CURL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CURL_REFERER = "https://music.163.com/"
NETEASE_COVER_HOSTS = frozenset({"p1.music.126.net", "p2.music.126.net"})
# Fixed, redacted cover-error classes exposed as `cover_error`. Only the exact
# allowlisted hosts above may be fetched; nothing here echoes a host/URL/IP.
_COVER_ERRORS = frozenset({
    "host_untrusted",
    "url_invalid",
    "address_untrusted",
    "http_error",
    "payload_invalid",
    "decode_failed",
    "embed_failed",
    "unknown",
})
_COVER_ERROR_REASONS = {
    "cover_url_untrusted": "host_untrusted",
    "cover_url_invalid": "url_invalid",
    "cover_dns_too_many_addresses": "address_untrusted",
    "cover_dns_invalid": "address_untrusted",
    "cover_dns_not_public": "address_untrusted",
    "cover_dns_empty": "address_untrusted",
    "cover_dns_resolve_failed": "address_untrusted",
    "cover_fetch_http_error": "http_error",
    "cover_fetch_payload_invalid": "payload_invalid",
    "cover_fetch_unknown": "unknown",
    "cover_http_status": "http_error",
    "cover_response_invalid": "payload_invalid",
    "cover_mime_invalid": "payload_invalid",
    "cover_dimensions_invalid": "payload_invalid",
    "cover_dimensions_unsafe": "payload_invalid",
    "cover_too_large": "payload_invalid",
    "cover_encoding_invalid": "payload_invalid",
    "cover_decode_failed": "decode_failed",
}

_COVER_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
# The source CDN declares JPEG with the non-standard `image/jpg` alias. Only this
# closed alias table is accepted; declared parameters are limited to one
# `charset=<token>`. Nothing here relaxes the magic-byte comparison (A1).
_COVER_DECLARED_MIME_ALIASES = {
    "image/jpeg": "image/jpeg",
    "image/jpg": "image/jpeg",
    "image/png": "image/png",
    "image/webp": "image/webp",
}
_COVER_MIME_PARAM_TOKEN_RE = re.compile(r"[A-Za-z0-9._-]{1,32}")
UNKNOWN_ARTIST = "未知歌手"
ARTIST_SEPARATOR = "/"
MD5_RE = re.compile(r"[0-9a-fA-F]{32}")
DIGITS_RE = re.compile(r"[0-9]{1,10}")
# A delivered quality is reported as a lowercase server token; anything else is
# "unknown", never translated or inferred from type/bitrate/sample-rate.
QUALITY_LEVEL_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
SEARCH_LIMIT = 20
MAX_SEARCH_KEYWORD_CHARS = 120
_FILENAME_FORBIDDEN = frozenset("/\\:")
_CONTROL_CATEGORIES = frozenset({"Cc", "Cs"})
_SAVE_LOCK = threading.Lock()
_ANONYMOUS_MODULE = None
_LOGGER = logging.getLogger(__name__)


_MESSAGES = {
    "ok": "",
    "disabled": "手动保存未启用",
    "invalid_keyword": "搜索关键词必须是 1-120 字符",
    "config_invalid": "手动保存配置无效",
    "invalid_song_id": "song_id 必须是正整数",
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
    "staging_unavailable": "暂存不可用",
    "publish_failed": "发布失败",
    "conflict": "目标文件已存在",
    "internal": "内部错误",
}


class _SaveFailure(RuntimeError):
    """A fixed, input-redacted failure code for one save stage."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class Config:
    enabled: bool
    target_dir: str
    staging_dir: str
    musicbox_url: str
    quality: str = QUALITY_ORDER[0]
    cover_enabled: bool = True
    lyric_enabled: bool = True


@dataclass(frozen=True, slots=True)
class SaveDeps:
    """Test-only seams; production code resolves every field to a real default."""

    config: Mapping[str, str] | None = None
    stat: Callable[[str], object] | None = None
    geteuid: Callable[[], int] | None = None
    http_json: Callable[[str], object] | None = None
    download: Callable[[str, Path], object] | None = None
    probe: Callable[[Path], object] | None = None
    decode_check: Callable[[Path], None] | None = None
    tag: Callable[[Path, Path, Mapping[str, str]], None] | None = None
    open_store: Callable[[str, str], object] | None = None
    make_tempdir: Callable[[], Path] | None = None
    lyric_json: Callable[[str], object] | None = None
    verify_cover: Callable[[Path], None] | None = None
    cover_fetch: Callable[[str], object] | None = None
    validate_tagged: Callable[[Path, str, Mapping[str, str], bool], object] | None = None
    media_resolve: Callable[[str, int], object] | None = None


@dataclass(frozen=True, slots=True)
class _TrackMetadata:
    title: str
    artist: str
    album: str
    track_number: int | None
    disc_number: int | None
    cover_url: str
    lyricist: str
    composer: str


class _TagMetadata(dict[str, str]):
    """Normal tag fields plus trusted, non-tagging inputs for the tag adapter."""

    def __init__(
        self,
        values: Mapping[str, str],
        *,
        cover_path: Path | None = None,
        source_cover_indexes: tuple[int, ...] = (),
    ):
        super().__init__(values)
        self.cover_path = cover_path
        self.source_cover_indexes = source_cover_indexes


@dataclass(frozen=True, slots=True)
class _CoverAsset:
    data: bytes
    mime: str


@dataclass(frozen=True, slots=True)
class _QualityVerdict:
    """One quality-ladder response classified without any network or file access."""

    state: str
    failure_code: str = ""
    data: Mapping | None = None
    media_url: str = ""
    actual: str = ""
    actual_status: str = ""


@dataclass(frozen=True, slots=True)
class _QualitySelection:
    """The first verified full-track answer from the quality ladder."""

    requested: str
    data: Mapping
    media_url: str
    actual: str
    actual_status: str
    fallback: bool


def _field(deps: SaveDeps | None, name: str, default):
    if deps is None:
        return default
    value = getattr(deps, name, None)
    return default if value is None else value


def _env_path() -> Path:
    return Path(__file__).resolve().parent.parent / ENV_FILE_NAME


def read_env_file(path: Path | None = None) -> dict[str, str]:
    """Minimal KEY='value' reader; never creates, rewrites, or watches a file."""
    env_path = _env_path() if path is None else Path(path)
    try:
        text = env_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {}
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def _parse_enabled(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().casefold() in {"true", "1", "yes", "on"}


def _parse_bool_default(value: object, *, default: bool) -> bool:
    if type(value) is not str or not value.strip():
        return default
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes", "on"}:
        return True
    if normalized in {"false", "0", "no", "off"}:
        return False
    return default


def _normalize_quality(value: object) -> str:
    if type(value) is not str:
        return QUALITY_ORDER[0]
    candidate = value.strip().casefold()
    if candidate in QUALITY_ORDER:
        return candidate
    return QUALITY_ORDER[0]


def _quality_order_for(max_quality: str) -> tuple[str, ...]:
    normalized = _normalize_quality(max_quality)
    return QUALITY_ORDER[QUALITY_ORDER.index(normalized):]


def load_config(
    *,
    values: Mapping[str, str] | None = None,
    env_path: Path | None = None,
) -> Config:
    source = read_env_file(env_path) if values is None else values
    target = source.get(TARGET_KEY, "") if isinstance(source, Mapping) else ""
    staging = source.get(STAGING_KEY, "") if isinstance(source, Mapping) else ""
    musicbox = source.get(MUSICBOX_KEY, "") if isinstance(source, Mapping) else ""
    quality = source.get(QUALITY_KEY) if isinstance(source, Mapping) else None
    cover_raw = source.get(COVER_ENABLED_KEY) if isinstance(source, Mapping) else None
    lyric_raw = source.get(LYRIC_ENABLED_KEY) if isinstance(source, Mapping) else None
    return Config(
        enabled=_parse_enabled(source.get(ENABLED_KEY) if isinstance(source, Mapping) else None),
        target_dir=target if type(target) is str else "",
        staging_dir=staging if type(staging) is str else "",
        musicbox_url=musicbox.strip() if type(musicbox) is str and musicbox.strip() else DEFAULT_MUSICBOX_URL,
        quality=_normalize_quality(quality),
        cover_enabled=_parse_bool_default(cover_raw, default=True),
        lyric_enabled=_parse_bool_default(lyric_raw, default=True),
    )


def _text(value: object, fallback: str = "") -> str:
    if type(value) is str:
        return value
    if type(value) is int and not isinstance(value, bool):
        return str(value)
    return fallback


def _positive_int(value: object) -> int | None:
    if type(value) is int and value > 0:
        return value
    if type(value) is str and DIGITS_RE.fullmatch(value.strip()):
        parsed = int(value.strip(), 10)
        return parsed if parsed > 0 else None
    return None


def _result(ok: bool, code: str, data: object = None) -> dict:
    return {
        "ok": bool(ok),
        "code": code,
        "message": _MESSAGES.get(code, _MESSAGES["internal"]),
        "data": data if isinstance(data, dict) else {},
    }


def _failure(code: str) -> dict:
    return _result(False, code)


def _default_geteuid() -> int:
    getter = getattr(os, "geteuid", None)
    if getter is None:
        raise OSError("geteuid_unavailable")
    return getter()


def _is_configured_path(path: str) -> bool:
    """The anonymous-staging deployment is Linux-only; require POSIX-absolute paths."""
    return path.startswith("/") and not any(
        character in path for character in (chr(0), chr(13), chr(10))
    )


def _validate_config(config: Config, deps: SaveDeps | None) -> str:
    if not config.enabled:
        return "disabled"
    target = config.target_dir
    staging = config.staging_dir
    if not target or not staging:
        return "config_invalid"
    if not _is_configured_path(target) or not _is_configured_path(staging):
        return "config_invalid"
    stat_fn = _field(deps, "stat", os.stat)
    try:
        target_info = stat_fn(target)
        staging_info = stat_fn(staging)
    except (OSError, TypeError, ValueError, AttributeError):
        return "config_invalid"
    try:
        if not stat_module.S_ISDIR(target_info.st_mode):
            return "config_invalid"
        if not stat_module.S_ISDIR(staging_info.st_mode):
            return "config_invalid"
        if target_info.st_dev != staging_info.st_dev:
            return "config_invalid"
        if (target_info.st_dev, target_info.st_ino) == (
            staging_info.st_dev,
            staging_info.st_ino,
        ):
            return "config_invalid"
        effective_uid = _field(deps, "geteuid", _default_geteuid)()
    except (OSError, AttributeError, TypeError, ValueError):
        return "config_invalid"
    if type(effective_uid) is not int:
        return "config_invalid"
    if staging_info.st_uid != effective_uid:
        return "config_invalid"
    if stat_module.S_IMODE(staging_info.st_mode) != 0o700:
        return "config_invalid"
    return "ok"


def status(*, deps: SaveDeps | None = None) -> dict:
    try:
        config = load_config(values=_field(deps, "config", None))
    except (OSError, TypeError, ValueError):
        return _failure("internal")
    data = {
        "enabled": bool(config.enabled),
        "target_dir": config.target_dir,
        "staging_dir": config.staging_dir,
        "quality_order": list(_quality_order_for(config.quality)),
        "max_bytes": MAX_BYTES,
    }
    if not config.enabled:
        return {
            "ok": False,
            "code": "disabled",
            "message": _MESSAGES["disabled"],
            "data": data,
        }
    return {"ok": True, "code": "ok", "message": "", "data": data}


def _search_item_artist(item: Mapping) -> str:
    entries = item.get("ar")
    if isinstance(entries, list):
        names = [
            entry["name"].strip()
            for entry in entries
            if isinstance(entry, Mapping)
            and type(entry.get("name")) is str
            and entry["name"].strip()
        ]
        if names:
            return " / ".join(names)
    artist = item.get("artist")
    return artist.strip() if type(artist) is str else ""


def _search_item_album(item: Mapping) -> str:
    for key in ("al", "album"):
        value = item.get(key)
        if isinstance(value, Mapping):
            name = value.get("name")
            if type(name) is str and name.strip():
                return name.strip()
    for key in ("album_name", "album"):
        value = item.get(key)
        if type(value) is str and value.strip():
            return value.strip()
    return ""


def _search_item_duration(item: Mapping) -> int:
    duration_ms = item.get("dt")
    if type(duration_ms) is int and not isinstance(duration_ms, bool) and duration_ms > 0:
        return duration_ms
    raw = item.get("duration")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        value = float(raw)
        if value > 0:
            # Raw NetEase rows carry ms; the service web fallback already normalizes to s.
            return int(round(value if value > 10000 else value * 1000.0))
    return 0


def _sanitize_search_item(item: object) -> "dict | None":
    if not isinstance(item, Mapping):
        return None
    song_id = item.get("song_id")
    if type(song_id) is not int or isinstance(song_id, bool):
        song_id = _positive_int(item.get("id"))
    if type(song_id) is not int or isinstance(song_id, bool) or song_id <= 0:
        return None
    title = _text(item.get("name")) or _text(item.get("title")) or _text(item.get("song_name"))
    quality = _text(item.get("quality")) or _text(item.get("level"))
    return {
        "song_id": song_id,
        "title": title.strip(),
        "artist": _search_item_artist(item),
        "album": _search_item_album(item),
        "quality": quality.strip(),
        "duration": _search_item_duration(item),
    }


def search_tracks(keyword: object, *, deps: SaveDeps | None = None) -> dict:
    try:
        if deps is not None and not isinstance(deps, SaveDeps):
            return _failure("internal")
        if type(keyword) is not str:
            return _failure("invalid_keyword")
        cleaned = keyword.strip()
        if not cleaned or len(cleaned) > MAX_SEARCH_KEYWORD_CHARS:
            return _failure("invalid_keyword")
        config = load_config(values=_field(deps, "config", None))
        if not config.enabled:
            return _failure("disabled")
        http_json = _field(deps, "http_json", _default_http_json)
        url = (
            config.musicbox_url.rstrip("/")
            + "/api/v1/search?keyword=" + quote(cleaned, safe="")
            + "&type=song&limit=" + str(SEARCH_LIMIT)
        )
        try:
            payload = http_json(url)
        except Exception:
            raise _SaveFailure("service_unavailable") from None
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            raise _SaveFailure("service_unavailable")
        raw_items = payload.get("data")
        if not isinstance(raw_items, list):
            raise _SaveFailure("service_unavailable")
        items: list[dict] = []
        for raw_item in raw_items:
            sanitized = _sanitize_search_item(raw_item)
            if sanitized is not None:
                items.append(sanitized)
            if len(items) >= SEARCH_LIMIT:
                break
        return _result(True, "ok", {
            "items": items,
            "keyword": cleaned,
            "limit": SEARCH_LIMIT,
        })
    except _SaveFailure as exc:
        return _failure(exc.code)
    except Exception:
        return _failure("internal")


def save_track(song_id: object, *, deps: SaveDeps | None = None) -> dict:
    try:
        if type(song_id) is not int or song_id <= 0:
            return _failure("invalid_song_id")
        if deps is not None and not isinstance(deps, SaveDeps):
            return _failure("internal")
        config = load_config(values=_field(deps, "config", None))
        if not config.enabled:
            return _failure("disabled")
        validation = _validate_config(config, deps)
        if validation != "ok":
            return _failure(validation)
        if not _SAVE_LOCK.acquire(blocking=False):
            return _failure("busy")
        try:
            return _save_locked(song_id, config, deps)
        finally:
            _SAVE_LOCK.release()
    except _SaveFailure as exc:
        return _failure(exc.code)
    except Exception:
        return _failure("internal")


def _save_locked(song_id: int, config: Config, deps: SaveDeps | None) -> dict:
    http_json = _field(deps, "http_json", _default_info_http_json)
    media_resolve = _field(deps, "media_resolve", None)
    download = _field(deps, "download", None)
    if download is None:
        def download(media_url: str, destination: Path) -> object:
            return _default_download(media_url, destination, resolve=media_resolve)
    probe = _field(deps, "probe", _default_ffprobe)
    decode_check = _field(deps, "decode_check", _default_decode_check)
    tag = _field(deps, "tag", _default_tag)
    open_store = _field(deps, "open_store", _default_open_store)
    make_tempdir = _field(deps, "make_tempdir", _default_make_tempdir)

    track_metadata = _fetch_song_info(http_json, config.musicbox_url, song_id)
    title = track_metadata.title
    artist = track_metadata.artist
    album = track_metadata.album
    track_number = track_metadata.track_number
    disc_number = track_metadata.disc_number
    quality_order = _quality_order_for(config.quality)
    quality = _select_quality(http_json, config.musicbox_url, song_id, quality_order=quality_order)
    quality_data = quality.data
    media_url = quality.media_url
    api_type = _text(quality_data.get("type")).strip().casefold()
    source_type = api_type if api_type in {"flac", "mp3"} else ""

    store = None
    workdir = None
    try:
        try:
            workdir = Path(make_tempdir())
        except Exception:
            raise _SaveFailure("download_failed") from None
        source_path = workdir / "source.bin"
        try:
            download_result = download(media_url, source_path)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("download_failed") from None
        downloaded_size, downloaded_md5 = _normalize_download(download_result, source_path)

        api_size = quality_data.get("size")
        if type(api_size) is int and not isinstance(api_size, bool) and api_size > 0:
            if api_size != downloaded_size:
                raise _SaveFailure("integrity_mismatch")
        api_md5 = quality_data.get("md5")
        if type(api_md5) is str and api_md5.strip():
            if api_md5.strip().casefold() != downloaded_md5:
                raise _SaveFailure("integrity_mismatch")

        try:
            probe_result = probe(source_path)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("invalid_audio") from None
        actual_format = _probed_audio_format(probe_result)
        if not source_type:
            source_type = actual_format

        try:
            decode_check(source_path)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("invalid_audio") from None

        source_cover_indexes = _attached_pic_stream_indexes(probe_result)
        cover_asset = None
        cover_path = None
        cover_error = ""
        if not config.cover_enabled:
            source_cover_indexes = ()
            cover_status = "not_attempted"
        elif source_cover_indexes:
            cover_status = "preserved"
        elif track_metadata.cover_url:
            try:
                cover_asset = _fetch_cover_asset(track_metadata.cover_url, deps)
            except Exception as exc:
                cover_asset = None
                cover_status = "error"
                cover_error = _cover_error_code(exc, stage="fetch")
            else:
                if actual_format == "mp3" and cover_asset.mime == "image/webp":
                    cover_status = "unsupported"
                else:
                    try:
                        cover_path = _write_cover_file(workdir, cover_asset)
                        _field(deps, "verify_cover", _default_verify_cover)(cover_path)
                        cover_status = "embedded"
                    except Exception as exc:
                        cover_asset = None
                        cover_path = None
                        cover_status = "error"
                        cover_error = _cover_error_code(exc, stage="verify")
        else:
            cover_status = "unavailable"

        lyric_bytes = None
        if not config.lyric_enabled:
            lyric_status = "not_attempted"
        else:
            try:
                lyric_json = _field(deps, "lyric_json", _default_lyric_http_json)
                lyric_bytes = _fetch_lyric_bytes(lyric_json, config.musicbox_url, song_id)
                lyric_status = "unavailable" if lyric_bytes is None else "not_attempted"
            except Exception:
                lyric_status = "error"

        # The song-detail object has no credit fields; fall back to the credit
        # lines of the lyric text that is already fetched (and later saved as
        # the sibling .lrc sidecar). Explicit /info credits always win.
        lyricist = track_metadata.lyricist
        composer = track_metadata.composer
        used_lyric_credit = False
        if not lyricist or not composer:
            parsed_lyricist, parsed_composer = _credits_from_lyric(lyric_bytes)
            if not lyricist and parsed_lyricist:
                lyricist = parsed_lyricist
                used_lyric_credit = True
            if not composer and parsed_composer:
                composer = parsed_composer
                used_lyric_credit = True
        info_credit = bool(track_metadata.lyricist or track_metadata.composer)
        if not lyricist and not composer:
            credits_source = "upstream_unavailable"
        elif used_lyric_credit and info_credit:
            credits_source = "mixed"
        elif used_lyric_credit:
            credits_source = "lyric"
        else:
            credits_source = "info"

        metadata: dict[str, str] = {"title": title}
        if artist:
            metadata["artist"] = artist
        if album:
            metadata["album"] = album
        if track_number is not None:
            metadata["track"] = str(track_number)
        if disc_number is not None:
            metadata["disc"] = str(disc_number)
        if lyricist:
            metadata["lyricist"] = lyricist
        if composer:
            metadata["composer"] = composer
        tag_metadata = _TagMetadata(
            metadata,
            cover_path=cover_path,
            source_cover_indexes=source_cover_indexes,
        )
        tagged_path = workdir / ("tagged." + actual_format)
        try:
            tag(source_path, tagged_path, tag_metadata)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("tag_failed") from None
        _tagged_file_size(tagged_path)
        expected_cover = bool(source_cover_indexes or cover_path is not None)
        validate_tagged = _field(deps, "validate_tagged", _default_validate_tagged)
        try:
            tagged_probe = validate_tagged(tagged_path, actual_format, metadata, expected_cover)
        except _SaveFailure:
            raise _SaveFailure("tag_failed") from None
        except Exception:
            raise _SaveFailure("tag_failed") from None
        cover_embedded = bool(_attached_pic_stream_indexes(tagged_probe))
        if expected_cover:
            if cover_embedded:
                cover_status = "preserved" if source_cover_indexes else "embedded"
            else:
                # A requested image that was not safely attached after tagging.
                cover_status = "error"
                cover_error = "embed_failed"
        elif cover_status in {"embedded", "preserved"}:
            cover_status = "error"
            cover_error = "embed_failed"
        if cover_status != "error":
            cover_error = ""

        basename = build_basename(artist, title, actual_format)

        data = _success_data(
            status_value="published",
            song_id=song_id,
            title=title,
            artist=artist,
            album=album,
            quality_requested=quality.requested,
            quality_actual=quality.actual,
            quality_actual_status=quality.actual_status,
            quality_fallback=quality.fallback,
            source_type=source_type,
            actual_format=actual_format,
            size=downloaded_size,
            md5=downloaded_md5,
            basename=basename,
            target_dir=config.target_dir,
            cover_error=cover_error,
        )
        data.update({
            "title_status": "tagged",
            "artist_status": "tagged" if artist else "unavailable",
            "album_status": "tagged" if album else "unavailable",
            "lyricist": lyricist,
            "lyricist_status": "tagged" if lyricist else "unavailable",
            "composer": composer,
            "composer_status": "tagged" if composer else "unavailable",
            "credits_source": credits_source,
            "cover_status": cover_status,
            "cover_error": cover_error,
            "lyric_status": lyric_status,
            "cover_embedded": cover_embedded,
            "ownership": "",
            "lyric_ownership": "",
        })

        try:
            store = open_store(config.target_dir, config.staging_dir)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("staging_unavailable") from None
        try:
            handle = store.begin_owned_stage(TARGET_ID)
            # Fingerprint the exact tagged bytes handed to the no-replace
            # staging store. This is content evidence for later reconciliation;
            # it is not a re-read of the final filesystem entry.
            published_digest = hashlib.md5()
            published_sha256 = hashlib.sha256()
            published_size = 0
            with tagged_path.open("rb") as tagged_file:
                while True:
                    chunk = tagged_file.read(core.MAX_CHUNK_BYTES)
                    if not chunk:
                        break
                    published_digest.update(chunk)
                    published_sha256.update(chunk)
                    published_size += len(chunk)
                    store.append(handle, chunk)
            store.seal(handle)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("staging_unavailable") from None
        try:
            published = store.publish_noreplace(handle, basename)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("publish_failed") from None
        outcome = _store_outcome(published)
        if outcome == "published":
            # Publish evidence is only exposed after a successful no-replace
            # commit. The digest covers the exact tagged chunks submitted via
            # store.append, not a later filesystem reread.
            data["published_size"] = published_size
            data["published_md5"] = published_digest.hexdigest()
            data["published_sha256"] = published_sha256.hexdigest()
            data["ownership"] = _store_ownership(store)
            if data["ownership"] != "tagged":
                _LOGGER.warning(
                    "manual save ownership_unavailable: published file ownership "
                    "tagging not confirmed")
                _log_ownership_failure("ownership_unavailable")
            if lyric_bytes is not None:
                lyric_basename = basename.rsplit(".", 1)[0] + ".lrc"
                data["lyric_status"] = _publish_sidecar(store, lyric_basename, lyric_bytes)
                if data["lyric_status"] == "published":
                    data["lyric_ownership"] = _store_ownership(store)
                    if data["lyric_ownership"] != "tagged":
                        _LOGGER.warning(
                            "manual save lyric_ownership_unavailable: lyric "
                            "ownership tagging not confirmed")
                        _log_ownership_failure("lyric_ownership_unavailable")
            return _result(True, "ok", data)
        if outcome == "conflict":
            data["status"] = "conflict"
            data["cover_status"] = "not_published"
            data["cover_error"] = ""
            data["cover_embedded"] = False
            data["lyric_status"] = "not_attempted"
            return _result(False, "conflict", data)
        raise _SaveFailure("publish_failed")
    finally:
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


_RETRYABLE_HTTP_STATUSES = frozenset({408, 429})


def _is_retryable_metadata_error(exc: BaseException) -> bool:
    """True only for transient transport/HTTP failures (never payload errors)."""
    if isinstance(exc, _SaveFailure):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP_STATUSES or 500 <= exc.code <= 599
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if isinstance(exc, OSError):
        return True
    return False


def _retry_transient(
    operation: Callable[[], object],
    *,
    max_attempts: int,
    retry_delay: float,
    total_budget: float,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> object:
    """Bounded retry for one metadata HTTP call; never retries payload errors.

    ``sleep``/``monotonic`` default to the module-level ``time`` attributes at
    call time, so tests can monkeypatch ``route.time.sleep`` without waiting.
    """
    sleep_fn = time.sleep if sleep is None else sleep
    clock = time.monotonic if monotonic is None else monotonic
    deadline = clock() + total_budget
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return operation()
        except Exception as exc:
            if not _is_retryable_metadata_error(exc):
                raise
            last_error = exc
        if attempt >= max_attempts:
            break
        remaining = deadline - clock()
        if remaining <= 0:
            break
        sleep_fn(min(retry_delay, remaining))
    if last_error is None:
        raise _SaveFailure("service_unavailable")
    raise last_error


def _fetch_song_info(
    http_json: Callable[[str], object], musicbox_url: str, song_id: int
) -> _TrackMetadata:
    url = f"{musicbox_url.rstrip('/')}/api/v1/song/{song_id}/info"
    try:
        payload = _retry_transient(
            lambda: http_json(url),
            max_attempts=INFO_MAX_ATTEMPTS,
            retry_delay=INFO_RETRY_DELAY,
            total_budget=INFO_TOTAL_BUDGET,
        )
    except Exception:
        raise _SaveFailure("service_unavailable") from None
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise _SaveFailure("service_unavailable")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise _SaveFailure("service_unavailable")
    name = data.get("name")
    if type(name) is not str or not name.strip():
        raise _SaveFailure("service_unavailable")
    artists: list[str] = []
    artist_entries = data.get("ar")
    if isinstance(artist_entries, list):
        for entry in artist_entries:
            if isinstance(entry, Mapping):
                artist_name = entry.get("name")
                if type(artist_name) is str and artist_name.strip():
                    artists.append(artist_name.strip())
    album = ""
    cover_url = ""
    album_entry = data.get("al")
    if isinstance(album_entry, Mapping):
        album_name = album_entry.get("name")
        if type(album_name) is str and album_name.strip():
            album = album_name.strip()
        raw_cover_url = album_entry.get("picUrl")
        if type(raw_cover_url) is str:
            cover_url = raw_cover_url.strip()
    return _TrackMetadata(
        title=name.strip(),
        artist=ARTIST_SEPARATOR.join(artists),
        album=album,
        track_number=_positive_int(data.get("no")),
        disc_number=_positive_int(data.get("cd")),
        cover_url=cover_url,
        lyricist=_credit_text(data.get("lyricist")),
        composer=_credit_text(data.get("composer")),
    )


def _credit_text(value: object) -> str:
    """Read only an explicitly named credit; artists never stand in for credits."""
    if type(value) is str:
        return value.strip()[:256]
    if isinstance(value, Mapping):
        name = value.get("name")
        return name.strip()[:256] if type(name) is str else ""
    if isinstance(value, list):
        names = [
            entry.get("name", "").strip()[:256]
            for entry in value
            if isinstance(entry, Mapping) and type(entry.get("name")) is str
            and entry.get("name").strip()
        ]
        return ARTIST_SEPARATOR.join(names)[:512]
    return ""


# The NetEase song-detail object used by ``/api/v1/song/<id>/info`` carries
# ``ar``/``al`` but no credit fields (verified against NEMbox's weapi
# ``v3/song/detail``). The lyric endpoint, however, prefixes the timed lyrics
# with credit lines, e.g. "[00:00.000] 作词 : 周杰伦" / "[00:01.000] 作曲 : 周杰伦".
# These are the only upstream credits available to this route, so they are
# parsed from the same lyric text that is saved as the .lrc sidecar.
_CREDIT_LINE_RE = re.compile(
    r"^[ \t]*(?:\[[^\]\r\n]{0,64}\][ \t]*)?"
    r"(?P<label>作词|作曲|词|曲|lyricist|composer)[ \t]*[:：][ \t]*"
    r"(?P<value>.+?)[ \t]*$",
    re.IGNORECASE,
)
_CREDIT_SCAN_LINES = 64
_CREDIT_MAX_CHARS = 512
_CREDIT_LABELS = {
    "作词": "lyricist",
    "词": "lyricist",
    "lyricist": "lyricist",
    "作曲": "composer",
    "曲": "composer",
    "composer": "composer",
}


def _credits_from_lyric(lyric_bytes: object) -> tuple[str, str]:
    """Extract ``(lyricist, composer)`` from the leading credit lines of a lyric.

    Only the first ``_CREDIT_SCAN_LINES`` lines are inspected; the first
    explicit credit wins per role. Returns empty strings when the lyric is
    missing, undecodable, or carries no credit lines. Never raises.
    """
    if not isinstance(lyric_bytes, (bytes, bytearray)):
        return "", ""
    try:
        text = bytes(lyric_bytes).decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "", ""
    found = {"lyricist": "", "composer": ""}
    for line in text.splitlines()[:_CREDIT_SCAN_LINES]:
        match = _CREDIT_LINE_RE.match(line)
        if match is None:
            continue
        role = _CREDIT_LABELS.get(match.group("label").casefold())
        if role is None or found[role]:
            continue
        value = match.group("value").strip()[:_CREDIT_MAX_CHARS]
        if value:
            found[role] = value
        if found["lyricist"] and found["composer"]:
            break
    return found["lyricist"], found["composer"]


def _fetch_lyric_bytes(
    http_json: Callable[[str], object], musicbox_url: str, song_id: int
) -> bytes | None:
    url = f"{musicbox_url.rstrip('/')}/api/v1/song/{song_id}/lyric"
    payload = _retry_transient(
        lambda: http_json(url),
        max_attempts=LYRIC_MAX_ATTEMPTS,
        retry_delay=LYRIC_RETRY_DELAY,
        total_budget=LYRIC_TOTAL_BUDGET,
    )
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        raise ValueError("lyric_unavailable")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("lyric_payload_invalid")
    lyric = data.get("lyric")
    if lyric is None or (type(lyric) is str and not lyric.strip()):
        lyric = data.get("tlyric")
    if lyric is None:
        return None
    if type(lyric) is not str:
        raise ValueError("lyric_payload_invalid")
    cleaned = lyric.strip()
    if not cleaned:
        return None
    try:
        encoded = (cleaned + "\n").encode("utf-8", errors="strict")
    except UnicodeError:
        raise ValueError("lyric_payload_invalid") from None
    if len(encoded) > MAX_LYRIC_BYTES:
        raise ValueError("lyric_too_large")
    return encoded


def _attached_pic_stream_indexes(probe_result: object) -> tuple[int, ...]:
    if not isinstance(probe_result, Mapping):
        return ()
    streams = probe_result.get("streams")
    if not isinstance(streams, list):
        return ()
    indexes: list[int] = []
    for stream in streams:
        if not isinstance(stream, Mapping) or stream.get("codec_type") != "video":
            continue
        disposition = stream.get("disposition")
        index = stream.get("index")
        if (
            isinstance(disposition, Mapping)
            and disposition.get("attached_pic") in (1, True)
            and type(index) is int and index >= 0
        ):
            indexes.append(index)
    return tuple(indexes)


def _normalize_cover_url(value: object) -> str:
    if type(value) is not str or not value or len(value) > 4096:
        raise ValueError("cover_url_invalid")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("cover_url_invalid")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("cover_url_invalid") from None
    host = (parsed.hostname or "").casefold()
    scheme = parsed.scheme.casefold()
    valid_port = (
        port is None
        or (scheme == "http" and port == 80)
        or (scheme == "https" and port == 443)
    )
    if (
        scheme not in {"http", "https"}
        or parsed.username is not None or parsed.password is not None
        or not valid_port
        or parsed.fragment
        or not parsed.path.startswith("/")
    ):
        raise ValueError("cover_url_invalid")
    if host not in NETEASE_COVER_HOSTS:
        raise ValueError("cover_url_untrusted")
    # The source API historically returns both http and https picUrl values. A
    # trusted image hostname is upgraded; no redirect is followed by the fetcher.
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def _public_cover_addresses(host: str) -> list[str]:
    try:
        rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ValueError("cover_dns_resolve_failed") from None
    if len(rows) > 8:
        raise ValueError("cover_dns_too_many_addresses")
    addresses: list[str] = []
    for row in rows:
        try:
            address = ipaddress.ip_address(row[4][0])
        except (ValueError, IndexError):
            raise ValueError("cover_dns_invalid") from None
        if not address.is_global:
            raise ValueError("cover_dns_not_public")
        text = str(address)
        if text not in addresses:
            addresses.append(text)
    if not addresses:
        raise ValueError("cover_dns_empty")
    return addresses


class _PinnedCoverConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to a prevalidated public address."""

    def __init__(self, host: str, address: str):
        super().__init__(host, port=443, timeout=COVER_TIMEOUT, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        raw_socket = socket.create_connection((self._pinned_address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)
        except Exception:
            raw_socket.close()
            raise


def _sniff_cover_mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _normalize_cover_declared_mime(value: object) -> str:
    """Canonicalize one declared ``Content-Type`` or return ``""`` (A1).

    Accepts only the closed alias table, optional OWS, and at most one
    ``charset=<token>`` parameter. Multiple parameters, other names, quoted or
    empty values, extra ``;``, commas, spaces in the token, ``*`` or ``/`` in the
    media type all yield ``""``; the caller then fails closed as
    ``cover_mime_invalid``.
    """
    if type(value) is not str:
        return ""
    segments = value.split(";")
    if len(segments) > 2:
        return ""
    canonical = _COVER_DECLARED_MIME_ALIASES.get(segments[0].strip().casefold())
    if canonical is None:
        return ""
    if len(segments) == 2:
        name, separator, token = segments[1].partition("=")
        if not separator or name.strip().casefold() != "charset":
            return ""
        if _COVER_MIME_PARAM_TOKEN_RE.fullmatch(token.strip()) is None:
            return ""
    return canonical


def _default_cover_fetch(url: str) -> object:
    safe_url = _normalize_cover_url(url)
    parsed = urlsplit(safe_url)
    host = parsed.hostname or ""
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="%/?@!$&'()*+,;=:-._~")
    failure_class = "unknown"
    for address in _public_cover_addresses(host):
        connection = _PinnedCoverConnection(host, address)
        try:
            connection.request(
                "GET", path,
                headers={
                    "Host": host,
                    "Accept": "image/jpeg,image/png,image/webp",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                    "User-Agent": "fnmusic-ext/cover-fetch",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("cover_http_status")
            raw_length = response.getheader("Content-Length")
            if raw_length is not None:
                if not raw_length.isdigit() or int(raw_length) > MAX_COVER_BYTES:
                    raise ValueError("cover_too_large")
            if response.getheader("Content-Encoding", "identity").casefold() != "identity":
                raise ValueError("cover_encoding_invalid")
            data = response.read(MAX_COVER_BYTES + 1)
            if not data or len(data) > MAX_COVER_BYTES:
                raise ValueError("cover_too_large")
            declared_mime = _normalize_cover_declared_mime(
                response.getheader("Content-Type", ""))
            actual_mime = _sniff_cover_mime(data)
            if actual_mime not in _COVER_MIME_EXTENSIONS or declared_mime != actual_mime:
                raise ValueError("cover_mime_invalid")
            return {"data": data, "mime": actual_mime}
        except Exception as exc:
            # Only a fixed class token survives; never type(last_error).__name__,
            # a hostname, an IP, or a URL fragment.
            failure_class = _cover_fetch_failure_class(exc)
        finally:
            connection.close()
    raise ValueError("cover_fetch_" + failure_class)


def _cover_fetch_failure_class(exc: BaseException) -> str:
    """Classify one per-address cover fetch failure into a redacted class token."""
    if isinstance(exc, ValueError):
        reason = str(exc)
        if reason == "cover_http_status":
            return "http_error"
        if reason in {"cover_too_large", "cover_encoding_invalid", "cover_mime_invalid"}:
            return "payload_invalid"
    if isinstance(
        exc,
        (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError),
    ):
        return "http_error"
    return "unknown"


def _cover_error_code(exc: BaseException, *, stage: str = "fetch") -> str:
    """Map one cover failure to the fixed observable `cover_error` token."""
    if isinstance(exc, ValueError):
        token = _COVER_ERROR_REASONS.get(str(exc))
        if token is not None:
            return token
    if stage == "verify":
        if isinstance(exc, (OSError, ValueError, subprocess.SubprocessError)):
            return "decode_failed"
        return "unknown"
    if isinstance(exc, (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError)):
        return "http_error"
    return "unknown"


def _default_verify_cover(path: Path) -> None:
    try:
        data = path.read_bytes()
        if not data or len(data) > MAX_COVER_BYTES:
            raise ValueError("cover_too_large")
        mime = _sniff_cover_mime(data)
        if not mime:
            raise ValueError("cover_mime_invalid")
        _validate_cover_dimensions(*_cover_dimensions(data, mime))
    except (OSError, ValueError):
        raise ValueError("cover_decode_failed") from None
    command = [
        FFMPEG_BIN, "-v", "error", "-threads", "1", "-filter_threads", "1",
        "-max_alloc", str(MAX_COVER_DECODE_ALLOC), "-xerror",
        "-i", os.fspath(path), "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COVER_DECODE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("cover_decode_failed") from None
    if completed.returncode != 0:
        raise ValueError("cover_decode_failed")


def _fetch_cover_asset(url: str, deps: SaveDeps | None) -> _CoverAsset:
    safe_url = _normalize_cover_url(url)
    fetch = _field(deps, "cover_fetch", _default_cover_fetch)
    value = fetch(safe_url)
    if isinstance(value, Mapping):
        data, mime = value.get("data"), value.get("mime")
    elif isinstance(value, tuple) and len(value) == 2:
        data, mime = value
    else:
        raise ValueError("cover_response_invalid")
    if type(data) is not bytes or not data or len(data) > MAX_COVER_BYTES:
        raise ValueError("cover_response_invalid")
    if type(mime) is not str:
        raise ValueError("cover_mime_invalid")
    # The injected seam must already be canonical: `image/jpg` is NOT aliased
    # here (only the default fetch normalizes the server-declared header).
    normalized_mime = mime.strip().casefold()
    if normalized_mime != _sniff_cover_mime(data):
        raise ValueError("cover_mime_invalid")
    if normalized_mime not in _COVER_MIME_EXTENSIONS:
        raise ValueError("cover_mime_invalid")
    _validate_cover_dimensions(*_cover_dimensions(data, normalized_mime))
    return _CoverAsset(data, normalized_mime)


def _cover_dimensions(data: bytes, mime: str) -> tuple[int, int]:
    """Read image dimensions from its container header before invoking a decoder."""
    if mime == "image/png":
        if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
            raise ValueError("cover_dimensions_invalid")
        return int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")

    if mime == "image/jpeg":
        if not data.startswith(b"\xff\xd8"):
            raise ValueError("cover_dimensions_invalid")
        offset = 2
        sof_markers = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
        while offset < len(data):
            if data[offset] != 0xFF:
                raise ValueError("cover_dimensions_invalid")
            while offset < len(data) and data[offset] == 0xFF:
                offset += 1
            if offset >= len(data):
                break
            marker = data[offset]
            offset += 1
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7 or marker == 0x01:
                continue
            if offset + 2 > len(data):
                break
            segment_length = int.from_bytes(data[offset:offset + 2], "big")
            if segment_length < 2 or offset + segment_length > len(data):
                raise ValueError("cover_dimensions_invalid")
            if marker in sof_markers:
                if segment_length < 7:
                    raise ValueError("cover_dimensions_invalid")
                height = int.from_bytes(data[offset + 3:offset + 5], "big")
                width = int.from_bytes(data[offset + 5:offset + 7], "big")
                return width, height
            if marker == 0xDA:  # dimensions must be present before the scan data
                break
            offset += segment_length
        raise ValueError("cover_dimensions_invalid")

    if mime == "image/webp":
        if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise ValueError("cover_dimensions_invalid")
        declared_end = int.from_bytes(data[4:8], "little") + 8
        if declared_end > len(data) or declared_end < 20:
            raise ValueError("cover_dimensions_invalid")
        offset = 12
        while offset + 8 <= declared_end:
            chunk_type = data[offset:offset + 4]
            chunk_size = int.from_bytes(data[offset + 4:offset + 8], "little")
            start = offset + 8
            end = start + chunk_size
            if end > declared_end:
                raise ValueError("cover_dimensions_invalid")
            chunk = data[start:end]
            if chunk_type == b"VP8X" and len(chunk) >= 10:
                width = 1 + int.from_bytes(chunk[4:7], "little")
                height = 1 + int.from_bytes(chunk[7:10], "little")
                return width, height
            if chunk_type == b"VP8 " and len(chunk) >= 10 and chunk[3:6] == b"\x9d\x01\x2a":
                width = int.from_bytes(chunk[6:8], "little") & 0x3FFF
                height = int.from_bytes(chunk[8:10], "little") & 0x3FFF
                return width, height
            if chunk_type == b"VP8L" and len(chunk) >= 5 and chunk[0] == 0x2F:
                b1, b2, b3, b4 = chunk[1:5]
                width = 1 + b1 + ((b2 & 0x3F) << 8)
                height = 1 + (b2 >> 6) + (b3 << 2) + ((b4 & 0x0F) << 10)
                # The VP8L version occupies the top three bits. Bit 4 is the
                # alpha_is_used flag and is valid for lossless RGBA images.
                if b4 >> 5:
                    raise ValueError("cover_dimensions_invalid")
                return width, height
            offset = end + (chunk_size & 1)
        raise ValueError("cover_dimensions_invalid")

    raise ValueError("cover_dimensions_invalid")


def _validate_cover_dimensions(width: int, height: int) -> None:
    if (
        type(width) is not int or type(height) is not int
        or width <= 0 or height <= 0
        or width > MAX_COVER_WIDTH or height > MAX_COVER_HEIGHT
        or width * height > MAX_COVER_PIXELS
    ):
        raise ValueError("cover_dimensions_unsafe")


def _write_cover_file(workdir: Path, asset: _CoverAsset) -> Path:
    extension = _COVER_MIME_EXTENSIONS[asset.mime]
    path = workdir / ("source-cover" + extension)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            view = memoryview(asset.data)
            while view:
                written = handle.write(view)
                if not written:
                    raise OSError("cover_write_failed")
                view = view[written:]
            handle.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return path


def _publish_sidecar(store: object, basename: str, data: bytes) -> str:
    stage = None
    publication_started = False
    try:
        stage = store.begin_owned_stage(TARGET_ID)
        if stage is None:
            return "error"
        for offset in range(0, len(data), core.MAX_CHUNK_BYTES):
            store.append(stage, data[offset:offset + core.MAX_CHUNK_BYTES])
        store.seal(stage)
        publication_started = True
        outcome = _store_outcome(store.publish_noreplace(stage, basename))
        if outcome == "published":
            store.release_owned_stage(stage)
            stage = None
            return "published"
        if outcome == "conflict":
            store.abort_owned_stage(stage)
            stage = None
            return "conflict"
        return "unknown"
    except Exception:
        return "unknown" if publication_started else "error"
    finally:
        if stage is not None and not publication_started:
            try:
                store.abort_owned_stage(stage)
            except Exception:
                pass


_QUALITY_UNAVAILABLE = "unavailable"
_QUALITY_TRIAL = "trial"
_QUALITY_AVAILABLE = "available"
_QUALITY_ERROR = "error"


def _quality_error(code: str) -> _QualityVerdict:
    return _QualityVerdict(state=_QUALITY_ERROR, failure_code=code)


def _quality_identity_matches(data: Mapping, song_id: int) -> bool:
    """``data.id`` must identify the requested song; an optional alias must agree."""
    identity = _positive_int(data.get("id"))
    if identity is None or identity != song_id:
        return False
    if "songId" in data:
        alias = _positive_int(data.get("songId"))
        if alias is None or alias != identity or alias != song_id:
            return False
    return True


def _reported_quality_level(value: object) -> tuple[str, str]:
    """Return ``(token, status)``; only a legal server token is claimed as reported."""
    if type(value) is not str:
        return "", "unknown"
    token = value.strip().casefold()
    if not token or QUALITY_LEVEL_TOKEN_RE.fullmatch(token) is None:
        return "", "unknown"
    return token, "reported"


def _evaluate_quality_response(
    payload: object, song_id: int, tier: str
) -> _QualityVerdict:
    """Classify one ``/song/<id>/url`` response; pure, no I/O.

    ``tier`` is the ladder token that produced ``payload`` (kept for callers and
    tests; the verdict itself depends only on the response). The ladder may only
    fall through on *definitive* evidence: a 404 without a URL, or an explicit
    non-empty ``freeTrialInfo`` trial marker. Anything that cannot be verified
    fails closed instead of silently downgrading. A reported ``level`` is never a
    failure, even when it is lower than the requested tier.
    """
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        return _quality_error("quality_unverified")
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return _quality_error("quality_unverified")
    code = data.get("code")
    if type(code) is not int or code not in (200, 404):
        return _quality_error("quality_unverified")
    if not _quality_identity_matches(data, song_id):
        return _quality_error("quality_identity_mismatch")
    media_url = data.get("url")
    if code == 404:
        if media_url is None or (type(media_url) is str and not media_url):
            return _QualityVerdict(state=_QUALITY_UNAVAILABLE)
        return _quality_error("quality_unverified")
    if type(media_url) is not str or not media_url.startswith(("http://", "https://")):
        return _quality_error("quality_unverified")
    if "freeTrialInfo" not in data:
        # A missing key is not evidence of a full track; only an explicit null is.
        return _quality_error("quality_unverified")
    trial = data.get("freeTrialInfo")
    if trial is None:
        actual, actual_status = _reported_quality_level(data.get("level"))
        return _QualityVerdict(
            state=_QUALITY_AVAILABLE,
            data=data,
            media_url=media_url,
            actual=actual,
            actual_status=actual_status,
        )
    if isinstance(trial, Mapping) and trial:
        return _QualityVerdict(state=_QUALITY_TRIAL)
    return _quality_error("quality_unverified")


def _select_quality(
    http_json: Callable[[str], object],
    musicbox_url: str,
    song_id: int,
    *,
    quality_order: tuple[str, ...] = QUALITY_ORDER,
) -> _QualitySelection:
    base = musicbox_url.rstrip("/")
    trial_seen = False
    for index, quality in enumerate(quality_order):
        url = f"{base}/api/v1/song/{song_id}/url?quality={quality}"
        try:
            payload = http_json(url)
        except (OSError, TimeoutError):
            raise _SaveFailure("service_unavailable") from None
        except Exception:
            raise _SaveFailure("quality_unverified") from None
        verdict = _evaluate_quality_response(payload, song_id, quality)
        if verdict.state == _QUALITY_ERROR:
            raise _SaveFailure(verdict.failure_code)
        if verdict.state == _QUALITY_TRIAL:
            trial_seen = True
            continue
        if verdict.state == _QUALITY_UNAVAILABLE:
            continue
        fallback = index > 0 or (
            verdict.actual_status == "reported" and verdict.actual != quality
        )
        return _QualitySelection(
            requested=quality,
            data=verdict.data if verdict.data is not None else {},
            media_url=verdict.media_url,
            actual=verdict.actual,
            actual_status=verdict.actual_status,
            fallback=fallback,
        )
    if trial_seen:
        raise _SaveFailure("trial_only")
    raise _SaveFailure("no_quality_available")


def _normalize_download(download_result: object, source_path: Path) -> tuple[int, str]:
    if not isinstance(download_result, Mapping):
        raise _SaveFailure("download_failed")
    size = download_result.get("size")
    md5 = download_result.get("md5")
    if type(size) is not int or isinstance(size, bool) or not 0 < size <= MAX_BYTES:
        raise _SaveFailure("download_failed")
    if type(md5) is not str or MD5_RE.fullmatch(md5) is None:
        raise _SaveFailure("download_failed")
    try:
        actual_size = source_path.stat().st_size
    except OSError:
        raise _SaveFailure("download_failed") from None
    if actual_size != size:
        raise _SaveFailure("download_failed")
    return size, md5.casefold()


def _probed_audio_format(probe_result: object) -> str:
    if not isinstance(probe_result, Mapping):
        raise _SaveFailure("invalid_audio")
    streams = probe_result.get("streams")
    if not isinstance(streams, list):
        raise _SaveFailure("invalid_audio")
    if not any(
        isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
        for stream in streams
    ):
        raise _SaveFailure("invalid_audio")
    format_entry = probe_result.get("format")
    if not isinstance(format_entry, Mapping):
        raise _SaveFailure("invalid_audio")
    format_name = format_entry.get("format_name")
    if type(format_name) is not str:
        raise _SaveFailure("invalid_audio")
    normalized = format_name.strip().casefold().split(",")[0].strip()
    if normalized == "flac":
        return "flac"
    if normalized == "mp3":
        return "mp3"
    raise _SaveFailure("invalid_audio")


def _tagged_file_size(path: Path) -> int:
    try:
        size = path.stat().st_size
    except OSError:
        raise _SaveFailure("tag_failed") from None
    if size <= 0 or size > MAX_BYTES + MAX_TAGGED_EXTRA_BYTES:
        raise _SaveFailure("tag_failed")
    return size


def _store_outcome(result: object) -> str:
    value = getattr(result, "value", result)
    if type(value) is not str:
        return ""
    return value if value in {"published", "conflict"} else ""


def _store_ownership(store: object) -> str:
    """Advisory ownership outcome of the store's latest publication.

    The storage adapter reports ``"tagged"`` only when it confirmed the
    published inode carries the destination directory's owner/group with mode
    0640. Adapters without that telemetry (including test fakes) are reported
    as ``"unavailable"``; ownership never changes save success semantics.
    """
    value = getattr(store, "last_ownership", None)
    if type(value) is str and value in {"tagged", "unavailable"}:
        return value
    return "unavailable"


def _log_ownership_failure(code: str) -> None:
    """Best-effort fixed-code line in the desktop gateway log.

    Only runs on Linux (the deployment target), so the Windows test runner never
    touches the gateway log path. The import is lazy because ``webui_gateway``
    imports this module; a failure to log never affects the save result.
    """
    try:
        if not sys.platform.startswith("linux"):
            return
        if __package__:
            from . import webui_gateway as gateway  # noqa: PLC0415
        else:
            import webui_gateway as gateway  # noqa: PLC0415
        gateway.gateway_log(code)
    except Exception:  # noqa: BLE001 - diagnostics never affect a save
        pass


def _success_data(
    *,
    status_value: str,
    song_id: int,
    title: str,
    artist: str,
    album: str,
    quality_requested: str,
    quality_actual: str,
    quality_actual_status: str,
    quality_fallback: bool,
    source_type: str,
    actual_format: str,
    size: int,
    md5: str,
    basename: str,
    target_dir: str,
    cover_error: str,
) -> dict:
    return {
        "status": status_value,
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "album": album,
        "quality_requested": quality_requested,
        "quality_actual": quality_actual,
        "quality_actual_status": quality_actual_status,
        "quality_fallback": quality_fallback,
        "source_type": source_type,
        "format": actual_format,
        "size": size,
        "md5": md5,
        "basename": basename,
        "target_dir": target_dir,
        "cover_error": cover_error,
        "tagged": True,
        "cover_embedded": False,
    }


def _sanitize_filename_part(value: str) -> str:
    characters: list[str] = []
    for character in value:
        if character in _FILENAME_FORBIDDEN:
            characters.append("_")
        elif unicodedata.category(character) in _CONTROL_CATEGORIES:
            characters.append("_")
        else:
            characters.append(character)
    cleaned = "".join(characters).strip()
    while cleaned and cleaned[-1] in (" ", "."):
        cleaned = cleaned[:-1]
    return cleaned


def build_basename(artist: str, title: str, extension: str) -> str:
    safe_artist = _sanitize_filename_part(artist) or UNKNOWN_ARTIST
    safe_title = _sanitize_filename_part(title)
    suffix = "." + extension.casefold()
    stem = f"{safe_artist} - {safe_title}" if safe_title else safe_artist
    max_chars = core.MAX_BASENAME_CHARS
    while stem and (
        len(stem) + len(suffix) > max_chars
        or len(os.fsencode(stem + suffix)) > 255
    ):
        stem = stem[:-1]
    stem = stem.rstrip(" .")
    if not stem:
        stem = UNKNOWN_ARTIST
    return stem + suffix


def _anonymous_module():
    global _ANONYMOUS_MODULE
    module = _ANONYMOUS_MODULE
    if module is not None:
        return module
    if __package__:
        from . import manual_save_anonymous as module  # noqa: PLC0415
    else:
        root = str(Path(__file__).resolve().parent.parent)
        if root not in sys.path:
            sys.path.insert(0, root)
        try:
            import manual_save_anonymous as module  # noqa: PLC0415
        except ImportError:
            from proxy import manual_save_anonymous as module  # noqa: PLC0415
    _ANONYMOUS_MODULE = module
    return module


def _default_make_tempdir() -> Path:
    path = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX, dir=TEMP_ROOT))
    try:
        root = os.path.realpath(TEMP_ROOT)
        resolved = os.path.realpath(path)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)
        raise OSError("tempdir_unavailable") from None
    if resolved == root or os.path.commonpath([root, resolved]) != root:
        shutil.rmtree(path, ignore_errors=True)
        raise OSError("tempdir_unavailable")
    return path


def _default_http_json(url: str, *, timeout: float = API_TIMEOUT) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        status_code = getattr(response, "status", 200)
        if status_code != 200:
            raise OSError("http_status")
        payload = response.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("json_too_large")
    return json.loads(payload.decode("utf-8"))


def _default_lyric_http_json(url: str) -> object:
    """Lyric reads get a 30 s budget; the observed service can take 27 s+."""
    return _default_http_json(url, timeout=LYRIC_TIMEOUT)


def _default_info_http_json(url: str) -> object:
    """Info reads use the explicit INFO_TIMEOUT budget (same 15 s as API_TIMEOUT)."""
    return _default_http_json(url, timeout=INFO_TIMEOUT)


def _curl_config_quote(value: str) -> str:
    """Quote one curl-config value; framing bytes are rejected, not escaped."""
    if any(character in value for character in (chr(0), chr(13), chr(10))):
        raise _SaveFailure("download_failed")
    escaped = value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))
    return chr(34) + escaped + chr(34)


_MEDIA_URL_SCHEMES = frozenset({"http", "https"})
_MEDIA_URL_DEFAULT_PORTS = {"http": 80, "https": 443}
_MEDIA_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MEDIA_SUCCESS_STATUSES = frozenset({200, 206})
_MEDIA_HOST_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_MEDIA_HOST_MAX_CHARS = 253
_MEDIA_UNTRUSTED_V4_NETWORKS = (
    ipaddress.IPv4Network("100.64.0.0/10"),
    ipaddress.IPv4Network("198.18.0.0/15"),
)
_MEDIA_UNTRUSTED_V6_NETWORKS = (
    ipaddress.IPv6Network("::ffff:0:0/96"),
    ipaddress.IPv6Network("64:ff9b::/96"),
)


def _normalize_media_url(media_url: object) -> str:
    """Validate and canonicalize one media URL; every redirect hop repeats this.

    Framing bytes (NUL/CR/LF) map to ``download_failed`` because they would break
    the curl-config frame; every target-policy violation maps to
    ``download_target_untrusted`` and is never retried on another address. The
    query string is preserved verbatim (never decoded or re-encoded).
    """
    if type(media_url) is not str:
        raise _SaveFailure("download_failed")
    if any(character in media_url for character in (chr(0), chr(13), chr(10))):
        raise _SaveFailure("download_failed")
    if not 0 < len(media_url) <= DOWNLOAD_MAX_MEDIA_URL_CHARS:
        raise _SaveFailure("download_failed")
    for character in media_url:
        code = ord(character)
        if character in (" ", chr(92)) or code < 0x20 or code == 0x7F or code > 0x7E:
            raise _SaveFailure("download_target_untrusted")
    try:
        parts = urlsplit(media_url)
    except ValueError:
        raise _SaveFailure("download_target_untrusted") from None
    scheme = parts.scheme.casefold()
    if scheme not in _MEDIA_URL_SCHEMES:
        raise _SaveFailure("download_failed")
    if "@" in parts.netloc or "#" in media_url:
        raise _SaveFailure("download_target_untrusted")
    host = parts.hostname
    if not host or parts.netloc.startswith("["):
        raise _SaveFailure("download_target_untrusted")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise _SaveFailure("download_target_untrusted")
    if not host.isascii() or host.endswith(".") or len(host) > _MEDIA_HOST_MAX_CHARS:
        raise _SaveFailure("download_target_untrusted")
    host = host.casefold()
    labels = host.split(".")
    if any(_MEDIA_HOST_LABEL_RE.fullmatch(label) is None for label in labels):
        raise _SaveFailure("download_target_untrusted")
    try:
        port = parts.port
    except ValueError:
        raise _SaveFailure("download_target_untrusted") from None
    if port is not None and port != _MEDIA_URL_DEFAULT_PORTS[scheme]:
        raise _SaveFailure("download_target_untrusted")
    if not parts.path.startswith("/"):
        raise _SaveFailure("download_target_untrusted")
    normalized = scheme + "://" + host + parts.path
    if parts.query:
        normalized += "?" + parts.query
    return normalized


def _media_address_text(entry: object) -> str | None:
    """Extract one numeric address from a getaddrinfo entry (or a bare string)."""
    if type(entry) is str:
        return entry
    if isinstance(entry, (list, tuple)):
        if len(entry) == 5:
            sockaddr = entry[4]
            if isinstance(sockaddr, (list, tuple)) and sockaddr:
                first = sockaddr[0]
                return first if type(first) is str else None
            return None
        if 2 <= len(entry) <= 4:
            first = entry[0]
            return first if type(first) is str else None
    return None


def _media_address_is_public(address: str) -> bool:
    """Every resolved address must be a global, non-special-purpose unicast IP."""
    if not address or "%" in address or not address.isascii():
        return False
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    if not parsed.is_global:
        return False
    if (
        parsed.is_private
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_unspecified
    ):
        return False
    untrusted = (
        _MEDIA_UNTRUSTED_V4_NETWORKS
        if parsed.version == 4
        else _MEDIA_UNTRUSTED_V6_NETWORKS
    )
    return not any(parsed in network for network in untrusted)


def _resolve_media_addresses(host: str, port: int, resolve) -> list[str]:
    """Resolve one hop and enforce the address policy on the whole answer."""
    try:
        if resolve is None:
            entries = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        else:
            entries = resolve(host, port)
        entries = list(entries)
    except Exception:
        raise _SaveFailure("download_failed") from None
    addresses: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        address = _media_address_text(entry)
        if address is None:
            raise _SaveFailure("download_target_untrusted")
        if address in seen:
            continue
        seen.add(address)
        addresses.append(address)
    if not 1 <= len(addresses) <= DOWNLOAD_MAX_MEDIA_ADDRESSES:
        raise _SaveFailure("download_target_untrusted")
    if not all(_media_address_is_public(address) for address in addresses):
        raise _SaveFailure("download_target_untrusted")
    return addresses


def _curl_resolve_value(host: str, port: int, address: str) -> str:
    """Pin exactly one numeric address for curl ``--resolve``; IPv6 stays bracketed."""
    try:
        version = ipaddress.ip_address(address).version
    except ValueError:
        version = 0
    rendered = "[" + address + "]" if version == 6 else address
    return f"{host}:{port}:{rendered}"


def _curl_output_text(value: object) -> str:
    if type(value) is str:
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode("utf-8", "replace")
    return ""


def _last_curl_output_line(value: object) -> str:
    for line in reversed(_curl_output_text(value).splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _parse_probe_output(value: object) -> tuple[int, str] | None:
    line = _last_curl_output_line(value)
    if not line:
        return None
    status_text, _, redirect = line.partition(" ")
    try:
        status = int(status_text)
    except ValueError:
        return None
    return status, redirect.strip()


def _parse_transfer_status(value: object) -> int | None:
    line = _last_curl_output_line(value)
    if not line:
        return None
    try:
        return int(line.split(" ", 1)[0])
    except ValueError:
        return None


def _run_curl(config_lines: list[str], timeout_seconds: float):
    """Run one curl invocation; the URL only ever travels through stdin."""
    try:
        return subprocess.run(
            [CURL_BIN, "-K", "-"],
            input=chr(10).join(config_lines) + chr(10),
            text=True,
            timeout=timeout_seconds,
            capture_output=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _media_probe_config_lines(
    url: str,
    host: str,
    port: int,
    address: str,
    *,
    max_time_seconds: int,
    range_fallback: bool,
) -> list[str]:
    lines = [
        "url = " + _curl_config_quote(url),
        'output = "/dev/null"',
        "user-agent = " + _curl_config_quote(CURL_USER_AGENT),
        "referer = " + _curl_config_quote(CURL_REFERER),
        "silent",
        "show-error",
        "max-redirs = 0",
        "max-time = " + str(max_time_seconds),
        "resolve = " + _curl_config_quote(_curl_resolve_value(host, port, address)),
        'proto = "=http,https"',
        'write-out = "%{http_code} %{redirect_url}"',
    ]
    lines.append('range = "0-0"' if range_fallback else "head")
    return lines


def _probe_max_time(deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining < 1.0:
        raise _SaveFailure("download_failed")
    return max(1, int(min(DOWNLOAD_PROBE_TIMEOUT, remaining)))


def _probe_media_address(
    url: str,
    host: str,
    port: int,
    address: str,
    *,
    deadline: float,
) -> tuple[int, str] | None:
    """Probe one address: HEAD first, one range GET on an unusable HEAD status."""
    max_time_seconds = _probe_max_time(deadline)
    completed = _run_curl(
        _media_probe_config_lines(
            url, host, port, address,
            max_time_seconds=max_time_seconds,
            range_fallback=False,
        ),
        max_time_seconds + 10.0,
    )
    parsed = None
    if completed is not None and completed.returncode == 0:
        parsed = _parse_probe_output(completed.stdout)
    if parsed is None:
        return None
    status, redirect = parsed
    if status in _MEDIA_SUCCESS_STATUSES or status in _MEDIA_REDIRECT_STATUSES:
        return status, redirect
    # A HEAD refusal (403/405/501/204...) is not evidence against the URL itself:
    # retry the same address once as a bounded range GET.
    max_time_seconds = _probe_max_time(deadline)
    completed = _run_curl(
        _media_probe_config_lines(
            url, host, port, address,
            max_time_seconds=max_time_seconds,
            range_fallback=True,
        ),
        max_time_seconds + 10.0,
    )
    if completed is None or completed.returncode != 0:
        return None
    parsed = _parse_probe_output(completed.stdout)
    if parsed is None:
        return None
    status, redirect = parsed
    if status in _MEDIA_SUCCESS_STATUSES or status in _MEDIA_REDIRECT_STATUSES:
        return status, redirect
    return None


def _is_absolute_media_url(value: str) -> bool:
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme.casefold() in _MEDIA_URL_SCHEMES and bool(parts.netloc)


@dataclass(frozen=True, slots=True)
class _MediaProbeHit:
    address: str
    redirect_url: str


@dataclass(frozen=True, slots=True)
class _MediaTarget:
    url: str
    host: str
    port: int
    address: str


def _probe_media_hop(
    url: str,
    host: str,
    port: int,
    addresses: list[str],
    *,
    deadline: float,
) -> _MediaProbeHit:
    """Probe one hop address by address in DNS order, within the bounded budget."""
    for address in addresses[:DOWNLOAD_MAX_MEDIA_PROBE_ATTEMPTS]:
        result = _probe_media_address(url, host, port, address, deadline=deadline)
        if result is None:
            continue
        status, redirect = result
        if status in _MEDIA_SUCCESS_STATUSES:
            return _MediaProbeHit(address=address, redirect_url="")
        if redirect and _is_absolute_media_url(redirect):
            return _MediaProbeHit(address=address, redirect_url=redirect)
        # A 3xx without an absolute target is a probe failure for this address.
    raise _SaveFailure("download_failed")


def _follow_media_redirects(
    media_url: str,
    resolve: Callable[[str, int], object] | None = None,
    *,
    deadline: float | None = None,
) -> _MediaTarget:
    """Follow at most DOWNLOAD_MAX_REDIRECTS validated hops and pin one address."""
    url = _normalize_media_url(media_url)
    if deadline is None:
        deadline = time.monotonic() + DOWNLOAD_SUBPROCESS_TIMEOUT
    seen = {url}
    redirects = 0
    while True:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
        if port is None:
            port = _MEDIA_URL_DEFAULT_PORTS[parts.scheme]
        addresses = _resolve_media_addresses(host, port, resolve)
        hit = _probe_media_hop(url, host, port, addresses, deadline=deadline)
        if not hit.redirect_url:
            return _MediaTarget(
                url=url, host=host, port=port, address=hit.address,
            )
        redirects += 1
        if redirects > DOWNLOAD_MAX_REDIRECTS:
            raise _SaveFailure("download_failed")
        next_url = _normalize_media_url(hit.redirect_url)
        if parts.scheme == "https" and next_url.startswith("http://"):
            raise _SaveFailure("download_target_untrusted")
        if next_url in seen:
            raise _SaveFailure("download_failed")
        seen.add(next_url)
        url = next_url


def _media_transfer_config_lines(
    target: _MediaTarget,
    destination_path: str,
    *,
    max_time_seconds: int,
) -> list[str]:
    return [
        "url = " + _curl_config_quote(target.url),
        "output = " + _curl_config_quote(destination_path),
        "user-agent = " + _curl_config_quote(CURL_USER_AGENT),
        "referer = " + _curl_config_quote(CURL_REFERER),
        "fail",
        "silent",
        "show-error",
        "max-redirs = 0",
        "max-filesize = " + str(MAX_BYTES),
        'continue-at = "-"',
        "connect-timeout = 15",
        "max-time = " + str(max_time_seconds),
        "resolve = " + _curl_config_quote(
            _curl_resolve_value(target.host, target.port, target.address)
        ),
        'proto = "=http,https"',
        'write-out = "%{http_code}"',
    ]


def _discard_download_output(destination: Path) -> None:
    try:
        destination.unlink()
    except OSError:
        pass


def _default_download(
    media_url: str,
    destination: Path,
    *,
    resolve: Callable[[str, int], object] | None = None,
) -> object:
    """Download one signed media URL with curl after the delivery guard approved it.

    Python urllib is throttled/aborted by the NetEase CDN fingerprint on the NAS,
    so the transfer is delegated to curl with a browser-like UA/Referer. The URL
    and destination are passed through `curl -K -` stdin config so the signed URL
    never appears in argv (/proc/<pid>/cmdline), logs, exceptions or results.

    The delivery guard runs first: every redirect hop is URL-policy checked,
    DNS-resolved and probed with a single pinned ``--resolve`` address; the final
    transfer then reuses exactly that address (D-36: the transfer never switches
    addresses). Probe and transfer have separate budgets, so probing cannot eat
    into the transfer budget. curl --retry does not resume inside one invocation,
    so up to DOWNLOAD_MAX_ATTEMPTS Python-level attempts share the transfer
    budget and keep continue-at = "-" to resume the partial file between them.
    """
    destination = Path(destination)
    destination_path = os.fspath(destination)
    if type(destination_path) is not str or not destination.is_absolute():
        raise _SaveFailure("download_failed")
    probe_deadline = time.monotonic() + DOWNLOAD_SUBPROCESS_TIMEOUT
    target = _follow_media_redirects(media_url, resolve, deadline=probe_deadline)
    # The transfer budget starts only after probing: probing never consumes it.
    deadline = time.monotonic() + DOWNLOAD_SUBPROCESS_TIMEOUT
    completed_status = None
    attempts = 0
    while attempts < DOWNLOAD_MAX_ATTEMPTS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        attempt_timeout = min(DOWNLOAD_TIMEOUT, remaining)
        max_time_seconds = max(1, int(attempt_timeout))
        completed = _run_curl(
            _media_transfer_config_lines(
                target, destination_path, max_time_seconds=max_time_seconds,
            ),
            attempt_timeout + 10.0,
        )
        if completed is None or completed.returncode != 0:
            # Keep the partial file: the next attempt resumes it via continue-at.
            continue
        status = _parse_transfer_status(completed.stdout)
        if status in _MEDIA_SUCCESS_STATUSES:
            completed_status = status
            break
        _discard_download_output(destination)
        if status is not None and 300 <= status < 400:
            raise _SaveFailure("download_target_untrusted")
        raise _SaveFailure("download_failed")
    if completed_status is None:
        raise _SaveFailure("download_failed")
    try:
        size = destination.stat().st_size
    except OSError:
        raise _SaveFailure("download_failed") from None
    if size <= 0 or size > MAX_BYTES:
        raise _SaveFailure("download_failed")
    digest = hashlib.md5()
    try:
        with destination.open("rb") as handle:
            while True:
                chunk = handle.read(DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        raise _SaveFailure("download_failed") from None
    return {"size": size, "md5": digest.hexdigest()}


def _default_ffprobe(path: Path) -> object:
    command = [
        FFPROBE_BIN,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        os.fspath(path),
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise _SaveFailure("invalid_audio") from None
    if completed.returncode != 0:
        raise _SaveFailure("invalid_audio")
    try:
        return json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, ValueError):
        raise _SaveFailure("invalid_audio") from None


def _default_decode_check(path: Path) -> None:
    command = [
        FFMPEG_BIN, "-v", "error", "-xerror", "-i", os.fspath(path),
        "-map", "0:a", "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=DECODE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise _SaveFailure("invalid_audio") from None
    if completed.returncode != 0:
        raise _SaveFailure("invalid_audio")


def _default_validate_tagged(
    path: Path,
    expected_format: str,
    metadata: Mapping[str, str],
    expected_cover: bool,
) -> object:
    """Verify the tagged artifact with real ffprobe/ffmpeg before publication."""
    try:
        result = _default_ffprobe(path)
        if _probed_audio_format(result) != expected_format:
            raise ValueError("tagged_format_mismatch")
        if not isinstance(result, Mapping):
            raise ValueError("tagged_probe_invalid")
        streams = result.get("streams")
        if not isinstance(streams, list):
            raise ValueError("tagged_streams_invalid")
        audio_streams = [
            stream for stream in streams
            if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
        ]
        expected_codec = "flac" if expected_format == "flac" else "mp3"
        if not audio_streams or any(stream.get("codec_name") != expected_codec for stream in audio_streams):
            raise ValueError("tagged_codec_mismatch")
        _validate_tagged_metadata(result, metadata)
        _default_decode_check(path)

        cover_indexes = _attached_pic_stream_indexes(result)
        if bool(cover_indexes) is not expected_cover:
            raise ValueError("tagged_cover_missing_or_unexpected")
        if expected_cover:
            by_index = {
                stream.get("index"): stream
                for stream in streams
                if isinstance(stream, Mapping) and type(stream.get("index")) is int
            }
            for index in cover_indexes:
                stream = by_index.get(index)
                if stream is None:
                    raise ValueError("tagged_cover_stream_missing")
                _validate_cover_dimensions(stream.get("width"), stream.get("height"))
                _default_verify_tagged_cover(path, index)
        return result
    except Exception:
        raise _SaveFailure("tag_failed") from None


def _validate_tagged_metadata(probe_result: Mapping[str, object], metadata: Mapping[str, str]) -> None:
    format_entry = probe_result.get("format")
    if not isinstance(format_entry, Mapping):
        raise ValueError("tagged_format_tags_missing")
    tag_maps = [format_entry.get("tags")]
    streams = probe_result.get("streams")
    if isinstance(streams, list):
        tag_maps.extend(
            stream.get("tags")
            for stream in streams
            if isinstance(stream, Mapping) and stream.get("codec_type") == "audio"
        )
    tags: dict[str, str] = {}
    for tag_map in tag_maps:
        if not isinstance(tag_map, Mapping):
            continue
        for key, value in tag_map.items():
            if type(key) is str and type(value) is str:
                tags[key.casefold()] = value
    aliases = {
        "track": ("track", "tracknumber", "track number"),
        "disc": ("disc", "discnumber", "disc number", "disk", "disknumber"),
    }
    for key, expected in metadata.items():
        candidates = aliases.get(key.casefold(), (key.casefold(),))
        if not any(tags.get(candidate) == expected for candidate in candidates):
            raise ValueError("tagged_metadata_mismatch")


def _default_verify_tagged_cover(path: Path, stream_index: int) -> None:
    command = [
        FFMPEG_BIN, "-v", "error", "-threads", "1", "-filter_threads", "1",
        "-max_alloc", str(MAX_COVER_DECODE_ALLOC), "-xerror",
        "-i", os.fspath(path), "-map", f"0:{stream_index}",
        "-frames:v", "1", "-f", "null", "-",
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=COVER_DECODE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("tagged_cover_decode_failed") from None
    if completed.returncode != 0:
        raise ValueError("tagged_cover_decode_failed")


def _default_tag(source: Path, tagged: Path, metadata: Mapping[str, str]) -> None:
    command = [
        FFMPEG_BIN,
        "-y",
        "-v", "error",
        "-i", os.fspath(source),
    ]
    cover_path = getattr(metadata, "cover_path", None)
    source_cover_indexes = getattr(metadata, "source_cover_indexes", ())
    if isinstance(cover_path, Path):
        command.extend(["-i", os.fspath(cover_path)])
    command.extend([
        "-map", "0:a",
    ])
    for stream_index in source_cover_indexes:
        if type(stream_index) is int and stream_index >= 0:
            command.extend(["-map", f"0:{stream_index}"])
    if isinstance(cover_path, Path):
        command.extend(["-map", "1:v:0"])
    command.extend([
        "-c", "copy",
    ])
    for key, value in metadata.items():
        command.extend(["-metadata", f"{key}={value}"])
    cover_stream_count = len(source_cover_indexes) + (1 if isinstance(cover_path, Path) else 0)
    for stream_index in range(cover_stream_count):
        command.extend([
            f"-disposition:v:{stream_index}", "attached_pic",
            f"-metadata:s:v:{stream_index}", "title=Album cover",
            f"-metadata:s:v:{stream_index}", "comment=Cover (front)",
        ])
    command.append(os.fspath(tagged))
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=TAG_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise _SaveFailure("tag_failed") from None
    if completed.returncode != 0:
        raise _SaveFailure("tag_failed")


def _default_open_store(target_dir: str, staging_dir: str):
    anonymous = _anonymous_module()
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
    target_fd = -1
    staging_fd = -1
    try:
        try:
            target_fd = os.open(target_dir, flags)
            staging_fd = os.open(staging_dir, flags)
        except OSError:
            raise _SaveFailure("staging_unavailable") from None
        try:
            return anonymous.AnonymousLinuxStore(TARGET_ID, target_fd, staging_fd)
        except Exception:
            raise _SaveFailure("staging_unavailable") from None
    finally:
        for descriptor in (staging_fd, target_fd):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
