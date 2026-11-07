#!/usr/bin/env python3
"""Evidence probe: generation-bracket barriers (atime / syncfs / concurrent writer).

Follows scripts/btrfs_generation_semantics_probe.py. Answers:
  A) does a plain read after rename advance generation (atime false positive)?
  B) does O_NOATIME read avoid it?
  C) does an RW `noatime` mount avoid it?
  D) does syncfs() act as a commit barrier like sync()?
  E) does a bracket with sync barriers detect a concurrent writer?

Probe-only; one disposable /vol1 tree; self-cleanup in finally.
"""

from __future__ import annotations

import argparse
import ctypes
import fcntl
import json
import os
import subprocess
import sys
import tempfile
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


def syncfs(path: str) -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        return libc.syncfs(fd) == 0
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-root", required=True)
    args = parser.parse_args()
    root = args.probe_root
    if os.path.lexists(root):
        print(json.dumps({"error": "probe root exists"}))
        return 1
    source = os.path.join(root, "src")
    report: dict = {"probe_root": root, "time": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "kernel": os.uname().release, "tests": {}}
    try:
        os.makedirs(root, 0o755)
        os.chmod(root, 0o755)
        report["create_src"] = btrfs(["subvolume", "create", source])
        report["create_snapshot"] = btrfs(["subvolume", "snapshot", "-r", source, os.path.join(root, "snapshot-ro")])
        file_a = os.path.join(source, "a.txt")
        file_b = os.path.join(source, "b.txt")
        for path in (file_a, file_b):
            with open(path, "wb") as stream:
                stream.write(b"barrier-probe\n")
        os.sync()

        def gen() -> int:
            return subvol_info(source)["generation"]

        # A) plain read after rename (observed atime false positive in earlier run)
        os.rename(file_b, os.path.join(source, "b-renamed.txt"))
        os.sync()
        g0 = gen()
        with open(os.path.join(source, "b-renamed.txt"), "rb") as stream:
            stream.read()
        os.sync()
        g1 = gen()
        report["tests"]["A_plain_read_after_rename"] = {"before": g0, "after": g1, "changed": g1 != g0}

        # B) O_NOATIME read of the same file after another ctime change
        os.utime(os.path.join(source, "b-renamed.txt"), None)
        os.sync()
        g2 = gen()
        noatime_flag = getattr(os, "O_NOATIME", 0o1000000)
        fd = os.open(os.path.join(source, "b-renamed.txt"), os.O_RDONLY | noatime_flag | os.O_CLOEXEC)
        try:
            os.read(fd, 64)
        finally:
            os.close(fd)
        os.sync()
        g3 = gen()
        report["tests"]["B_O_NOATIME_read"] = {"before": g2, "after": g3, "changed": g3 != g2}

        # D) syncfs barrier after a data write
        g4 = gen()
        with open(os.path.join(source, "syncfs.txt"), "wb") as stream:
            stream.write(b"syncfs")
            stream.flush()
            os.fsync(stream.fileno())
        g5 = gen()
        ok = syncfs(source)
        g6 = gen()
        report["tests"]["D_syncfs_barrier"] = {
            "before_write": g4, "after_write_before_syncfs": g5, "after_syncfs": g6,
            "syncfs_rc_ok": ok, "changed_by_write": g5 != g4, "changed_after_syncfs": g6 != g5,
        }

        # E) concurrent writer during a bracket window
        g7 = gen()
        helper = os.path.join(root, "_concurrent_writer.py")
        writer_code = (
            "import os, sys, time\n"
            "time.sleep(1.0)\n"
            "p = sys.argv[1]\n"
            "with open(p, 'ab') as f:\n"
            "    f.write(b'concurrent')\n"
            "    f.flush()\n"
            "    os.fsync(f.fileno())\n"
            "print('writer-done')\n"
        )
        with open(helper, "w", encoding="utf-8") as stream:
            stream.write(writer_code)
        proc = subprocess.Popen([sys.executable, "-B", helper, file_a],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # scan-like reads in the bracket window, no barrier before the writer lands
        time.sleep(2.5)
        out, err = proc.communicate(timeout=10)
        os.sync()
        g8 = gen()
        report["tests"]["E_concurrent_writer_bracket"] = {
            "before": g7, "after": g8, "changed": g8 != g7,
            "writer_stdout": out.strip(), "writer_stderr": err.strip(),
        }
        os.unlink(helper)

        # C) RW mount with noatime in a private mount namespace, read through it
        dev = subprocess.run(["findmnt", "-no", "SOURCE", "/vol1"], capture_output=True, text=True).stdout.strip()
        snapid = subprocess.run(["btrfs", "subvolume", "show", source], capture_output=True, text=True).stdout
        sid = ""
        for line in snapid.splitlines():
            if "Subvolume ID:" in line:
                sid = line.split()[2]
                break
        mnt = "/tmp/fnm-noatime-mnt"
        os.makedirs(mnt, exist_ok=True)
        read_helper = tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8")
        read_helper.write(
            "import os, sys\n"
            "d = sys.argv[1]\n"
            "for name in sorted(os.listdir(d)):\n"
            "    p = os.path.join(d, name)\n"
            "    if os.path.isfile(p):\n"
            "        with open(p, 'rb') as f:\n"
            "            f.read()\n"
            "print('read-all')\n"
        )
        read_helper.close()
        # make atime old enough that relatime would update it
        target = os.path.join(source, "b-renamed.txt")
        os.utime(target, (1, 1))
        os.sync()
        g9 = gen()
        cmd = (f"mount -t btrfs -o rw,noatime,subvolid={sid} {dev} {mnt} && "
               f"python3 -B {read_helper.name} {mnt} && umount {mnt}")
        proc = subprocess.run(["unshare", "-m", "bash", "-c", cmd], capture_output=True, text=True)
        os.sync()
        g10 = gen()
        report["tests"]["C_noatime_rw_mount_read"] = {
            "subvolid": sid, "before": g9, "after": g10, "changed": g10 != g9,
            "mount_cmd_rc": proc.returncode, "stdout": proc.stdout.strip(), "stderr": proc.stderr.strip(),
            "target_atime_before": int(os.stat(target).st_atime),
        }
        os.unlink(read_helper.name)
        try:
            os.rmdir(mnt)
        except OSError:
            pass
    finally:
        for sub in (os.path.join(root, "snapshot-ro"), source):
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
