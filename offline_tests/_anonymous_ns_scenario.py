#!/usr/bin/env python3
"""Real-kernel failure scenarios for the anonymous staging candidate.

Executed only by ``offline_tests.test_manual_save_anonymous`` inside a private
unprivileged user+mount namespace (``unshare --user --map-root-user --mount``),
so the tmpfs mounts, the read-only bind mount, and the exhausted filesystem are
real kernel objects that disappear when this process exits. The helper writes
only inside the private tmpfs and never touches device services, ``/vol1``, the
production ledger, or the network.

Usage::

    _anonymous_ns_scenario.py <scenario> <base-dir> <repo-root>

Scenarios: ``erofs-destination``, ``erofs-staging``, ``enospc``.
The last stdout line is one JSON object; exit status 0 only when every
in-helper assertion passed.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from offline_tests._audit_support import WriteAudit  # noqa: E402 - path set above


def _run(args):
    completed = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command_failed:{args[0]}:{completed.returncode}:{completed.stderr.strip()[:300]}"
        )
    return completed


def _mount(*args):
    _run(["mount", *args])


def _mkdir(path):
    os.mkdir(path, 0o700)
    os.chmod(path, 0o700)


def _open_directory(path):
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)


def _entries(directory_fd):
    try:
        return sorted(os.listdir(directory_fd))
    except OSError as exc:
        return [f"ERROR:{exc.errno}"]


def _is_mount_read_only(path):
    try:
        return bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    except OSError:
        return None


def _close_test_descriptors(*descriptors):
    for descriptor in descriptors:
        if descriptor < 0:
            continue
        try:
            os.close(descriptor)
        except OSError:
            pass


def _erofs_destination(base, anonymous, core):
    """Real EROFS: destination is a read-only bind mount on the same tmpfs."""
    _mount("-t", "tmpfs", "-o", "size=8m", "tmpfs", base)
    staging = os.path.join(base, "staging")
    destination = os.path.join(base, "destination")
    read_only_view = os.path.join(base, "read-only-view")
    _mkdir(staging)
    _mkdir(destination)
    _mkdir(read_only_view)
    _mount("--bind", destination, read_only_view)
    _mount("-o", "remount,bind,ro", read_only_view)
    staging_fd = _open_directory(staging)
    destination_fd = _open_directory(destination)
    read_only_fd = _open_directory(read_only_view)
    try:
        staging_info = os.fstat(staging_fd)
        destination_info = os.fstat(destination_fd)
        read_only_info = os.fstat(read_only_fd)
        report = {
            "scenario": "erofs-destination",
            "euid_inside_namespace": os.geteuid(),
            "devices_equal": (
                staging_info.st_dev == destination_info.st_dev == read_only_info.st_dev
            ),
            "read_only_view_is_read_only": _is_mount_read_only(read_only_view),
        }

        payload = b"synthetic read-only filesystem payload"
        store = anonymous.AnonymousLinuxStore("target-1", read_only_fd, staging_fd)
        handle = store.begin_owned_stage("target-1")
        store.append(handle, payload)
        store.seal(handle)
        stage = store._stages[handle]
        audit = WriteAudit()
        audit.active = True
        try:
            store.publish_noreplace(handle, "read-only-destination.mp3")
            report["adapter_reason"] = None
        except anonymous.PublicationUncertain as exc:
            report["adapter_reason"] = exc.reason
        finally:
            audit.active = False
        report.update({
            "adapter_publication_attempted": stage.publication_attempted,
            "adapter_link_created": stage.link_created,
            "adapter_publication_confirmed": stage.publication_confirmed,
            "adapter_audit_write_violations": audit.violations(),
        })
        try:
            store.close()
            report["adapter_close_reason"] = None
        except anonymous.PublicationUncertain as exc:
            report["adapter_close_reason"] = exc.reason

        # Contract-level outcome for the same real EROFS via the offline core.
        core_store = anonymous.AnonymousLinuxStore("target-1", read_only_fd, staging_fd)
        plan = core.plan_single_file("target-1", "read-only-core.mp3", "mp3", len(payload))
        core_audit = WriteAudit()
        core_audit.active = True
        try:
            result = core.execute_single_file(plan, [payload], core_store)
        finally:
            core_audit.active = False
        report.update({
            "core_outcome": result.outcome.value,
            "core_reason": result.reason,
            "core_cleanup_pending": result.cleanup_pending,
            "core_stage_retained": len(core_store._stages),
            "core_audit_write_violations": core_audit.violations(),
        })
        try:
            core_store.close()
            report["core_store_close_reason"] = None
        except anonymous.PublicationUncertain as exc:
            report["core_store_close_reason"] = exc.reason

        report["staging_entries"] = _entries(staging_fd)
        report["destination_entries"] = _entries(destination_fd)
        report["read_only_view_entries"] = _entries(read_only_fd)
        return report
    finally:
        _close_test_descriptors(staging_fd, destination_fd, read_only_fd)


def _erofs_staging(base, anonymous, _core):
    """Real EROFS: O_TMPFILE is attempted on a read-only staging mount."""
    _mount("-t", "tmpfs", "-o", "size=8m", "tmpfs", base)
    staging_source = os.path.join(base, "staging-source")
    staging_view = os.path.join(base, "staging-view")
    destination = os.path.join(base, "destination")
    _mkdir(staging_source)
    _mkdir(staging_view)
    _mkdir(destination)
    _mount("--bind", staging_source, staging_view)
    _mount("-o", "remount,bind,ro", staging_view)
    staging_fd = _open_directory(staging_view)
    staging_source_fd = _open_directory(staging_source)
    destination_fd = _open_directory(destination)
    try:
        report = {
            "scenario": "erofs-staging",
            "euid_inside_namespace": os.geteuid(),
            "staging_view_is_read_only": _is_mount_read_only(staging_view),
            "devices_equal": os.fstat(staging_fd).st_dev == os.fstat(destination_fd).st_dev,
        }
        store = anonymous.AnonymousLinuxStore("target-1", destination_fd, staging_fd)
        audit = WriteAudit()
        audit.active = True
        try:
            store.begin_owned_stage("target-1")
            report["begin_reason"] = None
        except anonymous.UnsupportedAnonymousStaging as exc:
            report["begin_reason"] = exc.reason
        finally:
            audit.active = False
        report.update({
            "begin_error_text_is_fixed": report["begin_reason"] is not None,
            "stage_registered": len(store._stages),
            "audit_write_violations": audit.violations(),
        })
        try:
            store.close()
            report["store_close_reason"] = None
        except anonymous.PublicationUncertain as exc:
            report["store_close_reason"] = exc.reason
        report["staging_entries"] = _entries(staging_source_fd)
        report["destination_entries"] = _entries(destination_fd)
        return report
    finally:
        _close_test_descriptors(staging_fd, staging_source_fd, destination_fd)


def _enospc(base, anonymous, core):
    """Real ENOSPC: a 64 KiB tmpfs exhausted by the staging write."""
    _mount("-t", "tmpfs", "-o", "size=64k", "tmpfs", base)
    staging = os.path.join(base, "staging")
    destination = os.path.join(base, "destination")
    _mkdir(staging)
    _mkdir(destination)
    staging_fd = _open_directory(staging)
    destination_fd = _open_directory(destination)
    try:
        report = {
            "scenario": "enospc",
            "euid_inside_namespace": os.geteuid(),
        }
        payload = b"x" * (256 * 1024)
        store = anonymous.AnonymousLinuxStore("target-1", destination_fd, staging_fd)
        handle = store.begin_owned_stage("target-1")
        stage = store._stages[handle]
        audit = WriteAudit()
        audit.active = True
        try:
            store.append(handle, payload)
            report["append_reason"] = None
        except RuntimeError as exc:
            report["append_reason"] = str(exc)
        finally:
            audit.active = False
        report.update({
            "append_reason_is_fixed": report["append_reason"] == "stage_write_failed",
            "stage_sealed_after_failure": stage.sealed,
            "stage_publication_attempted": stage.publication_attempted,
            "stage_link_count_after_failure": os.fstat(stage.fd).st_nlink,
            "audit_write_violations": audit.violations(),
        })
        try:
            store.abort_owned_stage(handle)
            report["abort_succeeded"] = True
        except Exception as exc:  # noqa: BLE001 - record, the test asserts
            report["abort_succeeded"] = False
            report["abort_error"] = f"{type(exc).__name__}:{exc}"
        try:
            store.close()
            report["store_close_succeeded"] = True
        except Exception as exc:  # noqa: BLE001
            report["store_close_succeeded"] = False
            report["store_close_error"] = f"{type(exc).__name__}:{exc}"

        # Contract-level outcome for the same real ENOSPC via the offline core.
        core_store = anonymous.AnonymousLinuxStore("target-1", destination_fd, staging_fd)
        plan = core.plan_single_file("target-1", "enospc-core.mp3", "mp3", len(payload))
        core_audit = WriteAudit()
        core_audit.active = True
        try:
            result = core.execute_single_file(plan, [payload], core_store)
        finally:
            core_audit.active = False
        report.update({
            "core_outcome": result.outcome.value,
            "core_reason": result.reason,
            "core_cleanup_pending": result.cleanup_pending,
            "core_audit_write_violations": core_audit.violations(),
        })
        try:
            core_store.close()
            report["core_store_close_reason"] = None
        except anonymous.PublicationUncertain as exc:
            report["core_store_close_reason"] = exc.reason
        report["staging_entries"] = _entries(staging_fd)
        report["destination_entries"] = _entries(destination_fd)
        return report
    finally:
        _close_test_descriptors(staging_fd, destination_fd)


_SCENARIOS = {
    "erofs-destination": _erofs_destination,
    "erofs-staging": _erofs_staging,
    "enospc": _enospc,
}


def main(argv):
    if len(argv) != 4:
        print(json.dumps({"ok": False, "error": "usage: scenario base-dir repo-root"}))
        return 2
    scenario, base, repo_root = argv[1], argv[2], argv[3]
    if scenario not in _SCENARIOS:
        print(json.dumps({"ok": False, "error": f"unknown_scenario:{scenario}"}))
        return 2
    if not os.path.isdir(base):
        print(json.dumps({"ok": False, "error": "base_directory_missing"}))
        return 2
    sys.path.insert(0, repo_root)
    try:
        from proxy import manual_save_anonymous as anonymous
        from proxy import manual_save_core as core
    except Exception as exc:  # noqa: BLE001 - report as JSON
        print(json.dumps({"ok": False, "error": f"import_failed:{type(exc).__name__}:{exc}"}))
        return 1
    try:
        report = _SCENARIOS[scenario](base, anonymous, core)
    except Exception as exc:  # noqa: BLE001 - report as JSON for the outer test
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}:{exc}"}))
        return 1
    report["ok"] = True
    print(json.dumps(report, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
