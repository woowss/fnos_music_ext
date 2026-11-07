#!/usr/bin/env python3
"""Evidence probe: Btrfs subvolume generation semantics on fnOS /vol1.

Probe-only. Creates one disposable probe tree on /vol1, runs a fixed sequence of
mutations while reading the containing subvolume's GET_SUBVOL_INFO generation,
then removes the tree. Used to decide whether a generation bracket can detect
modifications during a scan window.

All writes stay inside the recorded probe tree. The script refuses to run if the
tree already exists and cleans up in a finally block.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time

MAGIC = 0x94
NR = 60
SIZE = 504
IOCTL = (2 << 30) | (SIZE << 16) | (MAGIC << 8) | NR
DIRFLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC


def subvol_info(path: str) -> dict:
    fd = os.open(path, DIRFLAGS)
    try:
        buf = bytearray(SIZE)
        fcntl.ioctl(fd, IOCTL, buf, True)
        u64 = lambda offset: int.from_bytes(buf[offset:offset + 8], "little")  # noqa: E731
        return {"treeid": u64(0), "generation": u64(280), "flags": u64(288)}
    finally:
        os.close(fd)


def btrfs(args: list) -> dict:
    proc = subprocess.run(["btrfs", *args], capture_output=True, text=True)
    return {"rc": proc.returncode, "out": proc.stdout.strip(), "err": proc.stderr.strip()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-root", required=True)
    parser.add_argument("--long-wait-seconds", type=int, default=35)
    args = parser.parse_args()

    root = args.probe_root
    if os.path.lexists(root):
        print(json.dumps({"error": "probe root already exists", "root": root}))
        return 1

    report: dict = {"probe_root": root, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "kernel": os.uname().release, "steps": []}
    source = os.path.join(root, "src")
    snapshot = os.path.join(root, "snapshot-ro")

    def mono() -> int:
        return subvol_info(source)["generation"]

    def step(name: str, action, *, sync: bool = True, wait: float = 0.0, target: str | None = None):
        target_path = target or source
        before = subvol_info(target_path)["generation"]
        result = None
        try:
            result = action()
        except OSError as exc:
            result = {"error": exc.errno, "str": exc.strerror}
        after_immediate = subvol_info(target_path)["generation"]
        after_wait = None
        if wait:
            time.sleep(wait)
            after_wait = subvol_info(target_path)["generation"]
        after_sync = None
        if sync:
            os.sync()
            after_sync = subvol_info(target_path)["generation"]
        else:
            # Settle the transaction so the next step starts from a committed state.
            os.sync()
        report["steps"].append({
            "name": name,
            "before": before,
            "after_immediate": after_immediate,
            "changed_immediately": after_immediate != before,
            "after_wait": after_wait,
            "changed_after_wait": (None if after_wait is None else after_wait != before),
            "after_sync": after_sync,
            "changed_after_sync": (None if after_sync is None else after_sync != before),
            "action_result": result,
        })
        return after_sync or after_wait or after_immediate

    try:
        os.makedirs(root, mode=0o755)
        os.chmod(root, 0o755)
        report["create_source_subvolume"] = btrfs(["subvolume", "create", source])
        payload = os.path.join(source, "payload.txt")
        with open(payload, "wb") as stream:
            stream.write(b"generation-baseline\n")
        os.sync()
        report["base"] = subvol_info(source)

        step("create_file_no_sync", lambda: open(os.path.join(source, "created.txt"), "wb").write(b"x") and "created",
             sync=False)
        step("create_file_then_sync", lambda: open(os.path.join(source, "created2.txt"), "wb").write(b"x") and "created")
        # fsync on the file only: does it commit the subvolume root item?
        def fsync_only():
            path = os.path.join(source, "fsync-only.txt")
            with open(path, "wb") as stream:
                stream.write(b"y")
                stream.flush()
                os.fsync(stream.fileno())
            return "written+fsynced"
        step("write_fsync_only_no_os_sync", fsync_only, sync=False)
        step("chmod_metadata_only", lambda: (os.chmod(payload, 0o640), "chmod")[1])
        step("truncate_file", lambda: (open(payload, "r+b").truncate(4), "truncated")[1])
        step("rename_file", lambda: (os.rename(payload, os.path.join(source, "renamed.txt")), "renamed")[1])
        step("append_bytes", lambda: (open(os.path.join(source, "renamed.txt"), "ab").write(b"+"), "appended")[1])
        step("rewrite_identical_bytes", lambda: (open(os.path.join(source, "renamed.txt"), "r+b").write(b"gene"), "rewritten")[1])
        step("create_then_unlink", lambda: (
            os.unlink(os.path.join(source, "created.txt")) if os.path.exists(os.path.join(source, "created.txt")) else None,
            "unlinked",
        )[1])
        step("read_only_noop", lambda: (open(os.path.join(source, "renamed.txt"), "rb").read(), "read")[1])
        # ABA: write A, sync, write back original baseline bytes, sync
        aba_path = os.path.join(source, "aba.txt")
        with open(aba_path, "wb") as stream:
            stream.write(b"ABA-original\n")
        os.sync()
        step("aba_write_A", lambda: (open(aba_path, "wb").write(b"ABA-modified-A\n"), "wrote-A")[1])
        step("aba_restore_original", lambda: (open(aba_path, "wb").write(b"ABA-original\n"), "restored")[1])
        step("utimensat_touch", lambda: (os.utime(os.path.join(source, "renamed.txt"), None), "touched")[1])
        # un-synced write then wait for the periodic transaction commit
        def unsynced_write():
            with open(os.path.join(source, "unsynced.txt"), "wb") as stream:
                stream.write(b"unsynced")
                stream.flush()
            return "written-no-sync"
        step("unsynced_write_then_wait", unsynced_write, sync=False, wait=args.long_wait_seconds)

        # phase 2: the trimacl bypass write into a read-only snapshot
        report["snapshot"] = btrfs(["subvolume", "snapshot", "-r", source, snapshot])
        report["snapshot_base"] = subvol_info(snapshot)
        def ro_bypass_write():
            path = os.path.join(snapshot, "ro-bypass.txt")
            with open(path, "wb") as stream:
                stream.write(b"written-into-ro-snapshot")
                stream.flush()
                os.fsync(stream.fileno())
            return "written"
        step("ro_snapshot_bypass_write_sync", ro_bypass_write, sync=True, target=snapshot)
        step("ro_snapshot_bypass_write_no_sync_wait",
             lambda: (open(os.path.join(snapshot, "ro-bypass2.txt"), "wb").write(b"x"), "written")[1],
             sync=False, wait=args.long_wait_seconds, target=snapshot)
        report["snapshot_after"] = subvol_info(snapshot)
    finally:
        for sub in (snapshot, source):
            if os.path.lexists(sub):
                report.setdefault("cleanup", []).append(btrfs(["subvolume", "delete", sub]))
        try:
            os.rmdir(root)
        except OSError:
            pass
        report["probe_root_removed"] = not os.path.lexists(root)

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
