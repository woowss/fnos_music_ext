"""LLM 推荐超时配置：真实推理模型单次调用实测约 53-90s，旧实现把
FNMUSIC_LLM_TIMEOUT_S 硬夹在 60s，导致每日推荐 LLM 层永远超时并降级为本地随机。
本测试锁定：env 可调到 120s，上限 180s，下限仍 5s。"""
from __future__ import annotations

from proxy import recommend


def test_llm_timeout_env_above_legacy_cap(monkeypatch):
    monkeypatch.setenv("FNMUSIC_LLM_TIMEOUT_S", "120")
    assert recommend.llm_timeout_s() == 120.0
    monkeypatch.setenv("FNMUSIC_LLM_TIMEOUT_S", "999")
    assert recommend.llm_timeout_s() == 180.0
    monkeypatch.setenv("FNMUSIC_LLM_TIMEOUT_S", "1")
    assert recommend.llm_timeout_s() == 5.0
    monkeypatch.delenv("FNMUSIC_LLM_TIMEOUT_S")
    assert recommend.llm_timeout_s() == 20.0
