"""FNMUSIC_LLM_EXTRA_HEADERS：服务商专属请求头（OpenCode Go 的 session、网关 UA）。

背景：OpenCode Go 的 /inference/go 网关要求 x-opencode-session 才能路由，
前置 Cloudflare 的网关还会按客户端签名拦截默认 UA。扩展需要一个通用出口，
否则只能给每家服务商硬编码。HTTP 一律走 httpx.MockTransport，不出网。
"""
import json

import httpx
import pytest

from proxy import recommend as dailyrec


@pytest.fixture
def anyio_backend():
    return "asyncio"


_CANDIDATES = [
    {"title": f"附加头歌{i}", "artist": f"附加头歌手{i}", "dimension": "genre", "reason": "t"}
    for i in range(3)
]


def _chat_body(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


def _configure_llm(monkeypatch) -> None:
    monkeypatch.setenv("FNMUSIC_LLM_BASE_URL", "http://llm.invalid/v1")
    monkeypatch.setenv("FNMUSIC_LLM_API_KEY", "test-key")
    monkeypatch.setenv("FNMUSIC_LLM_MODEL", "test-model")


def test_extra_headers_empty_by_default(monkeypatch):
    monkeypatch.delenv("FNMUSIC_LLM_EXTRA_HEADERS", raising=False)
    assert dailyrec.llm_extra_headers() == {}


def test_extra_headers_parse_scalar_values_only(monkeypatch):
    monkeypatch.setenv(
        "FNMUSIC_LLM_EXTRA_HEADERS",
        json.dumps({"x-opencode-session": "ses_1", "n": 2, "bad": {"a": 1}, "none": None}),
    )
    assert dailyrec.llm_extra_headers() == {"x-opencode-session": "ses_1", "n": "2"}


def test_extra_headers_invalid_json_ignored(monkeypatch):
    monkeypatch.setenv("FNMUSIC_LLM_EXTRA_HEADERS", "not-json")
    assert dailyrec.llm_extra_headers() == {}


@pytest.mark.anyio
async def test_extra_headers_sent_without_dropping_auth(monkeypatch):
    _configure_llm(monkeypatch)
    monkeypatch.setenv(
        "FNMUSIC_LLM_EXTRA_HEADERS",
        json.dumps({"x-opencode-session": "ses_test_1", "User-Agent": "UA/1"}),
    )
    captured: list = []
    content = json.dumps(_CANDIDATES, ensure_ascii=False)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_chat_body(content))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        recs = await dailyrec.call_llm(client, "推荐一些歌")

    assert len(recs) == len(_CANDIDATES)
    req = captured[0]
    assert req.headers["x-opencode-session"] == "ses_test_1"
    assert req.headers["user-agent"] == "UA/1"
    # 附加头不得冲掉鉴权与内容类型
    assert req.headers["authorization"] == "Bearer test-key"
    assert req.headers["content-type"].startswith("application/json")
