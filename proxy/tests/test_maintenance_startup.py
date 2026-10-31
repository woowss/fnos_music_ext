"""Isolated regression tests for the explicit maintenance-startup mode.

The runner must set FNMUSIC_TEST_ISOLATION_ROOT and all FNMUSIC/XDG paths
before pytest collection because proxy/tests/conftest.py imports proxy.app.
The fresh-process probe adds operation sentinels and a network-denying client.
"""
from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

# takeover.py uses Linux flock. On Windows, expose only import-time names; all
# subprocess, lock, socket, signal, and supervisor effects below are stubbed.
try:
    import fcntl  # noqa: F401
except ImportError:
    _fcntl_stub = types.ModuleType("fcntl")
    _fcntl_stub.LOCK_EX = 2
    _fcntl_stub.LOCK_NB = 4
    _fcntl_stub.flock = lambda *_args: None
    sys.modules["fcntl"] = _fcntl_stub
    try:
        from proxy import takeover
    finally:
        sys.modules.pop("fcntl", None)
else:
    from proxy import takeover

from proxy import app as collection_app


_ISOLATION_ROOT = Path(os.environ.get("FNMUSIC_TEST_ISOLATION_ROOT", ""))
assert str(_ISOLATION_ROOT) not in ("", "."), "run through the isolated TEMP runner"
assert Path(os.environ["FNMUSIC_HOME"]).resolve() == _ISOLATION_ROOT.resolve()
for _key in ("music_db", "upstream_sock", "cache_dir"):
    assert Path(collection_app.CONF[_key]).resolve().is_relative_to(_ISOLATION_ROOT.resolve()), _key
assert Path(os.environ["FNMUSIC_TEE_SAVE_DIR"]).resolve().is_relative_to(_ISOLATION_ROOT.resolve())


_FRESH_IMPORT_AND_LIFESPAN = r'''
import asyncio
import json
import logging
import os
from pathlib import Path

maintenance_value = os.environ.get("FNMUSIC_MAINTENANCE_STARTUP", "")
maintenance = maintenance_value == "1"
root = Path(os.environ["FNMUSIC_TEST_ISOLATION_ROOT"])
events = []
log_path = Path(os.environ["FNMUSIC_LOG_DIR"]) / "proxy.log"
log_path.parent.mkdir(parents=True, exist_ok=True)
old_log = b"preexisting-manual-log\n"
log_path.write_bytes(old_log)
recommend_dir = Path(os.environ["FNMUSIC_RECOMMEND_DIR"])
recommend_user = recommend_dir / "fixture-user"
recommend_user.mkdir(parents=True, exist_ok=True)
marker_path = recommend_dir / ".last-version"
old_marker = b"fixture-old-version\n"
marker_path.write_bytes(old_marker)
from datetime import datetime
daily_name = "daily-" + datetime.now().strftime("%Y%m%d") + ".json"
cache_path = recommend_user / daily_name
old_cache = b'{"fixture":"old-daily-cache"}\n'
cache_path.write_bytes(old_cache)

import fnlog
real_setup_logging = fnlog.setup_logging
real_import_purge = fnlog.purge_stale

def operation(name, value=None):
    events.append(name)
    if maintenance:
        raise RuntimeError("maintenance operation sentinel")
    return value

def setup_logging(*args, **kwargs):
    events.append("file_logging_setup")
    if maintenance:
        raise RuntimeError("maintenance file-logging sentinel")
    return real_setup_logging(*args, **kwargs)

def import_purge_stale(*args, **kwargs):
    events.append("file_logging_purge")
    if maintenance:
        raise RuntimeError("maintenance import-purge sentinel")
    return real_import_purge(*args, **kwargs)

fnlog.setup_logging = setup_logging
fnlog.purge_stale = import_purge_stale
import app
import maintenance_socket
app.tc.FFMPEG_BIN = None
app.tc.FFPROBE_BIN = None

real_recommend_sync = app._sync_recommend_version_marker
real_env_watch_stat = app._env_watch_stat
real_orphan_sweeper = app._lyric_orphan_sweeper
real_log_retention = app._log_retention_loop
real_env_watch = app._env_watch_loop

def snapshot_collect(_context):
    return operation("host_snapshot_collect", [])

def snapshot_write(_context):
    return operation("host_snapshot_write", True)

def recommend_sync():
    events.append("recommend_version_sync")
    if maintenance:
        raise RuntimeError("recommend-cache/marker sentinel")
    return real_recommend_sync()

def detect_library_dir():
    return operation("library_directory_detection", str(root / "music-library"))

def sweep_orphan_lyrics(*_args, **_kwargs):
    return operation("orphan_lyric_sweep", [])

def log_retention_purge(*_args, **_kwargs):
    return operation("log_retention_purge_task", [])

def env_watch_stat(*args, **kwargs):
    events.append("env_watch_stat")
    if maintenance:
        raise RuntimeError("env-watch sentinel")
    return real_env_watch_stat(*args, **kwargs)

app.env_snapshot_lines = snapshot_collect
app.write_env_snapshot = snapshot_write
app._sync_recommend_version_marker = recommend_sync
app.detect_library_dir = detect_library_dir
app.sweep_orphan_lyrics = sweep_orphan_lyrics
app.purge_stale = log_retention_purge
app._env_watch_stat = env_watch_stat
app.CONF["lx_enabled"] = os.environ.get("FNMUSIC_LX_ENABLED", "true").lower() in ("1", "true", "yes")
app.CONF["env_watch"] = os.environ.get("FNMUSIC_ENV_WATCH", "true").lower() in ("1", "true", "yes")

def track_task(name, coroutine):
    async def tracked():
        events.append(name + "_started")
        try:
            await coroutine()
        except asyncio.CancelledError:
            events.append(name + "_cancelled")
            raise
    return tracked

app._lyric_orphan_sweeper = track_task("orphan_lyric_sweeper", real_orphan_sweeper)
app._log_retention_loop = track_task("log_retention", real_log_retention)
app._env_watch_loop = track_task("env_watch", real_env_watch)

async def liked_scheduler(_app):
    events.append("liked_scheduler_started")
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        events.append("liked_scheduler_cancelled")
        raise

app._liked_scheduler_loop = liked_scheduler

async def maintenance_socket_start(_loop, _handler):
    events.append("maintenance_socket_started")
    return object()

async def maintenance_socket_stop(_server):
    events.append("maintenance_socket_stopped")

maintenance_socket.start = maintenance_socket_start
maintenance_socket.stop = maintenance_socket_stop

owned_created = []
owned_closed = []
borrowed_closed = []

class NoNetworkClient:
    def __init__(self, **_kwargs):
        self.identity = "owned-" + str(len(owned_created) + 1)
        owned_created.append(self.identity)
        events.append("owned_client_created")

    async def get(self, _path, **_kwargs):
        events.append("lx_warmup_http_sentinel")
        raise OSError("network blocked by test sentinel")

    async def aclose(self):
        owned_closed.append(self.identity)
        events.append("owned_client_closed")

class BorrowedClient:
    async def aclose(self):
        borrowed_closed.append("borrowed")
        events.append("borrowed_client_closed")

app.httpx.AsyncClient = NoNetworkClient
borrowed = None
if os.environ.get("FNMUSIC_TEST_BORROWED_MUSICBOX") == "1":
    borrowed = BorrowedClient()
    app.app.state.musicbox_client = borrowed

async def no_shutdown():
    pass

app.pubpl.shutdown = no_shutdown
app.tc.shutdown_all = no_shutdown
app._cancel_netease_resolutions = lambda: []

async def run_lifespan():
    async with app.lifespan(app.app):
        events.append("lifespan_enter_and_yield")
        await asyncio.sleep(0.05)
    events.append("lifespan_exit_complete")

asyncio.run(run_lifespan())
print("__STARTUP_RESULT__" + json.dumps({
    "maintenance_value": maintenance_value,
    "maintenance_enabled": app._MAINTENANCE_STARTUP,
    "events": events,
    "old_log_hex": log_path.read_bytes().hex(),
    "old_marker_hex": marker_path.read_bytes().hex(),
    "old_cache_hex": cache_path.read_bytes().hex() if cache_path.exists() else None,
    "fallback_cache_hex": (recommend_user / daily_name.replace(".json", ".fallback.json")).read_bytes().hex()
        if (recommend_user / daily_name.replace(".json", ".fallback.json")).exists() else None,
    "file_handler_exists": any(isinstance(handler, logging.FileHandler) for handler in app.logger.handlers),
    "owned_created": owned_created,
    "owned_closed": owned_closed,
    "borrowed_closed": borrowed_closed,
    "borrowed_retained": borrowed is not None and app.app.state.musicbox_client is borrowed,
}))
'''


def _fresh_startup_probe(
    tmp_path: Path,
    *,
    maintenance_value: str,
    background_jobs: str,
    lx_enabled: str,
    env_watch: str,
    borrowed_musicbox: bool = False,
) -> dict:
    values = {
        "HOME": str(tmp_path / "home"),
        "USERPROFILE": str(tmp_path / "home"),
        "FNMUSIC_HOME": str(tmp_path),
        "FNMUSIC_TEST_ISOLATION_ROOT": str(tmp_path),
        "FNMUSIC_LOG_DIR": str(tmp_path / "logs"),
        "FNMUSIC_CACHE_DIR": str(tmp_path / "cache"),
        "FNMUSIC_FAV_DIR": str(tmp_path / "favorites"),
        "FNMUSIC_PLAY_HISTORY_DIR": str(tmp_path / "history"),
        "FNMUSIC_RECOMMEND_DIR": str(tmp_path / "recommend"),
        "FNMUSIC_LIBRARY_DIR": str(tmp_path / "music-library"),
        "FNMUSIC_TEE_SAVE_DIR": "",
        "FNMUSIC_NM_PLAYLISTS_DIR": str(tmp_path / "nm-playlists"),
        "FNMUSIC_PUBLIC_PLAYLISTS_DIR": str(tmp_path / "public-playlists"),
        "FNMUSIC_MUSIC_DB": str(tmp_path / "music.db"),
        "FNMUSIC_UPSTREAM_SOCK": str(tmp_path / "upstream.sock"),
        "FNMUSIC_MUSICDL_URL": "http://127.0.0.1:1",
        "FNMUSIC_MUSICBOX_URL": "http://127.0.0.1:1",
        "FNMUSIC_LX_URL": "http://127.0.0.1:1",
        "FNMUSIC_LX_ENABLED": lx_enabled,
        "FNMUSIC_ENV_WATCH": env_watch,
        "FNMUSIC_BACKGROUND_JOBS": background_jobs,
        "FNMUSIC_MAINTENANCE_STARTUP": maintenance_value,
        "FNMUSIC_TEST_BORROWED_MUSICBOX": "1" if borrowed_musicbox else "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "XDG_CACHE_HOME": str(tmp_path / "xdg-cache"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg-config"),
    }
    env = os.environ.copy()
    env.update(values)
    env["PYTHONPATH"] = os.pathsep.join((
        str(Path(__file__).resolve().parents[2]),
        str(Path(__file__).resolve().parents[1]),
    ))
    result = subprocess.run(
        [sys.executable, "-B", "-c", _FRESH_IMPORT_AND_LIFESPAN],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode:
        # Child output can contain library diagnostics; return only safe shape.
        lines = result.stderr.rstrip().splitlines()
        error_kind = lines[-1].split(":", 1)[0] if lines else "no-stderr"
        raise AssertionError(f"isolated startup child exit={result.returncode}, error={error_kind}")
    marker = "__STARTUP_RESULT__"
    payloads = [line[len(marker):] for line in result.stdout.splitlines() if line.startswith(marker)]
    assert len(payloads) == 1
    return json.loads(payloads[0])


_STARTUP_CASES = [
    pytest.param("", "1", "true", "true", False, id="empty-default-normal"),
    pytest.param("false", "true", "true", "true", False, id="false-string-normal"),
    pytest.param("true", "yes", "false", "false", False, id="true-string-is-not-mode"),
    pytest.param("yes", "0", "true", "false", False, id="yes-string-is-not-mode"),
    pytest.param("0", "0", "true", "true", False, id="zero-string-normal-no-jobs"),
    pytest.param("1", "0", "true", "true", True, id="maintenance-mode-jobs-off"),
    pytest.param("1", "1", "true", "true", True, id="maintenance-mode-resists-inherited-jobs"),
]


@pytest.mark.parametrize(
    "maintenance_value,background_jobs,lx_enabled,env_watch,expected_maintenance",
    _STARTUP_CASES,
)
def test_fresh_import_and_lifespan_mode_matrix(
    tmp_path,
    maintenance_value,
    background_jobs,
    lx_enabled,
    env_watch,
    expected_maintenance,
):
    result = _fresh_startup_probe(
        tmp_path,
        maintenance_value=maintenance_value,
        background_jobs=background_jobs,
        lx_enabled=lx_enabled,
        env_watch=env_watch,
    )
    events = result["events"]
    assert result["maintenance_enabled"] is expected_maintenance
    assert "lifespan_enter_and_yield" in events
    assert "lifespan_exit_complete" in events
    assert len(result["owned_created"]) == 5
    assert len(result["owned_closed"]) == 5
    assert sorted(result["owned_created"]) == sorted(result["owned_closed"])
    assert len(set(result["owned_closed"])) == 5
    assert result["borrowed_closed"] == []
    assert result["borrowed_retained"] is False

    if expected_maintenance:
        assert result["old_log_hex"] == b"preexisting-manual-log\n".hex()
        assert result["old_marker_hex"] == b"fixture-old-version\n".hex()
        assert result["old_cache_hex"] == b'{"fixture":"old-daily-cache"}\n'.hex()
        assert result["file_handler_exists"] is False
        assert not {
            "file_logging_setup", "file_logging_purge", "host_snapshot_collect",
            "host_snapshot_write", "recommend_version_sync", "library_directory_detection",
            "orphan_lyric_sweep", "log_retention_purge_task", "orphan_lyric_sweeper_started",
            "log_retention_started", "env_watch_stat", "env_watch_started",
            "lx_warmup_http_sentinel", "liked_scheduler_started",
            "maintenance_socket_started", "maintenance_socket_stopped",
        }.intersection(events)
        return

    assert {
        "file_logging_setup", "file_logging_purge", "host_snapshot_collect",
        "host_snapshot_write", "recommend_version_sync",
    }.issubset(events)
    assert result["old_marker_hex"] != b"fixture-old-version\n".hex()
    assert result["old_cache_hex"] is None
    assert result["fallback_cache_hex"] == b'{"fixture":"old-daily-cache"}\n'.hex()
    jobs_enabled = (
        background_jobs.lower() in ("1", "true", "yes")
        and not expected_maintenance
    )
    if jobs_enabled:
        assert {
            "orphan_lyric_sweeper_started", "log_retention_started",
            "liked_scheduler_started", "maintenance_socket_started",
        }.issubset(events)
        assert {"orphan_lyric_sweeper_cancelled", "log_retention_cancelled"}.issubset(events)
        assert "liked_scheduler_cancelled" in events
        assert "maintenance_socket_stopped" in events
        assert "library_directory_detection" in events
        assert "orphan_lyric_sweep" in events
        assert "log_retention_purge_task" in events
        if env_watch.lower() in ("1", "true", "yes"):
            assert "env_watch_started" in events
            assert "env_watch_stat" in events
            assert "env_watch_cancelled" in events
        else:
            assert "env_watch_started" not in events
    else:
        assert not {
            "orphan_lyric_sweeper_started", "log_retention_started", "env_watch_started",
            "library_directory_detection", "orphan_lyric_sweep", "log_retention_purge_task",
            "env_watch_stat", "liked_scheduler_started", "maintenance_socket_started",
            "maintenance_socket_stopped",
        }.intersection(events)
    if lx_enabled.lower() in ("1", "true", "yes"):
        assert "lx_warmup_http_sentinel" in events
    else:
        assert "lx_warmup_http_sentinel" not in events


def test_lifespan_closes_owned_clients_once_but_keeps_borrowed_client(tmp_path):
    result = _fresh_startup_probe(
        tmp_path,
        maintenance_value="1",
        background_jobs="0",
        lx_enabled="false",
        env_watch="false",
        borrowed_musicbox=True,
    )

    assert result["maintenance_enabled"] is True
    assert len(result["owned_created"]) == 4
    assert len(result["owned_closed"]) == 4
    assert sorted(result["owned_created"]) == sorted(result["owned_closed"])
    assert len(set(result["owned_closed"])) == 4
    assert result["borrowed_closed"] == []
    assert result["borrowed_retained"] is True


def test_environment_forces_both_internal_flags_after_parent_and_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "FNMUSIC_MAINTENANCE_STARTUP='1'\nFNMUSIC_BACKGROUND_JOBS='0'\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("FNMUSIC_MAINTENANCE_STARTUP", "true")
    monkeypatch.setenv("FNMUSIC_BACKGROUND_JOBS", "yes")

    normal = takeover.environment(tmp_path)
    maintenance = takeover.environment(tmp_path, maintenance_startup=True)

    assert normal["FNMUSIC_MAINTENANCE_STARTUP"] == "0"
    assert normal["FNMUSIC_BACKGROUND_JOBS"] == "1"
    assert maintenance["FNMUSIC_MAINTENANCE_STARTUP"] == "1"
    assert maintenance["FNMUSIC_BACKGROUND_JOBS"] == "0"


def test_preflight_preserves_both_mode_flags_in_both_fresh_processes(tmp_path, monkeypatch):
    seen = []

    def fake_run(_command, *, env, **_kwargs):
        seen.append((env["FNMUSIC_MAINTENANCE_STARTUP"], env["FNMUSIC_BACKGROUND_JOBS"]))
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(takeover.subprocess, "run", fake_run)
    takeover.preflight(
        tmp_path,
        {"FNMUSIC_MAINTENANCE_STARTUP": "1", "FNMUSIC_BACKGROUND_JOBS": "0"},
    )

    assert seen == [("1", "0"), ("1", "0")]


@pytest.mark.parametrize(
    "maintenance_startup,expected_mode,expected_jobs",
    [(False, "0", "1"), (True, "1", "0")],
)
def test_supervisor_passes_final_mode_to_preflight_and_spawned_child(
    tmp_path,
    monkeypatch,
    maintenance_startup,
    expected_mode,
    expected_jobs,
):
    base = tmp_path / "install"
    base.mkdir()
    (base / ".env").write_text(
        "FNMUSIC_MAINTENANCE_STARTUP='true'\nFNMUSIC_BACKGROUND_JOBS='yes'\n",
        encoding="utf-8",
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state = type("State", (), {
        "directory": state_dir,
        "upstream": state_dir / "upstream.sock",
        "publish": lambda *_args: None,
        "restore": lambda *_args: None,
    })()
    preflight_envs = []
    child_envs = []
    readiness_modes = []

    def fake_preflight(_base, env):
        preflight_envs.append(env.copy())

    class FakeChild:
        pid = 43210
        stdout = object()

        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

        def wait(self, **_kwargs):
            self.returncode = 0
            return 0

    fake_child = FakeChild()

    def fake_popen(*_args, **kwargs):
        child_envs.append(kwargs["env"].copy())
        return fake_child

    class NoStartThread:
        def __init__(self, *_args, **_kwargs):
            pass

        def start(self):
            pass

        def join(self, **_kwargs):
            pass

    monkeypatch.setattr(takeover, "preflight", fake_preflight)
    monkeypatch.setattr(takeover.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(takeover.threading, "Thread", NoStartThread)
    monkeypatch.setattr(takeover.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(
        takeover,
        "snapshot",
        lambda _path: {"kind": "proxy", "process": {"pid": 43210}},
    )
    monkeypatch.setattr(takeover, "wait_official_target", lambda *_args: None)
    def fake_wait_ready(_state, _seconds, *, maintenance_startup=False):
        readiness_modes.append(maintenance_startup)

    monkeypatch.setattr(takeover, "wait_ready", fake_wait_ready)
    monkeypatch.setenv("FNMUSIC_MAINTENANCE_STARTUP", "yes")
    monkeypatch.setenv("FNMUSIC_BACKGROUND_JOBS", "true")

    with pytest.raises(takeover.Unsafe, match="proxy exited"):
        takeover.supervise(state, base, maintenance_startup=maintenance_startup)

    assert len(preflight_envs) == len(child_envs) == 1
    assert readiness_modes == [maintenance_startup]
    for env in (preflight_envs[0], child_envs[0]):
        assert env["FNMUSIC_MAINTENANCE_STARTUP"] == expected_mode
        assert env["FNMUSIC_BACKGROUND_JOBS"] == expected_jobs
        assert env["FNMUSIC_UPSTREAM_SOCK"] == str(state.upstream)
    assert preflight_envs[0] == child_envs[0]


@pytest.mark.parametrize("command", ["status", "preflight", "restore"])
def test_invalid_flag_is_rejected_before_state_or_filesystem_side_effects(monkeypatch, command):
    constructed = []

    def state_must_not_be_constructed(*_args, **_kwargs):
        constructed.append(True)
        raise AssertionError("argument validation must happen before State construction")

    monkeypatch.setattr(sys, "argv", ["takeover.py", command, "--maintenance-startup"])
    monkeypatch.setattr(takeover, "State", state_must_not_be_constructed)

    with pytest.raises(SystemExit) as exc:
        takeover.main()

    assert exc.value.code == 2
    assert constructed == []


@pytest.mark.parametrize(
    "maintenance_startup,expected",
    [(False, False), (True, True)],
)
def test_run_cli_propagates_mode_to_supervisor(tmp_path, monkeypatch, maintenance_startup, expected):
    received = []

    @contextlib.contextmanager
    def no_lock(self):
        yield

    class FakeState:
        def __init__(self, *_args, **_kwargs):
            self.directory = tmp_path

        lock = no_lock

    def fake_supervise(_state, _base, *, maintenance_startup=False):
        received.append(maintenance_startup)

    argv = ["takeover.py", "run", "--base", str(tmp_path)]
    if maintenance_startup:
        argv.append("--maintenance-startup")
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr(takeover, "State", FakeState)
    monkeypatch.setattr(takeover, "supervise", fake_supervise)
    monkeypatch.setattr(takeover.fcntl, "flock", lambda *_args: None)

    assert takeover.main() == 0
    assert received == [expected]
