#!/usr/bin/env python3
"""Run the offline suites without sharing app/conftest/environment globals.

Usage: python3 scripts/run_tests.py [--jobs 1] [--suite webui|offline] [--coverage]
Proxy/socket/deployment tests and infrastructure tests run exclusively. Only
service suites may overlap, in separate interpreters; no xdist is involved.
Node behavior tests are already included by webui-service/test_app_js.py.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO

ROOT = Path(__file__).resolve().parents[1]
# Longest services first. Each configured pytest testpath appears exactly once.
PHASES = (
    {"proxy": ("proxy/tests",)},
    {
        "webui": ("webui-service",),
        "musicdl": ("musicdl-service",),
        "lxmusic": ("lxmusic-service",),
    },
    {"infrastructure": ("container", "packaging/tests", "tests/runner")},
    # Keep stdlib-only safety regressions in their own interpreter and phase.
    {"offline": ("offline_tests",)},
)
SUITES = {name: paths for phase in PHASES for name, paths in phase.items()}
COVERAGE_TARGETS = (
    "proxy", "lxmusic-service", "musicdl-service", "musicbox-service", "webui-service",
)


@dataclass
class Running:
    process: subprocess.Popen
    log: BinaryIO
    started: float


def pytest_command(paths: tuple[str, ...], coverage: bool) -> list[str]:
    command = [sys.executable, "-m", "pytest", *paths, "-q", "--durations=5"]
    if coverage:
        command.extend(f"--cov={target}" for target in COVERAGE_TARGETS)
        command.append("--cov-report=")
    return command


def print_child_log(text: str) -> None:
    """Write decoded child output without failing on the console code page."""
    stream = sys.stdout
    encoding = getattr(stream, "encoding", None)
    if encoding:
        text = text.encode(encoding, errors="backslashreplace").decode(encoding)
    stream.write(text)
    stream.flush()


def suite_environment(name: str, work: Path) -> dict[str, str]:
    """Build a suite environment without leaking test state to the caller."""
    env = os.environ.copy()
    if name != "proxy":
        return env

    isolation_root = (work / "proxy" / "app").resolve()
    directory_paths = {
        "HOME": isolation_root / "home",
        "USERPROFILE": isolation_root / "home",
        "XDG_CONFIG_HOME": isolation_root / "xdg" / "config",
        "XDG_DATA_HOME": isolation_root / "xdg" / "data",
        "XDG_CACHE_HOME": isolation_root / "xdg" / "cache",
        "FNMUSIC_LOG_DIR": isolation_root / "logs",
        "FNMUSIC_CACHE_DIR": isolation_root / "cache",
        "FNMUSIC_FAV_DIR": isolation_root / "favorites",
        "FNMUSIC_PLAY_HISTORY_DIR": isolation_root / "history",
        "FNMUSIC_RECOMMEND_DIR": isolation_root / "recommend",
        "FNMUSIC_LIBRARY_DIR": isolation_root / "music-library",
        "FNMUSIC_TEE_SAVE_DIR": isolation_root / "music-library",
        "FNMUSIC_NM_PLAYLISTS_DIR": isolation_root / "nm-playlists",
        "FNMUSIC_PUBLIC_PLAYLISTS_DIR": isolation_root / "public-playlists",
        "WEBUI_REPO_DIR": isolation_root / "repo",
    }
    for directory in directory_paths.values():
        directory.mkdir(parents=True, exist_ok=True)

    env.update({key: str(value) for key, value in directory_paths.items()})
    env.update({
        "FNMUSIC_TEST_ISOLATION_ROOT": str(isolation_root),
        "FNMUSIC_HOME": str(isolation_root),
        "FNMUSIC_MUSIC_DB": str(isolation_root / "music.db"),
        "FNMUSIC_UPSTREAM_SOCK": str(isolation_root / "upstream.sock"),
        "FNMUSIC_ENV_FILE": str(isolation_root / ".env"),
        "FNMUSIC_LLM_USAGE_FILE": str(isolation_root / "llm_usage.json"),
        "PYTHONDONTWRITEBYTECODE": "1",
    })

    proxy_dir = str((ROOT.resolve() / "proxy").resolve())
    inherited_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [proxy_dir] + ([inherited_pythonpath] if inherited_pythonpath else [])
    )
    return env


def stop_children(running: dict[str, Running]) -> None:
    """Reap interrupted suites, including their socket/worker subprocesses."""
    for item in running.values():
        if os.name == "posix":
            try:
                os.killpg(item.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif item.process.poll() is None:
            item.process.terminate()
    deadline = time.monotonic() + 5
    for item in running.values():
        try:
            item.process.wait(timeout=max(0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            pass
    # The pytest leader may exit before one of its workers. Kill the whole group,
    # not just leaders whose poll() is still None.
    for item in running.values():
        if os.name == "posix":
            try:
                os.killpg(item.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif item.process.poll() is None:
            item.process.kill()
        item.process.wait()
        item.log.close()


def run_phase(
    suites: dict[str, tuple[str, ...]], jobs: int, work: Path, coverage: bool,
) -> dict[str, int]:
    pending = iter(suites.items())
    running: dict[str, Running] = {}
    results: dict[str, int] = {}
    exhausted = False
    try:
        while running or not exhausted:
            while not exhausted and len(running) < jobs:
                entry = next(pending, None)
                if entry is None:
                    exhausted = True
                    break
                name, paths = entry
                env = suite_environment(name, work)
                # Each process owns its cache and coverage output. tmp_path uses
                # pytest's unique per-session base (short enough for Unix sockets).
                env["COVERAGE_FILE"] = str(work / "coverage" / f".coverage.{name}")
                command = pytest_command(paths, coverage)
                command.extend(["-o", f"cache_dir={work / name / 'cache'}"])
                log = (work / f"{name}.log").open("w+b")
                print(f"=== {name}: starting ===", flush=True)
                try:
                    process = subprocess.Popen(
                        command, cwd=ROOT, env=env, stdout=log,
                        stderr=subprocess.STDOUT, start_new_session=(os.name == "posix"),
                    )
                except BaseException:
                    log.close()
                    raise
                running[name] = Running(process, log, time.monotonic())
            for name, item in list(running.items()):
                code = item.process.poll()
                if code is None:
                    continue
                elapsed = time.monotonic() - item.started
                item.log.seek(0)
                print_child_log(item.log.read().decode("utf-8", errors="replace"))
                item.log.close()
                print(f"=== {name}: exit {code}, {elapsed:.2f}s ===", flush=True)
                results[name] = code
                del running[name]
            if running:
                time.sleep(0.05)
    finally:
        stop_children(running)
    return results


def coverage_report(work: Path) -> int:
    env = os.environ.copy()
    env["COVERAGE_FILE"] = str(work / ".coverage")
    combined = subprocess.run(
        [sys.executable, "-m", "coverage", "combine", str(work / "coverage")],
        cwd=ROOT, env=env,
    )
    if combined.returncode:
        return combined.returncode
    return subprocess.run(
        [sys.executable, "-m", "coverage", "report"], cwd=ROOT, env=env,
    ).returncode


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, choices=(1, 2, 3), default=2,
                        help="parallel service interpreters (default: 2; 1 disables overlap)")
    parser.add_argument("--suite", action="append", choices=tuple(SUITES),
                        help="select suites; repeat to select more (default: all)")
    parser.add_argument("--coverage", action="store_true",
                        help="combine per-process coverage; requires pytest-cov")
    return parser.parse_args(argv)


def interrupt(signum: int, frame: object) -> None:
    # CI cancellation sends TERM; unwind run_phase so process groups are reaped.
    raise KeyboardInterrupt


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    selected = set(args.suite or SUITES)
    started = time.monotonic()
    results: dict[str, int] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="fn-tests-") as directory:
            work = Path(directory)
            (work / "coverage").mkdir()
            for phase in PHASES:
                suites = {name: paths for name, paths in phase.items() if name in selected}
                results.update(run_phase(suites, args.jobs, work, args.coverage))
            report_code = coverage_report(work) if args.coverage else 0
    except KeyboardInterrupt:
        print("\nInterrupted; test process groups stopped.", file=sys.stderr)
        return 130
    failed = [name for name, code in results.items() if code]
    print(f"=== Total: {len(results)} suites, {len(failed)} failed, "
          f"{time.monotonic() - started:.2f}s ===", flush=True)
    if failed:
        print("Failed suites: " + ", ".join(failed), file=sys.stderr)
    return int(bool(failed or report_code))


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, interrupt)
    raise SystemExit(main())
