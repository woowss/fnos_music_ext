# Offline anonymous staging candidate

## Status and boundary

`proxy/manual_save_anonymous.py` is an isolated Linux storage candidate. It is
not imported by the running proxy, is not connected to an HTTP route, and does
not make manual save available. The tests use synthetic byte strings and private
fixture directories. They do not call NAS or music-service APIs, use a network,
access a real song, or touch production storage. A test runner may itself be
hosted on a NAS, but it must be isolated from device services and production
mounts as described below.

This candidate supplies only a filesystem primitive. It does not establish
caller identity or consent, choose or authorize a library, obtain audio,
verify audio format/duration/rights, or prove the full manual-save pipeline.
`production_ready` remains false in the separate offline core. A Linux unit
test pass is not a deployment approval.

## Protocol

1. The caller opens distinct staging and destination directories and passes
   their file descriptors. The candidate never resolves paths. The staging
   directory must be owned by the current effective UID, mode `0700`, and on
   the same device as the destination.
2. Staging opens `.` relative to the staging descriptor with `O_TMPFILE`,
   `O_DIRECTORY`, `O_RDWR`, and `O_CLOEXEC`, mode `0600`. The freshly created
   inode is normalized to mode `0600` with `fchmod` and then strictly
   re-verified: regular file, owned by the effective UID, exactly `0600`, and
   `st_nlink == 0`. Normalization is mandatory on ACL-inheriting filesystems
   such as fnOS btrfs with `system.trim_acl`, where an `O_TMPFILE` inode
   requested as `0600` can report `0700`. If normalization fails or the mode
   is still not exactly `0600`, the descriptor is closed and staging fails
   closed with a fixed reason; there is no fallback. The file has no directory
   entry while being written and sealed with `fsync`. Verification reads use
   `pread` and return copied immutable bytes; the API does not return the
   internal read/write descriptor.
3. Publication makes exactly one `linkat(stage_fd, "", destination_fd,
   basename, AT_EMPTY_PATH)` call. The kernel's hard-link create is atomic and
   returns `EEXIST` without replacing an existing file. There is no `os.link`,
   named temporary file, procfs path, rename, or other fallback.
4. After a successful link, the candidate checks the inode link count and
   `fsync`s the destination directory. `publish_noreplace` returns
   `PUBLISHED` with the staging descriptor still open. The caller must then
   call `release_owned_stage`, which rechecks the inode and closes that
   descriptor. No staging name is unlinked because none exists.
5. If publication may have been attempted but its result is not confirmed, the
   whole store is frozen: no other handle may be read, written, sealed,
   published, aborted, released, or newly created. The candidate does not
   retry publication, unlink a target, or run filename cleanup. It retains the
   anonymous descriptors until process exit. A caller must treat this state as
   `COMMIT_UNKNOWN` and stop for separate reconciliation; this candidate does
   not implement reconciliation.

## Verification isolation gate

The file is opened `O_RDWR` because the writer and kernel publication
descriptor share one anonymous inode. The only verification API returns byte
copies from `pread`; it does not hand the verifier the writable descriptor.
However, Python underscore fields are not an isolation boundary against
hostile code running in the same process. Therefore
`PIPELINE_INTEGRATION_ALLOWED` is explicitly `False`: this candidate must not
be connected to the verification pipeline or a production route. A future
integration requires a separately reviewed process boundary that gives the
verifier a kernel-enforced read-only capability and has Linux runtime evidence.

## Host requirements and expected limits

Linux and a filesystem that supports `O_TMPFILE` are required. `linkat` with
`AT_EMPTY_PATH` can require `CAP_DAC_READ_SEARCH`; an ordinary Linux user is
not assumed to possess it. On `EPERM`, the adapter reports
`at_empty_path_capability_required` as an uncertain publication and freezes
the store. Unsupported filesystem/kernel errors are reported explicitly. It
never retries by another mechanism. Destination permission denial, a read-only
destination filesystem, and a filesystem link limit also receive distinct
reasons while keeping the publication attempt frozen. A mocked unit test
verifies the fail-closed response to a denied capability; that mock does not
validate the host kernel operation. The real publication acceptance test
requires successful `linkat(AT_EMPTY_PATH)`. A Linux run that lacks the
required capability, `O_TMPFILE`, or a compatible filesystem is a failure or
`REFUSED` run, not a skipped acceptance test.

Windows cannot run this POSIX operation. There, the test suite reports the
Linux integration class as skipped and verifies that constructing the adapter
fails with `linux_required`; a Windows skip is not Linux validation.

An ACL-inheriting filesystem may override the mode requested at creation. The
candidate does not assume the requested mode was honored: it normalizes every
freshly created anonymous inode to `0600` and re-verifies exactly `0600`,
failing closed if the normalization does not stick. This was observed on the
production `/vol1` filesystem (btrfs plus fnOS `system.trim_acl`); see the
recorded results below.

## Isolated Linux test runner

The Linux filesystem tests must run only in a dedicated, disposable, offline
runner with no production mounts. Do not run them directly on the NAS host, in
the application container, or against `/vol1`, `/data`, `/repo`, `/var/apps`,
the checkout, or another production path. The runner must not have network
access or a production-library mount. Keep the repository/current working
directory separate from the runner fixture root.

The test marker validates the fixture-root path, owner, mode, and contents; it
does not prove network isolation or inspect container mounts. The runner
provisioning itself must enforce those isolation requirements.

Before the test command, provision a new private fixture root inside that
runner and a marker file with exactly the required owner, mode, and bytes:

```sh
runner_root="$(mktemp -d /runner/fnmusic-offline.XXXXXX)"
chmod 700 "$runner_root"
printf 'fnmusic-offline-isolated-runner-v1\n' > "$runner_root/.fnmusic-offline-isolated-runner"
chmod 400 "$runner_root/.fnmusic-offline-isolated-runner"
export FNMUSIC_OFFLINE_TEST_ISOLATION=dedicated-disposable-runner-v1
export FNMUSIC_OFFLINE_TEST_ROOT="$runner_root"
python3 -B -m unittest -v offline_tests.test_manual_save_anonymous
```

The checker strictly resolves the root and rejects it unless the original
absolute path already matches the resolved path; it then opens the root with
`O_NOFOLLOW`. This rejects symlink paths. The root must be owned by the
effective UID and have mode `0700`. The marker
must be a regular, non-symlink file owned by that UID, mode `0400`, with exact
contents `fnmusic-offline-isolated-runner-v1\n`. The root must be outside
`/vol1`, `/data`, `/repo`, `/var/apps`, the source checkout, and the current
working directory. Missing or invalid isolation inputs make Linux tests fail
with `REFUSED`; they are not silently skipped. A Linux runner without working
`O_TMPFILE` is also refused. Permission denial or unsupported behavior in the
real `AT_EMPTY_PATH` test is a failure, not a skip.

The Linux fixtures are intentionally preserved for inspection. Each test
prints `OFFLINE_TEST_FIXTURE_PRESERVED=<path>`; the test harness does not use
`TemporaryDirectory` cleanup or recursively delete fixture contents. Closing
test-owned descriptors is separate from fixture removal. Inspect and remove
the disposable runner only after the run; do not point it at a directory with
valuable or production data.

The `_test_only_publish_fd_via_procfs` helper uses `/proc/self/fd` and
`os.link` as a test double for selected conflict/race/state tests. Those tests
exercise surrounding state handling only; they do not exercise the adapter's
real kernel publication call and are not a fallback implementation. The test
`test_real_at_empty_path_publication_when_host_privilege_allows_it` exercises
the actual `linkat(AT_EMPTY_PATH)` path with synthetic bytes; it fails if that
operation is unavailable.

## Recorded results

These records retain their scope; none is a production acceptance.

### Windows mutation and suite run, 2026-10-31

The four offline suites were run on Windows with the repository's bundled
Python 3.12.14 (`python -B -m unittest -q`). The Linux-only class skips, as
expected on Windows:

- `offline_tests.test_manual_save_anonymous`: 24 tests, 5 passed, 19 skipped.
- `offline_tests.test_manual_save_quality_policy`: 28 passed.
- `offline_tests.test_manual_save_best_quality`: 33 passed.
- `offline_tests.test_manual_save_verified_pipeline`: 18 passed.

Combined: 103 tests, 0 failures, 0 errors, 19 skipped.

Windows cannot execute the real Linux filesystem tests, so the strength of the
three new ACL-normalization tests was additionally checked against a simulated
Linux descriptor layer in a `%TEMP%` copy: with the `fchmod` normalization
present all three pass; with the normalization call removed all three fail
(`stage_permissions_or_link_count_invalid`, no
`UnsupportedAnonymousStaging` failure, and no `fchmod` call). That simulation
is mutation-strength evidence for the test code only; it is not Linux syscall
evidence and does not replace the isolated Linux run.

### Linux host run, 2026-10-31 (NAS kotomi)

Environment: NAS `kotomi`, Debian 12, kernel `6.18.18.c1126-trim`, Python
3.11.2, ordinary user uid 1000, `CapEff=0`. A private fixture root under the
host `/tmp` (ext4) was provisioned with the isolation environment documented
above. This is host-filesystem evidence only. The host `/tmp` run is not the
isolated disposable runner required above: it ran on a host that has `/vol1`
mounts and network access, and the marker checker validates only path, owner,
mode, and marker contents, not mounts or networking.

Pre-fix record (2026-10-31): the anonymous suite (21 tests at that time)
passed 21/21 with 0 skipped, including the real `linkat(AT_EMPTY_PATH)`
publication test. That run predates the three ACL-normalization tests.

Post-fix record (2026-10-31): the four offline suites were run on the same
host with `python3 -B -m unittest -q
offline_tests.test_manual_save_anonymous
offline_tests.test_manual_save_quality_policy
offline_tests.test_manual_save_best_quality
offline_tests.test_manual_save_verified_pipeline`: `Ran 103 tests in 2.584s`,
`OK`, **0 skipped**. The 103 tests are the 24 anonymous tests (now including
the three new ACL-normalization tests and the real `AT_EMPTY_PATH` test), 28
quality-policy tests, 33 best-quality tests, and 18 verified-pipeline tests.
This is the first Linux run that includes the three new ACL-normalization
tests. Evidence binding: the run used the module with SHA-256
`fa4dc6bafcd8bddacba78584ac97e04a6ac29a765791d2cb75f4b4ee6785bc52` and the
test module with SHA-256
`1dda5c5b09b60fd149494d1381ee5222e7e2b95f22b4e08ef5d1ff6e496f1446`.

### `/vol1` finding: btrfs + fnOS `system.trim_acl`

On `/vol1` (btrfs plus fnOS `system.trim_acl`), an `O_TMPFILE` inode opened
with mode `0600` (also probed with `0400`) reported mode `0700` through
`fstat`. Before this change, `begin_owned_stage` failed closed with
`stage_permissions_or_link_count_invalid`; that was the intended fail-closed
behavior and never a publication fallback. With the mandatory
`os.fchmod(fd, 0o600)` normalization, the synthetic begin/append/seal/publish
flow completed: the published file reported mode `0600`, uid 1000,
`st_nlink == 1`, and the expected bytes; `fchmod` is sticky on that
filesystem. A raw probe also confirmed that `linkat(AT_EMPTY_PATH)` and its
`EEXIST` no-replace behavior work on `/vol1` at uid 1000 without extra
capabilities. That capability observation is specific to that kernel and
filesystem and does not make `CAP_DAC_READ_SEARCH` generally unnecessary;
`EPERM` still yields `at_empty_path_capability_required` and an uncertain
publication.

Post-fix `/vol1` probe (2026-10-31, same module/test SHA-256 binding as the
post-fix host run above): the fixed module was exercised against the
dedicated directories `/vol1/1000/fnmusic-test-20261031-staging` (staging)
and `/vol1/1000/fnmusic-test-20261031` (destination) on btrfs plus
`system.trim_acl`, as uid 1000, with synthetic bytes only. It reported
`publish StoreResult.PUBLISHED`; the published file was 30 bytes with mode
`0600`, uid 1000, `st_nlink == 1`, and correct content. The probe reported
`staging_clean True` and `dest_clean True`; it unlinked its file, both
directories were empty afterwards, and the live library entry count stayed
2247; probe artifacts and `/tmp` files were removed. This shows that the
mandatory `os.fchmod(fd, 0o600)` normalization makes
begin/append/seal/publish succeed on the production filesystem while
preserving the exact `0600` invariant.

Limits of these records: both `/vol1` probes used synthetic bytes only and
only the dedicated test directories; no production data, no production
wiring, no real audio, no authorization or consent check, no scan/index, no
deployment or rollback, and no live device manual-save flow was exercised.
`PIPELINE_INTEGRATION_ALLOWED` remains `False`, and the separate offline
core's `production_ready` remains false. Filesystem test evidence on any host
does not authorize connecting this candidate.

An earlier user-provided isolated Docker run reported 21 passed, including the
real `AT_EMPTY_PATH` test, under a root runner with added capabilities. That
record remains evidence for that disposable runner only; it does not cover
uid 1000 or the installed app filesystem. The uid-1000 host run and the
`/vol1` finding above are the evidence relevant to the production UID and
filesystem.

## Existing named-file adapter

`proxy/manual_save_posix.py` remains unchanged. It uses a named staging file
and a `stat`-then-`link`/`unlink` lifecycle, which has a name-replacement
TOCTOU exposure in a same-UID threat model. It is not made safe by this
candidate and must not be used for real manual saves. Do not connect either
adapter to production until identity/consent, source verification, path policy,
process lifecycle, ambiguity recovery, platform-specific integration, and a
separately authorized device acceptance are reviewed.
