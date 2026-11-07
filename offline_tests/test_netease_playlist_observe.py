"""本地自检：P3 只读观察脚本的安全属性与报告形状（不联网、不连 NAS）。"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "tools" / "netease_playlist_observe.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("netease_playlist_observe_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OBSERVE = _load_module()


def test_stat_facts_reads_metadata_only(tmp_path):
    target = tmp_path / "cookie"
    target.write_bytes(b"SECRET_COOKIE_CONTENT")
    before = OBSERVE._stat_facts(target)
    assert before == {
        "provided": True, "exists": True,
        "size": len(b"SECRET_COOKIE_CONTENT"),
        "mtime_ns": before["mtime_ns"],
        "mode": before["mode"],
    }
    # 报告结构里不含文件内容，也不含路径名。
    assert "SECRET_COOKIE_CONTENT" not in json.dumps(before)


def test_stat_changed_detects_size_mtime_or_mode(tmp_path):
    target = tmp_path / "cookie"
    target.write_bytes(b"a")
    first = OBSERVE._stat_facts(target)
    assert OBSERVE._stat_changed(first, dict(first)) is False
    second = dict(first)
    second["size"] = first["size"] + 1
    assert OBSERVE._stat_changed(first, second) is True
    third = dict(first)
    third["mode"] = first["mode"] ^ 0o600
    assert OBSERVE._stat_changed(first, third) is True
    assert OBSERVE._stat_changed({"provided": True, "exists": False}, first) is None


def test_dir_digest_detects_changes_without_file_content(tmp_path):
    directory = tmp_path / "target"
    directory.mkdir()
    (directory / "a.flac").write_bytes(b"audio")
    before = OBSERVE._dir_digest(directory)
    assert before["exists"] is True and before["entries"] == 1
    assert before["truncated"] is False
    (directory / "b.flac").write_bytes(b"audio2")
    after = OBSERVE._dir_digest(directory)
    assert after["digest"] != before["digest"]
    # 摘要只由名字/大小/mtime/mode 组成，不含内容。
    assert "audio" not in json.dumps(before)


def test_dir_digest_bounds_entries(tmp_path, monkeypatch):
    directory = tmp_path / "target"
    directory.mkdir()
    for index in range(3):
        (directory / f"{index}.flac").write_bytes(b"x")
    monkeypatch.setattr(OBSERVE, "MAX_DIR_ENTRIES", 2)
    bounded = OBSERVE._dir_digest(directory)
    assert bounded["entries"] == 2
    assert bounded["truncated"] is True


def test_forbidden_log_counts_only(tmp_path):
    log = tmp_path / "musicbox.log"
    log.write_text(
        "GET /api/v6/playlist/detail 200\n"
        "GET /api/song/enhance/player/url 200\n"
        "GET /lyric?id=1 200\n"
        "GET /weapi/v3/song/detail 200\n",
        encoding="utf-8",
    )
    result = OBSERVE._count_forbidden_log_patterns(log)
    assert result["counts"] == {
        "/api/song/enhance/player/url": 1,
        "/weapi/v3/song/detail": 1,
        "/lyric": 1,
    }
    rendered = json.dumps(result)
    assert "playlist/detail 200" not in rendered  # 不回显日志行
    assert "id=1" not in rendered


def test_report_shape_has_no_sensitive_fields_and_redacts_playlist_id_by_default():
    args = OBSERVE._parse_args(["--playlist-id", "123456"])
    report = OBSERVE._build_report(args, {"git_head": "deadbeef"}, dry_run=False)
    assert report["playlist_id"].startswith("sha256:")
    assert "123456" not in json.dumps(report)
    for forbidden in ("name", "cover_url", "cookie", "token", "password", "target_dir"):
        assert forbidden not in report


def test_show_playlist_id_is_opt_in():
    args = OBSERVE._parse_args(["--playlist-id", "123456", "--show-playlist-id"])
    report = OBSERVE._build_report(args, {}, dry_run=False)
    assert report["playlist_id"] == 123456


def test_dir_changed_is_none_when_digest_truncated():
    before = {"exists": True, "truncated": True, "digest": "a"}
    after = {"exists": True, "truncated": True, "digest": "a"}
    assert OBSERVE._dir_changed(before, after) is None
    assert OBSERVE._dir_changed({"exists": True, "truncated": False, "digest": "a"},
                                {"exists": True, "truncated": False, "digest": "b"}) is True
    assert OBSERVE._dir_changed({"exists": False}, before) is None


def test_log_delta_uses_increments_not_absolute_counts(tmp_path):
    log = tmp_path / "musicbox.log"
    log.write_text("GET /lyric?id=1 200\n", encoding="utf-8")
    before = OBSERVE._count_forbidden_log_patterns(log)
    assert before["counts"]["/lyric"] == 1
    with log.open("a", encoding="utf-8") as handle:
        handle.write("GET /api/song/enhance/player/url 200\n")
    after = OBSERVE._count_forbidden_log_patterns(log)
    delta = OBSERVE._log_delta(before, after)
    assert delta["available"] is True
    assert delta["delta"]["/lyric"] == 0  # 历史行不算本次观察
    assert delta["delta"]["/api/song/enhance/player/url"] == 1


def test_dry_run_never_touches_network(tmp_path, monkeypatch):
    module_dir = tmp_path / "musicbox-service"
    module_dir.mkdir()
    (module_dir / "netease_playlist_snapshot.py").write_text(
        'PINNED_VERSION = "0.5.3"\n'
        'WHEEL_SHA256 = "7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12"\n'
        "def live_fetchers():\n"
        "    raise AssertionError(\"dry-run must not fetch\")\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        OBSERVE, "_observe_once", lambda _playlist_id: pytest.fail("dry-run must not read"),
    )
    code = OBSERVE.main(["--dry-run", "--module-dir", str(module_dir)])
    assert code == 0


def test_confirm_observe_required(tmp_path, capsys, monkeypatch):
    module_dir = tmp_path / "musicbox-service"
    module_dir.mkdir()
    (module_dir / "netease_playlist_snapshot.py").write_text(
        'PINNED_VERSION = "0.5.3"\nWHEEL_SHA256 = "x"\n', encoding="utf-8",
    )
    monkeypatch.delenv("FNMUSIC_MANUAL_SAVE_ENABLED", raising=False)
    monkeypatch.delenv("FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED", raising=False)
    code = OBSERVE.main([
        "--module-dir", str(module_dir), "--playlist-id", "7",
    ])
    assert code == 2
    assert "confirm-observe" in capsys.readouterr().err


def test_save_switch_on_fails_closed(tmp_path, monkeypatch):
    module_dir = tmp_path / "musicbox-service"
    module_dir.mkdir()
    (module_dir / "netease_playlist_snapshot.py").write_text(
        'PINNED_VERSION = "0.5.3"\nWHEEL_SHA256 = "x"\n', encoding="utf-8",
    )
    calls = []
    monkeypatch.setattr(OBSERVE, "_observe_once", lambda playlist_id: calls.append(playlist_id))
    monkeypatch.setenv("FNMUSIC_MANUAL_SAVE_ENABLED", "true")
    code = OBSERVE.main([
        "--module-dir", str(module_dir), "--playlist-id", "7", "--confirm-observe",
    ])
    assert code == 1
    assert calls == []  # 未发生任何读取


def test_script_import_has_no_network_or_file_side_effects():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    for forbidden in ("urllib.request", "requests.", "http.client", "socket.socket"):
        assert forbidden not in source
