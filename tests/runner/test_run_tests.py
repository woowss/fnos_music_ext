"""Regression tests for orchestration only; never run the business suites here."""
from collections import Counter
from contextlib import nullcontext
import configparser
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("fn_test_runner", ROOT / "scripts/run_tests.py")
runner = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runner
SPEC.loader.exec_module(runner)


def test_all_pytest_roots_run_exactly_once():
    config = configparser.ConfigParser()
    config.read(ROOT / "pytest.ini")
    configured = config["pytest"]["testpaths"].split()
    scheduled = [path for paths in runner.SUITES.values() for path in paths]
    assert Counter(configured) == Counter(scheduled)
    assert all(count == 1 for count in Counter(scheduled).values())
    # Directories, not a file allowlist: new tests in any suite are not lost.
    assert all((ROOT / path).is_dir() for path in scheduled)
    assert runner.PHASES[0] == {"proxy": ("proxy/tests",)}
    assert set(runner.PHASES[1]) == {"webui", "musicdl", "lxmusic"}
    assert set(runner.PHASES[2]) == {"infrastructure"}
    assert runner.PHASES[3] == {"offline": ("offline_tests",)}
    assert runner.SUITES["offline"] == ("offline_tests",)
    assert runner.parse_args(["--suite", "offline"]).suite == ["offline"]


def test_command_uses_current_interpreter_without_extra_node_or_xdist():
    command = runner.pytest_command(("webui-service",), False)
    assert command[:4] == [sys.executable, "-m", "pytest", "webui-service"]
    assert not any(arg.startswith("--cov") for arg in command)
    assert "-n" not in command
    covered = runner.pytest_command(("proxy/tests",), True)
    assert {arg for arg in covered if arg.startswith("--cov=")} == {
        f"--cov={target}" for target in runner.COVERAGE_TARGETS
    }
    assert "--cov-report=" in covered


def test_proxy_suite_environment_isolated_and_parent_unchanged(tmp_path, monkeypatch):
    inherited_pythonpath = str(tmp_path / "external-modules")
    parent_home = str(tmp_path / "external-home")
    monkeypatch.setenv("PYTHONPATH", inherited_pythonpath)
    monkeypatch.setenv("FNMUSIC_HOME", parent_home)
    parent_environment = os.environ.copy()
    work = tmp_path / "runner"
    work.mkdir()

    env = runner.suite_environment("proxy", work)

    root = (work / "proxy" / "app").resolve()
    assert env["FNMUSIC_TEST_ISOLATION_ROOT"] == str(root)
    assert env["FNMUSIC_HOME"] == str(root)
    assert env["HOME"] == env["USERPROFILE"] == str(root / "home")
    assert env["XDG_CONFIG_HOME"] == str(root / "xdg" / "config")
    assert env["XDG_DATA_HOME"] == str(root / "xdg" / "data")
    assert env["XDG_CACHE_HOME"] == str(root / "xdg" / "cache")
    for key in (
        "FNMUSIC_LOG_DIR", "FNMUSIC_CACHE_DIR", "FNMUSIC_FAV_DIR",
        "FNMUSIC_PLAY_HISTORY_DIR", "FNMUSIC_RECOMMEND_DIR",
        "FNMUSIC_LIBRARY_DIR", "FNMUSIC_TEE_SAVE_DIR",
        "FNMUSIC_NM_PLAYLISTS_DIR", "FNMUSIC_PUBLIC_PLAYLISTS_DIR",
        "WEBUI_REPO_DIR",
    ):
        assert Path(env[key]).is_relative_to(root), key
        assert Path(env[key]).is_dir(), key
    assert env["FNMUSIC_LIBRARY_DIR"] == env["FNMUSIC_TEE_SAVE_DIR"]
    for key, filename in (
        ("FNMUSIC_MUSIC_DB", "music.db"),
        ("FNMUSIC_UPSTREAM_SOCK", "upstream.sock"),
        ("FNMUSIC_ENV_FILE", ".env"),
        ("FNMUSIC_LLM_USAGE_FILE", "llm_usage.json"),
    ):
        assert env[key] == str(root / filename)
        assert not Path(env[key]).exists(), key
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert env["PYTHONPATH"] == os.pathsep.join([
        str((runner.ROOT.resolve() / "proxy").resolve()), inherited_pythonpath,
    ])
    assert os.environ.copy() == parent_environment


def test_proxy_subprocess_prefers_repository_module_to_inherited_path(
    tmp_path, monkeypatch, capsys,
):
    root = tmp_path / "repo"
    proxy_dir = root / "proxy"
    proxy_dir.mkdir(parents=True)
    inherited_dir = tmp_path / "inherited"
    inherited_dir.mkdir()
    (proxy_dir / "gateway_identity.py").write_text("SOURCE = 'proxy-first'\n")
    (inherited_dir / "gateway_identity.py").write_text("SOURCE = 'inherited'\n")
    monkeypatch.setattr(runner, "ROOT", root)
    monkeypatch.setenv("PYTHONPATH", str(inherited_dir))
    monkeypatch.setattr(
        runner, "pytest_command",
        lambda _paths, _coverage: [
            sys.executable, "-c",
            "import gateway_identity; print(gateway_identity.SOURCE)",
        ],
    )
    work = tmp_path / "runner"
    work.mkdir()

    results = runner.run_phase({"proxy": ("synthetic-probe",)}, 1, work, False)

    assert results == {"proxy": 0}
    assert "proxy-first" in capsys.readouterr().out
    assert os.environ["PYTHONPATH"] == str(inherited_dir)


def test_non_proxy_suite_environment_is_unchanged_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(tmp_path / "external-modules"))
    monkeypatch.setenv("FNMUSIC_HOME", str(tmp_path / "external-home"))
    parent_environment = os.environ.copy()

    env = runner.suite_environment("webui", tmp_path)

    assert env == parent_environment
    assert env is not os.environ


@pytest.mark.parametrize("jobs", ["0", "4", "-1"])
def test_cli_rejects_unbounded_parallelism(jobs):
    with pytest.raises(SystemExit):
        runner.parse_args(["--jobs", jobs])


def test_main_preserves_phase_barriers_and_deduplicates_selection(monkeypatch):
    calls = []

    def fake_phase(suites, jobs, work, coverage):
        assert work.is_dir()
        calls.append((list(suites), jobs, coverage))
        return {name: int(name == "webui") for name in suites}

    monkeypatch.setattr(runner, "run_phase", fake_phase)
    # Failure must be reported but must not skip remaining suites/phases.
    assert runner.main(["--jobs", "1"]) == 1
    assert calls == [(["proxy"], 1, False),
                     (["webui", "musicdl", "lxmusic"], 1, False),
                     (["infrastructure"], 1, False),
                     (["offline"], 1, False)]
    calls.clear()
    assert runner.main(["--suite", "lxmusic", "--suite", "lxmusic"]) == 0
    assert [names for names, _, _ in calls if names] == [["lxmusic"]]


# A real subprocess probe observes process-global state and scheduling without
# importing app, opening sockets, or depending on actual test-suite durations.
PROBE = """
import json, os, pathlib, sys, time
name, events = sys.argv[1:3]
def record(event):
    row = dict(name=name, event=event, pid=os.getpid(), at=time.monotonic(),
               coverage=os.environ['COVERAGE_FILE'],
               inherited=os.environ.get('FN_RUNNER_PROBE'),
               cwd=os.getcwd())
    with open(events, 'a') as f:
        f.write(json.dumps(row) + '\\n')
record('start')
os.environ['FN_RUNNER_PROBE'] = name
# First two workers rendezvous to prove overlap, rather than trusting a timing
# speedup; timeout makes a broken serial scheduler fail instead of hanging.
if name in ('first', 'second') and os.environ.get('FN_RUNNER_BARRIER') == 'yes':
    pathlib.Path(events + '.' + name).touch()
    deadline = time.monotonic() + 5
    while not all(pathlib.Path(events + '.' + n).exists() for n in ('first', 'second')):
        if time.monotonic() > deadline:
            sys.exit(7)
        time.sleep(.01)
time.sleep(.05)
record('end')
print('probe output ' + name)
sys.exit(3 if name == 'second' else 0)
"""


@pytest.mark.parametrize("jobs", [1, 2])
def test_real_process_limits_isolation_logs_and_failure_aggregation(
    tmp_path, monkeypatch, capsys, jobs,
):
    events = tmp_path / "events.jsonl"
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    monkeypatch.setenv("FN_RUNNER_PROBE", "parent")
    monkeypatch.setenv("FN_RUNNER_BARRIER", "yes" if jobs == 2 else "no")
    monkeypatch.setattr(runner, "pytest_command", lambda paths, coverage: [
        sys.executable, "-c", PROBE, paths[0], str(events),
    ])
    work = tmp_path / "work"
    work.mkdir()
    results = runner.run_phase(
        {name: (name,) for name in ("first", "second", "third")}, jobs, work, False,
    )
    assert results == {"first": 0, "second": 3, "third": 0}
    rows = [json.loads(line) for line in events.read_text().splitlines()]
    starts = {row["name"]: row for row in rows if row["event"] == "start"}
    ends = {row["name"]: row for row in rows if row["event"] == "end"}
    assert len({row["pid"] for row in starts.values()}) == 3
    assert len({row["coverage"] for row in starts.values()}) == 3
    assert all(row["inherited"] == "parent" for row in starts.values())
    assert all(row["cwd"] == str(tmp_path) for row in starts.values())
    assert os.environ["FN_RUNNER_PROBE"] == "parent"
    active = maximum = 0
    for row in rows:
        active += 1 if row["event"] == "start" else -1
        maximum = max(maximum, active)
    assert active == 0
    assert maximum == jobs
    if jobs == 2:
        assert starts["third"]["at"] >= min(ends[n]["at"] for n in ("first", "second"))
    output = capsys.readouterr().out
    assert all(f"probe output {name}" in output for name in starts)
    assert "second: exit 3" in output


def test_child_log_preserves_unicode_for_utf8_stdout(monkeypatch):
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="utf-8", errors="strict", newline="")
    monkeypatch.setattr(runner.sys, "stdout", stream)
    text = "测试输出：\ufffd😀\n"

    runner.print_child_log(text)

    assert output.getvalue() == text.encode("utf-8")


def test_main_escapes_unencodable_child_log_and_continues_phases(
    tmp_path, monkeypatch,
):
    work = tmp_path / "runner-work"
    work.mkdir()
    phases = ({"first": ("first",)}, {"second": ("second",)})
    monkeypatch.setattr(runner, "PHASES", phases)
    monkeypatch.setattr(runner, "SUITES", {
        name: paths for phase in phases for name, paths in phase.items()
    })
    monkeypatch.setattr(
        runner.tempfile, "TemporaryDirectory",
        lambda **_kwargs: nullcontext(str(work)),
    )

    payload = "中文：\ufffd😀".encode("utf-8") + b"\xff\n"

    def command(paths, _coverage):
        if paths == ("first",):
            script = (
                "import sys; "
                f"sys.stdout.buffer.write(bytes.fromhex('{payload.hex()}')); "
                "sys.stdout.buffer.flush(); sys.exit(7)"
            )
        else:
            script = "print('second phase output')"
        return [sys.executable, "-c", script]

    monkeypatch.setattr(runner, "pytest_command", command)
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding="gbk", errors="strict", newline="")
    monkeypatch.setattr(runner.sys, "stdout", stream)

    assert runner.main(["--jobs", "1"]) == 1

    rendered = output.getvalue().decode("gbk")
    assert "中文" in rendered
    assert "\\ufffd" in rendered
    assert "\\U0001f600" in rendered
    assert "=== first: exit 7" in rendered
    assert "=== second: exit 0" in rendered
    assert "=== Total: 2 suites, 1 failed" in rendered
    assert (work / "first.log").read_bytes() == payload


def test_launch_failure_reaps_previously_started_process(tmp_path, monkeypatch):
    processes = []
    real_popen = subprocess.Popen

    def launch(command, **kwargs):
        if processes:
            raise OSError("cannot launch next suite")
        process = real_popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    with pytest.raises(OSError, match="cannot launch"):
        runner.run_phase({"first": ("first",), "second": ("second",)}, 2, tmp_path, False)
    assert len(processes) == 1
    assert processes[0].poll() is not None


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cleanup")
def test_cleanup_kills_workers_even_after_leader_exits(tmp_path, monkeypatch):
    calls = []
    process = SimpleNamespace(pid=123456, wait=lambda **kwargs: 0)
    log = (tmp_path / "log").open("w+b")
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    runner.stop_children({"suite": runner.Running(process, log, 0)})
    assert calls == [(123456, signal.SIGTERM), (123456, signal.SIGKILL)]
    assert log.closed


def test_term_unwinds_and_returns_interrupt_status(monkeypatch, capsys):
    def interrupted(*args):
        runner.interrupt(signal.SIGTERM, None)

    monkeypatch.setattr(runner, "run_phase", interrupted)
    assert runner.main(["--suite", "lxmusic"]) == 130
    assert "Interrupted" in capsys.readouterr().err


@pytest.mark.skipif(os.name != "posix", reason="POSIX process group cancellation")
def test_real_term_stops_suite_and_its_worker(tmp_path):
    # Launch the real runner scheduler with a fake suite, not real app tests.
    # Its suite forks a worker and must reap it when CI cancels the runner.
    state = tmp_path / "state.json"
    worker = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_DFL); time.sleep(60)"
    suite = f"""
import json, os, pathlib, signal, subprocess, sys, time
worker = subprocess.Popen([sys.executable, '-c', {worker!r}])
def stop(signum, frame):
    worker.wait(timeout=3)
    pathlib.Path({str(state)!r} + '.reaped').touch()
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
pathlib.Path({str(state)!r}).write_text(json.dumps([os.getpid(), worker.pid]))
while True: time.sleep(.01)
"""
    bootstrap = f"""
import importlib.util, signal, sys
spec = importlib.util.spec_from_file_location('cancel_runner', {str(ROOT / 'scripts/run_tests.py')!r})
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
runner.PHASES = ({{'probe': ('unused',)}},)
runner.SUITES = runner.PHASES[0]
runner.pytest_command = lambda paths, coverage: [sys.executable, '-c', {suite!r}]
signal.signal(signal.SIGTERM, runner.interrupt)
raise SystemExit(runner.main([]))
"""
    process = subprocess.Popen([sys.executable, "-c", bootstrap],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pids = []
    try:
        deadline = time.monotonic() + 5
        while not state.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert state.exists(), "fake suite failed to start"
        pids = json.loads(state.read_text())
        process.terminate()
        stdout, stderr = process.communicate(timeout=8)
        assert process.returncode == 130, (stdout, stderr)
        assert "Interrupted" in stderr
        assert Path(str(state) + ".reaped").exists()
        for pid in pids:
            with pytest.raises(ProcessLookupError):
                os.kill(pid, 0)
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
        # A broken implementation must not leave the regression's own workers.
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_coverage_combines_only_current_run_and_propagates_failure(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0 if "combine" in command else 9)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    assert runner.coverage_report(tmp_path) == 9
    assert calls[0][0] == [sys.executable, "-m", "coverage", "combine", str(tmp_path / "coverage")]
    assert calls[1][0] == [sys.executable, "-m", "coverage", "report"]
    assert all(call[1]["env"]["COVERAGE_FILE"] == str(tmp_path / ".coverage") for call in calls)


def test_real_coverage_union_contains_both_suite_branches(tmp_path, monkeypatch):
    pytest.importorskip("pytest_cov")
    pytest.importorskip("coverage")
    module = tmp_path / "probe.py"
    module.write_text("def choose(flag):\n    if flag:\n        return 1\n    return 2\n")
    for name, value in (("first", "True"), ("second", "False")):
        (tmp_path / f"test_{name}.py").write_text(
            f"from probe import choose\ndef test_branch():\n    assert choose({value}) in (1, 2)\n"
        )
    work = tmp_path / "work"
    (work / "coverage").mkdir(parents=True)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    # A coverage source is an importable module, not its filename.
    monkeypatch.setattr(runner, "COVERAGE_TARGETS", ("probe",))
    results = runner.run_phase({
        "first": ("test_first.py",), "second": ("test_second.py",),
    }, 2, work, True)
    assert results == {"first": 0, "second": 0}
    assert runner.coverage_report(work) == 0
    from coverage import CoverageData
    data = CoverageData(basename=str(work / ".coverage"))
    data.read()
    assert {1, 2, 3, 4} <= set(data.lines(str(module)))


def test_ci_uses_runner_and_retains_unique_image_checks():
    ci = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())
    jobs = ci["jobs"]
    commands = [step.get("run", "") for step in jobs["test"]["steps"]]
    assert "python scripts/run_tests.py --jobs 2 --coverage" in commands
    assert not any("python -m pytest" in command for command in commands)
    assert "images" not in jobs
    images = jobs["images-multiarch"]
    build = next(step for step in images["steps"] if step.get("uses", "").startswith("docker/build-push-action@"))
    assert build["with"]["file"] == "container/Dockerfile"
    assert {row["platform"] for row in images["strategy"]["matrix"]["include"]} == {
        "linux/amd64", "linux/arm64",
    }
    steps = {step.get("name"): step for step in images["steps"]}
    assert steps["Verify on-demand musicdl healthz"]["if"] == "matrix.arch == 'x64'"
    assert "--require-non-root" in steps["Verify non-root contract for each bundled service"]["run"]
    assert "sleep 120" in steps["Smoke lxmusic chain on ${{ matrix.platform }}"]["run"]
