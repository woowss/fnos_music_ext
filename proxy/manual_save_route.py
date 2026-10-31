"""Manual single-track save engine: config, musicbox ladder, ffmpeg tags, anonymous publish."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat as stat_module
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.request
from urllib.parse import quote

if __package__:
    from . import manual_save_core as core
else:  # direct execution from the proxy source directory
    import manual_save_core as core


QUALITY_ORDER = ("jymaster", "hires", "lossless", "exhigh")
MAX_BYTES = 1 << 30
DOWNLOAD_CHUNK_BYTES = 1 << 20
MAX_TAGGED_EXTRA_BYTES = 1 << 20
MAX_JSON_BYTES = 1 << 20
API_TIMEOUT = 15.0
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

    title, artist, album, track_number, disc_number = _fetch_song_info(
        http_json, config.musicbox_url, song_id
    )
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

        metadata: dict[str, str] = {"title": title}
        if artist:
            metadata["artist"] = artist
        if album:
            metadata["album"] = album
        if track_number is not None:
            metadata["track"] = str(track_number)
        if disc_number is not None:
            metadata["disc"] = str(disc_number)
        tagged_path = workdir / ("tagged." + actual_format)
        try:
            tag(source_path, tagged_path, metadata)
        except _SaveFailure:
            raise
        except Exception:
            raise _SaveFailure("tag_failed") from None
        _tagged_file_size(tagged_path)

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
            return _result(True, "ok", data)
        if outcome == "conflict":
            data["status"] = "conflict"
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
) -> tuple[str, str, str, int | None, int | None]:
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
    album_entry = data.get("al")
    if isinstance(album_entry, Mapping):
        album_name = album_entry.get("name")
        if type(album_name) is str and album_name.strip():
            album = album_name.strip()
    return (
        name.strip(),
        ARTIST_SEPARATOR.join(artists),
        album,
        _positive_int(data.get("no")),
        _positive_int(data.get("cd")),
    )


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
    command = [FFMPEG_BIN, "-v", "error", "-i", os.fspath(path), "-f", "null", "-"]
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


def _default_tag(source: Path, tagged: Path, metadata: Mapping[str, str]) -> None:
    command = [
        FFMPEG_BIN,
        "-y",
        "-v", "error",
        "-i", os.fspath(source),
        "-map", "0:a",
        "-c", "copy",
    ]
    for key, value in metadata.items():
        command.extend(["-metadata", f"{key}={value}"])
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
