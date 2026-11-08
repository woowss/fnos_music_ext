"""首查延迟回归（真实用户 16.0s 首查：冷缓存搜索新关键词"晴天"）。

旧实现耗时构成（proxy.log `ms=16034` 的复现口径）：
  search_debounce_s(1.0) + musicbox 首屏(≈5s) + _wait_deep_pages 预算 10s ≈ 16s
其中 10s = min(10, search_timeout)，与首屏无关却阻塞响应（page=1 也要等深页填满）。

修复口径：
- page=1 只等第一页/首屏在线结果就返回，深页补齐转后台任务（沿用
  entry["extend_task"]/缓存结构，scope 隔离与去重不变）；
- 深页补齐仍占用响应的预算收敛为 search_deep_wait_s（默认 3s，仅翻页页）；
- 首屏另有 search_first_screen_s 独立上限（默认 8s，含 debounce）：到点返回
  200 + 已有结果，聚合继续在后台补全并落缓存（不再取消本轮）。
"""
import asyncio
import importlib
import json
import os
import time
from urllib.parse import quote

import httpx
import pytest
from starlette.requests import Request

p = importlib.import_module("proxy.app")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def request(query="", token="a"):
    return Request({"type": "http", "method": "GET", "path": "/music/api/v1/search/track",
                    "query_string": quote(query, safe="=&").encode(),
                    "headers": [(b"authorization", token.encode())],
                    "app": p.app, "scheme": "http", "server": ("test", 80)})


def online(sid, title, artist="A"):
    return {"id": sid, "source": "netease", "title": title, "artist": artist,
            "album": "", "duration_s": 200, "ext": "mp3",
            "cover_url": "", "lyric": ""}


def local_payload(titles=()):
    return {
        "code": 0,
        "msg": "OK",
        "data": {"list": [{"guid": f"local:{i}", "title": title, "artist": "本地歌手"}
                          for i, title in enumerate(titles)],
                 "total": len(titles)},
    }


def _upstream_transport(payload=None):
    payload = payload if payload is not None else {"code": 0, "data": {"list": [], "total": 0}}
    return httpx.MockTransport(lambda req: httpx.Response(200, json=payload))


@pytest.fixture
async def search_env(tmp_path, monkeypatch):
    """隔离搜索状态 + 默认配置（真实口径：debounce 1s / timeout 15s / 深页开）。"""
    p._SEARCH_CACHE.clear()
    p.reset_source_search_gates()
    for key, value in {
        "search_debounce_s": 1.0,
        "search_timeout": 15.0,
        "search_deep_page": True,
        "search_deep_max_pages": 10,
        "search_deep_wait_s": 3.0,
        "search_first_screen_s": 8.0,
        "netease_search_limit": 30,
        "online_limit": 30,
        "lx_search_limit": 20,
        "netease_enabled": True,
        "musicdl_enabled": False,
        "lx_enabled": False,
        "search_empty_retry": 0,
        "search_cache_ttl": 604800.0,
        "cache_dir": str(tmp_path / "cache"),
        "library_dir": str(tmp_path / "library"),
        "fav_dir": str(tmp_path / "fav"),
        "music_db": str(tmp_path / "missing.db"),
    }.items():
        monkeypatch.setitem(p.CONF, key, value)
    for attr in ("upstream_client", "musicdl_client", "musicbox_client", "lx_client"):
        monkeypatch.setattr(p.app.state, attr, None, raising=False)
    upstream = httpx.AsyncClient(transport=_upstream_transport(), base_url="http://test")
    musicbox = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(404, json={"ok": False})),
        base_url="http://test")
    p.app.state.upstream_client = upstream
    p.app.state.musicbox_client = musicbox
    try:
        yield
    finally:
        tasks = []
        for entry in list(p._SEARCH_CACHE.values()):
            for key in ("task", "extend_task"):
                task = entry.get(key)
                if task and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        p._SEARCH_CACHE.clear()
        p.reset_source_search_gates()
        await musicbox.aclose()
        await upstream.aclose()


def test_search_defaults_favor_first_screen():
    """默认深搜不再把首屏拖到十几秒：max_pages 3、深页阻塞预算 ≤3s、首屏 ≤8s。"""
    if any(os.environ.get(k) for k in (
            "FNMUSIC_SEARCH_DEEP_MAX_PAGES", "FNMUSIC_SEARCH_DEEP_WAIT_S",
            "FNMUSIC_SEARCH_FIRST_SCREEN_S")):
        pytest.skip("环境变量覆盖了搜索默认值")
    assert p.CONF["search_deep_max_pages"] == 3
    assert p.CONF["search_deep_wait_s"] <= 3.0
    assert p.CONF["search_first_screen_s"] <= 8.0


@pytest.mark.anyio
async def test_first_search_returns_first_screen_before_deep_pages(search_env, monkeypatch):
    """慢 musicbox（首屏 4s/页）：首屏响应受控（<7s）且返回部分结果，深页转后台补齐。"""
    first_page_sleep = 4.0
    deep_calls = {"n": 0}

    async def fake_musicbox_search(client, keyword, limit):
        await asyncio.sleep(first_page_sleep)
        return [online(f"netease:first{i}", f"晴天{i}") for i in range(30)]

    async def fake_musicbox_request(client, keyword, limit, offset=0):
        deep_calls["n"] += 1
        await asyncio.sleep(0.05)
        base = 1000 + deep_calls["n"] * 10
        return [online(f"netease:deep{base + i}", f"晴天深{deep_calls['n']}-{i}")
                for i in range(10)]

    monkeypatch.setattr(p, "fetch_musicbox_search", fake_musicbox_search)
    monkeypatch.setattr(p, "_musicbox_search_request", fake_musicbox_request)

    started = time.monotonic()
    response = await p.search_track(request("q=晴天&page=1&size=50"))
    elapsed = time.monotonic() - started
    body = json.loads(response.body)
    items = body["data"]["list"]

    # 旧实现 = debounce 1s + 首屏 4s + 深页预算 10s ≈ 15s；新实现只等首屏
    assert response.status_code == 200
    assert elapsed < 7.0, f"首屏被深页补齐拖慢: {elapsed:.2f}s"
    assert len(items) == 30, "首屏应返回第一页部分结果（不 500、不空）"
    assert body["data"]["total"] == 30

    entry = next(iter(p._SEARCH_CACHE.values()))
    fill = entry.get("extend_task")
    assert fill is not None and not fill.done(), "深页补齐应已转后台任务"
    # 后台补齐能力保留：页1需要 50 条，后台继续取到 30+10+10
    await asyncio.wait_for(asyncio.shield(fill), 2.0)
    assert len(entry["items"]) >= 50

    # 同词第二次请求：命中缓存 + 后台已补齐，立即返回完整首屏
    started = time.monotonic()
    response = await p.search_track(request("q=晴天&page=1&size=50"))
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 1.0, f"缓存命中仍慢: {elapsed:.2f}s"
    assert len(json.loads(response.body)["data"]["list"]) == 50


@pytest.mark.anyio
async def test_page_wait_budget_bounded_and_background_continues(search_env, monkeypatch):
    """翻页等深页有预算上限（search_deep_wait_s）：到点交现有切片，后台继续补。"""
    monkeypatch.setitem(p.CONF, "search_deep_wait_s", 0.25)
    deep_calls = {"n": 0}

    async def fake_musicbox_search(client, keyword, limit):
        return [online(f"netease:first{i}", f"晴天{i}") for i in range(30)]

    async def slow_musicbox_request(client, keyword, limit, offset=0):
        deep_calls["n"] += 1
        await asyncio.sleep(0.5)
        base = 2000 + deep_calls["n"] * 10
        return [online(f"netease:deep{base + i}", f"晴天深{deep_calls['n']}-{i}")
                for i in range(5)]

    monkeypatch.setattr(p, "fetch_musicbox_search", fake_musicbox_search)
    monkeypatch.setattr(p, "_musicbox_search_request", slow_musicbox_request)

    first = await p.search_track(request("q=晴天&page=1&size=50"))
    assert first.status_code == 200
    entry = next(iter(p._SEARCH_CACHE.values()))
    assert entry.get("extend_task") is not None  # 首屏已排后台补齐

    started = time.monotonic()
    response = await p.search_track(request("q=晴天&page=2&size=50"))
    elapsed = time.monotonic() - started
    assert response.status_code == 200
    assert elapsed < 1.2, f"翻页阻塞超过深页预算: {elapsed:.2f}s"
    # 短页自愈：total 按当时已有的池给出（后台仍在增长），客户端下次翻页会再触发补齐
    total = json.loads(response.body)["data"]["total"]
    assert 30 <= total <= len(entry["items"])


@pytest.mark.anyio
async def test_first_screen_timeout_returns_200_with_existing_results(search_env, monkeypatch):
    """音源挂住时首屏预算到点即返 200 + 已有结果；聚合不取消，继续后台补全落缓存。"""
    monkeypatch.setitem(p.CONF, "search_first_screen_s", 0.25)
    release = asyncio.Event()

    async def hanging_musicbox_search(client, keyword, limit):
        await release.wait()
        return [online("netease:late", "晴天")]

    monkeypatch.setattr(p, "fetch_musicbox_search", hanging_musicbox_search)
    upstream = httpx.AsyncClient(transport=_upstream_transport(local_payload(["本地晴天"])),
                                 base_url="http://test")
    p.app.state.upstream_client = upstream
    try:
        started = time.monotonic()
        response = await p.search_track(request("q=晴天&page=1&size=50"))
        elapsed = time.monotonic() - started
        items = json.loads(response.body)["data"]["list"]
        assert response.status_code == 200
        assert elapsed < 1.0, f"首屏预算未生效: {elapsed:.2f}s"
        assert items[0]["guid"] == "local:0"  # 已有官方本地结果照常返回
        entry = next(iter(p._SEARCH_CACHE.values()))
        assert entry.get("first_screen_timeout") is True
        assert not entry.get("abandoned"), "首屏预算用尽不应取消聚合（后台继续补全）"
        assert entry.get("task") is not None and not entry["task"].done()
    finally:
        release.set()
        await upstream.aclose()
        p.app.state.upstream_client = None
