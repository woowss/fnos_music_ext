# Offline playlist manifest candidate

## Scope

`musicbox-service/playlist_manifest.py` is an offline, pure candidate for
validating the ordered member positions in two injected NetEase response
objects. It has no route or caller in the service. It does not create a saved
playlist snapshot, fetch metadata, request audio, download or convert files,
write to the NAS library, or authorize an import.

The result's `enumeration_complete` only means that these local checks passed
for the supplied objects. It does not prove that the provider response was
complete, transactional, current, or authorized. Every result keeps
`metadata_complete`, `import_ready`, `production_ready`, and
`provider_verified` false.

## Input and validation

Call `snapshot_from_responses(account_response, playlist_response,
expected_playlist_id)` twice for independently obtained observations. The
function accepts only successful response objects with:

- a positive integer account `id`;
- a positive integer playlist `id` matching the requested ID;
- a non-negative integer `trackCount` and a `trackIds` list;
- at least one positive integer version from `updateTime`,
  `trackUpdateTime`, or `trackNumberUpdateTime`.

Booleans are not integers for this schema. Strings are not coerced to IDs.
Each track entry may be a positive integer or an object with an integer `id`.
Malformed entries stay at their original positions with `INVALID_TRACK_ID`.
Repeated IDs are retained at every position. Version keys and values are
compared as a complete ordered tuple of supported fields.

Two snapshots are compared by `compare_snapshots(before, after)`. Completeness
requires identical account, playlist, version-key set and values, declared
count, and full ordered ID positions, plus a position count equal to
`trackCount` and no invalid IDs. It compares the full sequences; it does not
deduplicate or infer completeness from a set, boundary IDs, or a hash.

The observed upstream client request used `n=100000` without offset
pagination. This candidate therefore rejects more than 100,000 declared or
returned positions with `POSITION_LIMIT_EXCEEDED`, and rejects exactly
100,000 with `SOURCE_LIMIT_REACHED`, since an exact capacity response cannot
rule out truncation. This is a conservative client-observation boundary, not
a verified provider limit. The current endpoint's playlist-directory limit
and metadata hydration are outside this module.

Stable response read failures yield `upstream_error/SOURCE_READ_FAILED`.
Unsupported or malformed input fails closed. A passed `SourceFailure` keeps
its status and error code. Count mismatch, invalid ID, and changed source
identity/version/order each have distinct outcomes.

## Fingerprint

`snapshot_fingerprint` is SHA-256 over canonical JSON containing schema and
provider labels, account and playlist IDs, count, version fields, and ordered
positions. It is a deterministic consistency label only. It is not a provider
signature, an authorization token, or proof of response completeness.

## Provider and side-effect gate

The candidate intentionally does not import or construct `NetEase-MusicBox`,
read a cookie, inspect environment or disk, or make a network request. Static
inspection found that the current SDK's `playlist_songlist` path requests
`n=100000` in one request and returns track IDs; it exposes no offset in that
method. The SDK's request code may persist a new `NMTID` cookie. The actual
installed artifact, response behavior, cookie side effects, and provider
completeness are not verified here. Do not wire a live adapter until an exact
release artifact and digest are selected and reviewed, and a separately
authorized read-only integration test can safely observe its side effects.

## Local synthetic validation

Run the focused candidate tests from the repository root:

```powershell
py -m pytest --noconftest -q proxy/tests/test_musicbox_playlist_manifest.py
```

`--noconftest` keeps this pure test isolated from the proxy app's autouse
fixtures. The full CI environment has the service dependencies, but this
single test does not need them. This is algorithm-only test evidence. It is
not an end-to-end import test or a NAS deployment acceptance result.
