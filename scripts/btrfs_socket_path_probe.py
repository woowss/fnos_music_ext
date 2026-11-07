#!/usr/bin/env python3
"""Compare Unix socket path permissions in a disposable /vol1/1000 test tree.

This experiment never opens, renames, connects to, or changes the installed
fnmusic-ext socket. Its results describe only the temporary tree it creates.
Run only from a reviewed, root-owned copy on kotomi; see the accompanying doc.
"""

from __future__ import annotations

import json
import errno
import os
import platform
import secrets
import socket
import stat
import struct
import subprocess
import sys
import threading
from pathlib import Path

BASE = Path("/vol1/1000")
PRODUCTION_DIR = Path("/vol1/@appcenter/fnmusic-ext")
EXPECTED_USER = "kurumi"
EXPECTED_UID = 1000
EXPECTED_GID = 1001
EXPECTED_GROUPS = (1000, 1001)  # primary and supplemental groups, sorted
EXPECTED_EXTRA_GROUPS = (1000,)
SOCKET_TIMEOUT = 3.0
PROCESS_TIMEOUT = 5.0
KNOWN_NAMES = ("service.sock", "moved.sock", "fake.sock", "protected")
ACL_ENTRIES = (
    (0x01, 0x07, 0xFFFFFFFF),  # USER_OBJ rwx
    (0x02, 0x07, 955),         # USER 955 rwx
    (0x04, 0x05, 0xFFFFFFFF),  # GROUP_OBJ r-x
    (0x10, 0x07, 0xFFFFFFFF),  # MASK rwx
    (0x20, 0x05, 0xFFFFFFFF),  # OTHER r-x
)
ACL_BYTES = struct.pack("<I", 2) + b"".join(
    struct.pack("<HHI", *entry) for entry in ACL_ENTRIES
)


def _status(status: str, **fields) -> dict:
    return {"status": status, **fields}


def _passwd_and_groups() -> tuple[tuple[int, int], tuple[int, ...]] | None:
    try:
        passwd = Path("/etc/passwd").read_text(encoding="utf-8").splitlines()
        group_lines = Path("/etc/group").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    entry = None
    for line in passwd:
        fields = line.split(":")
        if len(fields) >= 4 and fields[0] == EXPECTED_USER:
            try:
                entry = (int(fields[2]), int(fields[3]))
            except ValueError:
                return None
            break
    if entry != (EXPECTED_UID, EXPECTED_GID):
        return None
    groups = set()
    for line in group_lines:
        fields = line.split(":")
        if len(fields) >= 4 and EXPECTED_USER in fields[3].split(","):
            try:
                groups.add(int(fields[2]))
            except ValueError:
                return None
    groups.discard(EXPECTED_GID)
    if tuple(sorted(groups)) != EXPECTED_EXTRA_GROUPS:
        return None
    return entry, tuple(sorted(groups))


class ProbeBlocked(RuntimeError):
    def __init__(self, reason: str, *, evidence: dict | None = None, created: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.evidence = evidence
        self.created = created


def _parse_fdinfo_mount_id(text: str) -> dict:
    values = [line.partition(":")[2].strip() for line in text.splitlines()
              if line.partition(":")[0].strip() == "mnt_id"]
    if not values:
        return {"status": "NOT FOUND"}
    if len(values) != 1:
        return {"status": "DUPLICATE", "entry_count": len(values)}
    if not values[0].isdigit():
        return {"status": "ANOMALOUS"}
    return {"status": "read", "mount_id": values[0]}


def _mount_record_for_id(mount_id: str, rows: list[str]) -> dict:
    records = []
    malformed = 0
    for row in rows:
        fields = row.split()
        try:
            separator = fields.index("-")
            device_parts = fields[2].split(":")
            if (separator < 6 or len(fields) < separator + 4
                    or not fields[0].isdigit() or not fields[1].isdigit()
                    or len(device_parts) != 2 or not all(part.isdigit() for part in device_parts)):
                malformed += 1
                continue
            records.append({
                "mount_id": fields[0], "parent_id": fields[1], "device": fields[2],
                "root": fields[3], "mountpoint": fields[4],
                "filesystem_type": fields[separator + 1],
                "source": fields[separator + 2],
            })
        except (ValueError, IndexError):
            malformed += 1
    if malformed:
        return {"status": "ANOMALOUS", "malformed_entry_count": malformed,
                "mount_id": mount_id}
    matches = [record for record in records if record["mount_id"] == str(mount_id)]
    if not matches:
        return {"status": "NOT FOUND", "mount_id": str(mount_id)}
    if len(matches) != 1:
        return {"status": "DUPLICATE", "mount_id": str(mount_id),
                "entry_count": len(matches)}
    return {"status": "read", **matches[0]}


def _mount_for_fd(fd: int) -> dict:
    """Resolve the mount attached to an open descriptor by its kernel mount ID."""
    try:
        fdinfo = Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="utf-8")
    except OSError as exc:
        return {"status": "NOT FOUND", "errno": exc.errno}
    descriptor_mount = _parse_fdinfo_mount_id(fdinfo)
    if descriptor_mount.get("status") != "read":
        return descriptor_mount
    try:
        rows = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"status": "NOT FOUND", "mount_id": descriptor_mount["mount_id"],
                "errno": exc.errno}
    return _mount_record_for_id(descriptor_mount["mount_id"], rows)


def _require_btrfs_mount(fd: int) -> dict:
    mount = _mount_for_fd(fd)
    if mount.get("status") != "read":
        raise ProbeBlocked(f"descriptor mount ID could not be uniquely verified ({mount.get('status')})",
                           evidence=mount)
    if mount.get("filesystem_type") != "btrfs":
        raise ProbeBlocked("descriptor mount is not Btrfs", evidence=mount)
    return mount


def _mount_for(path: Path) -> dict:
    """Read mountinfo through a no-follow directory descriptor; no path guessing."""
    fd = None
    try:
        fd = _open_directory_path(path)
        return _mount_for_fd(fd)
    except OSError as exc:
        return {"status": "NOT FOUND", "errno": exc.errno}
    except ProbeBlocked as exc:
        return {"status": "ANOMALOUS", "reason": exc.reason}
    finally:
        if fd is not None:
            os.close(fd)


def _numeric_acl(fd: int) -> list[dict] | None:
    try:
        data = os.getxattr(fd, "system.posix_acl_access")
    except OSError as exc:
        if exc.errno == errno.ENODATA:
            return None
        raise
    if len(data) < 4 or (len(data) - 4) % 8:
        return None
    version = struct.unpack_from("<I", data, 0)[0]
    if version != 2:
        return None
    tags = {1: "user_obj", 2: "user", 4: "group_obj", 8: "group", 16: "mask", 32: "other"}
    rows = []
    for offset in range(4, len(data), 8):
        tag, perm, ident = struct.unpack_from("<HHI", data, offset)
        rows.append({"tag": tags.get(tag, "unknown"), "id": None if ident == 0xFFFFFFFF else ident,
                     "perm_octal": f"{perm:03o}"})
    return rows


def _rename_child_code() -> str:
    return r'''import json, os, socket, sys
fd=int(sys.argv[1])
def report_identity():
    uid=os.getresuid(); gid=os.getresgid(); groups=tuple(sorted(os.getgroups())); caps={}
    for line in open("/proc/self/status", encoding="ascii"):
        key, _, value=line.partition(":")
        if key in ("CapEff", "CapPrm", "CapInh", "CapAmb"):
            caps[key]=int(value.strip(),16)
    expected=(int(sys.argv[2]),int(sys.argv[3]),tuple(map(int,sys.argv[4].split(","))))
    ok=(uid==(expected[0],)*3 and gid==(expected[1],)*3 and groups==expected[2]
        and len(caps)==4 and all(v==0 for v in caps.values()))
    return {"verified":ok,"uid":uid,"gid":gid,"groups":groups,"capabilities_zero":len(caps)==4 and all(v==0 for v in caps.values())}
identity=report_identity()
if not identity["verified"]:
    print(json.dumps({"identity":identity,"rename_errno":"IDENTITY_MISMATCH"}),flush=True); raise SystemExit(40)
dfd=fd; base=f"/proc/self/fd/{dfd}"
try:
    os.stat("service.sock",dir_fd=dfd,follow_symlinks=False)
except OSError as e:
    print(json.dumps({"identity":identity,"fixture_reachable":False,"fixture_errno":e.errno}),flush=True); raise SystemExit(41)
def rename(src,dst):
    try: os.rename(src,dst,src_dir_fd=dfd,dst_dir_fd=dfd); return {"result":"success"}
    except OSError as e: return {"result":"error","errno":e.errno,"name":e.__class__.__name__}
first=rename("service.sock","moved.sock")
out={"identity":identity,"fixture_reachable":True,"socket_rename":first}
if first["result"]=="success":
    server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); server.settimeout(3)
    try:
        server.bind(base+"/fake.sock"); server.listen(1)
        os.rename("fake.sock","service.sock",src_dir_fd=dfd,dst_dir_fd=dfd)
        st=os.stat("service.sock",dir_fd=dfd,follow_symlinks=False)
        out["replacement"]={"result":"success","inode":st.st_ino,"device":st.st_dev}
        print(json.dumps(out,separators=(",",":")),flush=True)
        conn,_=server.accept(); conn.sendall(b"fake-service\n"); conn.close()
    except OSError as e:
        out["replacement"]={"result":"error","errno":e.errno,"name":e.__class__.__name__}
        print(json.dumps(out,separators=(",",":")),flush=True)
    finally:
        server.close()
else:
    print(json.dumps(out,separators=(",",":")),flush=True)
'''


def _trusted_python() -> str | None:
    candidates = (Path("/usr/bin/python3"), Path("/bin/python3"), Path("/usr/local/bin/python3"))
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            continue
        parent = resolved.parent
        trusted = True
        while True:
            try:
                item = parent.stat()
            except OSError:
                trusted = False
                break
            if not stat.S_ISDIR(item.st_mode) or item.st_uid != 0 or item.st_mode & 0o022:
                trusted = False
                break
            if parent.parent == parent:
                break
            parent = parent.parent
        if trusted:
            return str(resolved)
    return None


def _child_argv(code: str, directory_fd: int, python: str, groups: tuple[int, ...], *args: str) -> list[str]:
    group_arg = ",".join(map(str, groups))
    return [python, "-I", "-S", "-c", code, str(directory_fd), str(EXPECTED_UID),
            str(EXPECTED_GID), group_arg, *args]


def _run_child(code: str, directory_fd: int, python: str, groups: tuple[int, ...], *args: str) -> tuple[dict, int]:
    argv = _child_argv(code, directory_fd, python, groups, *args)
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            close_fds=True, pass_fds=(directory_fd,), env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
            user=EXPECTED_UID, group=EXPECTED_GID, extra_groups=list(groups), start_new_session=True,
        )
    except (OSError, ValueError):
        return {"status": "BLOCKED", "reason": "could not start bounded identity-checked child"}, 127
    try:
        output, _ = process.communicate(timeout=PROCESS_TIMEOUT)
    except subprocess.TimeoutExpired:
        reaped = _kill_and_reap(process)
        return {"status": "BLOCKED", "reason": "child timed out" if reaped else "child could not be reaped",
                "child_reaped": reaped}, 124
    if len(output) > 8192:
        return {"status": "BLOCKED", "reason": "child output exceeded bound"}, process.returncode
    try:
        result = json.loads(output.splitlines()[0])
    except (ValueError, IndexError, TypeError):
        return {"status": "BLOCKED", "reason": "child returned no bounded JSON result"}, process.returncode
    return result, int(process.returncode or 0)


def _spawn_child(code: str, directory_fd: int, python: str, groups: tuple[int, ...]):
    argv = _child_argv(code, directory_fd, python, groups)
    return subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        close_fds=True, pass_fds=(directory_fd,), env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
        user=EXPECTED_UID, group=EXPECTED_GID, extra_groups=list(groups), start_new_session=True,
    )


def _kill_and_reap(process: subprocess.Popen) -> bool:
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.communicate(timeout=1)
        return True
    except subprocess.TimeoutExpired:
        return False


def _run_rename_child(directory_fd: int, python: str, groups: tuple[int, ...]) -> tuple[dict, int]:
    import select

    try:
        process = _spawn_child(_rename_child_code(), directory_fd, python, groups)
    except (OSError, ValueError):
        return {"status": "BLOCKED", "reason": "could not start bounded identity-checked child"}, 127
    try:
        ready, _, _ = select.select([process.stdout], [], [], PROCESS_TIMEOUT)
        if not ready:
            reaped = _kill_and_reap(process)
            return {"status": "BLOCKED", "reason": "rename child timed out before result",
                    "child_reaped": reaped}, 124
        line = process.stdout.readline(8193)
        if len(line) > 8192:
            reaped = _kill_and_reap(process)
            return {"status": "BLOCKED", "reason": "rename child output exceeded bound",
                    "child_reaped": reaped}, 124
        try:
            result = json.loads(line)
        except (ValueError, TypeError):
            reaped = _kill_and_reap(process)
            return {"status": "BLOCKED", "reason": "rename child returned invalid JSON",
                    "child_reaped": reaped}, 124
        if result.get("replacement", {}).get("result") == "success":
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(SOCKET_TIMEOUT)
            try:
                client.connect(f"/proc/self/fd/{directory_fd}/service.sock")
                result["parent_reached_fake_service"] = client.recv(64).decode("ascii", "replace")
            except OSError as exc:
                result["parent_reached_fake_service"] = {"error": exc.errno or exc.__class__.__name__}
            finally:
                client.close()
        try:
            process.communicate(timeout=PROCESS_TIMEOUT)
        except subprocess.TimeoutExpired:
            reaped = _kill_and_reap(process)
            result["child_reaped"] = reaped
            return result, 124
        return result, int(process.returncode or 0)
    except subprocess.TimeoutExpired:
        reaped = _kill_and_reap(process)
        return {"status": "BLOCKED", "reason": "rename child timed out", "child_reaped": reaped}, 124


def _run_plain_rename(directory_fd: int, source: str, destination: str,
                      python: str, groups: tuple[int, ...]) -> tuple[dict, int]:
    code = r'''import json,os,stat,sys
fd=int(sys.argv[1]); user=int(sys.argv[2]); group=int(sys.argv[3]); groups=tuple(map(int,sys.argv[4].split(",")))
uid=os.getresuid(); gid=os.getresgid(); actual_groups=tuple(sorted(os.getgroups())); caps={}
for line in open("/proc/self/status",encoding="ascii"):
 key,_,value=line.partition(":")
 if key in ("CapEff","CapPrm","CapInh","CapAmb"): caps[key]=int(value.strip(),16)
verified=uid==(user,)*3 and gid==(group,)*3 and actual_groups==groups and len(caps)==4 and all(v==0 for v in caps.values())
out={"identity":{"verified":verified,"uid":uid,"gid":gid,"groups":actual_groups,"capabilities_zero":len(caps)==4 and all(v==0 for v in caps.values())}}
if not verified: out["rename"]={"result":"IDENTITY_MISMATCH"}; print(json.dumps(out)); raise SystemExit(40)
try: os.stat("protected",dir_fd=fd,follow_symlinks=False); out["fixture_reachable"]=True
except OSError as e: out["fixture_reachable"]=False; out["fixture_errno"]=e.errno; print(json.dumps(out)); raise SystemExit(41)
try:
 os.rename(sys.argv[5],sys.argv[6],src_dir_fd=fd,dst_dir_fd=fd); out["rename"]={"result":"success"}
 if sys.argv[5]=="protected":
  try:
   os.mkdir("protected",0o700,dir_fd=fd); st=os.stat("protected",dir_fd=fd,follow_symlinks=False)
   out["replacement"]={"result":"success","inode":st.st_ino,"device":st.st_dev,"uid":st.st_uid,"mode":format(stat.S_IMODE(st.st_mode),"04o")}
  except OSError as e: out["replacement"]={"result":"error","errno":e.errno,"name":e.__class__.__name__}
except OSError as e: out["rename"]={"result":"error","errno":e.errno,"name":e.__class__.__name__}
print(json.dumps(out,separators=(",",":")))
'''
    return _run_child(code, directory_fd, python, groups, source, destination)


def _safe_mkdir(parent_fd: int, name: str, mode: int) -> int:
    os.mkdir(name, mode, dir_fd=parent_fd)
    created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(created.st_mode) or created.st_uid != 0:
        raise OSError(errno.EPERM, "new directory did not have the expected type and owner")
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_uid, opened.st_gid) != (
                created.st_dev, created.st_ino, created.st_uid, created.st_gid):
            raise OSError(errno.ESTALE, "directory identity changed while opening")
        os.fchmod(fd, mode)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _clear_inherited_acls(fd: int) -> None:
    for attribute in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.removexattr(fd, attribute)
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise


def _create_server(directory_fd: int, name: str, response: bytes) -> tuple[socket.socket, dict, dict, threading.Thread]:
    address = f"/proc/self/fd/{directory_fd}/{name}"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(SOCKET_TIMEOUT)
    try:
        server.bind(address)
        os.chmod(name, 0o600, dir_fd=directory_fd, follow_symlinks=False)
        created = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        server.listen(1)
    except BaseException:
        server.close()
        raise
    result = {"peer_uid": None, "peer_gid": None, "error": None}

    def serve_one():
        try:
            conn, _ = server.accept()
            with conn:
                peer = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                _, peer_uid, peer_gid = struct.unpack("3i", peer)
                result.update(peer_uid=peer_uid, peer_gid=peer_gid)
                conn.sendall(response)
        except OSError as exc:
            result["error"] = exc.errno or exc.__class__.__name__

    thread = threading.Thread(target=serve_one, daemon=True)
    thread.start()
    identity = {"ino": created.st_ino, "uid": created.st_uid, "gid": created.st_gid,
                "mode": stat.S_IMODE(created.st_mode), "type": "socket", "dev": created.st_dev}
    return server, identity, result, thread


def _try_connect_child(directory_fd: int, python: str, groups: tuple[int, ...]) -> tuple[dict, int]:
    code = r'''import errno,json,os,socket,sys
fd=int(sys.argv[1]); user=int(sys.argv[2]); group=int(sys.argv[3]); groups=tuple(map(int,sys.argv[4].split(",")))
uid=os.getresuid(); gid=os.getresgid(); actual_groups=tuple(sorted(os.getgroups())); caps={}
for line in open("/proc/self/status",encoding="ascii"):
 key,_,value=line.partition(":")
 if key in ("CapEff","CapPrm","CapInh","CapAmb"): caps[key]=int(value.strip(),16)
ok=uid==(user,)*3 and gid==(group,)*3 and actual_groups==groups and len(caps)==4 and all(v==0 for v in caps.values())
if not ok: print(json.dumps({"identity_verified":False,"uid":uid,"gid":gid,"groups":actual_groups,"caps_zero":len(caps)==4 and all(v==0 for v in caps.values())})); raise SystemExit(40)
try: os.stat("service.sock",dir_fd=fd,follow_symlinks=False)
except OSError as e: print(json.dumps({"identity_verified":True,"fixture_reachable":False,"fixture_errno":e.errno})); raise SystemExit(41)
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(2)
try:
 s.connect(f"/proc/self/fd/{fd}/service.sock"); data=s.recv(64); result={"connect":"success","response":data.decode("ascii","replace")}
except OSError as e: result={"connect":"error","errno":e.errno,"name":errno.errorcode.get(e.errno,"OTHER")}
finally: s.close()
result["identity_verified"]=True; result["fixture_reachable"]=True
print(json.dumps(result,separators=(",",":")))
'''
    return _run_child(code, directory_fd, python, groups)


def _verify_acl(fd: int) -> dict:
    try:
        os.setxattr(fd, "system.posix_acl_access", ACL_BYTES)
        data = os.getxattr(fd, "system.posix_acl_access")
        st = os.fstat(fd)
    except OSError as exc:
        return {"status": "NOT RUN", "reason": exc.errno or exc.__class__.__name__}
    rows = _numeric_acl(fd)
    expected = [
        {"tag": "user_obj", "id": None, "perm_octal": "007"},
        {"tag": "user", "id": 955, "perm_octal": "007"},
        {"tag": "group_obj", "id": None, "perm_octal": "005"},
        {"tag": "mask", "id": None, "perm_octal": "007"},
        {"tag": "other", "id": None, "perm_octal": "005"},
    ]
    mismatches = []
    if st.st_uid != 0:
        mismatches.append({"field": "owner_uid", "expected": 0, "actual": st.st_uid})
    if st.st_gid != 0:
        mismatches.append({"field": "owner_gid", "expected": 0, "actual": st.st_gid})
    actual_mode = stat.S_IMODE(st.st_mode)
    if actual_mode != 0o775:
        mismatches.append({"field": "mode", "expected": "0775", "actual": f"{actual_mode:04o}"})
    if rows != expected:
        for index in range(max(len(expected), len(rows or []))):
            expected_entry = expected[index] if index < len(expected) else None
            actual_entry = rows[index] if rows is not None and index < len(rows) else None
            if expected_entry != actual_entry:
                mismatches.append({"field": f"acl_entry_{index}", "expected": expected_entry,
                                   "actual": actual_entry})
    if data != ACL_BYTES and not mismatches:
        mismatches.append({"field": "acl_encoding", "expected": "exact numeric ACL bytes",
                           "actual": "entries decoded but encoding differs"})
    return {"status": "MATCH" if not mismatches else "MISMATCH", "owner": [st.st_uid, st.st_gid],
            "mode": f"{actual_mode:04o}", "acl": rows, "mismatches": mismatches}


def _inode_matches(directory_fd: int, name: str, identity: dict | None) -> bool:
    if not identity:
        return False
    try:
        current = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    return (current.st_ino == identity.get("ino") and current.st_uid == identity.get("uid")
            and current.st_dev == identity.get("dev", current.st_dev)
            and ((identity.get("type") == "socket" and stat.S_ISSOCK(current.st_mode))
                 or (identity.get("type") == "directory" and stat.S_ISDIR(current.st_mode))))


def _remove_known(directory_fd: int, name: str, identity: dict | None) -> bool:
    if not _inode_matches(directory_fd, name, identity):
        return False
    try:
        if identity["type"] == "directory":
            os.rmdir(name, dir_fd=directory_fd)
        else:
            os.unlink(name, dir_fd=directory_fd)
        return True
    except OSError:
        return False


def _run_case(root_fd: int, name: str, mode: int, python: str,
              groups: tuple[int, ...], use_acl: bool = False) -> dict:
    case_fd = None
    server = None
    thread = None
    known: dict[str, dict] = {}
    safe_to_cleanup = True
    output = {"name": name, "requested_parent_mode": f"{mode:04o}"}
    try:
        case_fd = _safe_mkdir(root_fd, name, mode)
        st = os.fstat(case_fd)
        output["inode"] = st.st_ino
        output["device"] = st.st_dev
        if st.st_uid != 0 or st.st_gid != 0:
            output.update(status="BLOCKED", reason="case directory is not root-owned")
        elif use_acl:
            try:
                os.removexattr(case_fd, "system.posix_acl_default")
            except OSError as exc:
                if exc.errno != errno.ENODATA:
                    raise
            output["acl_reproduction"] = _verify_acl(case_fd)
            if output["acl_reproduction"]["status"] != "MATCH":
                output.update(status="NOT RUN", reason="observed ACL could not be reproduced exactly")
        else:
            _clear_inherited_acls(case_fd)
            os.fchmod(case_fd, mode)
            if stat.S_IMODE(os.fstat(case_fd).st_mode) != mode or _numeric_acl(case_fd) is not None:
                output.update(status="BLOCKED", reason="parent mode did not match")
        if not output.get("status"):
            server, identity, peer_result, thread = _create_server(case_fd, "service.sock", b"root-service\n")
            known["service.sock"] = identity
            if (identity["uid"] != 0 or identity["gid"] != 0 or identity["mode"] != 0o600):
                output.update(status="BLOCKED", reason="root socket owner, group, or mode did not match")
            else:
                connect, rc = _try_connect_child(case_fd, python, groups)
                if connect.get("child_reaped") is False:
                    safe_to_cleanup = False
                server.close()
                thread.join(timeout=SOCKET_TIMEOUT + 0.5)
                output["connect_test"] = connect
                if not connect.get("identity_verified"):
                    if safe_to_cleanup:
                        os.fchmod(case_fd, 0o700)
                    output.update(status="BLOCKED", reason="target identity did not validate")
                elif not connect.get("fixture_reachable"):
                    output.update(status="BLOCKED", reason="target could not reach the socket fixture by dirfd")
                elif rc != 0:
                    output.update(status="BLOCKED", reason="target socket child exited unsuccessfully")
                elif connect.get("connect") == "success" and (
                        peer_result["peer_uid"] != EXPECTED_UID or peer_result["peer_gid"] != EXPECTED_GID):
                    output.update(status="BLOCKED", reason="SO_PEERCRED did not match target user")
                elif connect.get("connect") == "success" and connect.get("response") != "root-service\n":
                    output.update(status="BLOCKED", reason="root socket returned an unexpected response")
                elif thread.is_alive():
                    output.update(status="BLOCKED", reason="root socket server thread did not stop")
                else:
                    output["root_server_peer"] = {"uid": peer_result["peer_uid"], "gid": peer_result["peer_gid"]}
                    output["root_server_error"] = peer_result["error"]
                    output["server_response"] = "root-service\n" if connect.get("response") == "root-service\n" else None
                    rename, rc = _run_rename_child(case_fd, python, groups)
                    output["rename_test"] = rename
                    if rename.get("child_reaped") is False:
                        safe_to_cleanup = False
                    else:
                        # Stop any same-user race as soon as the last target child exits.
                        os.fchmod(case_fd, 0o700)
                    if rename.get("socket_rename", {}).get("result") == "success":
                        known.pop("service.sock", None)
                        known["moved.sock"] = {"ino": identity["ino"], "uid": 0, "type": "socket", "dev": identity["dev"]}
                    if rename.get("replacement", {}).get("result") == "success":
                        known["service.sock"] = {"ino": rename["replacement"].get("inode"),
                                                  "uid": EXPECTED_UID, "type": "socket",
                                                  "dev": rename["replacement"].get("device")}
                    mock_not_verified = (
                        rename.get("replacement", {}).get("result") == "success"
                        and rename.get("parent_reached_fake_service") != "fake-service\n")
                    if not rename.get("identity", {}).get("verified"):
                        output.update(status="BLOCKED", reason="rename child identity did not validate")
                    elif not rename.get("fixture_reachable"):
                        output.update(status="BLOCKED", reason="rename child could not reach the socket fixture by dirfd")
                    elif rc != 0 or mock_not_verified:
                        output.update(status="BLOCKED", reason="rename identity or bounded child execution failed")
                    else:
                        output["replacement_verification"] = rename.get("parent_reached_fake_service")
                        output["status"] = "COMPLETE"
                    if name == "parent-0777":
                        positive = _positive_control_confirmed(connect, rename)
                        output["positive_control"] = "CONFIRMED" if positive else "NOT CONFIRMED"
    except OSError as exc:
        output.update(status="BLOCKED", reason=exc.errno or exc.__class__.__name__)
    finally:
        if server is not None:
            try:
                server.close()
            except OSError:
                pass
        if thread is not None:
            thread.join(timeout=SOCKET_TIMEOUT + 0.5)
            if thread.is_alive():
                safe_to_cleanup = False
        if case_fd is not None:
            try:
                if not safe_to_cleanup:
                    os.close(case_fd)
                    case_fd = None
                    output["cleanup"] = {"status": "BLOCKED", "case_directory_retained": True}
                    output.update(status="BLOCKED", reason="a probe process or server thread may still hold the fixture")
                else:
                    # The child is reaped before this point. Revoke its directory permissions
                    # before path-based unlink so a writable fixture cannot be swapped in a race.
                    os.fchmod(case_fd, 0o700)
                    for obj_name in KNOWN_NAMES:
                        _remove_known(case_fd, obj_name, known.get(obj_name))
                    residual = os.listdir(case_fd)
                    output["cleanup"] = {"status": "PASS" if not residual else "BLOCKED",
                                         "residual_entry_count": len(residual)}
                    case_st = os.fstat(case_fd)
                    case_identity = {"ino": case_st.st_ino, "uid": 0, "type": "directory", "dev": case_st.st_dev}
                    os.close(case_fd)
                    case_fd = None
                    if not residual and _remove_known(root_fd, name, case_identity):
                        output["cleanup"]["case_directory_removed"] = True
                    else:
                        output["cleanup"]["case_directory_removed"] = False
                        output.update(status="BLOCKED", reason="case changed or retained unknown entries")
            except OSError:
                output["cleanup"] = {"status": "BLOCKED"}
                output.update(status="BLOCKED", reason="case cleanup could not be verified")
                try:
                    os.close(case_fd)
                except OSError:
                    pass
    return output


def _replace_protected_child(root_fd: int, python: str, groups: tuple[int, ...]) -> dict:
    parent_fd = None
    inner_fd = None
    replacement_identity = None
    safe_to_cleanup = True
    result_out = {"status": "BLOCKED"}
    try:
        parent_fd = _safe_mkdir(root_fd, "parent-replace", 0o777)
        _clear_inherited_acls(parent_fd)
        os.fchmod(parent_fd, 0o777)
        parent_stat = os.fstat(parent_fd)
        inner_fd = _safe_mkdir(parent_fd, "protected", 0o700)
        inner_stat = os.fstat(inner_fd)
        inner_identity = {"ino": inner_stat.st_ino, "uid": 0, "type": "directory", "dev": inner_stat.st_dev}
        result, rc = _run_plain_rename(parent_fd, "protected", "protected.moved", python, groups)
        if result.get("child_reaped") is False:
            safe_to_cleanup = False
        else:
            # Revoke the target's access immediately after the child is reaped.
            os.fchmod(parent_fd, 0o700)
        changed = _inode_matches(parent_fd, "protected.moved", inner_identity)
        replacement = result.get("replacement", {})
        changed_back = (replacement.get("result") == "success" and replacement.get("uid") == EXPECTED_UID
                        and replacement.get("inode") != inner_identity["ino"])
        if changed_back:
            replacement_identity = {"ino": replacement.get("inode"), "uid": EXPECTED_UID,
                                    "type": "directory", "dev": replacement.get("device")}
        result_out = {"status": "COMPLETE" if rc == 0 and result.get("identity", {}).get("verified")
                      and result.get("fixture_reachable") else "BLOCKED",
                "parent_inode": parent_stat.st_ino,
                "parent_device": parent_stat.st_dev,
                "parent_mode": f"{stat.S_IMODE(parent_stat.st_mode):04o}",
                "protected_child_mode": f"{stat.S_IMODE(os.fstat(inner_fd).st_mode):04o}",
                "rename_test": result, "fixture_reachable": result.get("fixture_reachable"),
                "protected_child_renamed": changed,
                "protected_child_recreated_by_target": changed_back}
    except OSError as exc:
        result_out.update(status="BLOCKED", reason=exc.errno or exc.__class__.__name__)
    finally:
        inner_stat = os.fstat(inner_fd) if inner_fd is not None else None
        inner_ino = inner_stat.st_ino if inner_stat else -1
        inner_dev = inner_stat.st_dev if inner_stat else -1
        if inner_fd is not None:
            os.close(inner_fd)
        if parent_fd is not None:
            try:
                if not safe_to_cleanup:
                    os.close(parent_fd)
                    parent_fd = None
                    result_out["cleanup"] = {"status": "BLOCKED", "parent_directory_retained": True}
                    result_out.update(status="BLOCKED", reason="replacement child could not be reaped")
                else:
                    # The target child has exited. Make the fixture inaccessible before
                    # checking and removing the recorded entries.
                    os.fchmod(parent_fd, 0o700)
                    for name, identity in (
                        ("protected", {"ino": inner_ino, "uid": 0, "type": "directory", "dev": inner_dev}),
                        ("protected.moved", {"ino": inner_ino, "uid": 0, "type": "directory", "dev": inner_dev}),
                        ("protected", replacement_identity),
                    ):
                        _remove_known(parent_fd, name, identity)
                    residual = os.listdir(parent_fd)
                    result_out["cleanup"] = {"status": "PASS" if not residual else "BLOCKED",
                                             "residual_entry_count": len(residual)}
                    parent_stat = os.fstat(parent_fd)
                    parent_identity = {"ino": parent_stat.st_ino, "uid": 0, "type": "directory", "dev": parent_stat.st_dev}
                    os.close(parent_fd)
                    parent_fd = None
                    if not residual and _remove_known(root_fd, "parent-replace", parent_identity):
                        result_out["cleanup"]["parent_directory_removed"] = True
                    else:
                        result_out["cleanup"]["parent_directory_removed"] = False
                        result_out.update(status="BLOCKED", reason="replacement experiment retained unknown entries")
            except OSError:
                result_out["cleanup"] = {"status": "BLOCKED"}
                result_out.update(status="BLOCKED", reason="replacement cleanup could not be verified")
                try:
                    os.close(parent_fd)
                except OSError:
                    pass
    return result_out


def _directory_open_flags() -> int:
    required = (getattr(os, "O_DIRECTORY", 0), getattr(os, "O_NOFOLLOW", 0))
    if not all(required):
        raise ProbeBlocked("directory-descriptor O_NOFOLLOW support is required")
    return os.O_RDONLY | required[0] | required[1] | getattr(os, "O_CLOEXEC", 0)


def _same_directory(left, right) -> bool:
    return (stat.S_ISDIR(left.st_mode) and stat.S_ISDIR(right.st_mode)
            and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino))


def _open_child_directory(parent_fd: int, name: str) -> tuple[int, os.stat_result]:
    """Open one fixed path component and reject symlinks or identity races."""
    flags = _directory_open_flags()
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ProbeBlocked(f"path component {name} is not a real directory")
    fd = os.open(name, flags, dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_directory(before, opened) or not _same_directory(after, opened):
            raise ProbeBlocked(f"path component {name} changed while opening")
        return fd, opened
    except BaseException:
        os.close(fd)
        raise


def _open_experiment_base() -> tuple[int, int, int, os.stat_result, os.stat_result]:
    """Open /, /vol1, and /vol1/1000 component by component without symlinks."""
    root_path_fd = os.open("/", _directory_open_flags())
    vol1_fd = base_fd = None
    try:
        vol1_fd, vol1_stat = _open_child_directory(root_path_fd, "vol1")
        base_fd, base_stat = _open_child_directory(vol1_fd, "1000")
        return root_path_fd, vol1_fd, base_fd, vol1_stat, base_stat
    except BaseException:
        for fd in (base_fd, vol1_fd, root_path_fd):
            if fd is not None:
                os.close(fd)
        raise


def _verify_experiment_base(root_path_fd: int, vol1_fd: int, base_fd: int,
                            vol1_stat, base_stat, expected_mount_id: str) -> dict:
    """Reopen both fixed path components and recheck descriptor identity/mount IDs."""
    current_vol1_fd = current_base_fd = None
    try:
        held_vol1 = os.fstat(vol1_fd)
        held_base = os.fstat(base_fd)
        if not _same_directory(held_vol1, vol1_stat) or not _same_directory(held_base, base_stat):
            raise ProbeBlocked("held experiment path identity changed before creation")

        current_vol1_fd, current_vol1 = _open_child_directory(root_path_fd, "vol1")
        if not _same_directory(current_vol1, held_vol1):
            raise ProbeBlocked("/vol1 path identity changed before creation")
        current_base_fd, current_base = _open_child_directory(current_vol1_fd, "1000")
        if not _same_directory(current_base, held_base):
            raise ProbeBlocked("/vol1/1000 path identity changed before creation")

        held_mount = _require_btrfs_mount(base_fd)
        current_mount = _require_btrfs_mount(current_base_fd)
        if (held_mount.get("mount_id") != expected_mount_id
                or current_mount.get("mount_id") != expected_mount_id
                or held_mount.get("mount_id") != current_mount.get("mount_id")):
            raise ProbeBlocked("/vol1/1000 mount ID changed before creation",
                               evidence={"held": held_mount, "current_path": current_mount})

        final_base = os.stat("1000", dir_fd=current_vol1_fd, follow_symlinks=False)
        current_open = os.fstat(current_base_fd)
        if not _same_directory(final_base, current_open) or not _same_directory(current_open, held_base):
            raise ProbeBlocked("/vol1/1000 path identity changed during pre-creation check")
        return current_mount
    finally:
        for fd in (current_base_fd, current_vol1_fd):
            if fd is not None:
                os.close(fd)


def _validate_new_root_entry(entry_stat, opened_stat, base_stat) -> None:
    if (not stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode)
            or not stat.S_ISDIR(opened_stat.st_mode) or stat.S_ISLNK(opened_stat.st_mode)):
        raise ProbeBlocked("new experiment directory is not a real directory")
    if (entry_stat.st_uid != 0 or opened_stat.st_uid != 0
            or entry_stat.st_dev != base_stat.st_dev or opened_stat.st_dev != base_stat.st_dev
            or (entry_stat.st_dev, entry_stat.st_ino, entry_stat.st_uid, entry_stat.st_gid)
            != (opened_stat.st_dev, opened_stat.st_ino, opened_stat.st_uid, opened_stat.st_gid)):
        raise ProbeBlocked("new experiment directory device, inode, owner, or type did not match")


def _create_experiment_root(root_path_fd: int, vol1_fd: int, base_fd: int, root_name: str,
                            vol1_stat, base_stat, expected_mount_id: str) -> tuple[int, os.stat_result]:
    _verify_experiment_base(root_path_fd, vol1_fd, base_fd, vol1_stat, base_stat, expected_mount_id)
    os.mkdir(root_name, 0o700, dir_fd=base_fd)
    root_fd = None
    try:
        entry_stat = os.stat(root_name, dir_fd=base_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(entry_stat.st_mode) or stat.S_ISLNK(entry_stat.st_mode)
                or entry_stat.st_dev != base_stat.st_dev or entry_stat.st_uid != 0):
            raise ProbeBlocked("new experiment path entry failed its device, owner, or type check",
                               created=True)
        root_fd = os.open(root_name, _directory_open_flags(), dir_fd=base_fd)
        opened_stat = os.fstat(root_fd)
        _validate_new_root_entry(entry_stat, opened_stat, base_stat)
        return root_fd, opened_stat
    except ProbeBlocked as exc:
        if root_fd is not None:
            os.close(root_fd)
        raise ProbeBlocked(exc.reason, evidence=exc.evidence, created=True) from exc
    except OSError as exc:
        if root_fd is not None:
            os.close(root_fd)
        raise ProbeBlocked(f"new experiment directory identity could not be verified ({exc.errno or exc.__class__.__name__})",
                           created=True) from exc


def _open_directory_path(path: Path) -> int:
    if not path.is_absolute() or any(part in (".", "..") for part in path.parts):
        raise ProbeBlocked("metadata path is not a fixed absolute directory path")
    fd = os.open("/", _directory_open_flags())
    try:
        for component in path.parts[1:]:
            child_fd, _ = _open_child_directory(fd, component)
            os.close(fd)
            fd = child_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _compare_observed(left, right) -> str:
    if left is None or right is None:
        return "unknown"
    return "same" if left == right else "different"


def _production_comparison(base_stat, base_mount: dict, production: dict) -> dict:
    prod_mount = production.get("filesystem", {})
    base_mount_id = base_mount.get("mount_id") if base_mount.get("status") == "read" else None
    prod_mount_id = prod_mount.get("mount_id") if prod_mount.get("status") == "read" else None
    prod_device = (production.get("device")
                   if production.get("identity_verified") is True else None)
    return {
        "mount_id": _compare_observed(base_mount_id, prod_mount_id),
        "st_dev": _compare_observed(base_stat.st_dev if base_stat is not None else None,
                                    prod_device),
        "same_mount_does_not_prove_same_subvolume": True,
    }


def _path_metadata(path: Path) -> dict:
    try:
        info = path.lstat()
    except OSError as exc:
        return {"status": "NOT FOUND", "errno": exc.errno,
                "filesystem": {"status": "NOT FOUND"}}
    metadata = {"status": "read", "type": "directory" if stat.S_ISDIR(info.st_mode) else "other",
                "uid": info.st_uid, "gid": info.st_gid, "mode": f"{stat.S_IMODE(info.st_mode):04o}",
                "device": None, "lstat_device_observation": info.st_dev,
                "inode": info.st_ino, "identity_verified": False,
                "filesystem": {"status": "unknown"}}
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        return metadata
    fd = None
    try:
        fd = _open_directory_path(path)
        opened = os.fstat(fd)
        if not _same_directory(info, opened):
            metadata["filesystem"] = {"status": "unknown", "reason": "path identity changed"}
        else:
            metadata["device"] = opened.st_dev
            metadata["identity_verified"] = True
            metadata["filesystem"] = _mount_for_fd(fd)
    except (OSError, ProbeBlocked) as exc:
        metadata["filesystem"] = {"status": "unknown",
                                  "errno": getattr(exc, "errno", None),
                                  "reason": exc.reason if isinstance(exc, ProbeBlocked) else None}
    finally:
        if fd is not None:
            os.close(fd)
    return metadata


def _overall_status(cases: list[dict], protected: dict | None) -> str:
    cases_complete = (len(cases) == 3
                      and all(case.get("status") in ("COMPLETE", "NOT RUN") for case in cases))
    positive_control = next((case.get("positive_control") for case in cases
                             if case.get("name") == "parent-0777"), None)
    if (cases_complete and positive_control == "CONFIRMED"
            and protected is not None and protected.get("status") == "COMPLETE"):
        return "COMPLETE"
    return "PARTIAL"


def _positive_control_confirmed(connect_test: dict, rename_test: dict) -> bool:
    connect_completed = (
        connect_test.get("identity_verified") is True
        and connect_test.get("fixture_reachable") is True
        and (connect_test.get("connect") == "success"
             or (connect_test.get("connect") == "error" and isinstance(connect_test.get("errno"), int)))
    )
    return (connect_completed
            and rename_test.get("fixture_reachable") is True
            and rename_test.get("socket_rename", {}).get("result") == "success"
            and rename_test.get("replacement", {}).get("result") == "success"
            and rename_test.get("parent_reached_fake_service") == "fake-service\n")


def run_probe() -> dict:
    if not sys.platform.startswith("linux") or not hasattr(os, "geteuid"):
        return _status("BLOCKED", reason="Linux is required")
    if os.geteuid() != 0:
        return _status("BLOCKED", reason="run manually as root; this script does not invoke sudo")
    account = _passwd_and_groups()
    if account is None:
        return _status("BLOCKED", reason="local kurumi identity or supplemental groups differ from the approved values")
    _, groups = account
    if tuple(sorted((EXPECTED_GID, *groups))) != EXPECTED_GROUPS:
        return _status("BLOCKED", reason="kurumi group membership differs from the approved values")
    try:
        python = _trusted_python()
        if not python:
            return _status("BLOCKED", reason="no trusted system Python executable found")
        root_path_fd, vol1_fd, base_fd, vol1_stat, base_stat = _open_experiment_base()
    except (OSError, ProbeBlocked) as exc:
        return _status("BLOCKED", reason=(exc.reason if isinstance(exc, ProbeBlocked)
                                           else f"experiment prerequisites unavailable: {exc.errno or exc.__class__.__name__}"))
    root_name = "btrfs-sock-probe-" + secrets.token_hex(16)
    experiment_path = str(BASE / root_name)
    root_fd = None
    root_stat = None
    root_created = False
    cases = []
    protected = None
    production_metadata = _path_metadata(PRODUCTION_DIR)
    mount = {"status": "NOT RUN"}
    result = {"status": "BLOCKED", "reason": "experiment did not finish"}
    try:
        mount = _require_btrfs_mount(base_fd)
        root_fd, root_stat = _create_experiment_root(
            root_path_fd, vol1_fd, base_fd, root_name, vol1_stat, base_stat, mount["mount_id"])
        root_created = True
        result["experiment_directory"] = experiment_path
        _clear_inherited_acls(root_fd)
        os.fchmod(root_fd, 0o700)
        root_stat = os.fstat(root_fd)
        for case_name, mode, use_acl in (("parent-0755", 0o755, False),
                                        ("parent-0777", 0o777, False),
                                        ("parent-acl", 0o775, True)):
            cases.append(_run_case(root_fd, case_name, mode, python, groups, use_acl))
        protected = _replace_protected_child(root_fd, python, groups)
        result = {
            "status": _overall_status(cases, protected),
            "platform": platform.system(), "target_user": EXPECTED_USER,
            "target_uid": EXPECTED_UID, "target_gid": EXPECTED_GID,
            "target_groups": list(sorted((EXPECTED_GID, *groups))),
            "experiment_base": str(BASE), "experiment_directory": str(BASE / root_name),
            "base_filesystem": mount,
            "production_filesystem_read_only_comparison": production_metadata,
            "production_vs_experiment_comparison": _production_comparison(
                base_stat, mount, production_metadata),
            "cases": cases, "protected_child_rename": protected,
            "interpretation": "These isolated outcomes do not prove production path replacement or exploitability.",
        }
    except ProbeBlocked as exc:
        root_created = root_created or exc.created
        if exc.evidence and "held" not in exc.evidence:
            mount = exc.evidence
        recheck = {"base_mount_recheck": exc.evidence} if exc.evidence and "held" in exc.evidence else {}
        result = _status("BLOCKED", reason=exc.reason, base_filesystem=mount,
                         production_filesystem_read_only_comparison=production_metadata,
                         production_vs_experiment_comparison=_production_comparison(base_stat, mount, production_metadata),
                         **recheck,
                         **({"experiment_directory": experiment_path} if root_created else {}))
    except OSError as exc:
        result = _status("BLOCKED", reason=f"isolated experiment could not complete: {exc.errno or exc.__class__.__name__}",
                         partial_cases=cases,
                         base_filesystem=mount,
                         production_filesystem_read_only_comparison=production_metadata,
                         production_vs_experiment_comparison=_production_comparison(base_stat, mount, production_metadata),
                         **({"experiment_directory": experiment_path} if root_created else {}))
    finally:
        if root_fd is not None:
            try:
                residual = os.listdir(root_fd)
                cleanup = {"status": "PRESERVED_EMPTY_PRIVATE_DIRECTORY" if not residual else "BLOCKED",
                           "residual_entry_count": len(residual), "root_directory_removed": False}
                current = os.stat(root_name, dir_fd=base_fd, follow_symlinks=False)
                current_root = os.fstat(root_fd)
                path_identity_matches = (
                    _same_directory(current, root_stat) and _same_directory(current_root, root_stat)
                    and current.st_dev == current_root.st_dev == base_stat.st_dev
                    and current.st_ino == current_root.st_ino
                    and current.st_uid == current_root.st_uid == 0
                    and current.st_gid == current_root.st_gid)
                cleanup["root_path_identity_matches"] = path_identity_matches
                cleanup["root_mode"] = f"{stat.S_IMODE(current_root.st_mode):04o}"
                cleanup["root_retained_reason"] = (
                    "empty private directory intentionally retained; removing the outer entry by name "
                    "would race with writers to /vol1/1000")
                if not path_identity_matches or stat.S_IMODE(current_root.st_mode) != 0o700:
                    cleanup["status"] = "BLOCKED"
                os.close(root_fd)
                root_fd = None
                result["cleanup"] = cleanup
                if cleanup["status"] == "BLOCKED":
                    result["status"] = "BLOCKED"
                    result["cleanup_warning"] = "unknown or changed experiment entries were retained"
            except OSError:
                result["cleanup"] = {"status": "BLOCKED", "root_directory_removed": False}
                result["status"] = "BLOCKED"
                result["cleanup_warning"] = "experiment cleanup identity check failed; remaining entries were retained"
            if root_fd is not None:
                os.close(root_fd)
        elif root_created:
            result["cleanup"] = {"status": "BLOCKED", "root_directory_removed": False}
            result["experiment_directory"] = str(BASE / root_name)
            result["status"] = "BLOCKED"
            result["cleanup_warning"] = "new experiment directory identity could not be verified; retained for manual review"
        for fd in (base_fd, vol1_fd, root_path_fd):
            if fd is not None:
                os.close(fd)
    return result


def main() -> int:
    result = run_probe()
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result.get("status") == "COMPLETE" else (2 if result.get("status") == "BLOCKED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
