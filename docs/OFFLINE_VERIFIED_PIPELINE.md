# Offline verified manual-save pipeline

Contract: `FNMEXT-VALIDATED-STAGING-20261030-01`.

This is a synthetic-only orchestration contract. It adds no network client,
audio source, filesystem adapter, NAS path, route, service hook, or UI. The
source, stager, verifier, and no-replace publisher are injected interfaces;
the accompanying tests use in-memory fakes. The pipeline is not connected to
`manual_save_core.py` or any existing storage implementation.

## Flow and gates

`run_manual_save()` accepts a selected track ID, display duration, preliminary
preview/rights state, and declared byte length. The request has no path, output
name, or payload field. A preview, unknown preview state, or unverified/denied
rights status is rejected before source or staging calls. Stream limits are
fixed: 512 MiB total, 1 MiB per non-empty `bytes` chunk, and 4096 chunks. The
declared length must match exactly. `CTENFDAM` is rejected even when split
across chunks.

The verifier's positive duration must also be within an inclusive two seconds
of the selected track's displayed duration. This small tolerance allows for
integer-second rounding; it is a consistency check only. A matching duration
does not establish export rights or audio completeness: rights must be
independently verified and `complete` must independently be true.

After staging, the pipeline seals the staged object, asks the injected
verifier for completeness, duration, final preview/rights/NCM state, byte count,
and media format. Cancellation is checked before staging, while consuming
chunks, after the stream and immediately before the single
`publish_noreplace()` call; non-boolean or throwing cancellation checks fail
closed. Any missing or invalid verification field fails closed. The extension
format is passed from the verifier's allowlisted result;
there is no caller filename suffix from which to infer it. Destination naming
and no-overwrite semantics remain the publisher's responsibility.

Verification must be called through the injected
`VerifierSupervisor.run_with_timeout()` interface with a 30-second budget. A
completed result is accepted only when the supervisor reports that its worker
terminated; timeout or unconfirmed termination fails closed. This module does
not contain a concrete supervisor and cannot kill a synchronous verifier. The
fake supervisor tests exercise only the result protocol; they do not prove
wall-clock enforcement or process/thread termination. Therefore this value is
not a production timeout guarantee, and the pipeline remains unavailable for
production until a separately reviewed supervisor can actually terminate and
reap a timed-out verifier.

All result reasons are fixed codes and never include exception text, paths, or
payload. A destination conflict is returned without overwrite or retry. An
exception or ambiguous response from the publisher becomes
`commit_unknown`; the pipeline makes no second publish call and does not delete
or clean the stage because the final object may already exist. Other failures
also never publish. This module intentionally defines no cleanup/delete
interface; reconciliation and safe owned-stage lifecycle require a separate,
reviewed adapter contract.

## Validation

Run from the repository root:

```powershell
python -B -m unittest -v offline_tests.test_manual_save_verified_pipeline
```

Tests use only synthetic byte strings and in-memory fakes. They verify stage,
seal, verify-supervision, cancellation, and publish ordering; inclusive duration
tolerance and mismatch rejection; separate duration, rights and completeness
gates; size and chunk bounds; preview/rights/NCM/format rejection; exception
sanitization; no-overwrite conflict handling; and no retry after an uncertain
publish result. Timeout tests verify fail-closed handling of protocol results,
not actual timed termination.

`PRODUCTION_READY` and every `PipelineResult.production_ready` are always
`False`. Passing these offline tests proves only orchestration behavior against
the fakes. It does not prove rights in a real account, a supported complete
audio source, decoding/format detection, a terminating verifier supervisor,
disk safety, NAS authorization, service lifecycle, indexing, or successful
deployment. Do not use this module to write music files or claim that a real
track was saved.
