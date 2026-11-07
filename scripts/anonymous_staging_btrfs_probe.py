#!/usr/bin/env python3
"""Evidence probe: anonymous O_TMPFILE staging + linkat(AT_EMPTY_PATH) on /vol1.

Probe-only. This script is not imported by the product, is not part of the
manual-save route, and does not change the production pipeline. It exists to
collect reproducible native evidence for the uncommitted candidate
``proxy/manual_save_anonymous.py`` on the real fnOS ``/vol1`` Btrfs filesystem
(mounted with the ``trimacl`` option).

Safety:
- The only writable locations are one newly created, explicitly recorded probe
  tree under ``/vol1/1000/fnmusic-btrfs-probe-20261107`` and ``/tmp``.
- The probe refuses to run if the probe tree already exists.
- Cleanup removes the whole probe tree and records the pre/post listing of
  ``/vol1/1000``.
- Production library, staging, ledger, services and containers are never
  touched.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time

PROBE_ROOT = "/vol1/1000/fnmusic-btrfs-probe-20261107"
DIRFLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
TARGET_ID = "target-1"
BIND_FILES = ("proxy/manual_save_anonymous.py", "proxy/manual_save_core.py")
PAYLOAD = b"fnmusic-synthetic-payload-0123456789"
PAYLOAD_SECOND = b"fnmusic-synthetic-second-payload-abcdef"
PUBLISHED_NAME = "probe-track-1.bin"


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_info(path: str) -> dict:
    try:
        info = os.stat(path)
    except OSError as exc:
        return {"path": path, "error": f"{type(exc).__name__} errno={exc.errno}"}
    return {
        "path": path,
        "mode": oct(stat.S_IMODE(info.st_mode)),
        "uid": info.st_uid,
        "gid": info.st_gid,
        "nlink": info.st_nlink,
        "dev": info.st_dev,
        "ino": info.st_ino,
        "size": info.st_size,
        "is_dir": stat.S_ISDIR(info.st_mode),
        "is_regular": stat.S_ISREG(info.st_mode),
    }


def entries(path: str):
    try:
        return sorted(os.listdir(path))
    except OSError as exc:
        return {"error": f"{type(exc).__name__} errno={exc.errno}"}


def caps() -> dict:
    out: dict = {}
    try:
        with open("/proc/self/status", "r", encoding="ascii") as stream:
            for line in stream:
                key, separator, value = line.partition(":")
                if separator and key in {"CapEff", "CapPrm", "Uid", "Gid", "Groups"}:
                    out[key] = value.strip()
    except OSError:
        out["unavailable"] = True
    return out


def vol1_mount() -> dict | None:
    try:
        with open("/proc/self/mounts", "r", encoding="utf-8") as stream:
            for line in stream:
                parts = line.split()
                if len(parts) >= 4 and parts[1] == "/vol1":
                    return {"source": parts[0], "target": parts[1], "fstype": parts[2], "options": parts[3]}
    except OSError:
        return None
    return None


def base_report(phase: str) -> dict:
    report = {
        "phase": phase,
        "time": now(),
        "euid": os.geteuid(),
        "egid": os.getegid(),
        "caps": caps(),
        "vol1_mount": vol1_mount(),
        "probe_root": PROBE_ROOT,
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }
    return report


def attempt(label: str, function) -> dict:
    entry: dict = {"label": label}
    try:
        value = function()
        entry["result"] = "ok"
        entry["value"] = value if isinstance(value, str) else str(value)
        return entry
    except Exception as exc:  # noqa: BLE001 - probe records the exact failure
        entry["result"] = "exception"
        entry["exc_type"] = type(exc).__name__
        entry["reason"] = getattr(exc, "reason", None)
        if isinstance(exc, OSError) and exc.errno is not None:
            entry["errno"] = exc.errno
            entry["errno_name"] = errno.errorcode.get(exc.errno)
        entry["str"] = str(exc)
        return entry


def raw_tmpfile_observation(staging_path: str) -> dict:
    """Observe the requested-mode behaviour of a raw O_TMPFILE inode."""
    result: dict = {"staging_path": staging_path}
    try:
        staging_fd = os.open(staging_path, DIRFLAGS)
    except OSError as exc:
        return {"error": f"open staging failed: {type(exc).__name__} errno={exc.errno}"}
    try:
        try:
            fd = os.open(
                ".",
                os.O_TMPFILE | os.O_DIRECTORY | os.O_RDWR | os.O_CLOEXEC,
                0o600,
                dir_fd=staging_fd,
            )
        except OSError as exc:
            return {"error": f"O_TMPFILE failed: {type(exc).__name__} errno={exc.errno}",
                    "errno_name": errno.errorcode.get(exc.errno)}
        try:
            before = os.fstat(fd)
            os.fchmod(fd, 0o600)
            after = os.fstat(fd)
            result.update({
                "requested_mode": "0o600",
                "mode_before_fchmod": oct(stat.S_IMODE(before.st_mode)),
                "mode_after_fchmod": oct(stat.S_IMODE(after.st_mode)),
                "uid": after.st_uid,
                "gid": after.st_gid,
                "nlink": after.st_nlink,
                "is_regular": stat.S_ISREG(after.st_mode),
            })
        finally:
            os.close(fd)
    finally:
        os.close(staging_fd)
    return result


def _dir_of(path: str) -> str:
    return os.path.dirname(path)


def published_state(destination: str) -> dict:
    published = os.path.join(destination, PUBLISHED_NAME)
    state: dict = {
        "destination_entries": entries(destination),
        "published_path": published,
        "published_exists": os.path.lexists(published),
    }
    if state["published_exists"]:
        state["published_info"] = file_info(published)
        state["published_sha256"] = sha256_file(published)
    return state


def success_path(anonymous, core, destination: str, staging: str) -> dict:
    """Construct, stage, seal, read back, publish, release."""
    out: dict = {}
    try:
        store = anonymous.AnonymousLinuxStore(TARGET_ID, os.open(destination, DIRFLAGS), os.open(staging, DIRFLAGS))
    except Exception as exc:  # noqa: BLE001
        out["construct"] = {"result": "exception", "exc_type": type(exc).__name__,
                            "reason": getattr(exc, "reason", None), "str": str(exc)}
        return out
    out["construct"] = "ok"
    try:
        try:
            handle = store.begin_owned_stage(TARGET_ID)
        except Exception as exc:  # noqa: BLE001
            out["begin"] = {"result": "exception", "exc_type": type(exc).__name__,
                            "reason": getattr(exc, "reason", None), "str": str(exc)}
            return out
        out["begin"] = "ok"
        stage = next(iter(store._stages.values()))  # probe-only introspection
        stage_info = os.fstat(stage.fd)
        out["stage_fd"] = {
            "mode": oct(stat.S_IMODE(stage_info.st_mode)),
            "uid": stage_info.st_uid,
            "nlink": stage_info.st_nlink,
            "is_regular": stat.S_ISREG(stage_info.st_mode),
            "inheritable": os.get_inheritable(stage.fd),
        }
        out["staging_entries_during_stage"] = entries(staging)
        out["destination_entries_during_stage"] = entries(destination)

        out["append"] = attempt("append", lambda: store.append(handle, PAYLOAD))
        out["seal"] = attempt("seal", lambda: store.seal(handle))
        readback = b""
        try:
            readback = store.read_for_verification(handle, 0, len(PAYLOAD))
        except Exception as exc:  # noqa: BLE001
            out["readback"] = {"result": "exception", "exc_type": type(exc).__name__,
                               "reason": getattr(exc, "reason", None), "str": str(exc)}
        else:
            out["readback"] = {
                "payload_sha256": hashlib.sha256(PAYLOAD).hexdigest(),
                "read_sha256": hashlib.sha256(readback).hexdigest(),
                "identical": readback == PAYLOAD,
            }
        out["staging_entries_after_seal"] = entries(staging)

        out["publish"] = attempt("publish_noreplace", lambda: store.publish_noreplace(handle, PUBLISHED_NAME))
        out["published"] = published_state(destination)
        if out.get("published", {}).get("published_exists"):
            out["release"] = attempt("release_owned_stage", lambda: store.release_owned_stage(handle))
    finally:
        out["close"] = attempt("close", store.close)
    return out


def conflict_path(anonymous, core, destination: str, staging: str) -> dict:
    """A second publication of the same basename must not replace the first."""
    out: dict = {}
    before = published_state(destination)
    out["before"] = before
    try:
        store = anonymous.AnonymousLinuxStore(TARGET_ID, os.open(destination, DIRFLAGS), os.open(staging, DIRFLAGS))
    except Exception as exc:  # noqa: BLE001
        out["construct"] = {"result": "exception", "exc_type": type(exc).__name__,
                            "reason": getattr(exc, "reason", None), "str": str(exc)}
        return out
    try:
        handle = store.begin_owned_stage(TARGET_ID)
        store.append(handle, PAYLOAD_SECOND)
        store.seal(handle)
        out["publish"] = attempt("publish_noreplace", lambda: store.publish_noreplace(handle, PUBLISHED_NAME))
        out["publish_is_conflict"] = out["publish"].get("value") == core.StoreResult.CONFLICT.value
        out["after"] = published_state(destination)
        out["content_unchanged"] = (
            out["after"].get("published_sha256") == before.get("published_sha256")
            and out["after"].get("published_info", {}).get("ino") == before.get("published_info", {}).get("ino")
        )
        out["abort"] = attempt("abort_owned_stage", lambda: store.abort_owned_stage(handle))
    finally:
        out["close"] = attempt("close", store.close)
    return out


def negative_paths(anonymous, destination: str, root_phase: str) -> dict:
    out: dict = {}

    # A staging directory that is not 0700 must be rejected.
    bad_staging = os.path.join(root_phase, "staging-not-private")
    os.makedirs(bad_staging, exist_ok=True)
    os.chmod(bad_staging, 0o755)
    dfd = os.open(destination, DIRFLAGS)
    bfd = os.open(bad_staging, DIRFLAGS)
    try:
        try:
            anonymous.AnonymousLinuxStore(TARGET_ID, dfd, bfd)
        except Exception as exc:  # noqa: BLE001
            out["staging_not_private"] = {"result": "exception", "exc_type": type(exc).__name__,
                                          "str": str(exc)}
        else:
            out["staging_not_private"] = {"result": "unexpected-accept"}
    finally:
        os.close(bfd)
        os.close(dfd)

    # Passing the same descriptor twice must be rejected as non-distinct.
    same_fd = os.open(destination, DIRFLAGS)
    try:
        try:
            anonymous.AnonymousLinuxStore(TARGET_ID, same_fd, same_fd)
        except Exception as exc:  # noqa: BLE001
            out["same_descriptor"] = {"result": "exception", "exc_type": type(exc).__name__,
                                      "str": str(exc)}
        else:
            out["same_descriptor"] = {"result": "unexpected-accept"}
    finally:
        os.close(same_fd)

    # An unsafe basename must be rejected before publication is attempted.
    dfd = os.open(destination, DIRFLAGS)
    sfd = os.open(os.path.join(_dir_of(destination), "staging"), DIRFLAGS)
    try:
        store = anonymous.AnonymousLinuxStore(TARGET_ID, dfd, sfd)
        try:
            handle = store.begin_owned_stage(TARGET_ID)
            store.append(handle, PAYLOAD)
            store.seal(handle)
            out["unsafe_basename"] = attempt(
                "publish_noreplace", lambda: store.publish_noreplace(handle, "bad/name")
            )
            out["unsafe_basename_abort"] = attempt(
                "abort_owned_stage", lambda: store.abort_owned_stage(handle)
            )
        finally:
            out["close"] = attempt("close", store.close)
    finally:
        os.close(sfd)
        os.close(dfd)
    return out


def phase_setup(code_root: str) -> dict:
    report = base_report("setup")
    report["code_root"] = code_root
    report["bind_hashes"] = {relative: sha256_file(os.path.join(code_root, relative)) for relative in BIND_FILES}
    report["vol1_1000_entries_before"] = entries("/vol1/1000")
    report["probe_root_exists_before"] = os.path.lexists(PROBE_ROOT)
    if report["probe_root_exists_before"]:
        raise SystemExit("refusing: probe root already exists")
    os.makedirs(PROBE_ROOT, mode=0o755)
    os.chmod(PROBE_ROOT, 0o755)
    with open(os.path.join(PROBE_ROOT, "PROBE-OWNER.txt"), "w", encoding="utf-8") as stream:
        stream.write("fnmusic Lead disposable evidence probe; removed in --phase cleanup\n")
    # Directories the root phase uses are owned by root from the start.
    root_phase = os.path.join(PROBE_ROOT, "root-phase")
    os.makedirs(root_phase, mode=0o700)
    os.chmod(root_phase, 0o700)
    for name, mode in (("staging", 0o700), ("destination", 0o755)):
        path = os.path.join(root_phase, name)
        os.makedirs(path, mode=mode)
        os.chmod(path, mode)
    # Directories the unprivileged phase uses are created by kurumi itself, so a
    # directory is never created by one uid and chowned to another.
    user_phase = os.path.join(PROBE_ROOT, "user-phase")
    for argv in (
        ["runuser", "-u", "kurumi", "--", "mkdir", "-p", "-m", "0700", user_phase],
        ["runuser", "-u", "kurumi", "--", "mkdir", "-p", "-m", "0700", os.path.join(user_phase, "staging")],
        ["runuser", "-u", "kurumi", "--", "mkdir", "-p", "-m", "0755", os.path.join(user_phase, "destination")],
    ):
        subprocess.run(argv, check=False, capture_output=True, text=True)
    report["probe_root_xattrs"] = sorted(os.listxattr(PROBE_ROOT))
    report["dirs"] = {
        path: file_info(path)
        for path in (
            PROBE_ROOT,
            os.path.join(PROBE_ROOT, "root-phase"),
            os.path.join(PROBE_ROOT, "root-phase", "staging"),
            os.path.join(PROBE_ROOT, "root-phase", "destination"),
            os.path.join(PROBE_ROOT, "user-phase"),
            os.path.join(PROBE_ROOT, "user-phase", "staging"),
            os.path.join(PROBE_ROOT, "user-phase", "destination"),
        )
    }
    report["vol1_1000_entries_after_setup"] = entries("/vol1/1000")
    return report


def phase_root(code_root: str) -> dict:
    report = base_report("root")
    report["code_root"] = code_root
    report["bind_hashes"] = {relative: sha256_file(os.path.join(code_root, relative)) for relative in BIND_FILES}
    root_phase = os.path.join(PROBE_ROOT, "root-phase")
    staging = os.path.join(root_phase, "staging")
    destination = os.path.join(root_phase, "destination")
    report["dirs"] = {path: file_info(path) for path in (staging, destination)}
    report["raw_tmpfile"] = raw_tmpfile_observation(staging)
    sys.path.insert(0, code_root)
    from proxy import manual_save_anonymous as anonymous  # noqa: PLC0415
    import proxy.manual_save_core as core  # noqa: PLC0415

    report["imported_hashes"] = {
        relative: sha256_file(os.path.join(code_root, relative)) for relative in BIND_FILES
    }
    for label, function in (
        ("success_path", lambda: success_path(anonymous, core, destination, staging)),
        ("conflict_path", lambda: conflict_path(anonymous, core, destination, staging)),
        ("negative_paths", lambda: negative_paths(anonymous, destination, root_phase)),
    ):
        try:
            report[label] = function()
        except Exception as exc:  # noqa: BLE001 - keep remaining scenarios observable
            report[label] = {"result": "exception", "exc_type": type(exc).__name__, "str": str(exc)}
    report["dirs_after"] = {path: file_info(path) for path in (staging, destination)}
    report["entries_after"] = {"staging": entries(staging), "destination": entries(destination)}
    return report


def phase_user(code_root: str) -> dict:
    report = base_report("user")
    report["code_root"] = code_root
    report["bind_hashes"] = {relative: sha256_file(os.path.join(code_root, relative)) for relative in BIND_FILES}
    user_phase = os.path.join(PROBE_ROOT, "user-phase")
    staging = os.path.join(user_phase, "staging")
    destination = os.path.join(user_phase, "destination")
    report["dirs"] = {path: file_info(path) for path in (staging, destination)}
    report["raw_tmpfile"] = raw_tmpfile_observation(staging)
    sys.path.insert(0, code_root)
    from proxy import manual_save_anonymous as anonymous  # noqa: PLC0415
    import proxy.manual_save_core as core  # noqa: PLC0415

    report["imported_hashes"] = {
        relative: sha256_file(os.path.join(code_root, relative)) for relative in BIND_FILES
    }
    try:
        store = anonymous.AnonymousLinuxStore(TARGET_ID, os.open(destination, DIRFLAGS), os.open(staging, DIRFLAGS))
    except Exception as exc:  # noqa: BLE001
        report["construct"] = {"result": "exception", "exc_type": type(exc).__name__,
                               "reason": getattr(exc, "reason", None), "str": str(exc)}
        return report
    report["construct"] = "ok"
    try:
        try:
            handle = store.begin_owned_stage(TARGET_ID)
        except Exception as exc:  # noqa: BLE001
            report["begin"] = {"result": "exception", "exc_type": type(exc).__name__,
                               "reason": getattr(exc, "reason", None), "str": str(exc)}
            return report
        report["begin"] = "ok"
        stage = next(iter(store._stages.values()))  # probe-only introspection
        stage_info = os.fstat(stage.fd)
        report["stage_fd"] = {
            "mode": oct(stat.S_IMODE(stage_info.st_mode)),
            "uid": stage_info.st_uid,
            "nlink": stage_info.st_nlink,
            "is_regular": stat.S_ISREG(stage_info.st_mode),
        }
        report["append"] = attempt("append", lambda: store.append(handle, PAYLOAD))
        report["seal"] = attempt("seal", lambda: store.seal(handle))
        try:
            readback = store.read_for_verification(handle, 0, len(PAYLOAD))
            report["readback"] = {"identical": readback == PAYLOAD,
                                  "read_sha256": hashlib.sha256(readback).hexdigest()}
        except Exception as exc:  # noqa: BLE001
            report["readback"] = {"result": "exception", "exc_type": type(exc).__name__,
                                  "reason": getattr(exc, "reason", None), "str": str(exc)}
        report["staging_entries"] = entries(staging)
        report["publish"] = attempt("publish_noreplace", lambda: store.publish_noreplace(handle, PUBLISHED_NAME))
        report["published"] = published_state(destination)
    finally:
        report["close"] = attempt("close", store.close)
    report["entries_after"] = {"staging": entries(staging), "destination": entries(destination)}
    return report


def walk_tree(root: str, depth: int = 3) -> list:
    rows = []
    base_depth = root.rstrip("/").count("/")
    for current, directories, files in os.walk(root):
        level = current.rstrip("/").count("/") - base_depth
        if level >= depth:
            directories[:] = []
        rows.append({"path": current, "dirs": sorted(directories), "files": sorted(files)})
    return rows


def phase_cleanup() -> dict:
    report = base_report("cleanup")
    report["probe_root_exists_before"] = os.path.lexists(PROBE_ROOT)
    report["tree_before"] = walk_tree(PROBE_ROOT) if report["probe_root_exists_before"] else []
    if report["probe_root_exists_before"]:
        try:
            shutil.rmtree(PROBE_ROOT)
            report["rmtree"] = "ok"
        except OSError as exc:
            report["rmtree"] = {"result": "exception", "exc_type": type(exc).__name__, "str": str(exc)}
    report["probe_root_exists_after"] = os.path.lexists(PROBE_ROOT)
    report["vol1_1000_entries_after"] = entries("/vol1/1000")
    return report


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("setup", "root", "user", "cleanup"))
    parser.add_argument("--code-root", default="/tmp/fnmusic-vol1-probe/code")
    args = parser.parse_args(argv)
    if args.phase == "setup":
        report = phase_setup(args.code_root)
    elif args.phase == "root":
        report = phase_root(args.code_root)
    elif args.phase == "user":
        report = phase_user(args.code_root)
    else:
        report = phase_cleanup()
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    try:
        with open(f"/tmp/fnmusic-vol1-probe-{args.phase}.json", "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
