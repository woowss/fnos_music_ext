#!/usr/bin/env python3
"""One-shot native smoke probe for the scanner candidate (SC-1/SC-2).

Boundary statement
------------------
This probe collects *smoke* evidence only. It is not acceptance evidence, not a
deployment authorization, and it never changes the product gates. The candidate
module's two capability gates stay ``False`` in the repository file; the probe
patch is applied **in memory only** inside a throwaway harness process, exactly
as the synthetic tests do. The scanner is still not wired into any product
route or write path.

What it does (must run as root on the target fnOS host)
------------------------------------------------------
1. Refuses to run if the probe root already exists. The probe root must be
   ``/vol1/1000/fnm-scanner-native-*``.
2. Creates one disposable Btrfs probe tree:

       <probe>/src/                 subvolume, chown <user>:<group>
       <probe>/src/lib/track.flac   created by <user> (so O_NOATIME is allowed)
       <probe>/src/sibling.flac     same bytes, outside the tested subtree
       <probe>/snap/                real read-only Btrfs snapshot of src
       <probe>/src2/                subvolume owned by root
       <probe>/src2/lib2/           chown <user>:<group>
       <probe>/src2/lib2/rootfile.bin  root-owned file (O_NOATIME denial)
       <probe>/snap2/               read-only snapshot of src2

3. Copies ``scripts/verify_position_file_evidence.py`` and a generated harness
   into an isolated ``/tmp`` staging directory.
4. Runs the harness as the unprivileged user (``runuser``) for these scenarios:

   A  root=<probe>/snap         btrfs-ro-snapshot, scan the snapshot subvolume
   B  root=<probe>/snap/lib     btrfs-ro-snapshot, scan a subtree inside it
   C  root=<probe>/src          btrfs-ro-snapshot, writable subvolume (negative)
   D  root=<probe>/snap2/lib2   btrfs-ro-snapshot, root-owned file (negative)
   E  root=<probe>/snap/lib     independent-copy profile (no RO pretence)
   F  CLI default profile, root=<probe>/snap (product gate must still block)

   For every scenario it records the real GET_SUBVOL_INFO identity (tree id,
   generation, flags, RO bit, UUID/parent UUID), the scanner JSON report, the
   real syncfs/sync barrier generations, the read mode, observed file stats
   (atime/mtime/ctime) before and after, capabilities of the scanning process,
   and the exit code.
5. Always cleans up: deletes the four subvolumes, removes the probe root and
   the /tmp staging directory, then verifies that ``/vol1`` has no related
   subvolume or path residue, that ``/vol1/1000`` top-level entries are
   unchanged, and that the fnmusic-ext service MainPID/NRestarts are unchanged.
6. Prints one JSON document to stdout (raw output hash is reported by the
   caller). Exit code 0 only when every assertion passes and cleanup is
   verified, 1 otherwise.

Usage
-----
    python3 -B scripts/scanner_native_smoke_probe.py \
        --verifier scripts/verify_position_file_evidence.py \
        --probe-root /vol1/1000/fnm-scanner-native-20261107 \
        --user kurumi

The script refuses unsafe probe roots, unknown fstypes, existing targets and
non-root execution. It only writes the disposable probe tree and /tmp.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any


PROBE_PARENT = "/vol1/1000"
PROBE_PREFIX = "fnm-scanner-native-"
DEFAULT_PROBE_ROOT = PROBE_PARENT + "/" + PROBE_PREFIX + "20261107"
DEFAULT_USER = "kurumi"
TRACK_BYTES = b"native smoke candidate audio bytes"
ROOT_BYTES = b"root owned file for noatime denial"
DEFAULT_PROFILE = "btrfs-ro-snapshot"
HARNESS_NAME = "scanner_native_harness.py"
VERIFIER_COPY_NAME = "verify_position_file_evidence.py"
PATH_ENV = {
    "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "PYTHONDONTWRITEBYTECODE": "1",
}


HARNESS_SOURCE = r'''#!/usr/bin/env python3
"""In-memory harness: patch the two capability gates, then call scan_tree.

The patch exists only inside this process. The module file is never written.
Prints one JSON document to stdout; exit code mirrors the CLI contract
(0 complete, 2 incomplete).
"""
import argparse
import hashlib
import importlib.util
import json
import os
import stat
import sys


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def process_identity():
    out = {"euid": os.geteuid(), "egid": os.getegid()}
    caps = {}
    try:
        with open("/proc/self/status", "r", encoding="ascii") as stream:
            for line in stream:
                key, separator, value = line.partition(":")
                if separator and key in {"CapEff", "CapPrm", "Uid", "Gid"}:
                    caps[key] = value.strip()
    except OSError:
        caps["unavailable"] = True
    out["caps"] = caps
    return out


def record_stat(path):
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        return {"path": path, "error": "errno=%s" % exc.errno}
    return {
        "path": path,
        "ino": info.st_ino,
        "size": info.st_size,
        "mode": stat.S_IMODE(info.st_mode),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "atime_ns": info.st_atime_ns,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def load_module(path):
    name = "verify_position_file_evidence_smoke"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def view_identity(module, root):
    """Read the containing subvolume identity straight from the real ioctl."""
    result = {"root": root, "method": "BTRFS_IOC_GET_SUBVOL_INFO via module functions"}
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as exc:
        result["open_error_errno"] = exc.errno
        return result
    try:
        try:
            info = module._read_btrfs_subvol_info(fd)
        except OSError as exc:
            result["ioctl_error_errno"] = exc.errno
            return result
        treeid, generation, flags, uuid, parent_uuid = info
        result.update({
            "treeid": treeid,
            "generation": generation,
            "flags": flags,
            "ro_bit0": bool(flags & module._BTRFS_ROOT_SUBVOL_RDONLY),
            "uuid_nonzero": any(uuid),
            "parent_uuid_nonzero": any(parent_uuid),
        })
        try:
            module._require_readonly_snapshot(fd)
            result["readonly_snapshot_accepted"] = True
        except OSError as exc:
            result["readonly_snapshot_accepted"] = False
            result["readonly_snapshot_error_errno"] = exc.errno
    finally:
        os.close(fd)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--module", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("--positions", required=True)
    parser.add_argument("--profile", default="btrfs-ro-snapshot")
    parser.add_argument("--observe-file", default=None)
    args = parser.parse_args()

    result = {
        "module_path": os.path.abspath(args.module),
        "module_sha256": sha256_file(args.module),
        "python": sys.version.split()[0],
        "process": process_identity(),
        "profile": args.profile,
        "root": args.root,
        "positions_sha256": hashlib.sha256(args.positions.encode("utf-8")).hexdigest(),
    }
    module = load_module(args.module)
    result["gates_original"] = {
        "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": module._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED,
        "_POINT_IN_TIME_SCAN_VALIDATED": module._POINT_IN_TIME_SCAN_VALIDATED,
        "_INDEPENDENT_COPY_VIEW_VALIDATED": module._INDEPENDENT_COPY_VIEW_VALIDATED,
    }
    module._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED = True
    module._POINT_IN_TIME_SCAN_VALIDATED = True
    result["gates_after_patch"] = {
        "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": module._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED,
        "_POINT_IN_TIME_SCAN_VALIDATED": module._POINT_IN_TIME_SCAN_VALIDATED,
        "_INDEPENDENT_COPY_VIEW_VALIDATED": module._INDEPENDENT_COPY_VIEW_VALIDATED,
    }
    result["view"] = view_identity(module, args.root)

    observed = {"path": args.observe_file, "before": None, "after": None}
    if args.observe_file:
        observed["before"] = record_stat(args.observe_file)
    report = None
    scan_error = None
    try:
        report = module.scan_tree(
            args.root,
            json.loads(args.positions),
            evidence_profile=args.profile,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed contract should make this unreachable
        scan_error = "%s: %s" % (type(exc).__name__, exc)
    if args.observe_file:
        observed["after"] = record_stat(args.observe_file)
    result["observed_file"] = observed
    result["scan_error"] = scan_error
    result["report"] = report
    result["complete"] = bool(report and report.get("complete") is True)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


class ProbeError(RuntimeError):
    pass


def _clip(text: str, limit: int = 1200) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "...[clipped]"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tool(*names: str) -> str:
    for name in names:
        if os.path.isabs(name) and os.access(name, os.X_OK):
            return name
        candidate = shutil.which(name, path=PATH_ENV["PATH"])
        if candidate:
            return candidate
    raise ProbeError("required tool not found: " + ", ".join(names))


def _run(argv: list[str], *, stdin: str | None = None, timeout: int = 180,
         clip: bool = True) -> dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            argv, input=stdin, capture_output=True, text=True,
            timeout=timeout, env=PATH_ENV,
        )
        return {
            "argv": list(argv),
            "returncode": proc.returncode,
            "stdout": _clip(proc.stdout) if clip else proc.stdout,
            "stderr": _clip(proc.stderr),
            "seconds": round(time.time() - started, 3),
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "argv": list(argv),
            "returncode": None,
            "stdout": "",
            "stderr": "%s: %s" % (type(exc).__name__, exc),
            "seconds": round(time.time() - started, 3),
        }


def _require_ok(record: dict[str, Any], what: str) -> dict[str, Any]:
    if record.get("returncode") != 0:
        raise ProbeError("%s failed: %s | stderr=%s"
                         % (what, record.get("stdout"), record.get("stderr")))
    return record


def _runuser_argv(user: str) -> list[str]:
    return [_tool("/usr/sbin/runuser", "/sbin/runuser", "runuser"), "-u", user, "--"]


def _read_file(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            return stream.read().strip()
    except OSError:
        return None


def _mount_info(target: str) -> dict[str, Any]:
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8") as stream:
            for line in stream:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == target:
                    return {"target": target, "fstype": parts[2], "options": parts[3]}
    except OSError:
        pass
    return {"target": target, "fstype": None, "options": None}


def _top_level(target: str) -> dict[str, Any]:
    names = sorted(os.listdir(target))
    return {"count": len(names), "names_sha256": _sha256_bytes("\n".join(names).encode("utf-8"))}


def _service_state() -> dict[str, Any]:
    state = {"is_active": None, "main_pid": None, "n_restarts": None}
    systemctl = shutil.which("systemctl", path=PATH_ENV["PATH"])
    if not systemctl:
        return state
    active = _run([systemctl, "is-active", "fnmusic-ext"], timeout=30)
    state["is_active"] = active["stdout"] or active["stderr"]
    show = _run([systemctl, "show", "fnmusic-ext", "-p", "MainPID", "-p", "NRestarts",
                 "--no-pager"], timeout=30)
    for line in show.get("stdout", "").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "MainPID":
            state["main_pid"] = value.strip()
        elif key == "NRestarts":
            state["n_restarts"] = value.strip()
    return state


def _subvolume_list(btrfs: str) -> dict[str, Any]:
    record = _run([btrfs, "subvolume", "list", "/vol1"], timeout=120, clip=False)
    output = record.get("stdout", "") or ""
    lines = [line for line in output.splitlines() if line.strip()]
    return {
        "returncode": record.get("returncode"),
        "line_count": len(lines),
        "lines_sha256": _sha256_bytes("\n".join(lines).encode("utf-8")),
        "stderr": _clip(record.get("stderr") or ""),
        "sample": lines[:3] + (["..."] if len(lines) > 6 else []) + lines[-3:],
    }


def _delete_subvolume(btrfs: str, path: str, commands: list[dict[str, Any]]) -> bool:
    record = _run([btrfs, "subvolume", "delete", path], timeout=120)
    commands.append(record)
    if record.get("returncode") == 0:
        return True
    # Read-only snapshots may need the ro property cleared before deletion.
    commands.append(_run([btrfs, "property", "set", "-t", "subvol", path, "ro", "false"],
                         timeout=120))
    retry = _run([btrfs, "subvolume", "delete", path], timeout=120)
    commands.append(retry)
    return retry.get("returncode") == 0


def _cleanup(probe_root: str, created_subvolumes: list[str], staging: str,
             btrfs: str, baseline: dict[str, Any], commands: list[dict[str, Any]],
             created_probe_root: bool) -> dict[str, Any]:
    cleanup: dict[str, Any] = {"subvolumes": {}, "probe_root_removed": None,
                               "staging_removed": None, "subvolume_list_clean": None,
                               "related_subvolumes_after": [], "subvolume_count": None,
                               "top_level": None,
                               "service": _service_state(),
                               "mount": _mount_info("/vol1")}
    for path in reversed(created_subvolumes):
        cleanup["subvolumes"][path] = _delete_subvolume(btrfs, path, commands)
    try:
        if created_probe_root and os.path.isdir(probe_root) and not os.path.islink(probe_root):
            os.rmdir(probe_root)
        cleanup["probe_root_removed"] = not os.path.lexists(probe_root)
    except OSError as exc:
        cleanup["probe_root_removed"] = False
        cleanup["probe_root_error"] = "errno=%s" % exc.errno
    if staging:
        shutil.rmtree(staging, ignore_errors=True)
        cleanup["staging_removed"] = not os.path.exists(staging)
    after_list = _run([btrfs, "subvolume", "list", "/vol1"], timeout=120, clip=False)
    after_text = after_list.get("stdout", "") or ""
    related = [line.strip() for line in after_text.splitlines() if PROBE_PREFIX in line]
    cleanup["related_subvolumes_after"] = related
    cleanup["subvolume_list_clean"] = not related
    cleanup["subvolume_count"] = {
        "baseline": (baseline.get("subvolume_list") or {}).get("line_count"),
        "after": len([line for line in after_text.splitlines() if line.strip()]),
        "unchanged": (baseline.get("subvolume_list") or {}).get("line_count")
        == len([line for line in after_text.splitlines() if line.strip()]),
    }
    top_level = _top_level(PROBE_PARENT)
    baseline_top = baseline.get("top_level") or {}
    top_level["unchanged"] = (
        top_level["names_sha256"] == baseline_top.get("names_sha256")
    )
    cleanup["top_level"] = top_level
    baseline_service = baseline.get("service") or {}
    cleanup["service_unchanged"] = (
        baseline_service.get("is_active") is not None
        and cleanup["service"]["is_active"] == baseline_service.get("is_active")
        and cleanup["service"]["main_pid"] == baseline_service.get("main_pid")
        and cleanup["service"]["n_restarts"] == baseline_service.get("n_restarts")
    )
    cleanup["mount_unchanged"] = cleanup["mount"] == baseline.get("mount")
    cleanup["ok"] = bool(
        all(cleanup["subvolumes"].values())
        and cleanup["probe_root_removed"]
        and cleanup["staging_removed"]
        and cleanup["subvolume_list_clean"]
        and cleanup["top_level"]["unchanged"]
        and cleanup["service_unchanged"]
        and cleanup["mount_unchanged"]
    )
    return cleanup


def _harness_run(key: str, *, runuser: list[str], python: str, harness: str,
                 module: str, root: str, positions: list[dict[str, Any]],
                 profile: str, observe_file: str | None,
                 commands: list[dict[str, Any]]) -> dict[str, Any]:
    argv = runuser + [python, "-B", harness, "--module", module, "--root", root,
                      "--positions", json.dumps(positions, separators=(",", ":")),
                      "--profile", profile]
    if observe_file:
        argv += ["--observe-file", observe_file]
    record = _run(argv, timeout=300, clip=False)
    raw_stdout = record.get("stdout") or ""
    parsed = None
    parse_error = None
    try:
        parsed = json.loads(raw_stdout)
    except ValueError as exc:
        parse_error = str(exc)
    entry = {
        "key": key,
        "argv": argv,
        "timeout_seconds": 300,
        "returncode": record.get("returncode"),
        "stdout_sha256": _sha256_bytes(raw_stdout.encode("utf-8")),
        "stdout": raw_stdout,
        "stderr": _clip(record.get("stderr") or ""),
        "seconds": record.get("seconds"),
        "parsed": parsed,
        "parse_error": parse_error,
    }
    commands.append({
        "argv": argv,
        "returncode": record.get("returncode"),
        "stdout": "[harness JSON omitted; see runs.%s]" % key,
        "stderr": _clip(record.get("stderr") or ""),
        "seconds": record.get("seconds"),
    })
    return entry


def _cli_run(*, runuser: list[str], python: str, module: str, root: str,
             positions: list[dict[str, Any]],
             commands: list[dict[str, Any]]) -> dict[str, Any]:
    argv = runuser + [python, "-B", module, "--root", root]
    record = _run(argv, stdin=json.dumps({"positions": positions}),
                  timeout=180, clip=False)
    raw_stdout = record.get("stdout") or ""
    parsed = None
    try:
        parsed = json.loads(raw_stdout)
    except ValueError:
        pass
    commands.append({
        "argv": argv, "returncode": record.get("returncode"),
        "stdout": _clip(raw_stdout), "stderr": _clip(record.get("stderr") or ""),
        "seconds": record.get("seconds"),
    })
    return {
        "argv": argv,
        "returncode": record.get("returncode"),
        "stdout_sha256": _sha256_bytes(raw_stdout.encode("utf-8")),
        "stdout": raw_stdout,
        "stderr": _clip(record.get("stderr") or ""),
        "parsed": parsed,
    }


def _check(checks: list[dict[str, Any]], check_id: str, condition: bool,
           detail: str) -> bool:
    checks.append({"id": check_id, "pass": bool(condition), "detail": detail})
    return bool(condition)


def _run_assertions(runs: dict[str, Any], checks: list[dict[str, Any]],
                    uid: int) -> None:
    def harness(key: str) -> dict[str, Any]:
        entry = runs.get(key) or {}
        return entry.get("parsed") or {}

    a = harness("A_subvolume_root")
    b = harness("B_subtree")
    c = harness("C_writable_subvolume")
    d = harness("D_root_owned_file")
    e = harness("E_independent_copy")

    def report(h: dict[str, Any]) -> dict[str, Any]:
        return h.get("report") or {}

    a_view = a.get("view") or {}
    b_view = b.get("view") or {}
    c_view = c.get("view") or {}

    _check(checks, "GATE-1",
           a.get("gates_original") == {
               "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": False,
               "_POINT_IN_TIME_SCAN_VALIDATED": False,
               "_INDEPENDENT_COPY_VIEW_VALIDATED": False,
           },
           "harness loaded module with all three gates False: %s" % (a.get("gates_original"),))
    _check(checks, "GATE-2",
           a.get("gates_after_patch") == {
               "_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED": True,
               "_POINT_IN_TIME_SCAN_VALIDATED": True,
               "_INDEPENDENT_COPY_VIEW_VALIDATED": False,
           },
           "only the two capability gates were patched in memory: %s"
           % (a.get("gates_after_patch"),))
    process = a.get("process") or {}
    caps = process.get("caps") or {}
    _check(checks, "PRIV-1",
           process.get("euid") == uid
           and caps.get("CapEff") == "0000000000000000"
           and caps.get("CapPrm") == "0000000000000000",
           "harness ran as euid=%s CapEff=%s CapPrm=%s (expected euid=%s, all caps 0)"
           % (process.get("euid"), caps.get("CapEff"), caps.get("CapPrm"), uid))

    _check(checks, "VIEW-A1",
           a_view.get("readonly_snapshot_accepted") is True
           and a_view.get("ro_bit0") is True
           and a_view.get("uuid_nonzero") is True
           and a_view.get("parent_uuid_nonzero") is True
           and (a_view.get("treeid") or 0) > 0,
           "snap GET_SUBVOL_INFO treeid=%s gen=%s flags=%s ro_bit0=%s uuid=%s parent=%s"
           % (a_view.get("treeid"), a_view.get("generation"), a_view.get("flags"),
              a_view.get("ro_bit0"), a_view.get("uuid_nonzero"),
              a_view.get("parent_uuid_nonzero")))
    _check(checks, "VIEW-B1",
           b_view.get("readonly_snapshot_accepted") is True
           and b_view.get("treeid") == a_view.get("treeid"),
           "subtree root resolves to the same snapshot treeid %s (accepted=%s)"
           % (b_view.get("treeid"), b_view.get("readonly_snapshot_accepted")))

    ar = report(a)
    _check(checks, "A1", ar.get("complete") is True and ar.get("unknown_reason") is None,
           "snapshot subvolume scan complete=%s unknown_reason=%s"
           % (ar.get("complete"), ar.get("unknown_reason")))
    _check(checks, "A2", ar.get("read_mode") == "o_noatime",
           "read_mode=%s" % (ar.get("read_mode"),))
    _check(checks, "A3", ar.get("root_scope") == "subvolume_root",
           "root_scope=%s" % (ar.get("root_scope"),))
    barrier = ar.get("sync_barrier") or {}
    _check(checks, "A4",
           barrier.get("pre") == "done" and barrier.get("post") == "done"
           and barrier.get("mechanism") in ("syncfs", "sync"),
           "real barrier pre=%s post=%s mechanism=%s"
           % (barrier.get("pre"), barrier.get("post"), barrier.get("mechanism")))
    _check(checks, "A5",
           barrier.get("generation_start") is not None
           and barrier.get("generation_start") == barrier.get("generation_before_post_sync")
           == barrier.get("generation_end"),
           "generations before_pre=%s start=%s before_post=%s end=%s"
           % (barrier.get("generation_before_pre_sync"), barrier.get("generation_start"),
              barrier.get("generation_before_post_sync"), barrier.get("generation_end")))
    positions_a = ar.get("positions") or [{}]
    _check(checks, "A6",
           positions_a[0].get("status") == "ambiguous"
           and positions_a[0].get("match_count") == 2
           and (ar.get("limits") or {}).get("entries_seen") == 3,
           "whole snapshot scan sees lib + sibling: status=%s match_count=%s entries_seen=%s"
           % (positions_a[0].get("status"), positions_a[0].get("match_count"),
              (ar.get("limits") or {}).get("entries_seen")))
    observed = (a.get("observed_file") or {})
    before = observed.get("before") or {}
    after = observed.get("after") or {}
    stat_keys = ("ino", "size", "mode", "uid", "gid", "atime_ns", "mtime_ns", "ctime_ns")
    _check(checks, "A7",
           bool(before) and all(before.get(key) == after.get(key) for key in stat_keys),
           "observed file stats unchanged across the scan (atime/mtime/ctime/ino/size)")

    br = report(b)
    _check(checks, "B1", br.get("complete") is True and br.get("unknown_reason") is None,
           "subtree scan complete=%s unknown_reason=%s"
           % (br.get("complete"), br.get("unknown_reason")))
    _check(checks, "B2", br.get("read_mode") == "o_noatime",
           "read_mode=%s" % (br.get("read_mode"),))
    _check(checks, "B3", br.get("root_scope") == "subtree",
           "root_scope=%s" % (br.get("root_scope"),))
    positions_b = br.get("positions") or [{}]
    _check(checks, "B4",
           positions_b[0].get("status") == "unique_match"
           and positions_b[0].get("match_count") == 1,
           "subtree scan excludes the same-bytes sibling: status=%s match_count=%s"
           % (positions_b[0].get("status"), positions_b[0].get("match_count")))
    _check(checks, "B5", (br.get("limits") or {}).get("entries_seen") == 1,
           "subtree entries_seen=%s (lib contains only track.flac)"
           % ((br.get("limits") or {}).get("entries_seen"),))
    b_barrier = br.get("sync_barrier") or {}
    _check(checks, "B6",
           b_barrier.get("pre") == "done" and b_barrier.get("post") == "done"
           and b_barrier.get("generation_start") == b_barrier.get("generation_end"),
           "subtree barrier pre=%s post=%s start=%s end=%s"
           % (b_barrier.get("pre"), b_barrier.get("post"),
              b_barrier.get("generation_start"), b_barrier.get("generation_end")))
    b_observed = (b.get("observed_file") or {})
    b_before = b_observed.get("before") or {}
    b_after = b_observed.get("after") or {}
    _check(checks, "B7",
           bool(b_before) and all(b_before.get(key) == b_after.get(key) for key in stat_keys),
           "observed file stats unchanged across the subtree scan")

    cr = report(c)
    _check(checks, "C1",
           cr.get("complete") is False
           and cr.get("unknown_reason") == "ro_semantics_unavailable"
           and c_view.get("readonly_snapshot_accepted") is False,
           "writable subvolume rejected: complete=%s reason=%s readonly_accepted=%s"
           % (cr.get("complete"), cr.get("unknown_reason"),
              c_view.get("readonly_snapshot_accepted")))

    dr = report(d)
    _check(checks, "D1",
           dr.get("complete") is False
           and dr.get("unknown_reason") == "noatime_permission_denied"
           and dr.get("read_mode") == "o_noatime_denied",
           "root-owned file refused O_NOATIME: complete=%s reason=%s read_mode=%s"
           % (dr.get("complete"), dr.get("unknown_reason"), dr.get("read_mode")))

    er = report(e)
    e_checks = er.get("checks") or {}
    e_barrier = er.get("sync_barrier") or {}
    _check(checks, "E1",
           er.get("complete") is False
           and er.get("unknown_reason") == "ro_semantics_unavailable"
           and e_checks.get("ro_semantics") == "unavailable"
           and e_checks.get("generation_bracket") == "not_applicable"
           and e_checks.get("content_hash") == "performed"
           and e_barrier.get("pre") == "done" and e_barrier.get("post") == "done"
           and (e.get("complete") is False),
           "independent-copy scans with real barrier/hash but never claims complete: "
           "complete=%s reason=%s ro=%s bracket=%s hash=%s barrier=%s/%s"
           % (er.get("complete"), er.get("unknown_reason"), e_checks.get("ro_semantics"),
              e_checks.get("generation_bracket"), e_checks.get("content_hash"),
              e_barrier.get("pre"), e_barrier.get("post")))

    f = runs.get("F_cli_default") or {}
    f_parsed = f.get("parsed") or {}
    _check(checks, "F1",
           f.get("returncode") == 2
           and f_parsed == {"scope": "read_only_test_tree", "complete": False,
                            "positions": [{"position": 1, "status": "unknown"}]},
           "default CLI (product gates False) returned legacy unknown JSON and exit 2: %s"
           % (f.get("returncode"),))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verifier", default="scripts/verify_position_file_evidence.py")
    parser.add_argument("--probe-root", default=DEFAULT_PROBE_ROOT)
    parser.add_argument("--user", default=DEFAULT_USER)
    args = parser.parse_args(argv)

    probe_root = os.path.abspath(args.probe_root)
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary: dict[str, Any] = {
        "probe": "scanner-native-smoke",
        "schema": 1,
        "started_at": started,
        "probe_root": probe_root,
        "user": args.user,
        "environment": {},
        "hashes": {},
        "baseline": {},
        "commands": [],
        "runs": {},
        "checks": [],
        "cleanup": {},
        "result": "FAIL",
        "failure_reasons": [],
    }
    commands: list[dict[str, Any]] = summary["commands"]
    checks: list[dict[str, Any]] = summary["checks"]
    staging = ""
    created_subvolumes: list[str] = []
    created_probe_root = False
    baseline: dict[str, Any] = {}
    try:
        # ------------------------------ preconditions -----------------------
        if os.geteuid() != 0:
            raise ProbeError("the probe must run as root (setup/cleanup use subvolumes)")
        if os.path.dirname(probe_root) != PROBE_PARENT or not os.path.basename(probe_root).startswith(PROBE_PREFIX):
            raise ProbeError("probe root must be %s/%s* (got %s)"
                             % (PROBE_PARENT, PROBE_PREFIX, probe_root))
        if os.path.lexists(probe_root):
            raise ProbeError("probe root already exists, refusing to reuse it: %s" % probe_root)
        verifier = os.path.abspath(args.verifier)
        if not os.path.isfile(verifier):
            raise ProbeError("verifier not found: %s" % verifier)
        btrfs = _tool("/usr/bin/btrfs", "btrfs")
        runuser = _runuser_argv(args.user)
        python = _tool("/usr/bin/python3", "python3")
        id_info = _require_ok(_run([_tool("/usr/bin/id", "id"), "-u", args.user]),
                              "id -u %s" % args.user)
        uid = int(id_info["stdout"].strip())
        if uid == 0:
            raise ProbeError("scanning user must be unprivileged: %s has uid 0" % args.user)
        gid_info = _require_ok(_run([_tool("/usr/bin/id", "id"), "-gn", args.user]),
                               "id -gn %s" % args.user)
        group = gid_info["stdout"].strip()
        mount = _mount_info("/vol1")
        if mount.get("fstype") != "btrfs":
            raise ProbeError("/vol1 is not btrfs: %s" % mount)
        summary["environment"] = {
            "hostname": socket.gethostname(),
            "kernel": os.uname().release,
            "machine": os.uname().machine,
            "python": sys.version,
            "script_euid": os.geteuid(),
            "fnos_release": _read_file("/etc/fnos-release"),
            "os_release": _read_file("/etc/os-release"),
            "mount_vol1": mount,
            "btrfs_version": _run([btrfs, "--version"], timeout=30).get("stdout", ""),
            "user": {"name": args.user, "uid": uid, "group": group},
            "timezone": time.strftime("%z"),
        }
        summary["hashes"]["probe_script_sha256"] = _sha256_file(os.path.abspath(__file__))
        summary["hashes"]["verifier_source_sha256"] = _sha256_file(verifier)

        # ------------------------------- baseline ----------------------------
        top_level = _top_level(PROBE_PARENT)
        subvolumes = _subvolume_list(btrfs)
        baseline = {
            "top_level": top_level,
            "subvolume_list": subvolumes,
            "subvolume_list_text": "",
            "service": _service_state(),
            "mount": mount,
        }
        subvol_full = _run([btrfs, "subvolume", "list", "/vol1"], timeout=120, clip=False)
        baseline["subvolume_list_text"] = subvol_full.get("stdout", "") or ""
        if PROBE_PREFIX in baseline["subvolume_list_text"]:
            raise ProbeError("pre-existing %s* subvolume present; refusing to run" % PROBE_PREFIX)
        summary["baseline"] = {
            "top_level": top_level,
            "subvolume_list": subvolumes,
            "service": baseline["service"],
            "mount": mount,
        }

        # ------------------------------ probe tree ---------------------------
        os.mkdir(probe_root, 0o755)
        created_probe_root = True
        src = os.path.join(probe_root, "src")
        snap = os.path.join(probe_root, "snap")
        src2 = os.path.join(probe_root, "src2")
        snap2 = os.path.join(probe_root, "snap2")
        _require_ok(_run([btrfs, "subvolume", "create", src]), "create src subvolume")
        created_subvolumes.append(src)
        _require_ok(_run([_tool("/usr/bin/chown", "chown"), "%s:%s" % (args.user, group), src]),
                    "chown src to scanning user")
        shell = (
            "set -eu; cd %s; mkdir -p lib; "
            "printf '%%s' '%s' > lib/track.flac; "
            "printf '%%s' '%s' > sibling.flac; sync"
            % (json.dumps(src), TRACK_BYTES.decode("ascii"), TRACK_BYTES.decode("ascii"))
        )
        _require_ok(_run(runuser + ["/bin/sh", "-c", shell]), "create user-owned files")
        _require_ok(_run([btrfs, "subvolume", "snapshot", "-r", src, snap]),
                    "create read-only snapshot snap")
        created_subvolumes.append(snap)

        _require_ok(_run([btrfs, "subvolume", "create", src2]), "create src2 subvolume")
        created_subvolumes.append(src2)
        lib2 = os.path.join(src2, "lib2")
        os.mkdir(lib2, 0o755)
        _require_ok(_run([_tool("/usr/bin/chown", "chown"), "%s:%s" % (args.user, group), lib2]),
                    "chown lib2 to scanning user")
        with open(os.path.join(lib2, "rootfile.bin"), "wb") as stream:
            stream.write(ROOT_BYTES)
        os.chmod(os.path.join(lib2, "rootfile.bin"), 0o644)
        _require_ok(_run([btrfs, "subvolume", "snapshot", "-r", src2, snap2]),
                    "create read-only snapshot snap2")
        created_subvolumes.append(snap2)

        summary["probe_tree"] = {
            "root": probe_root,
            "src_subvolume": src,
            "snapshot": snap,
            "snapshot_subtree_root": os.path.join(snap, "lib"),
            "sibling_outside_subtree": os.path.join(snap, "sibling.flac"),
            "src2_subvolume": src2,
            "snapshot2": snap2,
            "root_owned_file": os.path.join(snap2, "lib2", "rootfile.bin"),
        }
        for path in (src, snap, src2, snap2):
            commands.append(_run([btrfs, "subvolume", "show", path], timeout=60))

        track_sha = _sha256_bytes(TRACK_BYTES)
        root_sha = _sha256_bytes(ROOT_BYTES)
        positions_track = [{"position": 1, "published_sha256": track_sha,
                            "published_size": len(TRACK_BYTES)}]
        positions_root = [{"position": 1, "published_sha256": root_sha,
                           "published_size": len(ROOT_BYTES)}]
        summary["published_positions"] = {
            "track": {"sha256": track_sha, "size": len(TRACK_BYTES)},
            "root_owned": {"sha256": root_sha, "size": len(ROOT_BYTES)},
        }

        # --------------------------- /tmp staging ----------------------------
        staging = tempfile.mkdtemp(prefix="fnm-scanner-native-smoke-", dir="/tmp")
        os.chmod(staging, 0o755)
        verifier_copy = os.path.join(staging, VERIFIER_COPY_NAME)
        shutil.copyfile(verifier, verifier_copy)
        os.chmod(verifier_copy, 0o644)
        harness = os.path.join(staging, HARNESS_NAME)
        with open(harness, "w", encoding="utf-8") as stream:
            stream.write(HARNESS_SOURCE)
        os.chmod(harness, 0o644)
        summary["hashes"]["staged_verifier_sha256"] = _sha256_file(verifier_copy)
        summary["hashes"]["harness_source_sha256"] = _sha256_bytes(
            HARNESS_SOURCE.encode("utf-8"))
        summary["staging_dir"] = staging

        # ------------------------------- runs --------------------------------
        def run(key: str, root: str, positions: list[dict[str, Any]],
                profile: str = DEFAULT_PROFILE,
                observe_file: str | None = None) -> dict[str, Any]:
            entry = _harness_run(
                key, runuser=runuser, python=python, harness=harness,
                module=verifier_copy, root=root, positions=positions,
                profile=profile, observe_file=observe_file, commands=commands,
            )
            summary["runs"][key] = entry
            return entry

        run("A_subvolume_root", snap, positions_track,
            observe_file=os.path.join(snap, "lib", "track.flac"))
        run("B_subtree", os.path.join(snap, "lib"), positions_track,
            observe_file=os.path.join(snap, "lib", "track.flac"))
        run("C_writable_subvolume", src, positions_track)
        run("D_root_owned_file", os.path.join(snap2, "lib2"), positions_root,
            observe_file=os.path.join(snap2, "lib2", "rootfile.bin"))
        run("E_independent_copy", os.path.join(snap, "lib"), positions_track,
            profile="independent-copy",
            observe_file=os.path.join(snap, "lib", "track.flac"))
        summary["runs"]["F_cli_default"] = _cli_run(
            runuser=runuser, python=python, module=verifier_copy,
            root=snap, positions=positions_track, commands=commands,
        )

        summary["hashes"]["staged_verifier_after_sha256"] = _sha256_file(verifier_copy)
        summary["hashes"]["verifier_unchanged"] = (
            summary["hashes"]["staged_verifier_after_sha256"]
            == summary["hashes"]["verifier_source_sha256"]
        )

        # ----------------------------- assertions ----------------------------
        _run_assertions(summary["runs"], checks, uid)
        _check(checks, "HASH-1",
               summary["hashes"]["staged_verifier_after_sha256"]
               == summary["hashes"]["verifier_source_sha256"],
               "verifier module file was not modified by the in-memory harness: %s"
               % (summary["hashes"]["staged_verifier_after_sha256"],))
    except (ProbeError, OSError, ValueError) as exc:
        summary["failure_reasons"].append("%s: %s" % (type(exc).__name__, exc))
    finally:
        try:
            summary["cleanup"] = _cleanup(
                probe_root, created_subvolumes, staging,
                _tool("/usr/bin/btrfs", "btrfs"), baseline, commands,
                created_probe_root,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must never hide the result
            summary["cleanup"] = {"ok": False,
                                  "error": "%s: %s" % (type(exc).__name__, exc)}

    checks_ok = bool(checks) and all(item["pass"] for item in checks)
    cleanup_ok = bool((summary.get("cleanup") or {}).get("ok"))
    if not checks_ok:
        summary["failure_reasons"].append("one or more assertions failed")
    if not cleanup_ok:
        summary["failure_reasons"].append("cleanup verification failed")
    summary["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    summary["result"] = "PASS" if (checks_ok and cleanup_ok) else "FAIL"
    summary["counts"] = {
        "checks_total": len(checks),
        "checks_passed": sum(1 for item in checks if item["pass"]),
        "runs": len(summary["runs"]),
    }
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if summary["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
