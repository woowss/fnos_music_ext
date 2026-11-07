#!/usr/bin/env python3
"""Evidence probe: real Btrfs subvolume state for the read-only snapshot gate.

Probe-only. This script is not imported by the product. It validates, on the real
fnOS ``/vol1`` Btrfs filesystem, the native properties that
``scripts/verify_position_file_evidence.py`` assumes:

1. ``BTRFS_IOC_GET_SUBVOL_INFO`` returns a usable tree id, generation, flags,
   UUID and parent UUID for a real read-only snapshot created by
   ``btrfs subvolume snapshot -r``.
2. ``BTRFS_ROOT_SUBVOL_RDONLY`` really is bit 0 of the returned
   ``root_item.flags`` (set for a read-only snapshot, clear after
   ``btrfs property set ... ro false``).
3. A plain directory of the top-level subvolume and an ordinary read-write
   subvolume are rejected by ``_require_readonly_snapshot``.
4. Whether the returned root generation changes after a transient
   RO -> RW -> write -> RO cycle, which is the property behind
   ``_BTRFS_GENERATION_RELOCK_GUARD_VALIDATED`` (currently ``False``).

Safety: writes are restricted to one explicitly recorded probe tree under
``/vol1/1000/fnmusic-btrfs-probe-20261107/btrfs-phase``; the subvolumes are
deleted in the cleanup phase. Production library, staging, ledger, services and
containers are never touched.
"""

from __future__ import annotations

import argparse
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
VERIFIER_RELATIVE = "scripts/verify_position_file_evidence.py"


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


def btrfs(command: list) -> dict:
    argv = ["btrfs", *command]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        return {"argv": argv, "result": "exception", "exc_type": type(exc).__name__, "str": str(exc)}
    return {
        "argv": argv,
        "result": "ok" if proc.returncode == 0 else "nonzero",
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


def base_report(phase: str) -> dict:
    return {
        "phase": phase,
        "time": now(),
        "euid": os.geteuid(),
        "egid": os.getegid(),
        "caps": caps(),
        "vol1_mount": vol1_mount(),
        "probe_root": PROBE_ROOT,
        "python": sys.version.split()[0],
    }


def phase_btrfs(code_root: str) -> dict:
    report = base_report("btrfs")
    report["code_root"] = code_root
    report["verifier_sha256"] = sha256_file(os.path.join(code_root, VERIFIER_RELATIVE))
    sys.path.insert(0, os.path.join(code_root, "scripts"))
    import verify_position_file_evidence as verifier  # noqa: PLC0415

    report["verifier_gate_constant"] = verifier._BTRFS_GENERATION_RELOCK_GUARD_VALIDATED
    report["verifier_rdonly_bit_constant"] = verifier._BTRFS_ROOT_SUBVOL_RDONLY
    report["verifier_ioctl"] = hex(verifier._BTRFS_IOC_GET_SUBVOL_INFO)
    try:
        verifier._require_unprivileged_process()
    except Exception as exc:  # noqa: BLE001
        report["unprivileged_process_gate"] = {"result": "rejected", "exc_type": type(exc).__name__,
                                               "errno": getattr(exc, "errno", None), "str": str(exc)}
    else:
        report["unprivileged_process_gate"] = {"result": "accepted"}

    phase_dir = os.path.join(PROBE_ROOT, "btrfs-phase")
    source = os.path.join(phase_dir, "subvol-src")
    snapshot = os.path.join(phase_dir, "snapshot-ro")
    report["phase_dir_exists_before"] = os.path.lexists(phase_dir)
    if report["phase_dir_exists_before"]:
        raise SystemExit("refusing: btrfs probe phase dir already exists")
    # 0755 so an unprivileged probe process can traverse to the subvolumes
    # while the snapshots are still read-only.
    os.makedirs(phase_dir, mode=0o755)
    os.chmod(phase_dir, 0o755)
    report["dirs"] = {path: file_info(path) for path in (phase_dir, PROBE_ROOT)}

    report["create_source_subvolume"] = btrfs(["subvolume", "create", source])
    if report["create_source_subvolume"]["result"] != "ok":
        return report
    # Make the snapshot root directory and one file writable by both root and
    # the unprivileged probe uid, so a refused write can only come from the
    # Btrfs read-only subvolume state, not from ordinary permission bits.
    os.chmod(source, 0o777)
    payload_path = os.path.join(source, "payload.txt")
    with open(payload_path, "wb") as stream:
        stream.write(b"fnmusic synthetic snapshot payload\n")
    os.chmod(payload_path, 0o666)
    report["source_modes"] = {
        "source_dir": oct(stat.S_IMODE(os.stat(source).st_mode)),
        "payload": oct(stat.S_IMODE(os.stat(payload_path).st_mode)),
    }
    report["snapshot_readonly"] = btrfs(["subvolume", "snapshot", "-r", source, snapshot])
    report["subvolume_show_source"] = btrfs(["subvolume", "show", source])
    report["subvolume_show_snapshot"] = btrfs(["subvolume", "show", snapshot])

    def read_info(path: str) -> dict:
        out: dict = {"path": path}
        try:
            fd = os.open(path, DIRFLAGS)
        except OSError as exc:
            out["error"] = f"open failed: {type(exc).__name__} errno={exc.errno}"
            return out
        try:
            treeid, generation, flags, uuid, parent_uuid = verifier._read_btrfs_subvol_info(fd)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__} errno={getattr(exc, 'errno', None)} str={exc}"
            return out
        finally:
            os.close(fd)
        out.update({
            "treeid": treeid,
            "generation": generation,
            "flags_hex": hex(flags),
            "flags_dec": flags,
            "root_subvol_rdonly_bit0": bool(flags & verifier._BTRFS_ROOT_SUBVOL_RDONLY),
            "uuid": uuid.hex(),
            "parent_uuid": parent_uuid.hex(),
        })
        return out

    def require(path: str) -> dict:
        out: dict = {"path": path}
        try:
            fd = os.open(path, DIRFLAGS)
        except OSError as exc:
            out["error"] = f"open failed: {type(exc).__name__} errno={exc.errno}"
            return out
        try:
            treeid, generation, flags, uuid, parent_uuid = verifier._require_readonly_snapshot(fd)
            out.update({
                "result": "accepted",
                "treeid": treeid,
                "generation": generation,
                "flags_hex": hex(flags),
                "uuid": uuid.hex(),
                "parent_uuid": parent_uuid.hex(),
            })
        except Exception as exc:  # noqa: BLE001
            out.update({
                "result": "rejected",
                "exc_type": type(exc).__name__,
                "errno": getattr(exc, "errno", None),
                "str": str(exc),
            })
        finally:
            os.close(fd)
        return out

    report["info_source_rw"] = read_info(source)
    report["info_snapshot_ro"] = read_info(snapshot)
    report["info_plain_dir_top_subvolume"] = read_info(PROBE_ROOT)
    report["require_snapshot_ro"] = require(snapshot)
    report["require_source_rw"] = require(source)
    report["require_plain_dir"] = require(PROBE_ROOT)

    # A read-only snapshot must reject a file creation inside it.
    ro_write: dict = {"path": os.path.join(snapshot, "must-not-exist.txt")}
    try:
        with open(ro_write["path"], "wb") as stream:
            stream.write(b"x")
    except OSError as exc:  # noqa: BLE001
        ro_write.update({"result": "rejected", "exc_type": type(exc).__name__,
                         "errno": exc.errno, "str": str(exc)})
    else:
        ro_write["result"] = "unexpected-accept"
    report["write_inside_readonly_snapshot"] = ro_write
    report["ro_property_get"] = btrfs(["property", "get", "-ts", snapshot, "ro"])
    os.sync()
    after_ro_write = read_info(snapshot)
    report["info_snapshot_after_ro_write"] = after_ro_write
    report["ro_write_observed_effect"] = {
        "file_present": os.path.exists(ro_write["path"]),
        "snapshot_readonly_bit_still_set": bool(after_ro_write.get("root_subvol_rdonly_bit0")),
        "generation_before": report["info_snapshot_ro"].get("generation"),
        "generation_after": after_ro_write.get("generation"),
        "generation_changed": report["info_snapshot_ro"].get("generation") != after_ro_write.get("generation"),
    }

    before = report["info_snapshot_ro"]
    report["relock_set_rw"] = btrfs(["property", "set", "-ts", snapshot, "ro", "false"])
    report["info_snapshot_after_set_rw"] = read_info(snapshot)
    marker = os.path.join(snapshot, "relock-marker.txt")
    write_marker: dict = {"path": marker}
    try:
        with open(marker, "wb") as stream:
            stream.write(b"fnmusic synthetic relock marker\n")
            stream.flush()
            os.fsync(stream.fileno())
        write_marker["result"] = "written"
        dir_fd = os.open(snapshot, DIRFLAGS)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError as exc:  # noqa: BLE001
        write_marker.update({"result": "exception", "exc_type": type(exc).__name__,
                             "errno": exc.errno, "str": str(exc)})
    report["relock_write_marker"] = write_marker
    report["relock_set_ro"] = btrfs(["property", "set", "-ts", snapshot, "ro", "true"])
    after = read_info(snapshot)
    report["info_snapshot_after_relock"] = after
    report["require_snapshot_after_relock"] = require(snapshot)
    report["relock_comparison"] = {
        "treeid_same": before.get("treeid") == after.get("treeid"),
        "uuid_same": before.get("uuid") == after.get("uuid"),
        "parent_uuid_same": before.get("parent_uuid") == after.get("parent_uuid"),
        "readonly_bit_restored": bool(after.get("root_subvol_rdonly_bit0")),
        "generation_before": before.get("generation"),
        "generation_after": after.get("generation"),
        "generation_changed_after_write_relock": before.get("generation") != after.get("generation"),
    }

    # Second cycle without any data write: does locking alone change the generation?
    report["cycle2_set_rw"] = btrfs(["property", "set", "-ts", snapshot, "ro", "false"])
    report["cycle2_set_ro"] = btrfs(["property", "set", "-ts", snapshot, "ro", "true"])
    cycle2 = read_info(snapshot)
    report["info_snapshot_after_cycle2"] = cycle2
    report["cycle2_comparison"] = {
        "generation_after_first_relock": after.get("generation"),
        "generation_after_second_relock": cycle2.get("generation"),
        "generation_changed_without_write": after.get("generation") != cycle2.get("generation"),
    }

    # A write to the snapshot after the final lock must be refused again.
    final_write: dict = {"path": os.path.join(snapshot, "post-relock-write.txt")}
    try:
        with open(final_write["path"], "wb") as stream:
            stream.write(b"x")
    except OSError as exc:  # noqa: BLE001
        final_write.update({"result": "rejected", "exc_type": type(exc).__name__,
                            "errno": exc.errno, "str": str(exc)})
    else:
        final_write["result"] = "unexpected-accept"
    report["write_after_final_lock"] = final_write

    report["entries_after"] = {
        "phase_dir": entries(phase_dir),
        "source": entries(source),
        "snapshot": entries(snapshot),
    }
    return report


def phase_user(code_root: str) -> dict:
    """Unprivileged read of the existing RO snapshot (scanner-side capability)."""
    report = base_report("btrfs_user")
    report["code_root"] = code_root
    report["verifier_sha256"] = sha256_file(os.path.join(code_root, VERIFIER_RELATIVE))
    sys.path.insert(0, os.path.join(code_root, "scripts"))
    import verify_position_file_evidence as verifier  # noqa: PLC0415

    try:
        verifier._require_unprivileged_process()
    except Exception as exc:  # noqa: BLE001
        report["unprivileged_process_gate"] = {"result": "rejected", "exc_type": type(exc).__name__,
                                               "errno": getattr(exc, "errno", None), "str": str(exc)}
    else:
        report["unprivileged_process_gate"] = {"result": "accepted"}

    phase_dir = os.path.join(PROBE_ROOT, "btrfs-phase")
    source = os.path.join(phase_dir, "subvol-src")
    snapshot = os.path.join(phase_dir, "snapshot-ro")

    def read_info(path: str) -> dict:
        out: dict = {"path": path}
        try:
            fd = os.open(path, DIRFLAGS)
        except OSError as exc:
            out["error"] = f"open failed: {type(exc).__name__} errno={exc.errno}"
            return out
        try:
            treeid, generation, flags, uuid, parent_uuid = verifier._read_btrfs_subvol_info(fd)
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__} errno={getattr(exc, 'errno', None)} str={exc}"
            return out
        finally:
            os.close(fd)
        out.update({
            "treeid": treeid,
            "generation": generation,
            "flags_hex": hex(flags),
            "root_subvol_rdonly_bit0": bool(flags & verifier._BTRFS_ROOT_SUBVOL_RDONLY),
            "uuid": uuid.hex(),
            "parent_uuid": parent_uuid.hex(),
        })
        return out

    def require(path: str) -> dict:
        out: dict = {"path": path}
        try:
            fd = os.open(path, DIRFLAGS)
        except OSError as exc:
            out["error"] = f"open failed: {type(exc).__name__} errno={exc.errno}"
            return out
        try:
            treeid, generation, flags, uuid, parent_uuid = verifier._require_readonly_snapshot(fd)
            out.update({"result": "accepted", "treeid": treeid, "generation": generation,
                        "flags_hex": hex(flags), "uuid": uuid.hex(), "parent_uuid": parent_uuid.hex()})
        except Exception as exc:  # noqa: BLE001
            out.update({"result": "rejected", "exc_type": type(exc).__name__,
                        "errno": getattr(exc, "errno", None), "str": str(exc)})
        finally:
            os.close(fd)
        return out

    report["info_snapshot_ro"] = read_info(snapshot)
    report["info_source_rw"] = read_info(source)
    report["info_plain_dir_top_subvolume"] = read_info(PROBE_ROOT)
    report["require_snapshot_ro"] = require(snapshot)
    report["require_source_rw"] = require(source)
    report["require_plain_dir"] = require(PROBE_ROOT)

    # Unprivileged write attempt inside the read-only snapshot. The snapshot
    # root directory is 0777, so a refusal would have to come from the
    # read-only subvolume state rather than ordinary permission bits.
    user_write: dict = {"path": os.path.join(snapshot, "user-phase-ro-write.txt")}
    try:
        with open(user_write["path"], "wb") as stream:
            stream.write(b"unprivileged write into ro snapshot\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:  # noqa: BLE001
        user_write.update({"result": "rejected", "exc_type": type(exc).__name__,
                           "errno": exc.errno, "str": str(exc)})
    else:
        user_write["result"] = "unexpected-accept"
    report["unprivileged_write_inside_readonly_snapshot"] = user_write
    report["unprivileged_ro_property_get"] = btrfs(["property", "get", "-ts", snapshot, "ro"])
    os.sync()
    report["info_snapshot_after_user_ro_write"] = read_info(snapshot)
    return report


def phase_cleanup() -> dict:
    report = base_report("btrfs_cleanup")
    phase_dir = os.path.join(PROBE_ROOT, "btrfs-phase")
    source = os.path.join(phase_dir, "subvol-src")
    snapshot = os.path.join(phase_dir, "snapshot-ro")
    report["exists_before"] = {"phase_dir": os.path.lexists(phase_dir),
                               "source": os.path.lexists(source),
                               "snapshot": os.path.lexists(snapshot)}
    report["unlock_snapshot"] = (
        btrfs(["property", "set", "-ts", snapshot, "ro", "false"]) if os.path.lexists(snapshot) else None
    )
    report["delete_snapshot"] = btrfs(["subvolume", "delete", snapshot]) if os.path.lexists(snapshot) else None
    report["delete_source"] = btrfs(["subvolume", "delete", source]) if os.path.lexists(source) else None
    report["exists_after_delete"] = {"phase_dir": os.path.lexists(phase_dir),
                                     "source": os.path.lexists(source),
                                     "snapshot": os.path.lexists(snapshot)}
    try:
        shutil.rmtree(phase_dir)
        report["rmtree_phase_dir"] = "ok"
    except OSError as exc:
        report["rmtree_phase_dir"] = {"result": "exception", "exc_type": type(exc).__name__, "str": str(exc)}
    report["phase_dir_exists_after"] = os.path.lexists(phase_dir)
    report["vol1_1000_entries_after"] = entries("/vol1/1000")
    return report


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("btrfs", "user", "cleanup"))
    parser.add_argument("--code-root", default="/tmp/fnmusic-vol1-probe/code")
    args = parser.parse_args(argv)
    if args.phase == "btrfs":
        report = phase_btrfs(args.code_root)
    elif args.phase == "user":
        report = phase_user(args.code_root)
    else:
        report = phase_cleanup()
    text = json.dumps(report, indent=2, ensure_ascii=False)
    print(text)
    try:
        with open(f"/tmp/fnmusic-btrfs-subvol-{args.phase}.json", "w", encoding="utf-8") as stream:
            stream.write(text + "\n")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
