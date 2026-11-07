# fnOS file ACL response decoder candidate

> Contract: FNMEXT-FILE-ACL-DECODER-20261025-01. This is a protocol-only
> candidate in the uncommitted fork; it is not a production permission check,
> authentication mechanism, save route, or deployment approval.

## Official response contract

The fnOS developer documentation for [`trim.file.checkUserACL`](https://developer.fnnas.com/api/authorization/file-acl/)
describes a backend response with a request ID, integer result code, message,
and an array of per-path `readable`, `writable`, and `deletable` booleans. The
request supplies the UID and path. The response example does **not** echo the
UID that was checked. The API therefore cannot by itself prove which user the
request represented.

The official page identifies scope `trim.file.userAcl` and minimum system and
App versions. This document does not claim that the installed extension has
the scope, that its lifecycle safely supplies an API token, or that the API is
available through the current service deployment.

## Candidate decoder behavior

`proxy/fnos_file_acl_protocol.py` is a standard-library-only, bounded decoder
for exactly one path result. It accepts raw UTF-8 JSON bytes up to 64 KiB,
correlates the exact request ID and path supplied by its caller, rejects
duplicate keys and unexpected schema, and requires each permission to be a
real JSON boolean. It does not normalize paths or check filesystem state.

Every failure produces a fixed, input-redacted reason and false permissions.
A valid response sets only `valid=true` and the three permissions. The frozen
result always leaves `authenticated=false` and `production_ready=false`; it
does not expose an `allow_write` or authorization alias. A valid response only
means that the JSON matched this decoder's protocol constraints. It does not
prove that fnOS sent it, that the response is fresh, or that the UID is
trusted.

## Required future integration gates

Before this decoder can participate in an actual file operation, a separately
reviewed transport layer must bind the `reqId` and exact path to a server-side
trusted UID, authenticate the official API response, and fail closed on
unsupported scope, missing credentials, timeout, malformed response, or
permission denial. The current source has not established the official token
lifecycle or scope handoff. G0 gateway identity and safe service lifecycle
remain unproven.

The API path result is also not yet bound to the directory file descriptor
used by `OfflineLinuxStore`. A future implementation must establish that
mapping without a check/use race before any storage adapter can rely on the
permission result. This candidate is not connected to `OfflineLinuxStore`,
the WebUI, a route, an API client, environment/token handling, or any NAS.

## Validation boundary

The tests use synthetic protocol responses only. They do not contact fnOS,
read credentials, check a real user's ACL, create files, or prove Linux/NAS
storage behavior. Local protocol tests cannot satisfy G0 or authorize a
production save.
