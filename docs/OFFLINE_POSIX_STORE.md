# Offline POSIX storage candidate

> Contract: `FNMEXT-OFFLINE-POSIX-STORE-20261024-01`. This is an isolated
> Linux-only candidate for offline tests. It is not connected to the extension,
> a service, an HTTP route, a job worker, or a NAS library.

## Scope and trust boundary

`proxy/manual_save_posix.py` implements the six-method storage protocol used by
`proxy.manual_save_core.execute_single_file`. It accepts only already-open
destination and private staging directory file descriptors. It duplicates
those descriptors, marks the copies non-inheritable, and anchors all file
operations to them. It never accepts or opens a destination path, creates a
directory, resolves a path, or authorizes a caller or target.

The caller that supplies the descriptors remains responsible for G0 identity,
actor authorization, target-to-directory mapping, directory selection, and
the lifecycle that opens and closes those descriptors. `target_id` is only a
binding label. It is not proof of identity or permission. The candidate assumes
the staging directory is owned by the effective UID, mode `0700`, and not
modifiable by another untrusted process with that same UID or by root.

Unsupported platforms and missing Linux `dir_fd`, `O_NOFOLLOW`, hard-link, or
directory-fsync primitives fail closed. There is no string-path or
`os.replace` fallback. A successful publication uses an atomic same-device
hard link and therefore refuses to replace any existing file, directory, or
symlink. Staging entries are random single components created exclusively
with mode `0600`; cleanup verifies the file descriptor and directory entry
still identify the same owned inode before unlinking the staging name.

The stage records three separate facts: a publication was attempted, the hard
link was created, and the destination directory sync was confirmed. The attempt
is frozen before rechecking stage identity; if that check fails, the core
records `commit_unknown` and the candidate refuses retry, abort, and release.
If the link call or destination-directory `fsync` raises, the core also records
`commit_unknown`; even when the final link is visible, sync has not been
confirmed. In either unknown state, publish retry, abort, and release all
refuse to act. The candidate retains its staging name and never removes the
final name. Only a successful directory sync marks publication confirmed,
after which the core may release the stage. Cleanup failures remain pending.
Closing the adapter closes only its owned descriptors and deliberately leaves
any remaining stage names for inspection; there is no startup scan or automatic
orphan cleanup.

## Test boundary

`offline_tests/test_manual_save_posix.py` covers import/platform rejection and
unknown-state method guards on Windows. Linux-specific tests cover descriptor
anchoring, symlink and path-replacement behavior, private staging, no-replace
conflicts, inode checks, short writes, ENOSPC, link errors, cancellation during
seal, concurrent publication, sync failures, and cleanup behavior. Linux tests
are explicitly skipped on non-Linux hosts; a skip is **NOT RUN**, not a pass.

On Linux, run:

```sh
python3 -B -m unittest discover -s offline_tests -p test_manual_save_posix.py -v
```

On Windows, the following only checks import safety and unsupported-platform
rejection. It does not exercise Linux filesystem semantics:

```powershell
py -B -m unittest discover -s offline_tests -p test_manual_save_posix.py -v
```

## Not production-ready

This candidate does not satisfy the project's production gates. In particular,
it does not establish:

- trusted G0 gateway identity, effective request-header handling, actor ACL,
  or a reviewed service start/rollback lifecycle;
- authorization for a destination or safe acquisition of destination FDs;
- Linux runtime validation (the Windows-host skips do not count);
- recovery after process crash, power loss, or NAS/Btrfs-specific durability;
- a permitted NetEase audio-file delivery path, metadata policy, or library
  indexing behavior;
- genuine `.ncm` to `.flac` conversion. The core rejects NCM bytes; it must not
  relabel an NCM container as FLAC. Every other source format is to be
  preserved by the product flow.

No production adapter, route, or NAS acceptance is claimed. There is no NAS
rollback procedure in this contract because the candidate is never deployed.
