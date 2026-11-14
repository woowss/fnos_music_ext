import io
import json
import os
import sys
import time
import asyncio
import importlib.util
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

# musicbox-service 内部使用裸导入（import runner / from netease_ext import ...），
# 仍需把服务目录加入 sys.path；但 app 本体以独立模块名加载，
# 避免与其他服务的顶层 `app` 模块在同一 pytest 会话中冲突
MUSICBOX_SERVICE_DIR = Path(__file__).resolve().parent.parent.parent / "musicbox-service"
if str(MUSICBOX_SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(MUSICBOX_SERVICE_DIR))

import runner
import netease_ext

_spec = importlib.util.spec_from_file_location("musicbox_service_app", MUSICBOX_SERVICE_DIR / "app.py")
musicbox_app = importlib.util.module_from_spec(_spec)
sys.modules["musicbox_service_app"] = musicbox_app
_spec.loader.exec_module(musicbox_app)

app = musicbox_app.app
UpstreamException = musicbox_app.UpstreamException


@pytest.fixture(autouse=True)
def reset_netease_ext_state(monkeypatch):
    """登录态 TTL 缓存与取链缓存是模块级状态，测试间必须隔离。"""
    monkeypatch.setattr(netease_ext, "_login_state", None)
    monkeypatch.setattr(netease_ext, "_url_cache", {})
    yield


def test_ensure_xdg_dirs_creates_all_directories(tmp_path, monkeypatch):
    cache_dir = tmp_path / "custom_cache"
    config_dir = tmp_path / "custom_config"
    data_dir = tmp_path / "custom_data"
    runtime_dir = tmp_path / "custom_runtime"

    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_dir))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))

    runner.ensure_xdg_dirs()

    # All base directories and netease-musicbox subdirectories must exist
    assert cache_dir.is_dir()
    assert (cache_dir / "netease-musicbox").is_dir()
    assert config_dir.is_dir()
    assert (config_dir / "netease-musicbox").is_dir()
    assert data_dir.is_dir()
    assert (data_dir / "netease-musicbox").is_dir()
    assert runtime_dir.is_dir()
    assert (runtime_dir / "netease-musicbox").is_dir()


@pytest.mark.asyncio
async def test_cached_playback_description_reports_remaining_lifetime(monkeypatch):
    playback = musicbox_app.playback
    cached_item = {"code": 200, "url": "https://cdn.example/fixture.flac", "expi": 1800}
    monkeypatch.setattr(playback, "_cache", {
        (playback._generation, 123, "lossless", "playback", 0): (cached_item, time.monotonic() + 8)})
    monkeypatch.setattr(playback, "_tasks", {})
    first = await playback.get_song_url_async(123, "lossless")
    second = await playback.get_song_url_async(123, "lossless")
    assert 0 < second["reuse_for_s"] <= first["reuse_for_s"] <= 8
    assert first["expiry_source"] == "policy"
    assert "reuse_for_s" not in cached_item


@pytest.mark.asyncio
async def test_netease_cache_and_inflight_include_purpose_and_context_generation(monkeypatch):
    """网易相同曲目与档位在不同用途或配置代下不得共享任务和缓存。"""
    playback = musicbox_app.playback
    monkeypatch.setattr(playback, "_cache", {})
    monkeypatch.setattr(playback, "_tasks", {})
    calls = []
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def request(song_id, quality, login_generation):
        call_id = len(calls) + 1
        calls.append((song_id, quality))
        if len(calls) == 2:
            both_started.set()
        await release.wait()
        return {"code": 200, "url": f"https://cdn.test/{call_id}.mp3"}

    monkeypatch.setattr(playback, "_request", request)
    playback_task = asyncio.create_task(playback.get_song_url_async(
        123, "standard", purpose="playback", context_generation=7))
    download_task = asyncio.create_task(playback.get_song_url_async(
        123, "standard", purpose="download", context_generation=7))
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()
    playback_result, download_result = await asyncio.gather(playback_task, download_task)

    assert playback_result["url"] != download_result["url"]
    assert calls == [(123, "standard"), (123, "standard")]
    await playback.get_song_url_async(123, "standard", purpose="download", context_generation=7)
    assert len(calls) == 2
    await playback.get_song_url_async(123, "standard", purpose="download", context_generation=8)
    assert len(calls) == 3


def test_auth_login_qr_with_upstream_qr_ascii(monkeypatch):
    test_ascii = "█▀▀▀▀▀▀▀█\n█ █▀▀▀█ █\n▀▀▀▀▀▀▀▀▀"

    monkeypatch.setattr(
        musicbox_app,
        "auth_login_qr",
        lambda: {"ok": True, "data": {"unikey": "test-key-123", "qr_ascii": test_ascii}},
    )

    with TestClient(app) as client:
        resp1 = client.get("/api/v1/auth/login/qr")
        assert resp1.status_code == 200
        assert "text/plain" in resp1.headers["content-type"]
        assert test_ascii in resp1.text
        assert resp1.text.endswith("\n")

        resp2 = client.get("/api/v1/auth/qr")
        assert resp2.status_code == 200
        assert "text/plain" in resp2.headers["content-type"]
        assert test_ascii in resp2.text
        assert resp2.text.endswith("\n")


def test_auth_login_qr_fallback_to_qrcode_generation(monkeypatch):
    pytest.importorskip("qrcode")
    monkeypatch.setattr(
        musicbox_app,
        "auth_login_qr",
        lambda: {"ok": True, "data": {"unikey": "test-fallback-key"}},
    )

    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/login/qr")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert len(resp.text) > 50
        assert resp.text.endswith("\n")


def test_auth_login_qr_missing_unikey_and_ascii(monkeypatch):
    monkeypatch.setattr(musicbox_app, "auth_login_qr", lambda: {"ok": True, "data": {}})

    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/login/qr")
        assert resp.status_code == 502
        rj = resp.json()
        assert rj["error"] == "upstream_error"
        assert "Missing unikey or qr_ascii" in rj["stderr"]


def test_auth_qr_png_endpoint_and_alias(monkeypatch):
    pytest.importorskip("qrcode")
    monkeypatch.setattr(
        musicbox_app,
        "auth_login_qr",
        lambda: {"ok": True, "data": {"unikey": "png-test-key"}},
    )

    with TestClient(app) as client:
        resp1 = client.get("/api/v1/auth/login/qr.png")
        assert resp1.status_code == 200
        assert resp1.headers["content-type"] == "image/png"
        assert resp1.content[:8] == b"\x89PNG\r\n\x1a\n"

        resp2 = client.get("/api/v1/auth/qr.png")
        assert resp2.status_code == 200
        assert resp2.headers["content-type"] == "image/png"
        assert resp2.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_get_clean_env_strips_proxy_vars(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:7890")
    cleaned = runner.get_clean_env()
    assert "HTTP_PROXY" not in cleaned
    assert "https_proxy" not in cleaned


def test_ensure_xdg_dirs_creates_cookie_parent(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    runner.ensure_xdg_dirs()
    assert (tmp_path / "data" / "netease-musicbox").is_dir()


def test_musicbox_search_filters_unplayable_songs(monkeypatch):
    import netease_ext

    mock_search_rows = [
        {"song_id": 101, "song_name": "Free Song", "artist": "Singer", "quality": "exhigh"},
        {"song_id": 102, "song_name": "VIP Song No URL", "artist": "Singer", "quality": "lossless"},
        {"song_id": 103, "song_name": "Trial Snippet Song", "artist": "Singer", "quality": "standard"},
        {"song_id": 104, "song_name": "Dead Song", "artist": "Singer", "quality": "standard"},
    ]

    def mock_songs_url(ids):
        return [
            {"id": 101, "url": "http://audio.126.net/101.mp3", "code": 200, "fee": 0, "freeTrialInfo": None},
            {"id": 102, "url": None, "code": 404, "fee": 1, "freeTrialInfo": None},
            {"id": 103, "url": "http://audio.126.net/103_trial.mp3", "code": 200, "fee": 1, "freeTrialInfo": {"start": 0, "end": 30}},
            {"id": 104, "url": "", "code": 200, "fee": 0, "freeTrialInfo": None},
        ]

    class MockApi:
        def songs_url(self, ids):
            return mock_songs_url(ids)

        def get_account_info(self):
            return {"code": 200, "account": None, "profile": None}

    monkeypatch.setattr(musicbox_app, "search_catalog", lambda *a, **kw: mock_search_rows)
    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())

    with TestClient(app) as client:
        resp = client.get("/api/v1/search", params={"keyword": "test", "type": "song", "limit": 20})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["song_id"] == 101


def test_musicbox_songs_detail_returns_all_rows_with_covers(monkeypatch):
    """详情接口与可播性解耦（2.8.0）：封面/元数据绝不因取链判定整行丢弃。

    登录态抖动/VIP 权益波动会让 songs_url 拿不到完整 url，此前 102（取不到
    url）与 103（试听片段）的详情连同专辑封面一起消失，表现为"已登录 VIP
    却大面积无封面"。可播过滤只属于搜索入口。
    """
    import netease_ext

    def mock_songs_detail(ids):
        return [
            {"id": 101, "name": "Free Song", "ar": [{"name": "A"}], "al": {"name": "Album", "picUrl": "http://img/1.jpg"}, "dt": 200000},
            {"id": 102, "name": "VIP Song", "ar": [{"name": "A"}], "al": {"name": "Album", "picUrl": "http://img/2.jpg"}, "dt": 200000},
            {"id": 103, "name": "Trial Song", "ar": [{"name": "A"}], "al": {"name": "Album", "picUrl": "http://img/3.jpg"}, "dt": 30000},
        ]

    def mock_songs_url(ids):
        return [
            {"id": 101, "url": "http://audio.126.net/101.mp3", "code": 200, "fee": 0, "freeTrialInfo": None},
            {"id": 102, "url": None, "code": 404, "fee": 1, "freeTrialInfo": None},
            {"id": 103, "url": "http://audio.126.net/103_trial.mp3", "code": 200, "fee": 1, "freeTrialInfo": {"start": 0, "end": 30}},
        ]

    class MockApi:
        def songs_detail(self, ids):
            return mock_songs_detail(ids)

        def songs_url(self, ids):
            raise AssertionError("详情接口不得触发取链过滤")

        def get_account_info(self):
            return {"code": 200, "account": None, "profile": None}

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())

    with TestClient(app) as client:
        resp = client.get("/api/v1/songs/detail", params={"ids": "101,102,103"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        # 全量返回且保持请求顺序，封面随行
        assert [row["song_id"] for row in data["data"]] == [101, 102, 103]
        assert [row["album_pic_url"] for row in data["data"]] == [
            "http://img/1.jpg", "http://img/2.jpg", "http://img/3.jpg",
        ]


def test_musicbox_search_logged_in_vip_playable(monkeypatch):
    import netease_ext

    mock_search_rows = [
        {"song_id": 201, "song_name": "VIP Song With Perm", "artist": "Singer", "quality": "lossless"},
        {"song_id": 202, "song_name": "Paid Album Without Perm", "artist": "Singer", "quality": "lossless"},
    ]

    def mock_songs_url(ids):
        return [
            {"id": 201, "url": "http://audio.126.net/vip_full.mp3", "code": 200, "fee": 1, "freeTrialInfo": None},
            {"id": 202, "url": None, "code": 404, "fee": 4, "freeTrialInfo": None},
        ]

    class MockApi:
        def songs_url(self, ids):
            return mock_songs_url(ids)

        def get_account_info(self):
            return {"code": 200, "account": {"id": 12345}, "profile": {"nickname": "VIPUser"}}

    monkeypatch.setattr(musicbox_app, "search_catalog", lambda *a, **kw: mock_search_rows)
    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())

    with TestClient(app) as client:
        resp = client.get("/api/v1/search", params={"keyword": "test", "type": "song", "limit": 20})
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 1
        assert data["data"][0]["song_id"] == 201


def test_musicbox_search_fallback_on_cloudsearch_failure(monkeypatch):
    fallback_called = []

    def boom_catalog(*a, **kw):
        raise RuntimeError("cloudsearch blocked")

    def mock_search_web_fallback(keyword, stype="song", limit=20, offset=0):
        fallback_called.append((keyword, stype, limit, offset))
        return [
            {
                "song_id": 99901,
                "id": 99901,
                "song_name": "海阔天空",
                "title": "海阔天空",
                "artist": "Beyond",
                "album_name": "Words & Music",
                "album": "Words & Music",
                "duration": 240.0,
                "quality": "lossless",
            }
        ]

    monkeypatch.setattr(musicbox_app, "search_catalog", boom_catalog)
    monkeypatch.setattr(musicbox_app, "search_web_fallback", mock_search_web_fallback)
    monkeypatch.setattr(musicbox_app, "filter_playable_song_ids", lambda ids: set(ids))

    with TestClient(app) as client:
        resp = client.get("/api/v1/search", params={"keyword": "海阔天空", "type": "song", "limit": 10})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["song_id"] == 99901
        assert data["data"][0]["song_name"] == "海阔天空"
        assert len(fallback_called) == 1
        assert fallback_called[0][0] == "海阔天空"


def test_musicbox_search_fallback_on_cloudsearch_empty(monkeypatch):
    def mock_search_web_fallback(keyword, stype="song", limit=20, offset=0):
        return [
            {
                "song_id": 88801,
                "id": 88801,
                "song_name": "Fallback Song",
                "title": "Fallback Song",
                "artist": "Artist",
                "album_name": "Album",
                "album": "Album",
                "duration": 210.0,
                "quality": "lossless",
            }
        ]

    monkeypatch.setattr(musicbox_app, "search_catalog", lambda *a, **kw: [])
    monkeypatch.setattr(musicbox_app, "search_web_fallback", mock_search_web_fallback)
    monkeypatch.setattr(musicbox_app, "filter_playable_song_ids", lambda ids: {88801} if 88801 in ids else set())

    with TestClient(app) as client:
        resp = client.get("/api/v1/search", params={"keyword": "test", "type": "song", "limit": 20})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert len(data["data"]) == 1
        assert data["data"][0]["song_id"] == 88801


def test_search_web_fallback_function(monkeypatch):
    import netease_ext

    fake_resp_data = {
        "result": {
            "songs": [
                {
                    "id": 1357375695,
                    "name": "海阔天空",
                    "artists": [{"id": 11127, "name": "Beyond"}],
                    "album": {"id": 78372827, "name": "精选系列"},
                    "duration": 239560,
                }
            ],
            "songCount": 1,
        },
        "code": 200,
    }

    class MockResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return fake_resp_data

    class MockHttpxClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def post(self, url, headers=None, data=None):
            assert url == "https://music.163.com/api/search/get/web"
            assert data["s"] == "海阔天空"
            assert data["type"] == "1"
            return MockResponse()

    monkeypatch.setattr(netease_ext, "httpx", type("MockHttpxMod", (), {"Client": MockHttpxClient}))

    items = netease_ext.search_web_fallback("海阔天空", stype="song", limit=5)
    assert len(items) == 1
    assert items[0]["song_id"] == 1357375695
    assert items[0]["song_name"] == "海阔天空"
    assert items[0]["artist"] == "Beyond"
    assert items[0]["album_name"] == "精选系列"
    assert abs(items[0]["duration"] - 239.56) < 0.01




# ------------------------------------------------ 未覆盖端点与错误信封补测 ---

def test_healthz_endpoint():
    with TestClient(app) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    rj = resp.json()
    assert rj["status"] == "ok"
    assert rj["package"] == "fnmusic-netease"
    assert rj["protocol"] == "pure-python-weapi-eapi"


def test_reset_api_invalidates_vip_entitlement(monkeypatch):
    import netease_ext

    monkeypatch.setattr(netease_ext, "_vip_state", (True, time.monotonic()))
    netease_ext.reset_api("test account switch")
    assert netease_ext._vip_state is None


def test_song_url_invalid_quality_rejected():
    with TestClient(app) as client:
        resp = client.get("/api/v1/song/123/url", params={"quality": "ultra"})
    assert resp.status_code == 400
    assert "Invalid quality" in resp.json()["detail"]


def test_song_url_forwards_quality_and_fresh_to_async_resolver(monkeypatch):
    captured = {}

    async def resolve(sid, quality, fresh=False, purpose="playback", context_generation=0):
        captured["args"] = (sid, quality, fresh, purpose, context_generation)
        return {"code": 200, "url": "http://m.test/a.flac"}

    monkeypatch.setattr(musicbox_app, "get_song_url_async", resolve)
    with TestClient(app) as client:
        resp = client.get("/api/v1/song/123/url", params={
            "quality": "lossless", "fresh": 1, "purpose": "download", "generation": 9,
        })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["data"]["code"] == 200
    assert resp.json()["data"]["url"].endswith(".flac")
    assert captured["args"] == (123, "lossless", True, "download", 9)


def test_song_info_artist_album_playlist_inproc(monkeypatch):
    captured = []

    monkeypatch.setattr(musicbox_app, "song_detail_raw", lambda sid: captured.append(("song", sid)) or {"id": sid})
    monkeypatch.setattr(musicbox_app, "artist_detail", lambda aid, limit=20: captured.append(("artist", aid, limit)) or {"ok": True, "data": {}})
    monkeypatch.setattr(musicbox_app, "album_detail", lambda alid: captured.append(("album", alid)) or {"ok": True, "data": {}})
    monkeypatch.setattr(musicbox_app, "playlist_detail_raw", lambda pid: captured.append(("playlist", pid)) or {"ok": True, "data": {}})
    with TestClient(app) as client:
        assert client.get("/api/v1/song/123/info").status_code == 200
        assert client.get("/api/v1/artist/456", params={"limit": 50}).status_code == 200
        assert client.get("/api/v1/album/789").status_code == 200
        assert client.get("/api/v1/playlist/1000").status_code == 200
    assert captured == [
        ("song", 123),
        ("artist", 456, 50),
        ("album", 789),
        ("playlist", 1000),
    ]


def test_song_info_in_process_raw_detail_and_404(monkeypatch):
    raw = {
        "name": "晴天", "ar": [{"name": "周杰伦"}],
        "al": {"name": "叶惠美", "picUrl": "http://img.test/yhm.jpg"},
        "dt": 269000, "sq": {"size": 25000000},
    }
    monkeypatch.setattr(musicbox_app, "song_detail_raw", lambda sid: raw if sid == 186016 else None)
    with TestClient(app) as client:
        resp = client.get("/api/v1/song/186016/info")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True and body["data"] == raw

        resp404 = client.get("/api/v1/song/42/info")
        assert resp404.status_code == 404


def test_search_web_fallback_row_carries_album_cover(monkeypatch):
    """web 兜底搜索行带 album.picUrl：主接口被风控时行内自带封面。"""
    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": {"songs": [{
                "id": 186016, "name": "晴天",
                "artists": [{"name": "周杰伦"}],
                "album": {"name": "叶惠美", "picUrl": "http://img.test/yhm.jpg"},
                "duration": 269000,
            }]}}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, **kwargs):
            return FakeResp()

    monkeypatch.setattr(netease_ext.httpx, "Client", FakeClient)
    rows = netease_ext.search_web_fallback("晴天")
    assert rows and rows[0]["song_id"] == 186016
    assert rows[0]["album_pic_url"] == "http://img.test/yhm.jpg"
    assert rows[0]["album_name"] == "叶惠美"


def test_song_lyric_ok_and_upstream_error(monkeypatch):
    import netease_ext

    class FakeApi:
        def song_lyric(self, sid):
            assert sid == 123
            return ["[00:01]晴天"]

        def song_tlyric(self, sid):
            return []

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: FakeApi())
    with TestClient(app) as client:
        resp = client.get("/api/v1/song/123/lyric")
    assert resp.status_code == 200
    rj = resp.json()
    assert rj["ok"] is True
    assert rj["data"]["lyric"] == "[00:01]晴天"
    assert rj["data"]["tlyric"] == ""

    def _boom(sid):
        raise RuntimeError("lyric upstream down")

    monkeypatch.setattr(musicbox_app, "song_lyric_pair", _boom)
    with TestClient(app) as client:
        resp = client.get("/api/v1/song/123/lyric")
    assert resp.status_code == 200
    rj = resp.json()
    assert rj["ok"] is False
    assert "lyric upstream down" in rj["error"]


def test_auth_status_and_login_check(monkeypatch):
    monkeypatch.setattr(musicbox_app, "fetch_auth_status", lambda: {"ok": True, "data": {"logged_in": True}})
    monkeypatch.setattr(
        musicbox_app,
        "auth_login_check",
        lambda unikey: {"ok": True, "data": {"code": 801, "unikey": unikey}},
    )
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/status")
        assert resp.status_code == 200
        assert resp.json()["data"]["logged_in"] is True

        resp2 = client.get("/api/v1/auth/login/check", params={"unikey": "   "})
        assert resp2.status_code == 400

        resp3 = client.get("/api/v1/auth/login/check", params={"unikey": "key-1"})
        assert resp3.status_code == 200
        assert resp3.json()["data"]["unikey"] == "key-1"


def test_auth_login_post_builds_qr_url(monkeypatch):
    monkeypatch.setattr(
        musicbox_app,
        "auth_login_qr",
        lambda: {"ok": True, "data": {"unikey": "key-abc", "qr_url": "https://music.163.com/login?codekey=key-abc"}},
    )
    with TestClient(app) as client:
        resp = client.post("/api/v1/auth/login")
    assert resp.status_code == 200
    payload = resp.json()["data"]
    assert payload["qr_url"] == "https://music.163.com/login?codekey=key-abc"


def test_upstream_failure_maps_to_502(monkeypatch):
    def boom():
        raise RuntimeError("netease exploded")

    monkeypatch.setattr(musicbox_app, "fetch_auth_status", boom)
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/status")
    assert resp.status_code == 502
    rj = resp.json()
    assert rj["error"] == "upstream_error"
    assert "netease exploded" in rj["stderr"]


def test_upstream_timeout_maps_to_504(monkeypatch):
    async def boom_url(*a, **kw):
        raise musicbox_app.playback.ResolutionTimeout("playback timed out")

    monkeypatch.setattr(musicbox_app, "get_song_url_async", boom_url)
    with TestClient(app) as client:
        resp = client.get("/api/v1/song/123/url", params={"quality": "exhigh"})
    assert resp.status_code == 504


# ------------------------------------------------------------- 推荐路由 ---

def _mock_netease_api(monkeypatch, detail_rows, url_rows, logged_in=False):
    """替换 netease_ext 的 NEMbox API 实例：详情 + 可播性 URL + 登录态。"""
    import netease_ext

    class MockApi:
        def songs_detail(self, ids):
            return detail_rows

        def songs_url(self, ids):
            return url_rows

        def get_account_info(self):
            return ({"account": {"id": 1}, "profile": {"nickname": "u"}} if logged_in
                    else {"account": None, "profile": None})

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())


def test_recommend_songs_hydrates_and_filters_playable(monkeypatch):
    raw_rows = [
        {"song_id": 301, "song_name": "日推免费", "artist": "S1", "duration": 210},
        {"song_id": 302, "song_name": "日推VIP", "artist": "S1", "duration": 210},
        {"song_id": "bad", "song_name": "畸形id", "artist": "S1", "duration": 210},
    ]

    detail_rows = [
        {"id": 301, "name": "日推免费", "ar": [{"name": "S1"}], "al": {"name": "专辑", "picUrl": "http://img/1.jpg"}, "dt": 210000},
        {"id": 302, "name": "日推VIP", "ar": [{"name": "S1"}], "al": {"name": "专辑", "picUrl": "http://img/2.jpg"}, "dt": 210000},
    ]
    url_rows = [
        {"id": 301, "url": "http://audio.126.net/301.mp3", "code": 200, "fee": 0, "freeTrialInfo": None},
        {"id": 302, "url": None, "code": 404, "fee": 1, "freeTrialInfo": None},
    ]
    monkeypatch.setattr(musicbox_app, "recommend_songs_raw", lambda limit=30: raw_rows)
    _mock_netease_api(monkeypatch, detail_rows, url_rows, logged_in=False)

    with TestClient(app) as client:
        resp = client.get("/api/v1/recommend/songs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["logged_in"] is False
    assert [row["song_id"] for row in data["data"]] == [301]
    assert data["data"][0]["name"] == "日推免费"
    assert data["data"][0]["duration_ms"] == 210000


def test_recommend_songs_not_logged_in_passthrough(monkeypatch):
    monkeypatch.setattr(musicbox_app, "recommend_songs_raw", lambda limit=30: [])
    _mock_netease_api(monkeypatch, [], [], logged_in=False)
    with TestClient(app) as client:
        resp = client.get("/api/v1/recommend/songs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "not_logged_in"


def test_toplist_without_index_returns_chart_list(monkeypatch):
    monkeypatch.setattr(
        musicbox_app,
        "toplist_raw",
        lambda index=-1, limit=60: {"ok": True, "data": [{"index": 0, "name": "飙升榜", "id": 19723756}]},
    )
    with TestClient(app) as client:
        resp = client.get("/api/v1/toplist")
    assert resp.status_code == 200
    assert resp.json()["data"][0]["name"] == "飙升榜"


def test_toplist_with_index_hydrates_playable_songs(monkeypatch):
    detail_rows = [{"id": 501, "name": "热歌", "ar": [{"name": "S"}], "al": {"name": "A", "picUrl": ""}, "dt": 200000}]
    url_rows = [{"id": 501, "url": "http://audio.126.net/501.mp3", "code": 200, "fee": 0, "freeTrialInfo": None}]
    monkeypatch.setattr(
        musicbox_app,
        "toplist_raw",
        lambda index=-1, limit=60: {"ok": True, "data": [{"song_id": 501, "song_name": "热歌", "artist": "S", "duration": 200}]},
    )
    _mock_netease_api(monkeypatch, detail_rows, url_rows, logged_in=False)

    with TestClient(app) as client:
        resp = client.get("/api/v1/toplist", params={"index": 3, "limit": 60})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["index"] == 3
    assert [row["song_id"] for row in data["data"]] == [501]


def test_auth_login_check_resets_resident_api_on_803(monkeypatch):
    """扫码成功（803）必须重置常驻 NetEase 实例，否则服务进程内仍是旧登录态。"""
    import netease_ext

    resets = []
    monkeypatch.setattr(netease_ext, "reset_api", lambda reason="": resets.append(reason))

    class FakeClient:
        def qr_check(self, unikey):
            return {"ok": True, "data": {"code": 803, "nickname": "u", "logged_in": True}}

    monkeypatch.setattr(netease_ext.netease_client, "get_client", lambda: FakeClient())
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/login/check", params={"unikey": "k1"})
        assert resp.status_code == 200
        assert resp.json()["data"]["code"] == 803
    assert resets == ["qr login success"]


def test_auth_login_check_keeps_api_before_803(monkeypatch):
    """待扫码（801）/已扫待确认（802）不重置实例。"""
    import netease_ext

    resets = []
    monkeypatch.setattr(netease_ext, "reset_api", lambda reason="": resets.append(reason))

    class FakeClient:
        def qr_check(self, unikey):
            return {"ok": True, "data": {"code": 801, "message": "waiting", "logged_in": False}}

    monkeypatch.setattr(netease_ext.netease_client, "get_client", lambda: FakeClient())
    with TestClient(app) as client:
        resp = client.get("/api/v1/auth/login/check", params={"unikey": "k1"})
        assert resp.json()["data"]["code"] == 801
    assert resets == []


def test_check_is_logged_in_ttl_cache_and_rebuild(monkeypatch):
    """成功态 300s 内复用缓存；过期先重建实例（重读磁盘 cookie）再探测。"""
    probes = {"n": 0}

    class FakeApi:
        def get_account_info(self):
            probes["n"] += 1
            return {"account": {"id": 1}}

    resets = []
    monkeypatch.setattr(netease_ext, "_api_instance", FakeApi())
    monkeypatch.setattr(netease_ext, "_login_state", None)
    monkeypatch.setattr(netease_ext, "reset_api", lambda reason="": resets.append(reason))
    try:
        assert netease_ext.check_is_logged_in() is True
        assert probes["n"] == 1
        # TTL 内：直接复用缓存结果
        assert netease_ext.check_is_logged_in() is True
        assert probes["n"] == 1
        # 缓存过期：重建实例后重新探测一次
        monkeypatch.setattr(netease_ext, "_login_state", (True, time.monotonic() - 301.0))
        assert netease_ext.check_is_logged_in() is True
        assert resets == ["login-state ttl expired"]
        assert probes["n"] == 2
    finally:
        netease_ext._login_state = None


def test_get_song_url_inproc_with_quality_and_cache(monkeypatch):
    """进程内取链：临时改写全局音质并恢复，结果短缓存，不同音质另查。"""
    import types

    calls = {"n": 0}

    class FakeApi:
        def songs_url(self, ids):
            calls["n"] += 1
            return [{"id": ids[0], "code": 200, "url": "http://cdn/1.flac", "fee": 8}]

    class FakeConfig:
        def __init__(self):
            self.config = {"music_quality": {"value": "standard"}}

        def get(self, name):
            return self.config[name]["value"]

    # CI 不安装 NEMbox：注入 fake 模块（get_song_url 内部延迟 from NEMbox.config import Config）
    fake_pkg = types.ModuleType("NEMbox")
    fake_mod = types.ModuleType("NEMbox.config")
    fake_mod.Config = FakeConfig
    fake_pkg.config = fake_mod
    monkeypatch.setitem(sys.modules, "NEMbox", fake_pkg)
    monkeypatch.setitem(sys.modules, "NEMbox.config", fake_mod)

    monkeypatch.setattr(netease_ext, "_api_instance", FakeApi())
    monkeypatch.setattr(netease_ext, "_url_cache", {})
    item = netease_ext.get_song_url(186016, "lossless")
    assert item["url"] == "http://cdn/1.flac"
    # 全局音质已恢复（CLI cmd_song_url 同款临时改写）
    assert FakeConfig().config["music_quality"]["value"] == "standard"
    # 缓存命中：不再发起请求
    assert netease_ext.get_song_url(186016, "lossless") == item
    assert calls["n"] == 1
    # 不同音质：另查
    netease_ext.get_song_url(186016, "exhigh")
    assert calls["n"] == 2
    netease_ext.get_song_url(186016, "lossless", fresh=True)
    assert calls["n"] == 3
    monkeypatch.setattr(netease_ext, "_URL_CACHE_MAX", 2)
    netease_ext.get_song_url(999, "lossless")
    assert len(netease_ext._url_cache) == 2


def test_song_url_async_success_preserves_response_without_sync_cli(monkeypatch):
    async def inproc(sid, quality, fresh=False, purpose="playback", context_generation=0):
        return {"url": "http://inproc/1.flac", "code": 200}

    monkeypatch.setattr(musicbox_app, "get_song_url_async", inproc)
    with TestClient(app) as client:
        r2 = client.get("/api/v1/song/42/url", params={"quality": "exhigh"})
        assert r2.json() == {"ok": True, "data": {"url": "http://inproc/1.flac", "code": 200}}


def test_user_playlists_endpoint_maps_rows(monkeypatch):
    class MockApi:
        def get_account_info(self):
            return {"code": 200, "account": {"id": 365}, "profile": {"nickname": "u"}}

        def user_playlist(self, uid, offset=0, limit=100):
            assert uid == 365
            return [
                {"id": 111, "name": "我喜欢的音乐", "coverImgUrl": "http://img/111.jpg", "trackCount": 9,
                 "createTime": 1700000000000, "updateTime": 1700000100000, "userId": 365, "specialType": 5},
                {"id": 222, "name": "别人的歌单", "coverImgUrl": "", "trackCount": 3,
                 "createTime": 1700000000000, "updateTime": 1700000000000, "userId": 999},
            ]

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    with TestClient(app) as client:
        resp = client.get("/api/v1/user/playlists")
        assert resp.status_code == 200
        data = resp.json()
    assert data["ok"] is True and data["logged_in"] is True
    assert data["account_uid"] == 365
    assert len(data["data"]) == 2
    row = data["data"][0]
    assert row["playlist_id"] == 111
    assert row["name"] == "我喜欢的音乐"
    assert row["cover_url"] == "http://img/111.jpg"
    assert row["track_count"] == 9
    assert row["user_id"] == 365
    assert row["created_at"] == 1700000000  # 毫秒 -> 秒
    assert data["data"][1]["user_id"] == 999  # 自建/收藏判定字段随行下发


def test_user_playlists_endpoint_not_logged_in(monkeypatch):
    class MockApi:
        def get_account_info(self):
            return {"code": 301, "account": None, "profile": None}

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    with TestClient(app) as client:
        resp = client.get("/api/v1/user/playlists")
        data = resp.json()
    assert data["ok"] is False
    assert data["error"] == "not_logged_in"
    assert data["logged_in"] is False


def test_user_playlist_tracks_endpoint(monkeypatch):
    class MockApi:
        def get_account_info(self):
            return {"code": 200, "account": {"id": 1}}

        def playlist_songlist(self, playlist_id):
            assert playlist_id == 111
            return [{"id": 101, "v": 3}, {"id": 102}, 103]

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    monkeypatch.setattr(musicbox_app, "batch_song_details", lambda ids: [
        {"song_id": i, "name": f"s{i}", "artist": "a", "album_name": "al", "album_pic_url": "", "duration_ms": 1000}
        for i in ids
    ])
    with TestClient(app) as client:
        resp = client.get("/api/v1/user/playlists/111/tracks")
        assert resp.status_code == 200
        data = resp.json()
    assert data["ok"] is True
    # trackIds 的 dict / 裸 int 两种形态都提取，且保持歌单内顺序
    assert [r["song_id"] for r in data["data"]] == [101, 102, 103]


def test_user_playlist_tracks_endpoint_not_logged_in(monkeypatch):
    class MockApi:
        def get_account_info(self):
            return {"code": 301, "account": None, "profile": None}

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    with TestClient(app) as client:
        resp = client.get("/api/v1/user/playlists/111/tracks")
        data = resp.json()
    assert data["ok"] is False and data["error"] == "not_logged_in"


def test_public_playlist_tracks_preserves_order_and_marks_vip(monkeypatch):
    class MockApi:
        def eapi_request(self, path, params):
            assert path == "/api/v6/playlist/detail"
            assert params["id"] == 111
            return {"playlist": {"id": 111, "trackCount": 2, "trackIds": [{"id": 101}, {"id": 102}]}}

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    monkeypatch.setattr(netease_ext, "batch_song_details", lambda ids: [
        {"song_id": i, "name": f"s{i}", "artist": "a", "album_name": "al",
         "album_pic_url": "", "duration_ms": 1000, "fee": 1 if i == 101 else 0}
        for i in ids
    ])
    monkeypatch.setattr(musicbox_app, "vip_entitled", lambda: False)
    with TestClient(app) as client:
        resp = client.get("/api/v1/public-playlists/111/tracks", params={"source": "netease", "kind": "playlist"})
        assert resp.status_code == 200
        data = resp.json()
    assert data["ok"] is True and data["total"] == 2 and data["truncated"] is False
    assert [row["song_id"] for row in data["data"]] == [101, 102]
    assert data["data"][0]["needs_vip"] is True
    assert "needs_vip" not in data["data"][1]


def test_public_playlist_categories_contract_groups_tags_and_specializes_chart(monkeypatch):
    grouped = {"语种": [{"id": "华语", "name": "华语"}]}
    high_tags = [{"id": "精品", "name": "精品"}]

    def fake_categories(kind):
        assert kind == "playlist"
        return {
            "ok": True,
            "categories": [{"id": "", "name": "全部"}, {"id": "语种", "name": "语种"}],
            "tags": grouped,
            "highquality_tags": high_tags,
        }

    monkeypatch.setattr(musicbox_app, "public_playlist_categories", fake_categories)
    with TestClient(app) as client:
        playlist = client.get("/api/v1/public-playlists/categories", params={"source": "netease", "kind": "playlist"})
        chart = client.get("/api/v1/public-playlists/categories", params={"source": "netease", "kind": "chart"})

    assert playlist.status_code == 200
    payload = playlist.json()
    assert payload["categories"][0] == {"id": "", "name": "全部"}
    assert payload["categories"][1]["list"] == grouped["语种"]
    assert payload["highquality_tags"] == high_tags
    assert chart.status_code == 200
    assert chart.json()["categories"] == [{"id": "", "name": "全部"}]


def test_missing_public_tracks_is_error_not_empty(monkeypatch):
    class MockApi:
        def eapi_request(self, path, params):
            return {"code": 500}
    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    with pytest.raises(RuntimeError, match="unavailable"):
        netease_ext.public_playlist_tracks(111)


def test_highquality_tags_accept_direct_and_nested_samples(monkeypatch):
    monkeypatch.setattr(netease_ext, "_public_get", lambda *args: {
        "code": 200, "tags": [{"name": "华语"}, {"playlistTag": {"name": "摇滚"}}],
    })
    result = netease_ext.public_playlist_categories("highquality")
    assert result["tags"] == [{"id": "华语", "name": "华语"}, {"id": "摇滚", "name": "摇滚"}]


def test_public_business_failure_is_not_successful_empty_list(monkeypatch):
    class MockApi:
        def request(self, method, path, params):
            return {"code": 503}
    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    with pytest.raises(RuntimeError, match="503"):
        netease_ext.public_playlists("top")


def test_public_playlist_search_parses_playlists_and_advances_native_offset(monkeypatch):
    """歌单搜索走 /weapi/search/get type=1000：offset 按上游原始窗口推进，has_more 对账 total。"""
    captured: dict = {}

    def fake_post(path, params):
        captured.update(path=path, params=dict(params))
        remaining = max(0, 57 - int(params["offset"]))
        rows = [
            {"id": 1000 + i, "name": f"歌单{i}", "coverImgUrl": f"http://img/{i}.jpg",
             "trackCount": 10 + i, "creator": {"nickname": "作者"}}
            for i in range(min(int(params["limit"]), remaining))
        ]
        return {"code": 200, "result": {"playlists": rows, "playlistCount": 57}}

    monkeypatch.setattr(netease_ext, "_public_post", fake_post)

    page1 = netease_ext.public_playlist_search(" 华语 ", page=1, limit=30)
    assert captured["path"] == "/weapi/search/get"
    assert captured["params"] == {"s": "华语", "type": 1000, "offset": 0, "total": "true", "limit": 30}
    assert page1["total"] == 57 and len(page1["items"]) == 30 and page1["has_more"] is True
    assert page1["items"][0]["id"] == "1000" and page1["items"][0]["author"] == "作者"

    # 末页：offset 30 + 27 = 57 对齐 total，不再有下一页；offset 按页码推进
    page2 = netease_ext.public_playlist_search("华语", page=2, limit=30)
    assert captured["params"]["offset"] == 30
    assert len(page2["items"]) == 27 and page2["has_more"] is False

    # 空关键词直接空结果，不打上游
    captured.clear()
    empty = netease_ext.public_playlist_search("   ")
    assert empty == {"ok": True, "items": [], "total": 0, "has_more": False}
    assert captured == {}


@pytest.mark.parametrize("total", [0, 99999])
def test_public_playlist_search_empty_raw_page_stops_despite_stale_total(monkeypatch, total):
    monkeypatch.setattr(netease_ext, "_public_post", lambda *a, **k: {
        "code": 200, "result": {"playlists": [], "playlistCount": total, "more": True},
    })
    result = netease_ext.public_playlist_search("华语", page=2, limit=30)
    assert result["items"] == [] and result["raw_count"] == 0
    assert result["total"] == total and result["has_more"] is False


def test_public_playlist_search_filtered_raw_window_uses_native_page_boundary(monkeypatch):
    offsets = []

    def fake_post(path, params):
        offsets.append(params["offset"])
        # 第 1 页部分行无身份；第 2 页全被过滤，但原始窗口已经到达总数。
        rows = [{"id": 1, "name": "有效"}, {}, None] if params["offset"] == 0 else [{}, None]
        return {"code": 200, "result": {"playlists": rows, "playlistCount": 5}}

    monkeypatch.setattr(netease_ext, "_public_post", fake_post)
    first = netease_ext.public_playlist_search("华语", page=1, limit=3)
    last = netease_ext.public_playlist_search("华语", page=2, limit=3)
    assert offsets == [0, 3]
    assert len(first["items"]) == 1 and first["raw_count"] == 3 and first["has_more"] is True
    assert last["items"] == [] and last["raw_count"] == 2 and last["has_more"] is False

    # 非空但全部无效的中间窗口不误判成真空页，下页仍可请求。
    monkeypatch.setattr(netease_ext, "_public_post", lambda *a, **k: {
        "code": 200, "result": {"playlists": [{}, None], "playlistCount": 100},
    })
    filtered = netease_ext.public_playlist_search("华语", page=2, limit=3)
    assert filtered["items"] == [] and filtered["raw_count"] == 2 and filtered["has_more"] is True


def test_public_playlist_search_via_channel_requires_keyword(monkeypatch):
    monkeypatch.setattr(netease_ext, "_public_post", lambda *a, **k: pytest.fail("不应发起上游请求"))
    assert netease_ext.public_playlists("search", keyword="")["items"] == []


def test_highquality_empty_rows_report_no_more(monkeypatch):
    """more=true 但整页为空时没有可推进的游标：必须判停，否则前端会从头重复第一页。"""
    monkeypatch.setattr(netease_ext, "_public_get", lambda *a, **k: {
        "code": 200, "playlists": [], "more": True, "total": 99,
    })
    result = netease_ext.public_playlists("highquality")
    assert result["items"] == [] and result["has_more"] is False and result["next_cursor"] == ""


def test_public_playlists_endpoint_search_and_deep_pages(monkeypatch):
    captured: dict = {}

    def fake_public_playlists(channel, category="", sort="hot", page=1, limit=30, cursor="", keyword=""):
        captured.update(channel=channel, keyword=keyword, page=page)
        return {"ok": True, "items": [], "total": 0, "has_more": False}

    monkeypatch.setattr(musicbox_app, "public_playlists", fake_public_playlists)
    with TestClient(app) as client:
        r = client.get("/api/v1/public-playlists", params={
            "source": "netease", "channel": "search", "keyword": "华语", "page": 101,
        })
        assert r.status_code == 200
        assert captured == {"channel": "search", "keyword": "华语", "page": 101}
        missing = client.get("/api/v1/public-playlists", params={"source": "netease", "channel": "search"})
        assert missing.status_code == 400
        bad = client.get("/api/v1/public-playlists", params={"source": "netease", "channel": "nope"})
        assert bad.status_code == 400


def test_search_offset_routes_to_web_fallback(monkeypatch):
    """cloudsearch 空时深分页安全降级到 web 搜索接口：offset/limit 透传，可播过滤沿用。"""
    captured: dict = {}

    def fake_fallback(keyword, stype="song", limit=20, offset=0):
        captured.update(keyword=keyword, stype=stype, limit=limit, offset=offset)
        return [
            {"song_id": 33894312, "song_name": "晴天", "artist": "周杰伦", "duration": 269},
            {"song_id": 186016, "song_name": "晴天 (Live)", "artist": "周杰伦", "duration": 301},
        ]

    monkeypatch.setattr(musicbox_app, "search_catalog", lambda *a, **kw: [])
    monkeypatch.setattr(musicbox_app, "search_web_fallback", fake_fallback)
    monkeypatch.setattr(musicbox_app, "filter_playable_song_ids", lambda ids: set(ids))

    with TestClient(app) as client:
        r = client.get("/api/v1/search", params={"keyword": "晴天", "offset": 50, "limit": 30})
    assert r.status_code == 200
    assert captured == {"keyword": "晴天", "stype": "song", "limit": 30, "offset": 50}
    data = r.json()["data"]
    assert [it["song_id"] for it in data] == [33894312, 186016]


def test_search_offset_zero_uses_cloudsearch_inproc(monkeypatch):
    """offset=0 优先走 EAPI cloudsearch；fallback 不应被触发。"""
    calls = []
    fallback_calls = []
    rows = [{"song_id": 33894312, "name": "晴天"}]

    def catalog(keyword, stype="song", limit=20, offset=0):
        calls.append((keyword, stype, limit, offset))
        return rows

    def fallback(*args, **kwargs):
        fallback_calls.append((args, kwargs))
        return []

    monkeypatch.setattr(musicbox_app, "search_catalog", catalog)
    monkeypatch.setattr(musicbox_app, "search_web_fallback", fallback)
    monkeypatch.setattr(musicbox_app, "filter_playable_song_ids", lambda ids: set(ids))
    with TestClient(app) as client:
        r = client.get("/api/v1/search", params={"keyword": "周杰伦", "offset": 0})
    assert r.status_code == 200
    assert r.json()["data"] == rows
    assert calls == [("周杰伦", "song", 20, 0)]
    assert fallback_calls == []


def test_map_song_detail_maps_fee():
    row = netease_ext._map_song_detail({"id": 1, "name": "x", "dt": 1000, "fee": 1})
    assert row["fee"] == 1
    assert netease_ext._map_song_detail({"id": 2, "name": "y"})["fee"] == 0
    assert netease_ext._map_song_detail({"id": 3, "name": "z", "fee": "1"})["fee"] == 1


def test_user_playlist_tracks_marks_needs_vip(monkeypatch):
    """issue #57：无 VIP 权益账号的歌单曲目 fee==1（VIP 曲）带 needs_vip 标记；
    fee 0/8 不标记——点播即知只有试听。"""
    class MockApi:
        def get_account_info(self):
            return {"code": 200, "account": {"id": 1}, "profile": {"vipType": 0}}

        def playlist_songlist(self, playlist_id):
            return [101, 102, 103]

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    monkeypatch.setattr(netease_ext, "_vip_state", None)
    monkeypatch.setattr(musicbox_app, "batch_song_details", lambda ids: [
        {"song_id": 101, "name": "free", "fee": 0},
        {"song_id": 102, "name": "vip song", "fee": 1},
        {"song_id": 103, "name": "free-lq", "fee": 8},
    ])
    with TestClient(app) as client:
        resp = client.get("/api/v1/user/playlists/111/tracks")
        data = resp.json()
    assert data["ok"] is True
    by_id = {r["song_id"]: r for r in data["data"]}
    assert "needs_vip" not in by_id[101]
    assert by_id[102].get("needs_vip") is True
    assert "needs_vip" not in by_id[103]


def test_user_playlist_tracks_no_mark_for_vip_account(monkeypatch):
    """VIP 权益账号可完整播放，不打标。"""
    class MockApi:
        def get_account_info(self):
            return {"code": 200, "account": {"id": 1}, "profile": {"vipType": 11}}

        def playlist_songlist(self, playlist_id):
            return [102]

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    monkeypatch.setattr(netease_ext, "_vip_state", None)
    monkeypatch.setattr(musicbox_app, "batch_song_details", lambda ids: [
        {"song_id": 102, "name": "vip song", "fee": 1},
    ])
    with TestClient(app) as client:
        resp = client.get("/api/v1/user/playlists/111/tracks")
        data = resp.json()
    assert data["ok"] is True
    assert "needs_vip" not in data["data"][0]


def test_playable_recommendation_rows_marks_vip(monkeypatch):
    """issue #57：推荐/榜单行同样在可播过滤后带 needs_vip（无 VIP 权益账号）。"""
    class MockApi:
        def get_account_info(self):
            return {"code": 200, "account": {"id": 1}, "profile": {"vipType": 0}}

        def songs_url(self, ids):
            return [
                {"id": i, "url": f"http://audio.126.net/{i}.mp3", "code": 200, "fee": 0, "freeTrialInfo": None}
                for i in ids
            ]

    monkeypatch.setattr(netease_ext, "_get_api_locked", lambda: MockApi())
    monkeypatch.setattr(netease_ext, "_vip_state", None)
    monkeypatch.setattr(musicbox_app, "batch_song_details", lambda ids: [
        {"song_id": 101, "name": "free", "fee": 0},
        {"song_id": 102, "name": "vip", "fee": 1},
    ])
    rows = musicbox_app._playable_recommendation_rows([{"song_id": 101}, {"song_id": 102}], 10)
    by_id = {r["song_id"]: r for r in rows}
    assert by_id[102].get("needs_vip") is True
    assert "needs_vip" not in by_id[101]
