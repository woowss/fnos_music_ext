#!/usr/bin/env python3
"""Run a contained Unix-socket permission probe under /run on Linux."""

from __future__ import annotations

import errno
import json
import os
import secrets
import selectors
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path


RUN = Path("/run")
TARGET_USER = "kurumi"
TARGET_UID = 1000
TARGET_GID = 1001
TARGET_GROUPS = (1000, 1001)
TARGET_SUPPLEMENTARY = (1000,)
SOCKET_TIMEOUT = 2.0
PROCESS_TIMEOUT = 6.0
ROOT_RESPONSE = b"root-service\n"
FAKE_RESPONSE = b"fake-service\n"
KNOWN_SOCKET_NAMES = ("service.sock", "moved.sock")


class ProbeBlocked(RuntimeError):
    def __init__(self, reason: str, *, created: bool = False):
        super().__init__(reason)
        self.created = created


def _status(name: str, **values) -> dict:
    return {"status": name, **values}


def _directory_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not directory or not nofollow:
        raise ProbeBlocked("directory O_NOFOLLOW support is required")
    return os.O_RDONLY | directory | nofollow | getattr(os, "O_CLOEXEC", 0)


def _same_dir(left, right) -> bool:
    return (stat.S_ISDIR(left.st_mode) and stat.S_ISDIR(right.st_mode)
            and (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino))


def _same_dir_metadata(left, right) -> bool:
    return (_same_dir(left, right)
            and (left.st_uid, left.st_gid, stat.S_IMODE(left.st_mode))
            == (right.st_uid, right.st_gid, stat.S_IMODE(right.st_mode)))


def _same_object(left, right) -> bool:
    return ((left.st_dev, left.st_ino, left.st_uid, left.st_gid,
             stat.S_IFMT(left.st_mode))
            == (right.st_dev, right.st_ino, right.st_uid, right.st_gid,
                stat.S_IFMT(right.st_mode)))


def _open_child_dir(parent_fd: int, name: str):
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not stat.S_ISDIR(before.st_mode) or stat.S_ISLNK(before.st_mode):
        raise ProbeBlocked(f"{name} is not a real directory")
    fd = os.open(name, _directory_flags(), dir_fd=parent_fd)
    try:
        opened = os.fstat(fd)
        after = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not _same_dir(before, opened) or not _same_dir(after, opened):
            raise ProbeBlocked(f"{name} changed while opening")
        return fd, opened
    except BaseException:
        os.close(fd)
        raise


def _fd_mount_id(text: str) -> dict:
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
    records, malformed = [], 0
    for row in rows:
        fields = row.split()
        try:
            separator = fields.index("-")
            device = fields[2].split(":")
            if (separator < 6 or len(fields) < separator + 4
                    or not fields[0].isdigit() or not fields[1].isdigit()
                    or len(device) != 2 or not all(part.isdigit() for part in device)):
                malformed += 1
                continue
            records.append({"mount_id": fields[0], "device": fields[2],
                            "mountpoint": fields[4],
                            "filesystem_type": fields[separator + 1],
                            "source": fields[separator + 2]})
        except (ValueError, IndexError):
            malformed += 1
    if malformed:
        return {"status": "ANOMALOUS", "malformed_entry_count": malformed}
    matches = [entry for entry in records if entry["mount_id"] == str(mount_id)]
    if not matches:
        return {"status": "NOT FOUND", "mount_id": str(mount_id)}
    if len(matches) != 1:
        return {"status": "DUPLICATE", "mount_id": str(mount_id), "entry_count": len(matches)}
    return {"status": "read", **matches[0]}


def _mount_for_fd(fd: int) -> dict:
    try:
        fdinfo = Path(f"/proc/self/fdinfo/{fd}").read_text(encoding="ascii")
        descriptor = _fd_mount_id(fdinfo)
        if descriptor.get("status") != "read":
            return descriptor
        rows = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return {"status": "NOT FOUND", "errno": exc.errno}
    return _mount_record_for_id(descriptor["mount_id"], rows)


def _require_tmpfs(fd: int) -> dict:
    mount = _mount_for_fd(fd)
    if mount.get("status") != "read":
        raise ProbeBlocked(f"/run mount ID is not uniquely readable ({mount.get('status')})")
    if mount.get("filesystem_type") != "tmpfs":
        raise ProbeBlocked("/run descriptor is not on tmpfs")
    return mount


_ACL_TAGS = {1: "user_obj", 2: "user", 4: "group_obj", 8: "group", 16: "mask", 32: "other"}
_ACL_UNDEFINED = 0xFFFFFFFF


def _numeric_acl(fd: int, attribute: str) -> list[dict] | None:
    try:
        data = os.getxattr(fd, attribute)
    except OSError as exc:
        if exc.errno == errno.ENODATA:
            return None
        raise ProbeBlocked(f"cannot read {attribute} ({exc.errno or exc.__class__.__name__})") from exc
    if len(data) < 4 or (len(data) - 4) % 8 or struct.unpack_from("<I", data)[0] != 2:
        raise ProbeBlocked(f"{attribute} has an unsupported encoding")
    entries = []
    for offset in range(4, len(data), 8):
        tag, permissions, identifier = struct.unpack_from("<HHI", data, offset)
        name = _ACL_TAGS.get(tag)
        if name is None or permissions & ~0x7:
            raise ProbeBlocked(f"{attribute} contains an unknown ACL entry")
        is_named = name in ("user", "group")
        if (is_named and identifier == _ACL_UNDEFINED) or (not is_named and identifier != _ACL_UNDEFINED):
            raise ProbeBlocked(f"{attribute} contains an invalid ACL identifier")
        entries.append({"tag": name, "id": identifier if is_named else None, "perm": permissions})
    tags = [entry["tag"] for entry in entries]
    for required in ("user_obj", "group_obj", "other"):
        if tags.count(required) != 1:
            raise ProbeBlocked(f"{attribute} is missing or duplicates {required}")
    if tags.count("mask") > 1 or (any(tag in tags for tag in ("user", "group")) != ("mask" in tags)):
        raise ProbeBlocked(f"{attribute} has an invalid ACL mask")
    for tag in ("user", "group"):
        ids = [entry["id"] for entry in entries if entry["tag"] == tag]
        if len(ids) != len(set(ids)):
            raise ProbeBlocked(f"{attribute} duplicates a named {tag}")
    return entries


def _acl_write_allowed(info, acl: list[dict] | None) -> bool:
    if acl is None:
        if info.st_uid == TARGET_UID:
            permissions = (stat.S_IMODE(info.st_mode) >> 6) & 7
        elif info.st_gid in TARGET_GROUPS:
            permissions = (stat.S_IMODE(info.st_mode) >> 3) & 7
        else:
            permissions = stat.S_IMODE(info.st_mode) & 7
        return bool(permissions & 2)
    get = lambda tag, ident=None: next((entry["perm"] for entry in acl
                                        if entry["tag"] == tag and entry["id"] == ident), None)
    if info.st_uid == TARGET_UID:
        permissions = get("user_obj")
    else:
        named_user = get("user", TARGET_UID)
        mask = get("mask")
        if named_user is not None:
            permissions = named_user & (mask if mask is not None else 7)
            return bool(permissions & 2)
        group_permissions = 0
        group_match = False
        if info.st_gid in TARGET_GROUPS:
            group_match = True
            group_permissions |= get("group_obj") or 0
        for entry in acl:
            if entry["tag"] == "group" and entry["id"] in TARGET_GROUPS:
                group_match = True
                group_permissions |= entry["perm"]
        if group_match:
            return bool((group_permissions & (mask if mask is not None else 7)) & 2)
        permissions = get("other")
    return bool((permissions or 0) & 2)


def _acl_report(fd: int, info) -> dict:
    access = _numeric_acl(fd, "system.posix_acl_access")
    default = _numeric_acl(fd, "system.posix_acl_default")
    access_write = _acl_write_allowed(info, access)
    default_write = _acl_write_allowed(info, default) if default is not None else False
    if access_write or default_write:
        raise ProbeBlocked("UID 1000 has write permission on / or /run ACL")
    return {"access_acl": access if access is not None else "absent",
            "default_acl": default if default is not None else "absent",
            "uid_1000_access_write": False, "uid_1000_default_write": False}


def _local_target_groups() -> tuple[int, ...] | None:
    try:
        passwd = Path("/etc/passwd").read_text(encoding="utf-8").splitlines()
        group_lines = Path("/etc/group").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    user = None
    for line in passwd:
        fields = line.split(":")
        if len(fields) >= 4 and fields[0] == TARGET_USER:
            try:
                user = (int(fields[2]), int(fields[3]))
            except ValueError:
                return None
            break
    if user != (TARGET_UID, TARGET_GID):
        return None
    supplementary = set()
    for line in group_lines:
        fields = line.split(":")
        if len(fields) >= 4 and TARGET_USER in fields[3].split(","):
            try:
                supplementary.add(int(fields[2]))
            except ValueError:
                return None
    supplementary.discard(TARGET_GID)
    if tuple(sorted(supplementary)) != (1000,):
        return None
    return TARGET_GROUPS


def _target_identity_verified(identity: dict) -> bool:
    if not isinstance(identity, dict):
        return False
    try:
        uid = tuple(map(int, identity.get("uid", ())))
        gid = tuple(map(int, identity.get("gid", ())))
        groups = tuple(sorted(map(int, identity.get("groups", ()))))
    except (TypeError, ValueError):
        return False
    return (identity.get("verified") is True and uid == (TARGET_UID,) * 3
            and gid == (TARGET_GID,) * 3 and groups == TARGET_SUPPLEMENTARY
            and identity.get("capabilities_zero") is True)


def _target_observation_complete(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    connect = result.get("connect", {})
    rename = result.get("rename", {})
    if not isinstance(connect, dict) or not isinstance(rename, dict):
        return False
    return (_target_identity_verified(result.get("identity", {}))
            and result.get("fixture_reachable") is True
            and (connect.get("result") == "success"
                 or (connect.get("result") == "error" and isinstance(connect.get("errno"), int)))
            and (rename.get("result") == "success"
                 or (rename.get("result") == "error" and isinstance(rename.get("errno"), int))))


def _trusted_python() -> str | None:
    for candidate in (Path("/usr/bin/python3"), Path("/bin/python3"), Path("/usr/local/bin/python3")):
        try:
            resolved = candidate.resolve(strict=True)
            info = resolved.stat()
        except OSError:
            continue
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            continue
        parent, trusted = resolved.parent, True
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


def _runtime_preflight() -> dict:
    if not sys.platform.startswith("linux"):
        raise ProbeBlocked("Linux is required")
    if not hasattr(os, "geteuid") or os.geteuid() != 0:
        raise ProbeBlocked("run manually as root; the script does not invoke sudo")
    if _local_target_groups() != TARGET_GROUPS:
        raise ProbeBlocked("local UID 1000 identity or groups differ from the approved values")
    python = _trusted_python()
    if not python:
        raise ProbeBlocked("no trusted system Python executable found")

    root_fd = run_fd = None
    try:
        root_path_stat = os.stat("/", follow_symlinks=False)
        root_fd = os.open("/", _directory_flags())
        root_stat = os.fstat(root_fd)
        if (not _same_dir_metadata(root_path_stat, root_stat) or root_stat.st_uid != 0
                or root_stat.st_gid != 0 or stat.S_IMODE(root_stat.st_mode) != 0o755):
            raise ProbeBlocked("/ must be the same root:root directory with mode 0755")
        run_entry = os.stat("run", dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISDIR(run_entry.st_mode) or stat.S_ISLNK(run_entry.st_mode):
            raise ProbeBlocked("/run is not a real directory")
        run_fd = os.open("run", _directory_flags(), dir_fd=root_fd)
        run_stat = os.fstat(run_fd)
        run_after = os.stat("run", dir_fd=root_fd, follow_symlinks=False)
        if (not _same_dir_metadata(run_entry, run_stat) or not _same_dir_metadata(run_after, run_stat)
                or run_stat.st_uid != 0 or run_stat.st_gid != 0
                or stat.S_IMODE(run_stat.st_mode) != 0o755):
            raise ProbeBlocked("/run must be the same root:root directory with mode 0755")
        root_acl = _acl_report(root_fd, root_stat)
        run_acl = _acl_report(run_fd, run_stat)
        mount = _require_tmpfs(run_fd)
        return {"root_fd": root_fd, "run_fd": run_fd, "root_stat": root_stat,
                "run_stat": run_stat, "root_acl": root_acl, "run_acl": run_acl,
                "mount": mount, "python": python}
    except BaseException:
        for fd in (run_fd, root_fd):
            if fd is not None:
                os.close(fd)
        raise


def _clear_acls(fd: int) -> None:
    for attribute in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.removexattr(fd, attribute)
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise


def _object_identity(info) -> dict:
    kind = "socket" if stat.S_ISSOCK(info.st_mode) else "directory" if stat.S_ISDIR(info.st_mode) else "other"
    return {"device": info.st_dev, "inode": info.st_ino, "uid": info.st_uid,
            "gid": info.st_gid, "type": kind}


def _matches_identity(info, identity: dict) -> bool:
    return (_object_identity(info) == identity)


def _remove_known(directory_fd: int, name: str, identity: dict) -> bool:
    try:
        info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except OSError:
        return False
    if not _matches_identity(info, identity) or identity.get("type") != "socket":
        return False
    try:
        os.unlink(name, dir_fd=directory_fd)
        return True
    except OSError:
        return False


def _lock_case_dir(root_fd: int, case_name: str, case_fd: int, identity: dict) -> bool:
    try:
        before = os.stat(case_name, dir_fd=root_fd, follow_symlinks=False)
        opened = os.fstat(case_fd)
        if not _matches_identity(before, identity) or not _matches_identity(opened, identity):
            return False
        _clear_acls(case_fd)
        os.fchmod(case_fd, 0o700)
        after = os.stat(case_name, dir_fd=root_fd, follow_symlinks=False)
        return (_matches_identity(after, identity) and stat.S_IMODE(os.fstat(case_fd).st_mode) == 0o700)
    except (OSError, ProbeBlocked):
        return False


def _cleanup_case(root_fd: int, case_name: str, case_fd: int,
                  case_identity: dict, known: dict[str, dict]) -> str:
    if not _lock_case_dir(root_fd, case_name, case_fd, case_identity):
        return "BLOCKED"
    try:
        names = set(os.listdir(case_fd))
    except OSError:
        return "BLOCKED"
    if names != set(known):
        return "BLOCKED"
    for name, identity in known.items():
        try:
            current = os.stat(name, dir_fd=case_fd, follow_symlinks=False)
        except OSError:
            return "BLOCKED"
        if not _matches_identity(current, identity):
            return "BLOCKED"
    for name, identity in known.items():
        if not _remove_known(case_fd, name, identity):
            return "BLOCKED"
    try:
        if os.listdir(case_fd):
            return "BLOCKED"
        current = os.stat(case_name, dir_fd=root_fd, follow_symlinks=False)
        opened = os.fstat(case_fd)
        if not _matches_identity(current, case_identity) or not _matches_identity(opened, case_identity):
            return "BLOCKED"
        os.rmdir(case_name, dir_fd=root_fd)
    except OSError:
        return "BLOCKED"
    return "COMPLETE"


def _make_root_server(case_fd: int):
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.settimeout(SOCKET_TIMEOUT)
    try:
        server.bind(f"/proc/self/fd/{case_fd}/service.sock")
        os.chmod("service.sock", 0o600, dir_fd=case_fd, follow_symlinks=False)
        info = os.stat("service.sock", dir_fd=case_fd, follow_symlinks=False)
        if (not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != 0
                or stat.S_IMODE(info.st_mode) != 0o600):
            raise ProbeBlocked("root service socket owner, type, or mode did not match")
        server.listen(2)
    except BaseException:
        server.close()
        raise
    result = {"clients": [], "first_error": None, "second_client": "pending"}

    def serve_one():
        for index in range(2):
            try:
                conn, _ = server.accept()
                with conn:
                    peer = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
                    pid, uid, gid = struct.unpack("3i", peer)
                    result["clients"].append({"peer_pid": pid, "peer_uid": uid, "peer_gid": gid,
                                              "response": ROOT_RESPONSE.decode()})
                    conn.sendall(ROOT_RESPONSE)
                    if index == 1:
                        result["second_client"] = "received"
            except OSError as exc:
                if index == 0:
                    result["first_error"] = exc.errno or exc.__class__.__name__
                else:
                    result["second_client"] = "NOT RECEIVED WITHIN TIMEOUT"
                break

    thread = threading.Thread(target=serve_one, daemon=True)
    thread.start()
    return server, _object_identity(info), result, thread


def _child_code() -> str:
    return r'''import errno,json,os,socket,stat,sys
uid=int(sys.argv[1]); gid=int(sys.argv[2]); groups=tuple(map(int,sys.argv[3].split(",")))
base=sys.argv[4]; control=sys.argv[5]; caps={}
for line in open("/proc/self/status",encoding="ascii"):
 key,_,value=line.partition(":")
 if key in ("CapEff","CapPrm","CapInh","CapAmb"): caps[key]=int(value.strip(),16)
u=os.getresuid(); g=os.getresgid(); actual=tuple(sorted(os.getgroups()))
identity={"verified":u==(uid,)*3 and g==(gid,)*3 and actual==groups and len(caps)==4 and all(v==0 for v in caps.values()),
 "uid":u,"gid":g,"groups":actual,"capabilities_zero":len(caps)==4 and all(v==0 for v in caps.values())}
out={"identity":identity,"fixture_reachable":False}
if not identity["verified"]:
 print(json.dumps({"event":"result",**out}),flush=True); raise SystemExit(40)
service=os.path.join(base,"service.sock"); moved=os.path.join(base,"moved.sock")
try: os.stat(base)
except OSError as e:
 out["fixture_errno"]=e.errno; print(json.dumps({"event":"result",**out}),flush=True); raise SystemExit(41)
out["fixture_reachable"]=True
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); s.settimeout(1.5)
try:
 s.connect(service); out["connect"]={"result":"success"}
 try: out["connect"]["response"]=s.recv(64).decode("ascii","replace")
 except OSError as e: out["connect"]["response_error"]=e.errno
except OSError as e: out["connect"]={"result":"error","errno":e.errno,"name":errno.errorcode.get(e.errno,"OTHER")}
finally: s.close()
try: os.rename(service,moved); out["rename"]={"result":"success"}
except OSError as e: out["rename"]={"result":"error","errno":e.errno,"name":errno.errorcode.get(e.errno,"OTHER")}
if control=="positive" and out["rename"]["result"]=="success":
 fake=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM); fake.settimeout(3)
 try:
  fake.bind(service); os.chmod(service,0o600); fake.listen(1)
  info=os.stat(service,follow_symlinks=False)
  out["replacement"]={"result":"success",**{"device":info.st_dev,"inode":info.st_ino,"uid":info.st_uid,"gid":info.st_gid,"type":"socket"}}
  print(json.dumps({"event":"ready",**out}),flush=True)
  conn,_=fake.accept()
  with conn: conn.sendall(b"fake-service\n")
  print(json.dumps({"event":"done","fake_server":"served"}),flush=True)
 except OSError as e:
  out["replacement"]={"result":"error","errno":e.errno,"name":errno.errorcode.get(e.errno,"OTHER")}
  print(json.dumps({"event":"result",**out}),flush=True)
 finally: fake.close()
else:
 if control=="positive": out["replacement"]={"result":"NOT RUN"}
 print(json.dumps({"event":"result",**out}),flush=True)
'''


def _child_argv(python: str, case_path: str, positive: bool) -> list[str]:
    return [python, "-I", "-S", "-c", _child_code(), str(TARGET_UID), str(TARGET_GID),
            ",".join(map(str, TARGET_SUPPLEMENTARY)), case_path,
            "positive" if positive else "negative"]


def _read_line_bounded(stream, timeout: float) -> str:
    selector = selectors.DefaultSelector()
    deadline = time.monotonic() + timeout
    pending = bytearray()
    try:
        selector.register(stream.fileno(), selectors.EVENT_READ)
        while len(pending) <= 65536:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                raise TimeoutError("child did not report readiness")
            chunk = os.read(stream.fileno(), 4096)
            if not chunk:
                raise EOFError("child exited before readiness")
            pending.extend(chunk)
            if b"\n" in pending:
                line, trailing = bytes(pending).split(b"\n", 1)
                if trailing:
                    raise ValueError("child sent unexpected data before root-client exchange")
                return line.decode("utf-8", "strict") + "\n"
        raise ValueError("child readiness record exceeded its size limit")
    finally:
        selector.close()


def _kill_reap(process) -> bool:
    try:
        process.terminate()
        process.wait(timeout=1)
        return True
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=1)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    except OSError:
        return process.poll() is not None


def _root_client(case_path: str) -> dict:
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(SOCKET_TIMEOUT)
    try:
        client.connect(os.path.join(case_path, "service.sock"))
        peer = client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", peer)
        response = client.recv(64).decode("ascii", "replace")
        return {"result": "success", "peer_pid": pid, "peer_uid": uid,
                "peer_gid": gid, "response": response}
    except OSError as exc:
        return {"result": "error", "errno": exc.errno, "name": errno.errorcode.get(exc.errno, "OTHER")}
    finally:
        client.close()


def _run_target(python: str, case_path: str, positive: bool) -> tuple[dict, int, dict]:
    try:
        process = subprocess.Popen(
            _child_argv(python, case_path, positive), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, close_fds=True,
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, user=TARGET_UID,
            group=TARGET_GID, extra_groups=[1000], start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        return {"status": "BLOCKED", "reason": exc.errno or exc.__class__.__name__}, 127, {"reaped": True}
    result, client = {}, {"result": "NOT RUN"}
    try:
        if positive:
            first = json.loads(_read_line_bounded(process.stdout, PROCESS_TIMEOUT))
            if first.get("event") == "ready":
                result.update(first)
                client = _root_client(case_path)
                tail, _ = process.communicate(timeout=PROCESS_TIMEOUT)
                lines = tail.decode("utf-8", "replace").splitlines()
                if lines:
                    result["fake_server"] = json.loads(lines[-1]).get("fake_server", "unknown")
            else:
                result.update(first)
                process.communicate(timeout=PROCESS_TIMEOUT)
        else:
            output, _ = process.communicate(timeout=PROCESS_TIMEOUT)
            lines = output.decode("utf-8", "replace").splitlines()
            if lines:
                result.update(json.loads(lines[-1]))
        return result, process.returncode, {"reaped": True, "root_client": client}
    except (OSError, ValueError, TimeoutError, EOFError, subprocess.TimeoutExpired) as exc:
        reaped = _kill_reap(process)
        return {"status": "BLOCKED", "reason": exc.__class__.__name__}, process.poll(), {
            "reaped": reaped, "root_client": client}


def _root_service_baseline_valid(root_client: dict, root_service: dict) -> bool:
    if not isinstance(root_client, dict) or not isinstance(root_service, dict):
        return False
    clients = root_service.get("clients", [])
    first = clients[0] if isinstance(clients, list) and clients else {}
    return (root_client.get("result") == "success" and root_client.get("peer_uid") == 0
            and root_client.get("peer_gid") == 0 and root_client.get("response") == ROOT_RESPONSE.decode()
            and first.get("peer_uid") == 0 and first.get("peer_gid") == 0
            and first.get("response") == ROOT_RESPONSE.decode())


def _root_service_target_valid(result: dict, root_service: dict) -> bool:
    connect = result.get("connect", {}) if isinstance(result, dict) else {}
    clients = root_service.get("clients", []) if isinstance(root_service, dict) else []
    if not isinstance(connect, dict) or not isinstance(clients, list):
        return False
    if connect.get("result") == "success":
        second = clients[1] if len(clients) > 1 else {}
        return (second.get("peer_uid") == TARGET_UID and second.get("peer_gid") == TARGET_GID
                and second.get("response") == ROOT_RESPONSE.decode()
                and connect.get("response") == ROOT_RESPONSE.decode())
    return (connect.get("result") == "error" and isinstance(connect.get("errno"), int)
            and connect.get("errno") in (errno.EACCES, errno.EPERM)
            and len(clients) == 1 and root_service.get("second_client") == "NOT RECEIVED WITHIN TIMEOUT")


def _permission_denied_observation(result: dict) -> bool:
    if not isinstance(result, dict):
        return False
    for key in ("connect", "rename"):
        operation = result.get(key, {})
        if not isinstance(operation, dict):
            return False
        if operation.get("result") == "success":
            continue
        if operation.get("result") != "error" or operation.get("errno") not in (errno.EACCES, errno.EPERM):
            return False
    return True


def _positive_confirmed(result: dict, root_client: dict, root_service: dict,
                        root_service_client: dict) -> bool:
    if not all(isinstance(item, dict) for item in (result, root_client, root_service)):
        return False
    return (_target_observation_complete(result)
            and _root_service_baseline_valid(root_service_client, root_service)
            and _root_service_target_valid(result, root_service)
            and result.get("rename", {}).get("result") == "success"
            and result.get("replacement", {}).get("result") == "success"
            and root_client.get("result") == "success"
            and root_client.get("peer_uid") == TARGET_UID
            and root_client.get("peer_gid") == TARGET_GID
            and root_client.get("response") == FAKE_RESPONSE.decode()
            and result.get("fake_server") == "served")


def _overall_status(cases: list[dict], positive: str, cleanup: str) -> str:
    if cleanup != "COMPLETE":
        return "BLOCKED"
    if any(case.get("status") == "BLOCKED" for case in cases):
        return "BLOCKED"
    if any(case.get("status") == "UNSAFE_OBSERVED" for case in cases):
        return "UNSAFE_OBSERVED"
    if positive != "CONFIRMED" or any(case.get("status") != "COMPLETE" for case in cases):
        return "PARTIAL"
    return "COMPLETE"


def _create_case_dir(root_fd: int, name: str, mode: int, run_device: int):
    os.mkdir(name, mode, dir_fd=root_fd)
    entry = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    if (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode)
            or entry.st_uid != 0 or entry.st_dev != run_device):
        raise ProbeBlocked(f"{name} identity did not match after mkdir")
    fd = os.open(name, _directory_flags(), dir_fd=root_fd)
    try:
        opened = os.fstat(fd)
        if not _same_dir(entry, opened) or opened.st_uid != 0 or opened.st_dev != run_device:
            raise ProbeBlocked(f"{name} identity did not match after open")
        _clear_acls(fd)
        os.fchmod(fd, mode)
        opened = os.fstat(fd)
        if stat.S_IMODE(opened.st_mode) != mode:
            raise ProbeBlocked(f"{name} mode did not match")
        return fd, _object_identity(opened)
    except BaseException:
        os.close(fd)
        raise


def _create_lab_root(run_fd: int, name: str, run_stat):
    os.mkdir(name, 0o711, dir_fd=run_fd)
    fd = None
    try:
        entry = os.stat(name, dir_fd=run_fd, follow_symlinks=False)
        if (not stat.S_ISDIR(entry.st_mode) or stat.S_ISLNK(entry.st_mode)
                or entry.st_uid != 0 or entry.st_dev != run_stat.st_dev):
            raise ProbeBlocked("experiment root entry identity did not match")
        fd = os.open(name, _directory_flags(), dir_fd=run_fd)
        opened = os.fstat(fd)
        if not _same_dir(entry, opened) or opened.st_uid != 0 or opened.st_dev != run_stat.st_dev:
            raise ProbeBlocked("experiment root descriptor identity did not match")
        _clear_acls(fd)
        os.fchmod(fd, 0o711)
        opened = os.fstat(fd)
        if stat.S_IMODE(opened.st_mode) != 0o711:
            raise ProbeBlocked("experiment root mode did not match")
        return fd, _object_identity(opened)
    except ProbeBlocked as exc:
        if fd is not None:
            os.close(fd)
        raise ProbeBlocked(str(exc), created=True) from exc
    except OSError as exc:
        if fd is not None:
            os.close(fd)
        raise ProbeBlocked(f"experiment root verification failed ({exc.errno or exc.__class__.__name__})",
                           created=True) from exc


def _verify_runtime_paths(context: dict) -> None:
    root_path = os.stat("/", follow_symlinks=False)
    root_fd_stat = os.fstat(context["root_fd"])
    run_path = os.stat("run", dir_fd=context["root_fd"], follow_symlinks=False)
    run_fd_stat = os.fstat(context["run_fd"])
    if (not _same_dir_metadata(root_path, root_fd_stat)
            or not _same_dir_metadata(run_path, run_fd_stat)
            or root_fd_stat.st_uid != 0 or root_fd_stat.st_gid != 0
            or stat.S_IMODE(root_fd_stat.st_mode) != 0o755
            or run_fd_stat.st_uid != 0 or run_fd_stat.st_gid != 0
            or stat.S_IMODE(run_fd_stat.st_mode) != 0o755):
        raise ProbeBlocked("/ or /run path identity changed before experiment creation")
    mount = _require_tmpfs(context["run_fd"])
    if mount.get("mount_id") != context["mount"].get("mount_id"):
        raise ProbeBlocked("/run mount ID changed before experiment creation")
    root_acl = _acl_report(context["root_fd"], root_fd_stat)
    run_acl = _acl_report(context["run_fd"], run_fd_stat)
    if root_acl["uid_1000_access_write"] or root_acl["uid_1000_default_write"] \
            or run_acl["uid_1000_access_write"] or run_acl["uid_1000_default_write"]:
        raise ProbeBlocked("UID 1000 became writable on / or /run")
    context["root_acl"] = root_acl
    context["run_acl"] = run_acl
    context["mount"] = mount


def _run_lab(context: dict) -> dict:
    run_fd, root_path_fd = context["run_fd"], context["root_fd"]
    lab_name = "sock-path-probe-" + secrets.token_hex(16)
    lab_path = str(RUN / lab_name)
    lab_fd = None
    lab_identity = None
    lab_created = False
    cases, cleanup_results = [], []
    positive = "NOT CONFIRMED"
    result = {"status": "BLOCKED", "reason": "experiment did not finish"}
    try:
        _verify_runtime_paths(context)
        lab_fd, lab_identity = _create_lab_root(run_fd, lab_name, context["run_stat"])
        lab_created = True
        for case_name, mode in (("parent-0755", 0o755), ("parent-0777", 0o777)):
            case_fd = None
            server = None
            thread = None
            known = {}
            case_identity = None
            launch = {"reaped": True}
            case_result = {"name": case_name, "status": "PENDING"}
            root_service = {"clients": [], "first_error": None, "second_client": "pending"}
            root_service_client = {"result": "NOT RUN"}
            child = {}
            try:
                case_fd, case_identity = _create_case_dir(lab_fd, case_name, mode, context["run_stat"].st_dev)
                server, original_identity, root_service, thread = _make_root_server(case_fd)
                known["service.sock"] = original_identity
                case_path = str(RUN / lab_name / case_name)
                root_service_client = _root_client(case_path)
                case_result["root_socket_client"] = root_service_client
                if not _root_service_baseline_valid(root_service_client, root_service):
                    raise ProbeBlocked("root-owned mode-0600 service failed its root-client baseline")
                child, child_rc, launch = _run_target(context["python"], case_path, case_name == "parent-0777")
                case_result.update(child)
                rename = child.get("rename", {})
                unexpected = (case_name == "parent-0755"
                              and (child.get("connect", {}).get("result") == "success"
                                   or rename.get("result") == "success"))
                if unexpected:
                    case_result["status"] = "UNSAFE_OBSERVED"
                if not launch.get("reaped"):
                    case_result.update(status="BLOCKED", reason="target child was not reaped")
                elif not _target_identity_verified(child.get("identity", {})) or child_rc != 0:
                    if not unexpected:
                        case_result.update(status="BLOCKED", reason="target child identity or exit status failed")
                elif child.get("fixture_reachable") is not True:
                    if not unexpected:
                        case_result.update(status="BLOCKED", reason="target could not reach the isolated fixture")
                elif not _target_observation_complete(child):
                    if not unexpected:
                        case_result.update(status="PARTIAL", reason="connect or rename result was not completed")
                else:
                    if rename.get("result") == "success":
                        known.pop("service.sock", None)
                        known["moved.sock"] = original_identity
                    replacement = child.get("replacement", {})
                    if replacement.get("result") == "success":
                        info = os.stat("service.sock", dir_fd=case_fd, follow_symlinks=False)
                        candidate = _object_identity(info)
                        if (candidate.get("type") != "socket" or candidate.get("uid") != TARGET_UID
                                or candidate.get("gid") != TARGET_GID
                                or candidate.get("device") != replacement.get("device")
                                or candidate.get("inode") != replacement.get("inode")):
                            case_result.update(status="BLOCKED", reason="fake socket identity did not match")
                        else:
                            known["service.sock"] = candidate
                    if case_name == "parent-0755":
                        case_result["status"] = "UNSAFE_OBSERVED" if unexpected else "COMPLETE"
                    else:
                        root_client = launch.get("root_client", {})
                        positive = ("CONFIRMED" if _positive_confirmed(
                            child, root_client, root_service, root_service_client)
                                    else "NOT CONFIRMED")
                        case_result["root_client"] = root_client
                        case_result["positive_control"] = positive
                        if case_result.get("status") != "BLOCKED":
                            case_result["status"] = "COMPLETE" if positive == "CONFIRMED" else "PARTIAL"
            except (OSError, ProbeBlocked, ValueError) as exc:
                case_result.update(status="BLOCKED", reason=(exc.__class__.__name__))
            finally:
                thread_stopped = thread is None
                if thread is not None:
                    try:
                        thread.join(timeout=SOCKET_TIMEOUT + 0.5)
                        thread_stopped = not thread.is_alive()
                    except RuntimeError:
                        thread_stopped = False
                    if not thread_stopped:
                        if server is not None:
                            try:
                                server.close()
                            except OSError:
                                pass
                            try:
                                thread.join(timeout=0.5)
                                thread_stopped = not thread.is_alive()
                            except RuntimeError:
                                thread_stopped = False
                        if not thread_stopped:
                            case_result.update(status="BLOCKED", reason="root service thread did not stop")
                if server is not None:
                    try:
                        server.close()
                    except OSError:
                        case_result.update(status="BLOCKED", reason="root service close failed")
                case_result["root_service"] = root_service.copy()
                if (thread_stopped and launch.get("reaped") is True
                        and case_result.get("status") not in ("BLOCKED", "UNSAFE_OBSERVED")
                        and child):
                    if not _root_service_target_valid(child, root_service):
                        case_result.update(status="BLOCKED", reason="root service did not complete target observation")
                    elif case_name == "parent-0755" and not _permission_denied_observation(child):
                        case_result.update(status="PARTIAL", reason="target operation returned a non-permission result")
                    elif case_name == "parent-0777":
                        root_client = launch.get("root_client", {})
                        positive = ("CONFIRMED" if _positive_confirmed(
                            child, root_client, root_service, root_service_client)
                                    else "NOT CONFIRMED")
                        case_result["positive_control"] = positive
                        case_result["status"] = "COMPLETE" if positive == "CONFIRMED" else "PARTIAL"
                if case_fd is not None and case_identity is not None \
                        and launch.get("reaped") is True and thread_stopped:
                    try:
                        cleanup_results.append(_cleanup_case(lab_fd, case_name, case_fd,
                                                              case_identity, known))
                    except (OSError, ProbeBlocked):
                        cleanup_results.append("BLOCKED")
                else:
                    cleanup_results.append("BLOCKED")
                if case_fd is not None:
                    try:
                        os.close(case_fd)
                    except OSError:
                        case_result.update(status="BLOCKED", reason="case directory close failed")
            cases.append(case_result)
            if (case_result.get("status") != "COMPLETE"
                    or cleanup_results[-1] != "COMPLETE"):
                break
        cleanup_status = "COMPLETE" if cleanup_results and all(item == "COMPLETE" for item in cleanup_results) else "BLOCKED"
        result = {"status": _overall_status(cases, positive, cleanup_status),
                  "platform": "Linux", "target_uid": TARGET_UID, "target_gid": TARGET_GID,
                  "target_groups": list(TARGET_GROUPS), "experiment_directory": lab_path,
                  "run_mount": context["mount"], "root_acl": context["root_acl"],
                  "run_acl": context["run_acl"], "cases": cases,
                  "cleanup": {"status": cleanup_status, "experiment_root_removed": False}}
    except (OSError, ProbeBlocked) as exc:
        lab_created = lab_created or getattr(exc, "created", False)
        result = {"status": "BLOCKED", "reason": exc.__class__.__name__,
                  "experiment_directory": lab_path if lab_created else None,
                  "partial_cases": cases,
                  "cleanup": {"status": "BLOCKED", "experiment_root_removed": False}}
    finally:
        if lab_fd is not None:
            try:
                entries = os.listdir(lab_fd)
                path_entry = os.stat(lab_name, dir_fd=run_fd, follow_symlinks=False)
                opened = os.fstat(lab_fd)
                root_ok = (_matches_identity(path_entry, lab_identity)
                           and _matches_identity(opened, lab_identity)
                           and opened.st_dev == context["run_stat"].st_dev
                           and stat.S_IMODE(opened.st_mode) == 0o711)
                if entries or not root_ok:
                    result["status"] = "BLOCKED"
                    result["cleanup"] = {"status": "BLOCKED", "residual_entries": len(entries),
                                          "experiment_root_removed": False}
                else:
                    result.setdefault("cleanup", {})
                    result["cleanup"].update(status="PRESERVED_EMPTY_ROOT",
                                              residual_entries=0, experiment_root_removed=False)
                os.close(lab_fd)
            except OSError:
                result["status"] = "BLOCKED"
                result["cleanup"] = {"status": "BLOCKED", "experiment_root_removed": False}
    return result


def run_probe() -> dict:
    context = None
    try:
        context = _runtime_preflight()
        return _run_lab(context)
    except (ProbeBlocked, OSError) as exc:
        return _status("BLOCKED", reason=(str(exc) if isinstance(exc, ProbeBlocked)
                                           else f"preflight failed ({exc.errno or exc.__class__.__name__})"))
    finally:
        if context is not None:
            for fd in (context.get("run_fd"), context.get("root_fd")):
                if fd is not None:
                    os.close(fd)


def main() -> int:
    result = run_probe()
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result.get("status") in ("COMPLETE", "UNSAFE_OBSERVED") else 2 if result.get("status") == "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
