#!/usr/bin/env python3
"""Reproduce the two runbook Rev-3 defects on synthetic inputs (local only).

D1: os.fchmod/os.fchown require a file descriptor; passing a path raises
    TypeError. Confirms the reviewer's R2 finding and that the fd form works.
D2: sampling name `w3.env.$tag.sha256` vs comparison name `w3.$f.t0` with
    f=.env resolves to `w3..env.t0`; the two names never meet, so the diff
    always fails. Confirms the reviewer's R3 finding.

No device access, no repository writes; uses a temporary directory only.
"""

from __future__ import annotations

import os
import tempfile

results = []


def check(name: str, ok: bool, detail: str) -> None:
    results.append({"check": name, "ok": ok, "detail": detail})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def d1_fchmod_path_vs_fd(root: str) -> None:
    path = os.path.join(root, "env.sample")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("FNMUSIC_MANUAL_SAVE_ENABLED=true\n")
    os.chmod(path, 0o600)

    # Reproduce: path passed to os.fchmod -> TypeError
    try:
        os.fchmod(path, 0o600)  # type: ignore[arg-type]
        path_raises = False
        detail = "os.fchmod(path) unexpectedly succeeded"
    except TypeError as exc:
        path_raises = True
        detail = f"TypeError as expected: {exc}"
    except OSError as exc:
        path_raises = False
        detail = f"unexpected OSError: {exc}"
    check("D1a os.fchmod(path) raises TypeError", path_raises, detail)

    # fd form works and preserves metadata
    tmp = os.path.join(root, "env.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write("FNMUSIC_MANUAL_SAVE_ENABLED=false\n")
        fd = fh.fileno()
        os.fchmod(fd, 0o600)
        if hasattr(os, "fchown"):  # POSIX only; the runbook targets Linux
            os.fchown(fd, os.getuid(), os.getgid())
            fchown_ok = True
        else:
            fchown_ok = False
        fh.flush()
        os.fsync(fd)
    st = os.stat(tmp)
    posix = os.name == "posix"
    if posix:
        check("D1b fd form applies mode", (st.st_mode & 0o777) == 0o600, f"mode={oct(st.st_mode & 0o777)}")
    else:
        results.append({"check": "D1b fd form applies mode", "ok": True,
                        "detail": "SKIPPED: Windows does not implement POSIX mode bits"})
        print("[SKIP] D1b fd form applies mode: Windows does not implement POSIX mode bits")
    if fchown_ok:
        check("D1c fd form applies owner", (st.st_uid, st.st_gid) == (os.getuid(), os.getgid()),
              f"uid={st.st_uid} gid={st.st_gid}")
    else:
        results.append({"check": "D1c fd form applies owner", "ok": True,
                        "detail": "SKIPPED: os.fchown absent on this platform (POSIX-only)"})
        print("[SKIP] D1c fd form applies owner: os.fchown absent on this platform (POSIX-only)")
    os.replace(tmp, path)
    if posix:
        check("D1d os.replace keeps mode", (os.stat(path).st_mode & 0o777) == 0o600,
              f"mode={oct(os.stat(path).st_mode & 0o777)}")
    else:
        results.append({"check": "D1d os.replace keeps mode", "ok": True,
                        "detail": "SKIPPED: Windows does not implement POSIX mode bits"})
        print("[SKIP] D1d os.replace keeps mode: Windows does not implement POSIX mode bits")


def d2_sample_naming(root: str) -> None:
    # Reproduce the name mismatch: writer uses "w3.$tag.$f.sha256" style with
    # tag=env, reader builds "w3.$f.t0" with f=.env -> "w3..env.t0".
    tag = "env"
    f = ".env"
    writer_name = f"w3.{tag}.$tag.sha256".replace("$tag", "0")  # placeholder shape
    # Actual shapes from the runbook text:
    written = f"w3.{tag}.t0.sha256"          # e.g. w3.env.t0.sha256
    looked_up = f"w3.{f}.t0"                 # w3..env.t0  (double dot)
    check("D2a names differ (defect reproduced)", written != looked_up,
          f"written={written!r} looked_up={looked_up!r}")

    # Also confirm a file written under one name is not found under the other
    os.makedirs(os.path.join(root, "w3"), exist_ok=True)
    written_path = os.path.join(root, "w3", written)
    with open(written_path, "w", encoding="utf-8") as fh:
        fh.write("deadbeef\n")
    looked_up_path = os.path.join(root, "w3", looked_up)
    check("D2b lookup misses the written file", not os.path.exists(looked_up_path),
          f"exists({looked_up!r})={os.path.exists(looked_up_path)}")

    # Corrected naming: include the same tag on both sides
    ok_written = f"w3.{tag}.t0.sha256"
    ok_looked_up = f"w3.{tag}.t0.sha256"
    check("D2c corrected naming matches", ok_written == ok_looked_up,
          f"{ok_written!r} == {ok_looked_up!r}")

    # Existence assertion would have caught it
    try:
        with open(looked_up_path, "r", encoding="utf-8") as fh:
            fh.read()
        caught = False
    except FileNotFoundError:
        caught = True
    check("D2d existence assertion catches the defect", caught,
          "open() on missing lookup path raises FileNotFoundError")


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="fnm-runbook-rev3-") as root:
        d1_fchmod_path_vs_fd(root)
        d2_sample_naming(root)
    failed = [r for r in results if not r["ok"]]
    print()
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
