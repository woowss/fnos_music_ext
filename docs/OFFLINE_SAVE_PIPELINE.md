# Offline save pipeline acceptance

> Contract: `FNMEXT-OFFLINE-SAVE-PIPELINE-20261024-01`. This is a local integration check, not a production save/import feature or a NAS deployment approval.

## Scope

`offline_tests/test_manual_save_pipeline.py` combines the existing `ManualSaveJobsLedger` with the existing `execute_single_file` core. Each test uses a real temporary SQLite database and a test-only disk fixture rooted in that test's `TemporaryDirectory`. Inputs are synthetic bytes. The fixture is defined only in the test module; application code must not import it.

The fixture stages each file under an exclusive random name in the same temporary directory, writes all chunks, flushes and `fsync`s the staged file, then uses same-directory hard-link creation as the test's atomic no-replace publication operation. A destination collision returns the core's conflict result. Cleanup removes only the fixture's own stage. Tests deliberately retain the stage after an injected ambiguous publication or cleanup failure so the ledger's outcome can be inspected after reopening it.

This test fixture is not a production filesystem adapter. It does not establish directory authorization, safe path traversal, symlink or directory-replacement protection, Linux `dir_fd` behavior, NAS filesystem behavior, power-loss durability, or production cleanup ownership. No path outside each test's temporary directory is passed to the fixture.

## Covered behavior

- Three ordered playlist positions preserve two output formats and a repeated source position; the repeated output conflicts without changing the first published bytes.
- A different pre-existing destination is preserved byte-for-byte on conflict.
- The NCM magic `CTENFDAM`, split across chunks and mislabeled as `.flac`, is rejected before publication.
- A stream shorter than its declared length is rejected and its owned stage is cleaned.
- A cancellation requested through the real ledger while the fixture seals a stage is observed by the core's post-seal check; publication is prevented and queued positions are cancelled.
- An injected exception after hard-link creation is recorded as `commit_unknown`; reopening the SQLite ledger does not infer success from file presence or dispatch the queued item again.
- A post-publication stage cleanup failure is recorded as `published_cleanup_pending` and remains that way after reopening the ledger.
- Every returned result and persisted snapshot keeps `audio_verified=false`, `production_ready=false`, and `import_ready=false`.

The suite has no silent platform skip. If the temporary filesystem cannot provide hard-link publication, the required publication assertions fail and the limitation must be reported as blocked; do not count a skipped test as evidence.

## Reproduce

Run from the repository root:

```powershell
py -B -m unittest discover -s offline_tests -p test_manual_save_pipeline.py -v
py -B -m unittest discover -s offline_tests -p test_manual_save_core.py -v
py -B -m unittest discover -s offline_tests -p test_manual_save_jobs.py -v
git diff --check
```

## Validation recorded for this contract

- Date: 2026-10-24.
- Host: Windows, Python 3.14.6. These tests ran against the host's temporary filesystem; they are not Linux or NAS filesystem evidence.
- `test_manual_save_pipeline.py`: 7 tests passed.
- `test_manual_save_core.py`: 17 tests passed.
- `test_manual_save_jobs.py`: 19 tests passed.
- The tests used real temporary SQLite and temporary on-disk files, with no network, account, WebUI, media API, NAS, scanner, or production library access.

These results establish only the tested offline ledger/core interaction and test fixture behavior on this host. They do not prove production readiness or acceptance of any downloaded music.

## Remaining production gates

Before a production storage adapter or save/import entry point can be built or enabled, the project still needs reviewed G0 identity and lifecycle evidence, verified caller authorization and destination-directory scope, and a supported audio acquisition path. A production Linux adapter needs its own contract, independent review, and Linux validation for directory-handle anchoring, symlink/race resistance, atomic no-overwrite publication, disk-full and crash handling, cleanup ownership, and cancellation races. NCM must be converted to genuine decodable FLAC; other formats must remain unchanged. A real NAS acceptance run additionally needs a user-provided or read-only verified sample track and destination plus explicit authorization for that write. The Linux `/tmp` test recorded below is only an offline candidate check and does not satisfy any production acceptance gate.

## Linux POSIX adapter plus SQLite pipeline integration (candidate, 2026-10-25)

`offline_tests/test_manual_save_posix_pipeline.py` connects the real `ManualSaveJobsLedger.run_next()` to `manual_save_core.plan_single_file()` / `execute_single_file()` and the real `OfflineLinuxStore`. It uses one `TemporaryDirectory` for a synthetic SQLite ledger, an isolated destination directory, and a private `0700` staging directory, opened by the caller and passed as directory file descriptors. It does not import a test-only storage fixture and does not modify application wiring.

The Linux-only cases exercise:

- Ordered positions with synthetic `.mp3` and `.flac` bytes, a repeated source/output position, and a separate pre-existing-file conflict. They assert exact published bytes, no replacement of the pre-existing bytes, stage cleanup, ordered ledger outcomes, and matching state after ledger reopen.
- Split `CTENFDAM` magic disguised as FLAC plus a truncated stream. The core records fixed rejection reasons, publishes no final files, removes only stages it owns, and leaves an unrelated staging sentinel untouched.
- Cancellation requested immediately after the real adapter seals the first stage. The core cancels before publish, the later position is not dispatched, and reopening the ledger retains the cancellation state.
- An injected failure only at the destination-directory `fsync` after the hard link. The final bytes and owned stage remain; the ledger records `commit_unknown`; reopening does not dispatch later work or infer success from file existence.
- An injected failure only when unlinking the owned stage after publication. The final bytes remain, the ledger records `published_cleanup_pending`, and closing the adapter leaves the stage for reconciliation rather than deleting it.

All results remain `audio_verified=false`, `production_ready=false`, and `import_ready=false`. The tiny `.mp3`/`.flac` payloads are protocol fixtures, not playable audio; these tests do not implement or validate NCM conversion. This remains an offline candidate and is not wired to account access, WebUI/API, NAS, or the production library.

### Reproduce

Run from the repository root on Linux; all five tests must execute without platform skips:

```text
python3 -B -m unittest discover -s offline_tests -p test_manual_save_posix_pipeline.py -v
python3 scripts/run_tests.py --suite offline --jobs 1
```

On non-Linux hosts the five cases explicitly skip and are **NOT RUN** acceptance evidence.

### Validation recorded for this snapshot

- **Linux adapter/integration:** on `kotomi`, the reviewed snapshot was staged in the isolated directory `/tmp/fnmusic-posix-pipeline-20261025-839fd314`. SHA-256 checks for the four transferred files matched the local snapshot:

  | File | SHA-256 |
  | --- | --- |
  | `proxy/manual_save_core.py` | `53783B5874E7F87ED3E40741D2164DD799710DF95CFC7D78B28BEB66C612BA6C` |
  | `proxy/manual_save_jobs.py` | `7A9A3D3343630E357BBE574BF4DF6BADBDE1240FE39C3605CE6BB2DCE30A01F5` |
  | `proxy/manual_save_posix.py` | `D3963009317BAD76016D94A95B641FCFF22218927F8B6AEE9ED6B72800561054` |
  | `offline_tests/test_manual_save_posix_pipeline.py` | `A60EE4DAD74948B5EFE6A0F5DE7628EC85A2C95F9E8084425C6D3378C7612C13` |

  From that isolated directory, the following command was run:

  ```sh
  PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp/fnmusic-posix-pipeline-20261025-839fd314/test-tmp python3 -B -m unittest discover -s offline_tests -p test_manual_save_posix_pipeline.py -v
  ```

  Result: `Ran 5 tests in 0.398s`; `OK` — **5 passed, 0 skipped**.
- **Windows offline-suite record (separate evidence):** Host Windows, Python 3.14.6. The focused command `C:\Users\admin\AppData\Local\Temp\fnmusic-ci-deps-20261025-a1\Scripts\python.exe -B -m unittest discover -s offline_tests -p test_manual_save_posix_pipeline.py -v` exited 0 with 5 Linux-only skips. `scripts/run_tests.py --suite offline --jobs 1` under the same interpreter with UTF-8 output exited 0: 113 passed, 23 skipped, and 52 subtests passed. The 23 skips include all 5 Linux-only cases. These Windows results are not Linux or production evidence.
- **Acceptance boundary:** The Linux result validates the candidate POSIX adapter and SQLite integration using synthetic bytes and Linux file semantics only in the specified `/tmp` temporary directory on `kotomi`; it does not represent the `/vol1/1000/music` target music-library mount or scanner. The archived source and test temporary files were written only under `/tmp/fnmusic-posix-pipeline-20261025-839fd314`; the production music library, application configuration, services, and deployment were not modified. G0 gateway identity and lifecycle, account access, real audio, NCM conversion, desktop import, and production deployment remain unaccepted. The synthetic payloads are not playable audio, and NAS and production acceptance remain **NOT RUN**.
