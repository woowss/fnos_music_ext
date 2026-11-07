"""Read-only producer for two-observation NetEase playlist member snapshots.

This module is the only NetEase adapter used by the new read-only endpoint
``GET /api/v1/user/playlists/{playlist_id}/member-snapshot``.  It imports and
calls ``musicbox-service/playlist_manifest.py`` unchanged: two independently
obtained ``(account, playlist)`` response pairs are normalized by
``snapshot_from_responses`` and compared by ``compare_snapshots``, then folded
into the fixed payload of the manual-save playlist contract (§4.2).

Importing this module performs no network request and no filesystem access
beyond importing the sibling pure module ``playlist_manifest``.  The artifact
probe (installed distribution version + ``dist-info/METADATA`` presence) and
every provider read only run inside ``build_snapshot``.

Envelope ``ok`` semantics (P0 fix)
----------------------------------
Every payload returned by ``build_snapshot`` keeps ``"ok": true`` — including
the failure statuses ``unsupported``/``upstream_error``/``not_logged_in`` —
because ``ok`` is the *envelope* flag: "the producer answered with a complete,
fixed-shape payload".  The observation verdict lives in ``status`` and
``error_code``.  The gateway adapter
(``proxy/manual_save_playlist_jobs._validate_snapshot_envelope``) only accepts
``ok is False`` bodies when they carry one of its fixed ``PRODUCER_ERROR_CODES``
in ``error``; a failure status emitted as ``ok=false`` would therefore be
folded into ``producer_malformed_response`` and the §3.3 mapping
(403 ``not_logged_in`` / 502 ``playlist_unavailable`` / 500
``source_artifact_mismatch``) would never be reached.  The only ``ok=false``
response of this feature is the endpoint-level 503 ``{"ok": false,
"error": "manifest_disabled"}`` built in ``musicbox-service/app.py``.

Read-only switch source (P1)
----------------------------
``producer_enabled()`` reads **only the process environment**, at request time:

    FNMUSIC_PLAYLIST_MANIFEST_READONLY   # default false; 1/true/yes/on enables

It is intentionally *decoupled* from the save-side switches
(``FNMUSIC_MANUAL_SAVE_ENABLED`` / ``FNMUSIC_MANUAL_SAVE_PLAYLIST_ENABLED``):
the read-only observation window (§4.7) runs with saving fully disabled.

In this repository ``.env`` is a bind-mounted file, not process environment:
``[program:musicbox]`` has no ``environment=`` in ``container/supervisord.conf``
or ``container/supervisord-native.conf.in``, and
``container/env_flag.sh:export_source_env()`` exports only a fixed key list.
Until that key list gains ``FNMUSIC_PLAYLIST_MANIFEST_READONLY`` (P5 deployment
item; both deployment forms start supervisord from ``container/entrypoint.sh``,
so children inherit the export), setting the key in ``.env`` alone does not
reach this process and the endpoint stays disabled (fail-closed).  The accepted
alternative is a same-source ``.env`` loader in ``musicbox-service/app.py``;
neither change is made here to stay inside this phase's write scope.

Deliberate limits (contract §4.1/§4.2/§4.3):

- The wheel SHA-256 is a module constant recorded from the fixed evidence
  document.  It is **not** recomputed at runtime: the installed artifact is an
  unpacked ``dist-info`` tree, so runtime can only assert the version string
  and that ``dist-info/METADATA`` exists (exposed as
  ``source_artifact.dist_info_present``).  This is a version assertion, not a
  digest check; digest verification belongs to CI / the separately authorized
  read-only integration test.
- No song details, audio URL, lyric or cover request is made.  Metadata stays
  ``not_requested`` and ``metadata_complete``/``import_ready``/
  ``production_ready``/``provider_verified`` stay false.
- ``playlist_manifest.MAX_SOURCE_POSITIONS`` (>100000 declared or returned
  positions) is reported as ``unsupported`` and never clamped.  The 1000
  position ledger limit and its 409 ``playlist_too_large`` mapping belong to
  the gateway (§4.4); a snapshot of 1001..100000 positions is still returned
  verbatim so the gateway can reject it without trusting a truncation.
- Every exception is folded into a fixed ``status``/``error_code`` pair.  No
  URL, cookie, response text or exception message is echoed back.
"""

from __future__ import annotations

import importlib.metadata
import os
from typing import Any, Callable

import playlist_manifest


# —— Fixed artifact selection (docs/NETEASE_DELIVERY_EVIDENCE.md) ————————————————
SCHEMA_VERSION = 1
PROVIDER = "netease"
PACKAGE_NAME = "NetEase-MusicBox"
PINNED_VERSION = "0.5.3"
WHEEL_SHA256 = "7f695b358ee22eda8433ca31503ae60e461b00e636392f366123810afadf5e12"

# n = 1001 asks for one position beyond the ledger's 1000-position job limit so
# that "exactly 1000 tracks" and "truncated at 1000" are distinguishable
# (contract §4.2 item 2).  No offset pagination (s = 0), matching the observed
# upstream client request documented in docs/OFFLINE_PLAYLIST_MANIFEST.md.
PLAYLIST_DETAIL_POSITION_REQUEST = 1001
MAX_LEDGER_POSITIONS = 1000

# Fixed tokens (§3.9.2 whitelist style; `artifact_error_code` is producer-local
# and maps to the gateway's 500 `source_artifact_mismatch`, §4.1).
UNSUPPORTED_SOURCE = "UNSUPPORTED_SOURCE"
SOURCE_READ_FAILED = "SOURCE_READ_FAILED"
NOT_LOGGED_IN = "NOT_LOGGED_IN"
ARTIFACT_MISMATCH_ERROR = "source_artifact_mismatch"
DISABLED_ERROR = "manifest_disabled"

# Read-only observation switch (§4.2).  Default false; only true-ish values
# enable it.  Decoupled from the save-side engine switches on purpose.
PLAYLIST_MANIFEST_READONLY_ENV = "FNMUSIC_PLAYLIST_MANIFEST_READONLY"
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on"})

__all__ = [
    "SCHEMA_VERSION",
    "PROVIDER",
    "PACKAGE_NAME",
    "PINNED_VERSION",
    "WHEEL_SHA256",
    "PLAYLIST_DETAIL_POSITION_REQUEST",
    "MAX_LEDGER_POSITIONS",
    "ARTIFACT_MISMATCH_ERROR",
    "DISABLED_ERROR",
    "PLAYLIST_MANIFEST_READONLY_ENV",
    "producer_enabled",
    "build_snapshot",
    "live_fetchers",
]


def _positive_int(value: Any) -> bool:
    """Booleans are not integers for this schema (same rule as the candidate)."""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _flag_enabled(environ: Any, name: str) -> bool:
    value = environ.get(name) if hasattr(environ, "get") else None
    if not isinstance(value, str):
        return False
    return value.strip().lower() in _TRUE_TOKENS


def producer_enabled(environ: Any = None) -> bool:
    """Read-only switch only; ``FNMUSIC_PLAYLIST_MANIFEST_READONLY`` enables it.

    Save-side switches are deliberately ignored: the §4.7 read-only window runs
    with saving disabled.  Anything other than a true-ish string keeps the
    endpoint disabled (fail-closed).
    """
    env = os.environ if environ is None else environ
    return _flag_enabled(env, PLAYLIST_MANIFEST_READONLY_ENV)


# —— Artifact assertion (§4.1 items 2 and 3) ——————————————————————————————————
def _default_artifact_probe() -> "tuple[str | None, bool]":
    """Return ``(installed version, dist-info/METADATA present)``; never raises.

    Runtime can only assert the version string and that the distribution
    metadata file exists; the wheel digest cannot be recomputed from an
    unpacked install (§4.1 item 3).
    """
    try:
        distribution = importlib.metadata.distribution(PACKAGE_NAME)
    except Exception:
        return None, False
    try:
        raw_version = distribution.version
    except Exception:
        raw_version = None
    version = raw_version if isinstance(raw_version, str) and raw_version else None
    try:
        metadata_text = distribution.read_text("METADATA")
    except Exception:
        metadata_text = None
    metadata_present = isinstance(metadata_text, str) and bool(metadata_text.strip())
    return version, metadata_present


def _artifact_state(artifact_probe: Any) -> "tuple[dict, str | None]":
    """Build the fixed ``source_artifact`` block and its mismatch token."""
    probe = _default_artifact_probe if artifact_probe is None else artifact_probe
    version: str | None = None
    metadata_present = False
    try:
        result = probe()
    except Exception:
        result = None
    if isinstance(result, tuple) and len(result) == 2:
        raw_version, raw_present = result
        version = raw_version if isinstance(raw_version, str) and raw_version else None
        metadata_present = raw_present is True
    compatible = version == PINNED_VERSION and metadata_present
    block = {
        "provider": PROVIDER,
        "package": PACKAGE_NAME,
        "version": PINNED_VERSION,
        "wheel_sha256": WHEEL_SHA256,
        # Runtime observation only: the version string matched PINNED_VERSION
        # and dist-info/METADATA exists.  This is NOT a wheel digest check.
        "installed_version": version,
        "dist_info_present": metadata_present,
    }
    return block, (None if compatible else ARTIFACT_MISMATCH_ERROR)


# —— Response reading ——————————————————————————————————————————————————————————
def _is_logged_out(account_response: Any) -> bool:
    """Conservative logged-out detection; keeps the fixed 4-request budget.

    A response without ``account``/``profile`` but with a response code is the
    provider's logged-out shape (unauthenticated ``get_account_info``).  The
    candidate would otherwise classify it as an upstream read failure.
    """
    if account_response is None:
        return True
    if not isinstance(account_response, dict):
        return False
    code = account_response.get("code")
    if isinstance(code, int) and not isinstance(code, bool) and code == 301:
        return True
    if account_response.get("account") or account_response.get("profile"):
        return False
    return "code" in account_response


def _observe(
    playlist_id: int,
    fetch_account: Callable[[], Any],
    fetch_playlist: Callable[[int], Any],
) -> Any:
    """One independent observation: fresh account read, then fresh playlist read."""
    try:
        account_response = fetch_account()
        if _is_logged_out(account_response):
            return playlist_manifest.SourceFailure(
                status="not_logged_in", error_code=NOT_LOGGED_IN,
            )
        playlist_response = fetch_playlist(playlist_id)
        return playlist_manifest.snapshot_from_responses(
            account_response, playlist_response, playlist_id,
        )
    except Exception:  # noqa: BLE001  上游/适配层任意异常一律折叠为固定失败
        return playlist_manifest.SourceFailure(
            status="upstream_error", error_code=SOURCE_READ_FAILED,
        )


# —— Payload (§4.2) ————————————————————————————————————————————————————————————
def _payload(
    *,
    status: str,
    error_code: "str | None",
    playlist_id: Any,
    artifact: dict,
    artifact_error_code: "str | None" = None,
    manifest: Any = None,
    snapshot: Any = None,
) -> dict:
    """Assemble the fixed payload; unknown/extra upstream data never leaks in.

    ``ok`` stays ``true`` for every status: it marks a complete fixed-shape
    envelope, while ``status``/``error_code`` carry the observation verdict.
    The consumer only understands ``ok=false`` bodies that also carry one of
    its fixed ``error`` codes, so a failure status must not be emitted as
    ``ok=false`` (that would be folded into ``producer_malformed_response``).
    """
    if manifest is None:
        positions: "list[dict[str, Any]]" = []
        enumeration_complete = False
        position_count = 0
        fingerprint = None
        account_uid = None
        track_count = None
        schema_version = SCHEMA_VERSION
        provider = PROVIDER
        source_versions: "list[list[Any]]" = []
        resolved_playlist_id = playlist_id if _positive_int(playlist_id) else None
    else:
        positions = [
            {
                "position": row.position,
                "track_id": row.track_id,
                "error_code": row.error_code,
            }
            for row in manifest.items
        ]
        enumeration_complete = bool(manifest.enumeration_complete)
        position_count = int(manifest.position_count)
        fingerprint = manifest.source_fingerprint
        account_uid = manifest.account_uid
        track_count = manifest.track_count
        resolved_playlist_id = manifest.playlist_id
        if isinstance(snapshot, playlist_manifest.SourceSnapshot):
            schema_version = snapshot.schema_version
            provider = snapshot.provider
            source_versions = [[name, value] for name, value in snapshot.versions]
        else:
            schema_version = SCHEMA_VERSION
            provider = PROVIDER
            source_versions = []
    return {
        "ok": True,
        "status": status,
        "error_code": error_code,
        "schema_version": schema_version,
        "provider": provider,
        "account_uid": account_uid,
        "playlist_id": resolved_playlist_id,
        "track_count": track_count,
        "position_count": position_count,
        "enumeration_complete": enumeration_complete,
        "source_fingerprint": fingerprint,
        "source_versions": source_versions,
        "positions": positions,
        "metadata_status": "not_requested",
        "metadata_complete": False,
        "import_ready": False,
        "production_ready": False,
        "provider_verified": False,
        "source_artifact": dict(artifact),
        "artifact_error_code": artifact_error_code,
    }


def build_snapshot(
    playlist_id: Any,
    fetch_account: Callable[[], Any],
    fetch_playlist: Callable[[int], Any],
    *,
    artifact_probe: "Callable[[], tuple[str | None, bool]] | None" = None,
) -> dict:
    """Produce one snapshot payload from two independent injected observations.

    ``fetch_account()`` returns the raw account response (``None`` means "not
    logged in"); ``fetch_playlist(playlist_id)`` returns the raw playlist detail
    response.  Both must return fresh objects on every call — this function
    calls them once per observation, twice per snapshot, so a reused response
    object cannot fabricate agreement.  Any exception they raise is folded into
    ``upstream_error/SOURCE_READ_FAILED``.

    ``artifact_probe`` is injectable for tests; the default reads the installed
    distribution.  A version other than ``PINNED_VERSION`` (or missing
    ``METADATA``, or a failing probe) fails closed *before* any account or
    playlist read and returns ``status=unsupported`` with
    ``artifact_error_code="source_artifact_mismatch"``.
    """
    artifact, artifact_error = _artifact_state(artifact_probe)
    if artifact_error is not None:
        # Fail closed: never read the account or the playlist for an
        # unverified artifact (§4.1 item 2).
        return _payload(
            status="unsupported",
            error_code=UNSUPPORTED_SOURCE,
            playlist_id=playlist_id,
            artifact=artifact,
            artifact_error_code=artifact_error,
        )
    if not _positive_int(playlist_id):
        # Defensive only: the route rejects non-positive ids before this call.
        return _payload(
            status="unsupported",
            error_code=UNSUPPORTED_SOURCE,
            playlist_id=playlist_id,
            artifact=artifact,
        )

    before = _observe(playlist_id, fetch_account, fetch_playlist)
    after = _observe(playlist_id, fetch_account, fetch_playlist)
    manifest = playlist_manifest.compare_snapshots(before, after)
    snapshot = after if isinstance(after, playlist_manifest.SourceSnapshot) else None
    return _payload(
        status=manifest.status,
        error_code=manifest.error_code,
        playlist_id=playlist_id,
        artifact=artifact,
        manifest=manifest,
        snapshot=snapshot,
    )


# —— Live binding (thin adapter; no network at import) ——————————————————————————
def live_fetchers() -> "tuple[Callable[[], Any], Callable[[int], Any]]":
    """Bind the process-wide NetEase instance for ``build_snapshot``.

    ``netease_ext`` is imported lazily so this module stays import-safe without
    the SDK, the cookie file or the XDG directories.  The private lock/instance
    helpers are reused rather than duplicated because ``netease_ext.py`` must
    not be modified (§4.2 "只 import，不修改").
    """
    def fetch_account() -> Any:
        import netease_ext

        with netease_ext._api_lock:  # noqa: SLF001
            api = netease_ext._get_api_locked()
            return api.get_account_info()

    def fetch_playlist(playlist_id: int) -> Any:
        import netease_ext

        with netease_ext._api_lock:  # noqa: SLF001
            api = netease_ext._get_api_locked()
            return api.eapi_request("/api/v6/playlist/detail", {
                "id": int(playlist_id),
                "n": PLAYLIST_DETAIL_POSITION_REQUEST,
                "s": 0,
            })

    return fetch_account, fetch_playlist
