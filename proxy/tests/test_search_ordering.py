"""搜索在线结果排序回归（U8：搜索"晴天"首条是翻唱而非原唱）。

规则（稳定排序，只作用于在线段，本地官方结果位置不动）：
  标题与关键词完全相等（忽略大小写/空白）→ 标题以关键词开头
  → 标题包含关键词 → 其它；同级保持上游原序，不臆造热度字段。
"""
import importlib
import json
from urllib.parse import quote

import httpx
import pytest
from starlette.requests import Request

p = importlib.import_module("proxy.app")


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    p._SEARCH_CACHE.clear()
    p.reset_source_search_gates()
    for key, value in {
        "search_debounce_s": 0.0,
        "search_timeout": 2.0,
        "search_deep_page": False,
        "search_empty_retry": 0,
        "netease_enabled": True,
        "musicdl_enabled": False,
        "lx_enabled": False,
        "netease_search_limit": 50,
        "cache_dir": str(tmp_path / "cache"),
        "library_dir": str(tmp_path / "library"),
        "fav_dir": str(tmp_path / "fav"),
        "music_db": str(tmp_path / "missing.db"),
    }.items():
        monkeypatch.setitem(p.CONF, key, value)
    for attr in ("upstream_client", "musicdl_client", "musicbox_client", "lx_client"):
        monkeypatch.setattr(p.app.state, attr, None, raising=False)
    yield
    p.reset_source_search_gates()
    p._SEARCH_CACHE.clear()


def request(query="", token="a"):
    return Request({"type": "http", "method": "GET", "path": "/music/api/v1/search/track",
                    "query_string": quote(query, safe="=&").encode(),
                    "headers": [(b"authorization", token.encode())],
                    "app": p.app, "scheme": "http", "server": ("test", 80)})


def _online(sid, title, artist="歌手", duration=100):
    return {"id": sid, "source": "netease", "title": title, "artist": artist,
            "album": "", "duration_s": duration, "ext": "mp3",
            "cover_url": "", "lyric": ""}


def test_online_sort_tiers_and_stable_order():
    """档位：完全相等 → 前缀 → 包含 → 其它；同级严格保持输入顺序。"""
    items = [
        _online("netease:other", "雨天", "A"),
        _online("netease:contains", "我的晴天", "B"),
        _online("netease:startswith2", "晴天娃娃", "C"),
        _online("netease:exact2", "晴天", "D"),
        _online("netease:startswith1", "晴天 (Live)", "E"),
        _online("netease:exact1", "晴天", "F"),
    ]
    ordered = p.sort_online_items_for_keyword(items, "晴天")
    assert [item["id"] for item in ordered] == [
        "netease:exact2", "netease:exact1",          # 0 档：完全相等，保持原序
        "netease:startswith2", "netease:startswith1",  # 1 档：前缀，保持原序
        "netease:contains",                           # 2 档：包含
        "netease:other",                              # 3 档：其它
    ]


def test_online_sort_ignores_case_and_whitespace():
    """大小写/半角全角空格不参与比较；空关键词时保持原序。"""
    items = [
        _online("netease:contains", "My Love"),
        _online("netease:exact", "love"),
        _online("netease:startswith", "LOVE STORY"),
        _online("netease:fullwidth", "晴　天"),
    ]
    ordered = p.sort_online_items_for_keyword(items, " Love ")
    assert [item["id"] for item in ordered] == [
        "netease:exact", "netease:startswith", "netease:contains", "netease:fullwidth"]
    assert p.sort_online_items_for_keyword(items, "") == items
    assert [item["id"] for item in p.sort_online_items_for_keyword(items, "晴 天")] == [
        "netease:fullwidth", "netease:contains", "netease:exact", "netease:startswith"]


@pytest.mark.anyio
async def test_search_track_online_ordering_keeps_local_first_and_dedup(monkeypatch):
    """端到端：乱序 mock 在线结果被排序；本地首条位置不变；来源前缀与去重不变。"""
    local_payload = {
        "code": 0,
        "msg": "OK",
        "data": {"list": [{"guid": "local:1", "title": "夜曲", "artist": "周杰伦",
                           "album": "十一月的萧邦", "duration": 226000}], "total": 1},
    }
    raw_online = [
        _online("netease:rain", "雨天", "A"),
        _online("netease:contains", "我的晴天", "B"),
        _online("netease:cover", "晴天 (原唱 周杰伦)", "RyaVocal", duration=270),
        _online("netease:jay", "晴天", "周杰伦", duration=269),
        _online("netease:startswith", "晴天娃娃", "C"),
    ]

    async def fake_musicbox(client, keyword, limit):
        return raw_online

    monkeypatch.setattr(p, "fetch_musicbox_search", fake_musicbox)
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=local_payload)),
        base_url="http://test")
    musicbox = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(404, json={"ok": False})),
        base_url="http://test")
    p.app.state.upstream_client = client
    p.app.state.musicbox_client = musicbox
    try:
        response = await p.search_track(request("q=晴天&page=1&size=20"))
        payload = json.loads(response.body)["data"]
        items = payload["list"]
        # 本地官方结果仍在第一项、标题不变
        assert items[0]["guid"] == "local:1"
        assert items[0]["title"] == "夜曲"
        # 在线段按相关度排序：原唱"晴天"（完全相等）先于 RyaVocal 翻唱（前缀）
        online_titles = [item["title"] for item in items[1:]]
        assert online_titles == [
            "[music box] 晴天",
            "[music box] 晴天 (原唱 周杰伦)",
            "[music box] 晴天娃娃",
            "[music box] 我的晴天",
            "[music box] 雨天",
        ]
        # 来源标识前缀保留；总数 = 本地 1 + 在线 5
        assert payload["total"] == 6
        assert len(items) == 6
    finally:
        await musicbox.aclose()
        await client.aclose()
        p.app.state.upstream_client = None
        p.app.state.musicbox_client = None


@pytest.mark.anyio
async def test_online_sort_preserves_dedup_representative(monkeypatch):
    """排序不破坏去重代表与 _alternatives（代表=首个出现的条目）。"""
    raw = [
        _online("netease:first", "晴天", "周杰伦", duration=269),
        _online("netease:other", "雨天", "A"),
        # 与 first 同一录音（标题/歌手/时长一致）→ 归入 _alternatives，不出现在池里
        _online("netease:alt", "晴天", "周杰伦", duration=269),
    ]
    # 模拟 _aggregate_search 的写回顺序：去重 → 排序
    ordered = p.sort_online_items_for_keyword(
        p.deduplicate_online_items(raw), "晴天")
    assert [item["id"] for item in ordered] == ["netease:first", "netease:other"]
    assert [item["id"] for item in ordered[0]["_alternatives"]] == ["netease:alt"]
