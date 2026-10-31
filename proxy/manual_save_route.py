"""Manual single-track save engine: config, musicbox ladder, ffmpeg tags, anonymous publish."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import http.client
import ipaddress
import json
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
FFMPEG_BIN = "/usr/bin/ffmpeg"
FFPROBE_BIN = "/usr/bin/ffprobe"
CURL_BIN = "/usr/bin/curl"
CURL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
CURL_REFERER = "https://music.163.com/"
NETEASE_COVER_HOSTS = frozenset({"p1.music.126.net"})
_COVER_MIME_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
UNKNOWN_ARTIST = "未知歌手"
ARTIST_SEPARATOR = "/"
MD5_RE = re.compile(r"[0-9a-fA-F]{32}")
DIGITS_RE = re.compile(r"[0-9]{1,10}")
SEARCH_LIMIT = 20
MAX_SEARCH_KEYWORD_CHARS = 120
_FILENAME_FORBIDDEN = frozenset("/\\:")
_CONTROL_CATEGORIES = frozenset({"Cc", "Cs"})
_SAVE_LOCK = threading.Lock()
_ANONYMOUS_MODULE = None


_MESSAGES = {
    "ok": "",
    "disabled": "手动保存未启用",
    "invalid_keyword": "搜索关键词必须是 1-120 字符",
    "config_invalid": "手动保存配置无效",
    "invalid_song_id": "song_id 必须是正整数",
    "busy": "已有保存任务进行中",
    "service_unavailable": "音乐服务不可用",
    "no_quality_available": "没有可用的音质",
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


def load_config(
    *,
    values: Mapping[str, str] | None = None,
    env_path: Path | None = None,
) -> Config:
    source = read_env_file(env_path) if values is None else values
    target = source.get(TARGET_KEY, "") if isinstance(source, Mapping) else ""
    staging = source.get(STAGING_KEY, "") if isinstance(source, Mapping) else ""
    musicbox = source.get(MUSICBOX_KEY, "") if isinstance(source, Mapping) else ""
    return Config(
        enabled=_parse_enabled(source.get(ENABLED_KEY) if isinstance(source, Mapping) else None),
        target_dir=target if type(target) is str else "",
        staging_dir=staging if type(staging) is str else "",
        musicbox_url=musicbox.strip() if type(musicbox) is str and musicbox.strip() else DEFAULT_MUSICBOX_URL,
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
        "quality_order": list(QUALITY_ORDER),
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
    http_json = _field(deps, "http_json", _default_http_json)
    download = _field(deps, "download", _default_download)
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
    quality_requested, quality_data, media_url = _select_quality(
        http_json, config.musicbox_url, song_id
    )
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
        if source_cover_indexes:
            cover_status = "preserved"
        elif track_metadata.cover_url:
            try:
                cover_asset = _fetch_cover_asset(track_metadata.cover_url, deps)
                if actual_format == "mp3" and cover_asset.mime == "image/webp":
                    cover_status = "unsupported"
                else:
                    cover_path = _write_cover_file(workdir, cover_asset)
                    _field(deps, "verify_cover", _default_verify_cover)(cover_path)
                    cover_status = "embedded"
            except Exception:
                cover_asset = None
                cover_path = None
                cover_status = "error"
        else:
            cover_status = "unavailable"

        lyric_bytes = None
        try:
            lyric_json = _field(deps, "lyric_json", _default_http_json)
            lyric_bytes = _fetch_lyric_bytes(lyric_json, config.musicbox_url, song_id)
            lyric_status = "unavailable" if lyric_bytes is None else "not_attempted"
        except Exception:
            lyric_status = "error"

        metadata: dict[str, str] = {"title": title}
        if artist:
            metadata["artist"] = artist
        if album:
            metadata["album"] = album
        if track_number is not None:
            metadata["track"] = str(track_number)
        if disc_number is not None:
            metadata["disc"] = str(disc_number)
        if track_metadata.lyricist:
            metadata["lyricist"] = track_metadata.lyricist
        if track_metadata.composer:
            metadata["composer"] = track_metadata.composer
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
            cover_status = "preserved" if source_cover_indexes else "embedded"
        elif cover_status in {"embedded", "preserved"}:
            # A requested image that was not safely attached cannot be reported as present.
            cover_status = "error"

        basename = build_basename(artist, title, actual_format)
        quality_actual = _text(quality_data.get("level"), quality_requested) or quality_requested

        data = _success_data(
            status_value="published",
            song_id=song_id,
            title=title,
            artist=artist,
            album=album,
            quality_requested=quality_requested,
            quality_actual=quality_actual,
            source_type=source_type,
            actual_format=actual_format,
            size=downloaded_size,
            md5=downloaded_md5,
            basename=basename,
            target_dir=config.target_dir,
        )
        data.update({
            "title_status": "tagged",
            "artist_status": "tagged" if artist else "unavailable",
            "album_status": "tagged" if album else "unavailable",
            "lyricist": track_metadata.lyricist,
            "lyricist_status": "tagged" if track_metadata.lyricist else "unavailable",
            "composer": track_metadata.composer,
            "composer_status": "tagged" if track_metadata.composer else "unavailable",
            "cover_status": cover_status,
            "lyric_status": lyric_status,
            "cover_embedded": cover_embedded,
        })

        try:
            store = open_store(config.target_dir, config.staging_dir)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("staging_unavailable") from None
        try:
            handle = store.begin_owned_stage(TARGET_ID)
            published_digest = hashlib.md5()
            published_size = 0
            with tagged_path.open("rb") as tagged_file:
                while True:
                    chunk = tagged_file.read(core.MAX_CHUNK_BYTES)
                    if not chunk:
                        break
                    published_digest.update(chunk)
                    published_size += len(chunk)
                    store.append(handle, chunk)
            store.seal(handle)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("staging_unavailable") from None
        # size/md5 above are the verified download; published_* describe the
        # tagged bytes that are actually handed to the no-replace publish call.
        data["published_size"] = published_size
        data["published_md5"] = published_digest.hexdigest()
        try:
            published = store.publish_noreplace(handle, basename)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("publish_failed") from None
        outcome = _store_outcome(published)
        if outcome == "published":
            if lyric_bytes is not None:
                lyric_basename = basename.rsplit(".", 1)[0] + ".lrc"
                data["lyric_status"] = _publish_sidecar(store, lyric_basename, lyric_bytes)
            return _result(True, "ok", data)
        if outcome == "conflict":
            data["status"] = "conflict"
            data["cover_status"] = "not_published"
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


def _fetch_song_info(
    http_json: Callable[[str], object], musicbox_url: str, song_id: int
) -> _TrackMetadata:
    url = f"{musicbox_url.rstrip('/')}/api/v1/song/{song_id}/info"
    try:
        payload = http_json(url)
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


def _fetch_lyric_bytes(
    http_json: Callable[[str], object], musicbox_url: str, song_id: int
) -> bytes | None:
    url = f"{musicbox_url.rstrip('/')}/api/v1/song/{song_id}/lyric"
    payload = http_json(url)
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
        or host not in NETEASE_COVER_HOSTS
        or parsed.username is not None or parsed.password is not None
        or not valid_port
        or parsed.fragment
        or not parsed.path.startswith("/")
        or any(ord(character) < 32 or ord(character) == 127 for character in parsed.path + parsed.query)
    ):
        raise ValueError("cover_url_untrusted")
    # The source API historically returns both http and https picUrl values. A
    # trusted image hostname is upgraded; no redirect is followed by the fetcher.
    return urlunsplit(("https", host, parsed.path, parsed.query, ""))


def _public_cover_addresses(host: str) -> list[str]:
    rows = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
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


def _default_cover_fetch(url: str) -> object:
    safe_url = _normalize_cover_url(url)
    parsed = urlsplit(safe_url)
    host = parsed.hostname or ""
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    if parsed.query:
        path += "?" + quote(parsed.query, safe="%/?@!$&'()*+,;=:-._~")
    last_error: Exception | None = None
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
            declared_mime = response.getheader("Content-Type", "").split(";", 1)[0].strip().casefold()
            actual_mime = _sniff_cover_mime(data)
            if actual_mime not in _COVER_MIME_EXTENSIONS or declared_mime != actual_mime:
                raise ValueError("cover_mime_invalid")
            return {"data": data, "mime": actual_mime}
        except Exception as exc:
            last_error = exc
        finally:
            connection.close()
    raise ValueError(type(last_error).__name__ if last_error else "cover_fetch_failed")


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
    if type(mime) is not str or mime.casefold() != _sniff_cover_mime(data):
        raise ValueError("cover_mime_invalid")
    normalized_mime = mime.casefold()
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


def _select_quality(
    http_json: Callable[[str], object], musicbox_url: str, song_id: int
) -> tuple[str, Mapping, str]:
    base = musicbox_url.rstrip("/")
    for quality in QUALITY_ORDER:
        url = f"{base}/api/v1/song/{song_id}/url?quality={quality}"
        try:
            payload = http_json(url)
        except Exception:
            continue
        if not isinstance(payload, Mapping) or payload.get("ok") is not True:
            continue
        data = payload.get("data")
        if not isinstance(data, Mapping):
            continue
        media_url = data.get("url")
        if type(media_url) is not str or not media_url.startswith(("http://", "https://")):
            continue
        return quality, data, media_url
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


def _success_data(
    *,
    status_value: str,
    song_id: int,
    title: str,
    artist: str,
    album: str,
    quality_requested: str,
    quality_actual: str,
    source_type: str,
    actual_format: str,
    size: int,
    md5: str,
    basename: str,
    target_dir: str,
) -> dict:
    return {
        "status": status_value,
        "song_id": song_id,
        "title": title,
        "artist": artist,
        "album": album,
        "quality_requested": quality_requested,
        "quality_actual": quality_actual,
        "source_type": source_type,
        "format": actual_format,
        "size": size,
        "md5": md5,
        "basename": basename,
        "target_dir": target_dir,
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


def _default_http_json(url: str) -> object:
    request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(request, timeout=API_TIMEOUT) as response:
        status_code = getattr(response, "status", 200)
        if status_code != 200:
            raise OSError("http_status")
        payload = response.read(MAX_JSON_BYTES + 1)
    if len(payload) > MAX_JSON_BYTES:
        raise ValueError("json_too_large")
    return json.loads(payload.decode("utf-8"))


def _curl_config_quote(value: str) -> str:
    """Quote one curl-config value; framing bytes are rejected, not escaped."""
    if any(character in value for character in (chr(0), chr(13), chr(10))):
        raise _SaveFailure("download_failed")
    escaped = value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))
    return chr(34) + escaped + chr(34)


def _default_download(media_url: str, destination: Path) -> object:
    """Download one signed URL with curl; the URL travels only on curl's stdin.

    Python urllib is throttled/aborted by the NetEase CDN fingerprint on the NAS,
    so the transfer is delegated to curl with a browser-like UA/Referer. The URL
    and destination are passed through `curl -K -` stdin config so the signed URL
    never appears in argv (/proc/<pid>/cmdline), logs, exceptions or results.

    curl --retry does not resume inside one invocation (continue-at only applies
    when the output file already exists at start), so attempts are driven here:
    up to DOWNLOAD_MAX_ATTEMPTS runs share a DOWNLOAD_SUBPROCESS_TIMEOUT budget,
    and every attempt keeps continue-at = "-" so a later attempt resumes the
    partial file left by an earlier failed one.
    """
    destination = Path(destination)
    if type(media_url) is not str or not media_url.startswith(("http://", "https://")):
        raise _SaveFailure("download_failed")
    destination_path = os.fspath(destination)
    if type(destination_path) is not str or not destination.is_absolute():
        raise _SaveFailure("download_failed")
    common_config_lines = [
        "url = " + _curl_config_quote(media_url),
        "output = " + _curl_config_quote(destination_path),
        "user-agent = " + _curl_config_quote(CURL_USER_AGENT),
        "referer = " + _curl_config_quote(CURL_REFERER),
        "location",
        "fail",
        "silent",
        "show-error",
    ]
    tail_config_lines = [
        "max-filesize = " + str(MAX_BYTES),
        'continue-at = "-"',
    ]
    deadline = time.monotonic() + DOWNLOAD_SUBPROCESS_TIMEOUT
    completed_run = None
    attempts = 0
    while attempts < DOWNLOAD_MAX_ATTEMPTS:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        attempt_timeout = min(DOWNLOAD_TIMEOUT, remaining)
        max_time_seconds = max(1, int(attempt_timeout))
        config_lines = (
            common_config_lines
            + ["max-time = " + str(max_time_seconds)]
            + tail_config_lines
        )
        try:
            completed = subprocess.run(
                [CURL_BIN, "-K", "-"],
                input=chr(10).join(config_lines) + chr(10),
                text=True,
                timeout=attempt_timeout + 10.0,
                capture_output=True,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if completed.returncode == 0:
            completed_run = completed
            break
    if completed_run is None:
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
