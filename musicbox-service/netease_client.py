"""Pure-Python NetEase Cloud Music API client (WEAPI + EAPI via httpx).

Replaces the legacy ``NetEase-MusicBox==0.5.3`` / ``NEMbox`` subprocess stack
with a thread-safe, dependency-light HTTP client using ``httpx`` and
:mod:`netease_crypto`. Importing this module has zero filesystem or network
side effects.
"""
from __future__ import annotations

import hashlib
from http.cookiejar import Cookie, LWPCookieJar, MozillaCookieJar
import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

try:
    import netease_crypto
except ModuleNotFoundError:
    import sys as _sys

    _svc_dir = os.path.dirname(os.path.abspath(__file__))
    if _svc_dir not in _sys.path:
        _sys.path.insert(0, _svc_dir)
    import netease_crypto

logger = logging.getLogger("musicbox_service.netease_client")

PACKAGE_NAME = "fnmusic-netease"
PACKAGE_VERSION = "1.0.0"

WEAPI_BASE_URL = "https://music.163.com"
EAPI_BASE_URL = "https://interface3.music.163.com"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
DEFAULT_APPVER = "8.10.90"
DEFAULT_OS = "pc"
DEFAULT_OSVER = "Microsoft-Windows-10-Professional-build-19045-64bit"

EXTERNAL_API_URL_ENV = "FNMUSIC_NETEASE_EXTERNAL_API_URL"

# 1 = 综合单曲, 10 = 专辑, 100 = 歌手, 1000 = 歌单
SEARCH_TYPE_CODES: dict[str, int] = {
    "song": 1,
    "album": 10,
    "artist": 100,
    "playlist": 1000,
}


class NetEaseClientError(RuntimeError):
    """Raised when an upstream NetEase HTTP/API request fails."""

    def __init__(self, message: str, *, status_code: int = 502, api_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.api_code = api_code


def _xdg_data_home(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    raw = (env.get("XDG_DATA_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".local" / "share"


def default_cookie_path(environ: dict[str, str] | None = None) -> Path:
    """Return ``$XDG_DATA_HOME/netease-musicbox/cookie.txt`` for login state parity."""
    return _xdg_data_home(environ) / "netease-musicbox" / "cookie.txt"


def default_device_id(cookie_path: Path | None = None) -> str:
    seed = str(cookie_path or default_cookie_path())
    return hashlib.md5(f"fnmusic:{seed}".encode("utf-8")).hexdigest().upper()


def _eapi_header_payload(csrf: str = "", device_id: str = "") -> dict[str, str]:
    now_ms = int(time.time() * 1000)
    return {
        "osver": DEFAULT_OSVER,
        "deviceId": device_id or default_device_id(),
        "appver": DEFAULT_APPVER,
        "versioncode": "140",
        "mobilename": "",
        "buildver": str(int(now_ms // 1000))[:10],
        "resolution": "1920x1080",
        "__csrf": csrf,
        "os": DEFAULT_OS,
        "channel": "",
        "requestId": netease_crypto.build_request_id(),
    }


def _clean_proxies_env() -> bool:
    """Return whether httpx should trust proxy environment variables."""
    # Preserve container/runner semantics: proxy vars are stripped for domestic
    # NetEase hosts when explicit opt-in isn't present.
    return False


class NetEaseClient:
    """Pure-Python NetEase Cloud Music protocol client with LWP cookie jar."""

    def __init__(
        self,
        cookie_path: Path | str | None = None,
        *,
        timeout: float = 15.0,
        transport: httpx.BaseTransport | None = None,
        external_api_url: str | None = None,
    ) -> None:
        self._cookie_path = Path(cookie_path) if cookie_path else default_cookie_path()
        self._timeout = float(timeout)
        self._transport = transport
        self._external_api_url = (
            external_api_url
            if external_api_url is not None
            else os.environ.get(EXTERNAL_API_URL_ENV, "")
        ).strip().rstrip("/")
        self._lock = threading.RLock()
        self._jar: MozillaCookieJar = MozillaCookieJar(str(self._cookie_path))
        self._cookies_loaded = False
        self._http: httpx.Client | None = None
        self._account_cache: dict[str, Any] | None = None
        self._account_cache_at: float = 0.0
        self._vip_cache: dict[str, Any] | None = None
        self._vip_cache_at: float = 0.0

    @property
    def cookie_path(self) -> Path:
        return self._cookie_path

    def _ensure_cookies_loaded(self) -> None:
        with self._lock:
            if self._cookies_loaded:
                return
            jar = MozillaCookieJar(str(self._cookie_path))
            if self._cookie_path.exists():
                try:
                    jar.load(ignore_discard=True, ignore_expires=True)
                except Exception:  # noqa: BLE001
                    lwp_jar = LWPCookieJar(str(self._cookie_path))
                    try:
                        lwp_jar.load(ignore_discard=True, ignore_expires=True)
                        for cookie in lwp_jar:
                            jar.set_cookie(cookie)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Failed to load cookie jar %s: %s", self._cookie_path, exc)
            self._jar = jar
            self._cookies_loaded = True

    def reload_cookies(self) -> None:
        with self._lock:
            self._cookies_loaded = False
            self._account_cache = None
            self._account_cache_at = 0.0
            self._vip_cache = None
            self._vip_cache_at = 0.0
            self._ensure_cookies_loaded()
            if self._http is not None:
                try:
                    self._http.close()
                except Exception:  # noqa: BLE001
                    pass
                self._http = None

    def close(self) -> None:
        with self._lock:
            if self._http is not None:
                try:
                    self._http.close()
                except Exception:  # noqa: BLE001
                    pass
                self._http = None

    def _get_http(self) -> httpx.Client:
        with self._lock:
            self._ensure_cookies_loaded()
            if self._http is None:
                kwargs: dict[str, Any] = {
                    "timeout": self._timeout,
                    "cookies": self._jar,
                    "headers": {
                        "User-Agent": DEFAULT_USER_AGENT,
                        "Referer": f"{WEAPI_BASE_URL}/",
                    },
                }
                if self._transport is not None:
                    kwargs["transport"] = self._transport
                else:
                    kwargs["trust_env"] = _clean_proxies_env()
                self._http = httpx.Client(**kwargs)
            return self._http

    def _cookie_value(self, name: str) -> str:
        with self._lock:
            self._ensure_cookies_loaded()
            for cookie in self._jar:
                if cookie.name == name and cookie.value:
                    return str(cookie.value)
            return ""

    def cookie_dict(self) -> dict[str, str]:
        with self._lock:
            self._ensure_cookies_loaded()
            out: dict[str, str] = {"os": DEFAULT_OS, "appver": DEFAULT_APPVER}
            for cookie in self._jar:
                if cookie.name and cookie.value is not None:
                    out[cookie.name] = str(cookie.value)
            return out

    def has_auth_cookie(self) -> bool:
        return bool(self._cookie_value("MUSIC_U"))

    def set_cookie_value(
        self,
        name: str,
        value: str,
        domain: str = ".music.163.com",
        path: str = "/",
    ) -> None:
        with self._lock:
            self._ensure_cookies_loaded()
            expires = int(time.time()) + 365 * 24 * 3600
            cookie = Cookie(
                version=0,
                name=name,
                value=value,
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=True,
                domain_initial_dot=domain.startswith("."),
                path=path,
                path_specified=True,
                secure=False,
                expires=expires,
                discard=False,
                comment=None,
                comment_url=None,
                rest={},
            )
            self._jar.set_cookie(cookie)
            if self._http is not None:
                self._http.cookies.set(name, value, domain=domain, path=path)

    def save_cookies(self) -> None:
        """Atomically persist the LWP cookie jar without clobbering on error."""
        with self._lock:
            self._ensure_cookies_loaded()
            parent = self._cookie_path.parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_name = tempfile.mkstemp(prefix=".cookie-", suffix=".tmp", dir=str(parent))
            os.close(fd)
            tmp_path = Path(tmp_name)
            try:
                self._jar.save(str(tmp_path), ignore_discard=True, ignore_expires=True)
                try:
                    os.chmod(tmp_path, 0o600)
                except OSError:
                    pass
                os.replace(tmp_path, self._cookie_path)
            except Exception:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise

    def clear_cookies(self) -> None:
        with self._lock:
            self._jar = MozillaCookieJar(str(self._cookie_path))
            self._cookies_loaded = True
            self._account_cache = None
            self._account_cache_at = 0.0
            self._vip_cache = None
            self._vip_cache_at = 0.0
            if self._http is not None:
                try:
                    self._http.close()
                except Exception:  # noqa: BLE001
                    pass
                self._http = None
            self.save_cookies()

    def _sync_response_cookies(self, resp: httpx.Response, *, persist: bool = False) -> bool:
        mutated = False
        if resp.cookies:
            with self._lock:
                self._ensure_cookies_loaded()
                for name, value in resp.cookies.items():
                    if not name:
                        continue
                    prev = self._cookie_value(name)
                    if prev != value:
                        mutated = True
                    self.set_cookie_value(name, value)
        # Also honor Set-Cookie headers if response carries domain/expires details
        raw_set_cookies = resp.headers.get_list("set-cookie") if hasattr(resp.headers, "get_list") else []
        for header in raw_set_cookies:
            head = header.split(";", 1)[0]
            if "=" in head:
                name, value = head.split("=", 1)
                name = name.strip()
                value = value.strip()
                if name in {"MUSIC_U", "__csrf", "MUSIC_A"} and value:
                    with self._lock:
                        if self._cookie_value(name) != value:
                            mutated = True
                        self.set_cookie_value(name, value)
        if persist and mutated:
            self.save_cookies()
        return mutated

    def weapi_post(
        self,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        persist_cookies: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST to a NetEase ``/weapi/...`` endpoint with WEAPI AES+RSA crypto."""
        clean_path = path if path.startswith("/") else f"/{path}"
        csrf = self._cookie_value("__csrf")
        body_payload = dict(payload or {})
        body_payload.setdefault("csrf_token", csrf)
        encrypted = netease_crypto.weapi_encrypt(body_payload)
        url = f"{WEAPI_BASE_URL}{clean_path}"
        if csrf and "csrf_token=" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}csrf_token={csrf}"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": f"{WEAPI_BASE_URL}/",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            http = self._get_http()
            resp = http.post(
                url,
                data=encrypted,
                headers=headers,
                timeout=timeout or self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NetEaseClientError(f"NetEase WEAPI {clean_path} HTTP failed: {exc}") from exc

        self._sync_response_cookies(resp, persist=persist_cookies)
        try:
            parsed = resp.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise NetEaseClientError(
                f"NetEase WEAPI {clean_path} returned invalid JSON: {exc}"
            ) from exc
        if not isinstance(parsed, dict):
            raise NetEaseClientError(f"NetEase WEAPI {clean_path} returned unexpected payload type")
        return parsed

    def eapi_post(
        self,
        api_path: str,
        payload: dict[str, Any] | None = None,
        *,
        eapi_url: str | None = None,
        persist_cookies: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """POST to a NetEase ``/eapi/...`` endpoint with EAPI AES-ECB crypto."""
        clean_api_path = api_path if api_path.startswith("/") else f"/{api_path}"
        if eapi_url:
            target_url = eapi_url
        else:
            eapi_suffix = (
                clean_api_path[len("/api"):]
                if clean_api_path.startswith("/api/")
                else clean_api_path
            )
            target_url = f"{EAPI_BASE_URL}/eapi{eapi_suffix}"

        csrf = self._cookie_value("__csrf")
        header_obj = _eapi_header_payload(csrf=csrf, device_id=default_device_id(self._cookie_path))
        req_payload = dict(payload or {})
        req_payload.setdefault("header", json.dumps(header_obj, separators=(",", ":")))
        req_payload.setdefault("e_r", True)
        encrypted = netease_crypto.eapi_encrypt(clean_api_path, req_payload)
        parsed_host = urlsplit(target_url)
        referer = f"{parsed_host.scheme}://{parsed_host.netloc}/" if parsed_host.netloc else f"{WEAPI_BASE_URL}/"
        headers = {
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": referer,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        cookies = self.cookie_dict()
        cookies.setdefault("osver", header_obj["osver"])
        cookies.setdefault("deviceId", header_obj["deviceId"])
        cookies.setdefault("appver", header_obj["appver"])
        cookies.setdefault("versioncode", header_obj["versioncode"])
        try:
            http = self._get_http()
            resp = http.post(
                target_url,
                data=encrypted,
                headers=headers,
                cookies=cookies,
                timeout=timeout or self._timeout,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise NetEaseClientError(f"NetEase EAPI {clean_api_path} HTTP failed: {exc}") from exc

        self._sync_response_cookies(resp, persist=persist_cookies)
        try:
            if req_payload.get("e_r"):
                parsed = netease_crypto.eapi_response_decrypt(resp.content)
            else:
                parsed = resp.json()
        except Exception:  # noqa: BLE001
            try:
                parsed = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise NetEaseClientError(
                    f"NetEase EAPI {clean_api_path} response decode failed: {exc}"
                ) from exc
        if not isinstance(parsed, dict):
            raise NetEaseClientError(f"NetEase EAPI {clean_api_path} returned unexpected payload type")
        return parsed

    def external_api_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Optional ``FNMUSIC_NETEASE_EXTERNAL_API_URL`` hook for custom sidecars."""
        if not self._external_api_url:
            return None
        clean = path if path.startswith("/") else f"/{path}"
        url = f"{self._external_api_url}{clean}"
        try:
            with httpx.Client(timeout=self._timeout, trust_env=False) as client:
                resp = client.get(url, params=params or {})
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, dict) else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("External NetEase API %s failed: %s", clean, exc)
            return None

    # ------------------------------------------------------------ Auth / Account

    def get_account_info(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if (
                not refresh
                and self._account_cache is not None
                and (now - self._account_cache_at) < 30.0
            ):
                return self._account_cache
        data = self.weapi_post("/weapi/nuser/account/get", {})
        with self._lock:
            if isinstance(data, dict) and data.get("code") == 200:
                self._account_cache = data
                self._account_cache_at = time.monotonic()
        return data

    def get_vip_info(self, *, refresh: bool = False) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            if (
                not refresh
                and self._vip_cache is not None
                and (now - self._vip_cache_at) < 60.0
            ):
                return self._vip_cache
        data = self.weapi_post("/weapi/music-vip-membership/client/vip/info", {})
        with self._lock:
            if isinstance(data, dict) and data.get("code") == 200:
                self._vip_cache = data
                self._vip_cache_at = time.monotonic()
        return data

    def auth_status(self) -> dict[str, Any]:
        if not self.has_auth_cookie():
            return {"ok": True, "data": {"logged_in": False}}
        try:
            acct = self.get_account_info(refresh=True)
        except NetEaseClientError as exc:
            return {"ok": True, "data": {"logged_in": False, "error": str(exc)}}
        profile = acct.get("profile") if isinstance(acct, dict) else None
        account = acct.get("account") if isinstance(acct, dict) else None
        uid = None
        if isinstance(profile, dict) and profile.get("userId"):
            uid = profile["userId"]
        elif isinstance(account, dict) and account.get("id"):
            uid = account["id"]
        if not uid:
            return {"ok": True, "data": {"logged_in": False}}
        vip_type = (
            (profile or {}).get("vipType")
            or (account or {}).get("vipType")
            or 0
        )
        return {
            "ok": True,
            "data": {
                "logged_in": True,
                "user_id": int(uid),
                "uid": int(uid),
                "nickname": (profile or {}).get("nickname") or "",
                "avatar_url": (profile or {}).get("avatarUrl") or "",
                "vip_type": int(vip_type) if isinstance(vip_type, (int, float)) else 0,
            },
        }

    def qr_unikey(self) -> dict[str, Any]:
        data = self.weapi_post("/weapi/login/qrcode/unikey", {"type": 1})
        unikey = str(data.get("unikey") or data.get("codekey") or "")
        if not unikey:
            raise NetEaseClientError(f"Failed to obtain QR unikey: {data}")
        return {
            "ok": True,
            "data": {
                "unikey": unikey,
                "qr_url": f"https://music.163.com/login?codekey={unikey}",
            },
        }

    def qr_check(self, unikey: str) -> dict[str, Any]:
        data = self.weapi_post(
            "/weapi/login/qrcode/client/login",
            {"key": unikey, "type": 1},
            persist_cookies=True,
        )
        code = data.get("code")
        if code == 803:
            self.save_cookies()
            with self._lock:
                self._account_cache = None
                self._vip_cache = None
        return {
            "ok": True,
            "data": {
                "code": code,
                "message": data.get("message") or "",
                "nickname": data.get("nickname") or "",
                "avatar_url": data.get("avatarUrl") or "",
                "logged_in": code == 803,
            },
        }

    def logout(self) -> dict[str, Any]:
        try:
            self.weapi_post("/weapi/logout", {})
        except NetEaseClientError:
            pass
        self.clear_cookies()
        return {"ok": True, "data": {"logged_in": False}}

    # ------------------------------------------------------------ Search & Catalog

    def cloudsearch(
        self,
        keyword: str,
        *,
        search_type: str | int = "song",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if isinstance(search_type, int):
            type_code = search_type
        else:
            type_code = SEARCH_TYPE_CODES.get(str(search_type).strip().lower(), 1)
        payload = {
            "s": keyword,
            "type": type_code,
            "limit": int(limit),
            "offset": int(offset),
            "total": True,
        }
        return self.eapi_post(
            "/api/cloudsearch/get/web",
            payload,
            eapi_url=f"{EAPI_BASE_URL}/eapi/cloudsearch/get/web",
        )

    def song_detail(self, song_ids: list[int]) -> dict[str, Any]:
        clean_ids = [int(sid) for sid in song_ids if int(sid) > 0]
        if not clean_ids:
            return {"code": 200, "songs": [], "privileges": []}
        merged_songs: list[dict[str, Any]] = []
        merged_privs: list[dict[str, Any]] = []
        for idx in range(0, len(clean_ids), 1000):
            batch = clean_ids[idx : idx + 1000]
            res = self.weapi_post(
                "/weapi/v3/song/detail",
                {
                    "c": json.dumps([{"id": sid} for sid in batch], separators=(",", ":")),
                    "ids": json.dumps(batch, separators=(",", ":")),
                },
            )
            songs = res.get("songs")
            if isinstance(songs, list):
                merged_songs.extend(s for s in songs if isinstance(s, dict))
            privs = res.get("privileges")
            if isinstance(privs, list):
                merged_privs.extend(p for p in privs if isinstance(p, dict))
        return {"code": 200, "songs": merged_songs, "privileges": merged_privs}

    def song_url_v1(
        self,
        song_ids: list[int],
        *,
        level: str = "exhigh",
        encode_type: str = "flac",
    ) -> dict[str, Any]:
        clean_ids = [int(sid) for sid in song_ids if int(sid) > 0]
        if not clean_ids:
            return {"code": 200, "data": []}
        return self.eapi_post(
            "/api/song/enhance/player/url/v1",
            {
                "ids": json.dumps(clean_ids, separators=(",", ":")),
                "level": level,
                "encodeType": encode_type,
            },
            eapi_url=f"{EAPI_BASE_URL}/eapi/song/enhance/player/url/v1",
        )

    def song_lyric(self, song_id: int) -> dict[str, Any]:
        return self.weapi_post(
            "/weapi/song/lyric",
            {"id": int(song_id), "lv": -1, "tv": -1, "rv": -1, "kv": -1},
        )

    def playlist_detail(self, playlist_id: int, *, n: int = 100000, s: int = 8) -> dict[str, Any]:
        return self.weapi_post(
            "/weapi/v6/playlist/detail",
            {"id": int(playlist_id), "n": int(n), "s": int(s)},
        )

    def user_playlists(self, uid: int, *, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        return self.weapi_post(
            "/weapi/user/playlist",
            {"uid": int(uid), "limit": int(limit), "offset": int(offset), "includeVideo": True},
        )

    def album_detail(self, album_id: int) -> dict[str, Any]:
        return self.weapi_post(f"/weapi/v1/album/{int(album_id)}", {})

    def artist_detail(self, artist_id: int) -> dict[str, Any]:
        return self.weapi_post(f"/weapi/v1/artist/{int(artist_id)}", {})

    def recommend_songs(self) -> dict[str, Any]:
        return self.weapi_post("/weapi/v3/discovery/recommend/songs", {})

    def recommend_resource(self) -> dict[str, Any]:
        return self.weapi_post("/weapi/v1/discovery/recommend/resource", {})

    def personalized_playlists(self, *, limit: int = 30) -> dict[str, Any]:
        return self.weapi_post(
            "/weapi/personalized/playlist",
            {"limit": int(limit), "total": True, "n": 1000},
        )

    def toplist(self) -> dict[str, Any]:
        return self.weapi_post("/weapi/toplist/detail", {})

    def playlist_catalogue(self) -> dict[str, Any]:
        return self.weapi_post("/weapi/playlist/catalogue", {})

    def highquality_tags(self) -> dict[str, Any]:
        return self.weapi_post("/weapi/playlist/highquality/tags", {})

    def highquality_playlists(
        self,
        *,
        cat: str = "全部",
        limit: int = 30,
        lasttime: int = 0,
    ) -> dict[str, Any]:
        return self.weapi_post(
            "/weapi/playlist/highquality/list",
            {"cat": cat or "全部", "limit": int(limit), "lasttime": int(lasttime), "total": True},
        )

    def top_playlists(
        self,
        *,
        cat: str = "全部",
        order: str = "hot",
        limit: int = 30,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self.weapi_post(
            "/weapi/playlist/list",
            {
                "cat": cat or "全部",
                "order": order or "hot",
                "limit": int(limit),
                "offset": int(offset),
                "total": True,
            },
        )

    def raw_weapi(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.weapi_post(path, payload)

    def raw_eapi(
        self,
        api_path: str,
        eapi_url: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.eapi_post(api_path, payload, eapi_url=eapi_url)


_SHARED_LOCK = threading.RLock()
_SHARED_CLIENT: NetEaseClient | None = None


def get_client(*, reset: bool = False) -> NetEaseClient:
    """Return the process-wide lazy NetEaseClient instance."""
    global _SHARED_CLIENT
    with _SHARED_LOCK:
        if _SHARED_CLIENT is None:
            _SHARED_CLIENT = NetEaseClient()
        elif reset:
            _SHARED_CLIENT.reload_cookies()
        return _SHARED_CLIENT


def reset_client(reason: str = "") -> None:
    """Reload cookie state on the shared NetEase client."""
    global _SHARED_CLIENT
    with _SHARED_LOCK:
        if _SHARED_CLIENT is not None:
            _SHARED_CLIENT.reload_cookies()
            logger.info("netease client reloaded (%s)", reason or "manual")


def load_cookie_dict() -> dict[str, str]:
    """Return cookie mapping from the shared NetEase client."""
    return get_client().cookie_dict()
