# `/run` Unix socket path probe

This Linux-only probe compares Unix socket operations inside a disposable tree
under `/run`. It does not open or modify the installed extension socket, read
application configuration, or connect to a network service.

## Preconditions and isolation

Run only a reviewed, root-owned copy as root on Linux. Before creating anything,
the script opens `/` and `/run` with directory descriptors and `O_NOFOLLOW`,
checks the path entries against the open descriptors, requires root:root mode
`0755` for both directories, reads both access and default POSIX ACLs, and
blocks if UID 1000 (GID 1001, supplementary group 1000) has write permission.
It resolves the open `/run` descriptor's `mnt_id` through `/proc/self/fdinfo`
and requires exactly one matching `/proc/self/mountinfo` record of type
`tmpfs`. Missing, repeated, malformed, or unsupported ACL/mount data blocks
before the first write.

The script creates one random root-owned experiment directory in `/run` with
mode `0711`. It runs the target as UID 1000/GID 1001 with supplementary group
1000 and checks that all capability sets are zero. The child receives only
the absolute path to a fixture; `close_fds=True` prevents it from inheriting
the root test directory descriptor. No `preexec_fn` is used, and child Python
runs with `-I -S`.

In each case, a root client first connects to the root-owned mode `0600`
service; the script verifies its fixed response and `SO_PEERCRED` UID 0 before
starting the target process. The service then remains available for a second,
bounded connection from the target. The `parent-0755` case records the target's
actual connect and rename results; a successful connect or rename is reported
as `UNSAFE_OBSERVED`. The `parent-0777` positive control asks the target to
rename the original socket, replace its path with a mock socket, and serve a
fixed response. A root client verifies the mock peer credentials and response.
If the initial root-client check fails, the target test is not run. Failure
leaves the result `PARTIAL` or `BLOCKED`; it cannot be reported as a confirmed
control.

## Cleanup and interpretation

Every child process and socket operation has a timeout. After the child exits,
the script locks each case directory, checks its inode and ownership, and only
removes socket names whose device, inode, owner, group, and type still match
recorded objects. Unknown entries, failed checks, or cleanup errors are kept
and reported as `BLOCKED`; the script never recursively removes anything. It
retains the empty private outer directory and reports its path and cleanup
state.

`COMPLETE` means the preconditions, both cases, positive control, and case
cleanup completed. `UNSAFE_OBSERVED` means a target operation unexpectedly
succeeded in the `0755` case. `PARTIAL` means the positive control or another
observation did not complete. `BLOCKED` means a precondition, identity, process,
or cleanup check failed. Results describe only the disposable experiment; they
do not prove that a production socket can be replaced or that production was
tested.

Run the offline tests on a development machine with:

```bash
python -m unittest offline_tests.test_run_socket_path_probe -v
```

The offline tests simulate prerequisite, identity, ACL, control, and cleanup
outcomes. They do not exercise Linux mount namespaces, kernel ACL behavior, or
the root-run experiment. Until that separately reviewed Linux run is performed,
those behaviors remain unverified.
