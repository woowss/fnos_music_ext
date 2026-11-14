"""HTTP service wrapping pure-Python NetEase WEAPI/EAPI client."""
from __future__ import annotations

import io
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path as FsPath
from typing import Any

from fastapi import FastAPI, HTTPException, Path, Query, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import netease_client
import netease_ext
from netease_ext import (
    album_detail,
    artist_detail,
    auth_login_check,
    auth_login_qr,
    auth_logout,
    auth_status as fetch_auth_status,
    batch_song_details,
    check_is_logged_in,
    filter_playable_song_ids,
    playlist_detail_raw,
    playlist_track_ids,
    public_playlist,
    public_playlist_categories,
    public_playlist_tracks,
    public_playlists,
    recommend_songs_raw,
    reset_api,
    search_catalog,
    search_web_fallback,
    song_detail_raw,
    song_lyric_pair,
    toplist_raw,
    vip_entitled,
    user_playlists as fetch_user_playlists,
    user_playlist_preview as fetch_user_playlist_preview,
)
import netease_playlist_snapshot as playlist_snapshot
import playback
from playback import get_song_url_async
from runner import ensure_xdg_dirs

# 与其他服务对齐：不配 handler 时 INFO 会被丢弃（只剩 lastResort 的 WARNING+）
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("musicbox_service.app")
try:  # 本地文件日志（logs/musicbox.log，按天滚动保留 3 天）；失败静默降级
    import sys as _sys
    from pathlib import Path as _Path

    _sys.path.insert(0, str(_Path(__file__).resolve().parent.parent))
    from proxy.fnlog import setup_logging as _setup_logging

    _setup_logging("musicbox_service.app", "musicbox")
except Exception:  # noqa: BLE001  无 proxy/ 上下文（如独立调试）时仅 stdout
    pass

ensure_xdg_dirs()

SEARCH_TYPES = {"song", "album", "artist", "playlist"}
SEARCH_MODES = {"playback", "manual_save"}
# 统一官方七档支持：standard, higher, exhigh, lossless, hires, jymaster, sky
QUALITY_WHITELIST = {"standard", "higher", "exhigh", "lossless", "hires", "jymaster", "sky"}


class UpstreamException(Exception):
    def __init__(self, exit_code: int, stderr: str):
        self.exit_code = exit_code
        self.stderr = (stderr or "")[:2000]


@asynccontextmanager
async def lifespan(application):
    try:
        yield
    finally:
        await playback.shutdown()


app = FastAPI(title="fnmusic-musicbox", version="1.0.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request, exc: RequestValidationError):
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": exc.errors()})


@app.exception_handler(UpstreamException)
async def upstream_exception_handler(request, exc: UpstreamException):
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"error": "upstream_error", "exit_code": exc.exit_code, "stderr": exc.stderr},
    )


def _extract_payload(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        return payload["data"]
    return payload


def _parse_ids(ids_str: str | None) -> list[int]:
    if not ids_str or not ids_str.strip():
        raise HTTPException(status_code=422, detail="ids parameter is required")
    ids: list[int] = []
    for token in ids_str.split(","):
        token = token.strip()
        if not token:
            raise HTTPException(status_code=422, detail="Empty id in ids list")
        try:
            val = int(token)
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid id {token!r}") from None
        if val <= 0:
            raise HTTPException(status_code=422, detail=f"Invalid id {token!r}")
        ids.append(val)
    if not 1 <= len(ids) <= 100:
        raise HTTPException(status_code=422, detail="ids count must be 1..100")
    return ids


def _dotenv_candidates() -> list[FsPath]:
    env_override = (os.environ.get("FNMUSIC_ENV_PATH") or "").strip()
    candidates: list[FsPath] = []
    if env_override:
        candidates.append(FsPath(env_override))
    candidates.append(FsPath(__file__).resolve().parent.parent / ".env")
    candidates.append(FsPath("/repo/.env"))
    return candidates


def _read_dotenv_kv() -> dict[str, str]:
    for path in _dotenv_candidates():
        try:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed: dict[str, str] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, val = stripped.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[7:].strip()
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            if key:
                parsed[key] = val
        return parsed
    return {}


def _snapshot_producer_enabled() -> bool:
    readonly_key = playlist_snapshot.PLAYLIST_MANIFEST_READONLY_ENV
    if readonly_key in os.environ:
        return playlist_snapshot.producer_enabled(os.environ)
    env_kv = _read_dotenv_kv()
    if readonly_key in env_kv:
        return playlist_snapshot.producer_enabled(env_kv)
    if "FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED" in env_kv:
        return playlist_snapshot.producer_enabled({
            readonly_key: env_kv["FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED"],
        })
    return playlist_snapshot.producer_enabled(os.environ)


@app.get("/healthz")
def healthz():
    return {
        "status": "ok",
        "protocol": "pure-python-weapi-eapi",
        "package": netease_client.PACKAGE_NAME,
        "version": netease_client.PACKAGE_VERSION,
    }


@app.get("/api/v1/search")
def search(
    keyword: str = Query(...),
    type: str = Query("song"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    mode: str = Query("playback"),
):
    if not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword cannot be empty")
    if type not in SEARCH_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid type {type!r}")
    clean_mode = mode.strip().lower()
    if clean_mode not in SEARCH_MODES:
        clean_mode = "playback"

    items: list[dict[str, Any]] = []
    try:
        items = search_catalog(keyword, stype=type, limit=limit, offset=offset)
    except Exception as exc:
        logger.warning("cloudsearch failed (%s), falling back to web search", exc)
        items = []

    if not items and type == "song":
        try:
            items = search_web_fallback(keyword, stype=type, limit=limit, offset=offset)
        except Exception as exc:
            logger.warning("web fallback search failed: %s", exc)
            items = []

    if items and type == "song" and clean_mode == "playback":
        song_ids = [
            int(it.get("song_id") or it.get("id") or 0)
            for it in items
            if isinstance(it, dict) and (it.get("song_id") or it.get("id"))
        ]
        if song_ids:
            playable = filter_playable_song_ids(song_ids)
            items = [
                it
                for it in items
                if isinstance(it, dict) and int(it.get("song_id") or it.get("id") or 0) in playable
            ]

    return {"ok": True, "code": 200, "data": items, "mode": clean_mode}


@app.get("/api/v1/song/{song_id}/url")
async def song_url(
    song_id: int = Path(..., ge=1),
    quality: str = Query("exhigh"),
    fresh: int = Query(0),
    purpose: str = Query("playback"),
    generation: int = Query(0, ge=0),
):
    if quality not in QUALITY_WHITELIST:
        raise HTTPException(status_code=400, detail=f"Invalid quality {quality!r}")
    purpose = "download" if purpose.strip().lower() == "download" else "playback"
    try:
        item = await get_song_url_async(
            song_id, quality, fresh=bool(fresh), purpose=purpose, context_generation=generation
        )
        return {"ok": True, "data": item}
    except playback.ResolutionTimeout as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except playback.UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.get("/api/v1/song/{song_id}/info")
def song_info(song_id: int = Path(..., ge=1)):
    raw = song_detail_raw(song_id)
    if raw is not None:
        return {"ok": True, "data": raw}
    raise HTTPException(status_code=404, detail=f"Song {song_id} not found")


@app.get("/api/v1/songs/detail")
def songs_detail(ids: str = Query(None)):
    parsed = _parse_ids(ids)
    try:
        return {"ok": True, "data": batch_song_details(parsed)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/v1/song/{song_id}/lyric")
def song_lyric(song_id: int = Path(..., ge=1)):
    try:
        return {"ok": True, "data": song_lyric_pair(song_id)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


@app.get("/api/v1/artist/{artist_id}")
def artist(artist_id: int = Path(..., ge=1), limit: int = Query(20, ge=1, le=100)):
    try:
        return artist_detail(artist_id, limit=limit)
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.get("/api/v1/album/{album_id}")
def album(album_id: int = Path(..., ge=1)):
    try:
        return album_detail(album_id)
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.get("/api/v1/playlist/{playlist_id}")
def playlist(playlist_id: int = Path(..., ge=1)):
    try:
        return playlist_detail_raw(playlist_id)
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.get("/api/v1/user/playlists")
def user_playlists(limit: int = Query(100, ge=1, le=100)):
    """网易账号歌单列表（需扫码登录；自建/收藏的判定字段随行下发，由调用方过滤）。"""
    res = fetch_user_playlists(limit)
    if res is None:
        return {"ok": False, "error": "not_logged_in", "logged_in": False}
    return {"ok": True, "data": res["playlists"], "account_uid": res["uid"], "logged_in": True}


@app.get("/api/v1/user/playlists/{playlist_id}/preview")
def user_playlist_preview(playlist_id: int = Path(..., ge=1)):
    """读取当前账号歌单的临时预览；不下载、不保存，也不代表可导入。"""
    return fetch_user_playlist_preview(playlist_id)


@app.get("/api/v1/user/playlists/{playlist_id}/tracks")
def user_playlist_tracks(playlist_id: int = Path(..., ge=1)):
    """网易歌单曲目：trackIds -> 批量详情（不做可播过滤，VIP 曲目带 needs_vip 标记）。"""
    ids = playlist_track_ids(playlist_id)
    if ids is None:
        return {"ok": False, "error": "not_logged_in", "logged_in": False}
    return {"ok": True, "data": _mark_needs_vip(batch_song_details(ids[:1000]))}


@app.get("/api/v1/user/playlists/{playlist_id}/member-snapshot")
def user_playlist_member_snapshot(playlist_id: int = Path(..., ge=1)):
    """只读歌单成员快照：两次独立观测（2×account + 2×/api/v6/playlist/detail）。"""
    if not _snapshot_producer_enabled():
        return JSONResponse(status_code=503, content={"ok": False, "error": "manifest_disabled"})
    fetch_account, fetch_playlist = playlist_snapshot.live_fetchers()
    return playlist_snapshot.build_snapshot(playlist_id, fetch_account, fetch_playlist)


def _mark_needs_vip(rows: list[dict]) -> list[dict]:
    """issue #57：VIP 歌曲行级标记——无 VIP 权益的账号遇到 fee==1（VIP 曲目）时附 needs_vip=True。"""
    if vip_entitled():
        return rows
    for row in rows:
        if isinstance(row, dict) and row.get("fee") == 1:
            row["needs_vip"] = True
    return rows


def _playable_recommendation_rows(rows: Any, limit: int) -> list[dict]:
    """推荐输出 -> 批量详情 + 可播过滤（未登录剔除 VIP/试听片段）。"""
    ids: list[int] = []
    if isinstance(rows, list):
        for it in rows:
            if not isinstance(it, dict):
                continue
            try:
                sid = int(it.get("song_id") or it.get("id") or 0)
            except (ValueError, TypeError):
                sid = 0
            if sid > 0:
                ids.append(sid)
    hydrated = _mark_needs_vip(batch_song_details(ids[:100]))
    playable = filter_playable_song_ids([int(row["song_id"]) for row in hydrated])
    return [row for row in hydrated if int(row["song_id"]) in playable][:limit]


@app.get("/api/v1/recommend/songs")
def recommend_songs(limit: int = Query(30, ge=10, le=60)):
    """网易每日推荐（已登录为个性化推荐；未登录返回 not_logged_in / 空）。"""
    try:
        raw_rows = recommend_songs_raw(limit=limit)
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc
    if not check_is_logged_in() and not raw_rows:
        return {"ok": False, "error": "not_logged_in", "logged_in": False}
    rows = _playable_recommendation_rows(raw_rows, limit)
    return {"ok": True, "data": rows, "logged_in": check_is_logged_in()}


@app.get("/api/v1/toplist")
def toplist(index: int = Query(-1), limit: int = Query(60, ge=1, le=100)):
    """网易榜单：不带 index 返回榜单列表；带 index 返回该榜单可播曲目。"""
    try:
        data = toplist_raw(index=index, limit=limit)
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc
    if index < 0:
        return data
    rows = _playable_recommendation_rows(_extract_payload(data), limit)
    return {"ok": True, "data": rows, "index": index}


@app.get("/api/v1/public-playlists/capabilities")
def public_playlist_capabilities():
    logged_in = False
    try:
        logged_in = check_is_logged_in()
    except Exception:
        logged_in = False
    return {
        "ok": True,
        "provider": "musicbox",
        "sources": [{
            "id": "netease",
            "name": "网易云音乐",
            "playback_available": True,
            "channels": [
                {"id": "recommend", "name": "推荐歌单", "login_required": False},
                {"id": "top", "name": "热门与分类", "login_required": False},
                {"id": "highquality", "name": "精品歌单", "login_required": False},
                {"id": "search", "name": "搜索歌单", "login_required": False},
                {"id": "charts", "name": "排行榜", "login_required": False},
                {"id": "account_recommend", "name": "账号推荐歌单", "login_required": True},
            ],
            "logged_in": logged_in,
        }],
    }


@app.get("/api/v1/public-playlists/categories")
def public_playlist_categories_api(
    source: str = Query("netease"),
    kind: str = Query("playlist", pattern="^(playlist|chart)$"),
):
    if source not in ("netease", "wy", "163"):
        raise HTTPException(status_code=400, detail="unsupported source")
    try:
        if kind == "chart":
            return {
                "ok": True,
                "categories": [{"id": "", "name": "全部"}],
                "tags": [],
                "sorts": [],
            }
        data = public_playlist_categories("playlist")
        grouped = data.get("tags") if isinstance(data.get("tags"), dict) else {}
        data["categories"] = [{"id": "", "name": "全部"}] + [
            {"id": name, "name": name, "list": rows}
            for name, rows in grouped.items()
            if isinstance(rows, list)
        ]
        return data
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"netease categories failed: {exc}") from exc


@app.get("/api/v1/public-playlists")
def public_playlists_api(
    source: str = Query("netease"),
    channel: str = Query("top"),
    category: str = Query(""),
    sort: str = Query("hot"),
    keyword: str = Query(""),
    page: int = Query(1, ge=1, le=10000),
    limit: int = Query(30, ge=1, le=50),
    cursor: str = Query(""),
):
    if source not in ("netease", "wy", "163"):
        raise HTTPException(status_code=400, detail="unsupported source")
    channels = {"recommend", "top", "highquality", "search", "charts", "account_recommend"}
    if channel not in channels:
        raise HTTPException(status_code=400, detail=f"unsupported channel {channel}")
    if channel == "search" and not keyword.strip():
        raise HTTPException(status_code=400, detail="keyword is required for search channel")
    try:
        return public_playlists(channel, category, sort, page, limit, cursor, keyword)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"netease playlists failed: {exc}") from exc


@app.get("/api/v1/public-playlists/{playlist_id}")
def public_playlist_api(
    playlist_id: int = Path(..., ge=1),
    source: str = Query("netease"),
    kind: str = Query("playlist", pattern="^(playlist|chart)$"),
):
    if source not in ("netease", "wy", "163"):
        raise HTTPException(status_code=400, detail="unsupported source")
    try:
        return public_playlist(playlist_id, kind)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"netease playlist failed: {exc}") from exc


@app.get("/api/v1/public-playlists/{playlist_id}/tracks")
def public_playlist_tracks_api(
    playlist_id: int = Path(..., ge=1),
    source: str = Query("netease"),
    kind: str = Query("playlist", pattern="^(playlist|chart)$"),
):
    if source not in ("netease", "wy", "163"):
        raise HTTPException(status_code=400, detail="unsupported source")
    try:
        result = public_playlist_tracks(playlist_id, kind)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"netease playlist tracks failed: {exc}") from exc
    result["data"] = _mark_needs_vip(result.get("data") or [])
    return result


@app.get("/api/v1/auth/status")
def auth_status():
    try:
        return fetch_auth_status()
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.post("/api/v1/auth/login")
def auth_login():
    try:
        return auth_login_qr()
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.get("/api/v1/auth/login/check")
def auth_login_check_endpoint(unikey: str = Query(...)):
    if not unikey.strip():
        raise HTTPException(status_code=400, detail="unikey cannot be empty")
    try:
        return auth_login_check(unikey)
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.post("/api/v1/auth/logout")
def auth_logout_endpoint():
    try:
        return auth_logout()
    except Exception as exc:
        raise UpstreamException(1, str(exc)) from exc


@app.get("/api/v1/auth/login/qr.png")
@app.get("/api/v1/auth/qr.png")
def auth_login_qr_png():
    try:
        import qrcode
    except ImportError as exc:
        raise HTTPException(status_code=501, detail="qrcode extra not installed") from exc
    data = auth_login()
    payload = _extract_payload(data)
    unikey = ""
    if isinstance(payload, dict):
        unikey = str(payload.get("unikey") or payload.get("codekey") or "")
    if not unikey:
        raise UpstreamException(0, "Missing unikey in auth login response")
    qr_url = f"https://music.163.com/login?codekey={unikey}"
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


@app.get("/api/v1/auth/login/qr", response_class=Response)
@app.get("/api/v1/auth/qr", response_class=Response)
def auth_login_qr_text():
    data = auth_login()
    payload = _extract_payload(data)
    qr_ascii = ""
    unikey = ""
    if isinstance(payload, dict):
        qr_ascii = str(payload.get("qr_ascii") or "")
        unikey = str(payload.get("unikey") or payload.get("codekey") or "")
    if not qr_ascii:
        if not unikey:
            raise UpstreamException(0, "Missing unikey or qr_ascii in auth login response")
        qr_url = f"https://music.163.com/login?codekey={unikey}"
        try:
            import qrcode

            qr = qrcode.QRCode()
            qr.add_data(qr_url)
            qr.make(fit=True)
            f = io.StringIO()
            qr.print_ascii(out=f)
            qr_ascii = f.getvalue()
        except ImportError as exc:
            raise HTTPException(status_code=501, detail="qrcode extra not installed") from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to render QR ascii: {exc}") from exc
    if not qr_ascii.endswith("\n"):
        qr_ascii += "\n"
    return Response(content=qr_ascii, media_type="text/plain; charset=utf-8")
