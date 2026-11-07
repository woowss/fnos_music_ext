# Backup files-only fixture

This candidate is intended to check whether ordinary files in a previously
verified backup can be read back. It does not restore an extension, permissions,
links, service state, or a production rollback.

> **Current candidate is deliberately BLOCKED before filesystem access.** Both
> `restore_fixture()` and `scan_archive_fd()` return
> `archive_scan_isolation_unproven` without opening, statting, reading, hashing,
> or decoding the caller-supplied archive; the public entrypoint also does not
> inspect/open the destination. It does not invoke `tarfile`, decompress gzip
> data, create a fixture, or write a byte. The prior check of
> `TarInfo.pax_headers` was too late: `tarfile` had already parsed and allocated
> a PAX body. No `COMPLETE` result is possible from this snapshot.

## Safety rules

- There is no archive-input or destination-path operation in the current public
  entrypoint. The Linux/root, root-owned regular-file, digest, and exact `/run`
  tmpfs checks in lower-level helpers are prospective policy only; they are not
  reached by `restore_fixture()` and must not be described as current
  validation.
- If a future reviewed implementation enables those helpers, the intended
  destination policy is exactly `/run`, with the opened descriptor mapped by
  kernel mount ID to a `/`-rooted `/run` tmpfs mount using `rw,nosuid,nodev`;
  `/` and `/run` must be root-owned mode `0755` without POSIX ACLs. Those
  properties are **not checked by the current public entrypoint**.
- The existing `fstatvfs` byte/inode budget is only a storage-space check. It
  does not represent available RAM, swap, cgroup limits, or OOM safety, and the
  fixed block above stops before archive decoding or extraction. Do not use
  `/run` free space as a substitute for a memory check. Even a point-in-time
  `MemAvailable` or cgroup sample cannot guarantee that another process will not
  consume memory before a later write; the current candidate therefore does not
  attempt extraction while that concurrency/isolation boundary is unproven.
- The unreachable extraction path contains Linux probes for UID 1000 and UID
  65534, complete NSS group lists, zero capabilities, `NoNewPrivs=1`, and denied
  fixture access. Those probes do not run in this candidate because the fixed
  memory gate stops earlier. The two sampled identities are not an exhaustive
  proof for every non-privileged UID.
- `_secure_open_path(directory=False)` is disabled before its first `os.open`,
  so callers cannot block on a FIFO or open a device node through this helper.
  Directory-only uses retain no-follow component opens for latent destination
  checks. Neither public entrypoint opens or processes the archive. Random
  fixture creation and all later fixture operations are unreachable.
- The previous scanner's policy was to accept canonical relative directories
  and regular files, skip symlinks, and reject hardlinks, devices, FIFOs,
  unknown types, PAX metadata, duplicate/conflicting paths, and traversal
  names. That scanner is now disabled because its PAX rejection happened after
  `tarfile` had already allocated extension metadata. Those checks are not
  claimed to run in the current candidate.
- No archive member or setuid/setgid counters are produced. Current CLI output
  contains only fixed `status: BLOCKED`, `reason:
  archive_scan_isolation_unproven`, `archive_scan: not_run`, and
  `fixture_created: false`; it does not echo the supplied digest or path and
  never reports a scan result.
- If a future reviewed snapshot enables its unreachable file-only extractor,
  archive owner, group, mode, ACLs, xattrs, and special permission bits must
  remain unrestored; new directories would be `0700`, files `0600`, and bytes
  would require dirfd readback hashing. None of those writes or readback checks
  occur in this candidate.
- No compressed-input or decompressed-output limit is currently applied because
  the public entrypoint refuses before archive I/O. The 128 MiB compressed
  ceiling, 1 MiB hashing chunks, 5,000-member, 128 MiB total-file-data, 32 MiB
  per-file, 4,096-byte path, and 32-component values are prospective policy
  only; none is evidence of current archive validation.
- Output contains fixed error codes, not member names or file contents. There is
  no partial fixture or permission-probe
  canary in this candidate because it never creates either.

## Synthetic validation

### Windows: synthetic fail-closed and policy tests

On Windows, run:

```text
python -B -m unittest offline_tests.test_restore_fixture -v
```

These tests verify that normal archives, an 8 MiB gzip expansion, and an 8 MiB
PAX extended header are refused before any compressed bytes are read or
`tarfile` is called; they also verify the public API, CLI, and disabled file
open helper refuse before filesystem open/stat/read/hash calls. They exercise
destination/mount/ACL/space policy, NSS
group resolution, proc-status matching, and readback failure handling. The
resource tests assert a sub-512 KiB traced peak while the scan path is disabled.
Windows does not exercise Linux fork/dirfd behavior or actual Linux readback.
The FIFO/device-path checks are synthetic no-open assertions; this test run
does not exercise a Linux kernel FIFO or device node.

### Linux: isolated temporary-file checks only

Run the same command only in a disposable Linux test environment, against the
synthetic fixtures created by the test suite:

```text
python3 -B -m unittest offline_tests.test_restore_fixture -v
```

The Linux-only low-privilege probe and dirfd checks are synthetic policy tests;
the on-disk readback test uses a temporary directory and is skipped unless run
as Linux root. Neither test invokes a real archive restore. Passing either
platform's tests does not validate a NAS kernel, live cgroup ancestry, memory
pressure behavior, `/run` mount options, uid/gid mapping, Btrfs backup
readability, or runtime permissions.

This candidate cannot return `COMPLETE`; its fixed
`archive_scan_isolation_unproven` block occurs before any filesystem operation,
decompression, TAR metadata allocation, or extraction. The 8 MiB gzip/PAX tests
prove only that the current path refuses before reading the input; they do not
validate a bounded decoder. A future implementation must use a scanner whose
compressed-input, decompressed-output, and extension-header limits are enforced
before allocation, preferably in a separately resource-limited process. The
isolation and platform behavior must be proven on the actual supported runtimes
before enabling that path. Keep the tool blocked if that cannot be established;
do not substitute a read wrapper around `tarfile` or `/run` free space for a
decoder memory bound.

Do not run against a NAS backup. Any future change that enables extraction
requires a new exact-snapshot independent review and a separately approved
Linux test plan. If the NAS does not expose the required `/run` tmpfs and
ACL/access behavior, or if memory isolation cannot be proved, keep extraction
blocked; do not weaken these checks or substitute a Btrfs directory based only
on owner and mode bits.
