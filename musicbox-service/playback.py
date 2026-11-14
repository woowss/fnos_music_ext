"""Bounded asynchronous NetEase playback resolution with explicit quality."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from pathlib import Path
import sys
import threading
import time

import httpx

logger = logging.getLogger("musicbox_service.playback")
BUDGET_S = 12.0
PRIMARY_TIMEOUT_S = 10.0
CACHE_MAX = 4096
_lock = threading.Lock()
_generation = 0
_cache: dict = {}
_tasks: dict = {}


class CapabilityError(Exception):
    pass


class UpstreamError(Exception):
    pass


class ResolutionTimeout(Exception):
    pass


def _ensure_local_path() -> None:
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)


def reset() -> None:
    global _generation
    with _lock:
        _generation += 1
        _cache.clear()
    _ensure_local_path()
    import netease_client

    netease_client.reset_client("playback reset")


def _snapshot(generation: int):
    _ensure_local_path()
    import netease_client
    from runner import ensure_xdg_dirs

    ensure_xdg_dirs()
    client = netease_client.get_client()
    with _lock:
        if generation != _generation:
            raise UpstreamError("login state changed")
        client._ensure_cookies_loaded()
        cookie_dict = client.cookie_dict()
        csrf = cookie_dict.get("__csrf", "")
        header_cookie = netease_client._eapi_header_payload(
            csrf=csrf,
            device_id=netease_client.default_device_id(client.cookie_path),
        )
        headers = {
            "User-Agent": netease_client.DEFAULT_USER_AGENT,
            "Referer": f"{netease_client.WEAPI_BASE_URL}/",
        }
        cookies = [copy.copy(cookie) for cookie in client._jar]
        return header_cookie, headers, cookies


def _merge_cookies(generation: int, cookies: httpx.Cookies) -> None:
    _ensure_local_path()
    import netease_client

    client = netease_client.get_client()
    with _lock:
        if generation != _generation:
            return
        changed = False
        for cookie in cookies.jar:
            if cookie.domain.lstrip(".") not in ("music.163.com", "interface.music.163.com", "interface3.music.163.com", "163.com"):
                continue
            client._jar.set_cookie(copy.copy(cookie))
            changed = True
        if changed:
            try:
                client.save_cookies()
            except OSError:
                logger.warning("playback cookie persistence failed")


def _rows(payload) -> list:
    if not isinstance(payload, dict) or payload.get("code", 200) != 200:
        raise UpstreamError("NetEase rejected playback request")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise UpstreamError("invalid playback response")
    return rows


_STANDARD_LEVELS = {
    "standard": "standard", "higher": "higher", "exhigh": "exhigh",
    "lossless": "lossless", "hires": "hires", "jymaster": "jymaster", "sky": "sky",
}
_STANDARD_ENCODE_TYPES = {
    "standard": "mp3", "higher": "mp3", "exhigh": "mp3",
    "lossless": "flac", "hires": "flac", "jymaster": "flac", "sky": "flac",
}


def _safe_level_and_encode_type(quality: str) -> tuple[str, str]:
    """Authoritative 7-tier NetEase quality mapping (no silent downgrade)."""
    clean_q = str(quality or "").strip().lower()
    if clean_q in _STANDARD_LEVELS:
        return _STANDARD_LEVELS[clean_q], _STANDARD_ENCODE_TYPES[clean_q]
    return clean_q or "exhigh", _STANDARD_ENCODE_TYPES.get(clean_q, "mp3")


async def _request(song_id: int, quality: str, generation: int) -> dict:
    _ensure_local_path()
    import netease_client
    import netease_crypto

    header_cookie, headers, cookies = await asyncio.to_thread(_snapshot, generation)
    level, encode_type = _safe_level_and_encode_type(quality)
    api_path = "/api/song/enhance/player/url/v1"
    payload = {
        "ids": json.dumps([song_id], separators=(",", ":")),
        "level": level,
        "encodeType": encode_type,
        "e_r": True,
        "header": json.dumps(header_cookie, separators=(",", ":")),
    }
    eapi_headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": netease_client.DEFAULT_USER_AGENT,
        "Referer": f"{netease_client.EAPI_BASE_URL}/",
    }
    async with httpx.AsyncClient(timeout=PRIMARY_TIMEOUT_S, trust_env=False) as client:
        for cookie in cookies:
            client.cookies.jar.set_cookie(cookie)
        for key in ("os", "osver", "deviceId", "appver", "versioncode"):
            val = header_cookie.get(key)
            if val and not client.cookies.get(key):
                client.cookies.set(key, str(val))
        response = await asyncio.wait_for(client.post(
            f"{netease_client.EAPI_BASE_URL}/eapi/song/enhance/player/url/v1",
            data=netease_crypto.eapi_encrypt(api_path, payload),
            headers=eapi_headers,
        ), PRIMARY_TIMEOUT_S)
        response.raise_for_status()
        response_cookies = httpx.Cookies(response.cookies)
        try:
            data = response.json()
        except ValueError:
            data = netease_crypto.eapi_response_decrypt(response.content)
        rows = _rows(data)
        # Respect account entitlement and tier boundaries: never forge sky/jymaster via weapi 999k
        if not rows and level not in ("sky", "jymaster"):
            bitrate_map = {
                "standard": 128000, "higher": 192000, "exhigh": 320000,
                "lossless": 999000, "hires": 999000,
            }
            bitrate = bitrate_map.get(level)
            if bitrate:
                csrf = header_cookie.get("__csrf", "")
                fallback_headers = {key: value for key, value in headers.items() if key.lower() != "host"}
                response = await client.post(
                    f"{netease_client.WEAPI_BASE_URL}/weapi/song/enhance/player/url",
                    data=netease_crypto.weapi_encrypt({"ids": [song_id], "br": bitrate, "csrf_token": csrf}),
                    headers=fallback_headers,
                )
                response.raise_for_status()
                response_cookies.update(response.cookies)
                rows = _rows(response.json())
        if response_cookies:
            await asyncio.to_thread(_merge_cookies, generation, response_cookies)
    if not rows:
        return {"id": song_id, "code": 404, "url": None}
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise UpstreamError("invalid single-song playback response")
    item = rows[0]
    if item.get("id") is not None and str(item["id"]) != str(song_id):
        raise UpstreamError("mismatched playback song")
    if item.get("code") not in (200, 404):
        raise UpstreamError("NetEase returned a playback error")
    if item.get("code") == 200 and item.get("url") and not str(item["url"]).startswith(("http://", "https://")):
        raise UpstreamError("invalid playback URL")
    return item


async def _resolve(song_id: int, quality: str, generation: int,
                   purpose: str, context_generation: int) -> dict:
    started = time.monotonic()
    try:
        async with asyncio.timeout(BUDGET_S):
            item = await _request(song_id, quality, generation)
    except (TimeoutError, httpx.TimeoutException) as exc:
        raise ResolutionTimeout("NetEase playback resolution timed out") from exc
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise UpstreamError(f"NetEase playback request failed ({type(exc).__name__})") from exc
    with _lock:
        if generation != _generation:
            raise UpstreamError("login state changed")
        if item.get("code") not in (200, 404):
            raise UpstreamError("invalid playback status")
        if item.get("url") and not str(item["url"]).startswith(("http://", "https://")):
            raise UpstreamError("invalid playback URL")
        try:
            expi = float(item.get("expi") or 0)
        except (ValueError, TypeError):
            expi = 0
        ttl = min(max(expi - 60, 1), 600) if expi > 0 else 300
        if not item.get("url"):
            ttl = 60
        now = time.monotonic()
        for key in [key for key, value in _cache.items() if value[1] <= now]:
            del _cache[key]
        if len(_cache) >= CACHE_MAX:
            del _cache[min(_cache, key=lambda key: _cache[key][1])]
        _cache[(generation, song_id, quality, purpose, context_generation)] = (item, now + ttl)
    logger.info("playback resolved song=%s quality=%s purpose=%s available=%s ms=%d",
                song_id, quality, purpose, bool(item.get("url")), int((time.monotonic() - started) * 1000))
    return item


def _finish(task, key) -> None:
    if _tasks.get(key) is task:
        del _tasks[key]
    if not task.cancelled():
        task.exception()


async def get_song_url_async(song_id: int, quality: str, fresh: bool = False,
                             purpose: str = "playback", context_generation: int = 0) -> dict:
    purpose = "download" if str(purpose).strip().lower() == "download" else "playback"
    context_generation = max(0, int(context_generation or 0))
    with _lock:
        generation = _generation
        cache_key = (generation, song_id, quality, purpose, context_generation)
        cached = _cache.get(cache_key)
    key = (asyncio.get_running_loop(), *cache_key)
    task = _tasks.get(key)
    if task is None or task.done():
        if not fresh and cached and cached[1] > time.monotonic():
            return {**cached[0], "reuse_for_s": max(0.0, cached[1] - time.monotonic()),
                    "expiry_source": "policy"}
        task = asyncio.create_task(
            _resolve(song_id, quality, generation, purpose, context_generation))
        _tasks[key] = task
        task.add_done_callback(lambda done: _finish(done, key))
    result = await asyncio.shield(task)
    with _lock:
        selected = _cache.get(cache_key)
        remaining = max(0.0, selected[1] - time.monotonic()) if selected else 0.0
    return {**result, "reuse_for_s": remaining, "expiry_source": "policy"}


async def shutdown() -> None:
    tasks = [task for task in _tasks.values() if task.get_loop() is asyncio.get_running_loop()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
