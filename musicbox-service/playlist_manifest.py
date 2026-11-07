"""Pure, offline validation for two observations of a NetEase playlist.

This module deliberately has no provider, filesystem, process, cookie, or
network integration.  It validates only response objects supplied by its
caller.  A complete result means that the injected observations satisfy the
local enumeration rules; it does not prove that the provider returned every
playlist position or authorize a download/import.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


SCHEMA_VERSION = 1
PROVIDER = "netease"
MAX_SOURCE_POSITIONS = 100_000
_VERSION_FIELDS = ("updateTime", "trackUpdateTime", "trackNumberUpdateTime")
_FAILURE_STATUSES = frozenset({
    "unsupported", "upstream_error", "not_logged_in", "incomplete", "source_changed",
})
_ERROR_CODE_RE = re.compile(r"[A-Z0-9_]{1,64}\Z")


@dataclass(frozen=True)
class SourceFailure:
    """A normalized upstream failure; its stable reason is passed through."""

    status: str
    error_code: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, str) or self.status not in _FAILURE_STATUSES:
            raise ValueError("SourceFailure requires a recognized non-success status")
        if not isinstance(self.error_code, str) or _ERROR_CODE_RE.fullmatch(self.error_code) is None:
            raise ValueError("SourceFailure requires a stable uppercase error code")


@dataclass(frozen=True)
class PlaylistPosition:
    """One source position. Invalid IDs remain as positions with an error."""

    position: int
    track_id: int | None
    error_code: str | None
    raw_id_json: str


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable normalized fields required to compare playlist observations."""

    schema_version: int
    provider: str
    account_uid: int
    playlist_id: int
    track_count: int
    versions: tuple[tuple[str, int], ...]
    positions: tuple[PlaylistPosition, ...]

    def __post_init__(self) -> None:
        if not _well_formed_snapshot(self):
            raise ValueError("SourceSnapshot must contain normalized immutable source data")


@dataclass(frozen=True)
class ManifestPosition:
    position: int
    track_id: int | None
    error_code: str | None
    metadata_status: str = "not_requested"
    song: None = None


@dataclass(frozen=True)
class PlaylistManifest:
    status: str
    error_code: str | None
    enumeration_complete: bool
    metadata_complete: bool
    import_ready: bool
    production_ready: bool
    provider_verified: bool
    account_uid: int | None
    playlist_id: int | None
    track_count: int | None
    position_count: int
    items: tuple[ManifestPosition, ...]
    source_fingerprint: str | None


def _is_positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_non_negative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _canonical_json(value: Any) -> str | None:
    """Return stable JSON for JSON-shaped values, rejecting arbitrary objects."""
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError, OverflowError):
        return None


def _unsupported() -> SourceFailure:
    return SourceFailure(status="unsupported", error_code="UNSUPPORTED_SOURCE")


def _read_failed() -> SourceFailure:
    return SourceFailure(status="upstream_error", error_code="SOURCE_READ_FAILED")


def _well_formed_snapshot(snapshot: SourceSnapshot) -> bool:
    """Fail closed if a caller constructs a snapshot outside the parser."""
    if (
        not _is_positive_int(snapshot.schema_version)
        or snapshot.schema_version != SCHEMA_VERSION
        or snapshot.provider != PROVIDER
        or not _is_positive_int(snapshot.account_uid)
        or not _is_positive_int(snapshot.playlist_id)
        or not _is_non_negative_int(snapshot.track_count)
        or snapshot.track_count >= MAX_SOURCE_POSITIONS
        or not isinstance(snapshot.versions, tuple)
        or not snapshot.versions
        or not isinstance(snapshot.positions, tuple)
        or len(snapshot.positions) >= MAX_SOURCE_POSITIONS
    ):
        return False
    if any(not isinstance(pair, tuple) or len(pair) != 2 for pair in snapshot.versions):
        return False
    names = tuple(name for name, _value in snapshot.versions)
    if any(not isinstance(name, str) for name in names):
        return False
    if names != tuple(field for field in _VERSION_FIELDS if field in names):
        return False
    if any(
        name not in _VERSION_FIELDS or not _is_positive_int(value)
        for name, value in snapshot.versions
    ):
        return False
    for expected_position, row in enumerate(snapshot.positions, start=1):
        if (
            not isinstance(row, PlaylistPosition)
            or not _is_positive_int(row.position)
            or row.position != expected_position
        ):
            return False
        if not isinstance(row.raw_id_json, str):
            return False
        try:
            decoded_raw_id = json.loads(row.raw_id_json)
        except (json.JSONDecodeError, TypeError, ValueError):
            return False
        if _canonical_json(decoded_raw_id) != row.raw_id_json:
            return False
        if row.error_code is None:
            if (
                not _is_positive_int(row.track_id)
                or row.raw_id_json != _canonical_json(row.track_id)
            ):
                return False
        elif row.error_code != "INVALID_TRACK_ID" or row.track_id is not None:
            return False
    return True


def snapshot_from_responses(
    account_response: Any,
    playlist_response: Any,
    expected_playlist_id: Any,
) -> SourceSnapshot | SourceFailure:
    """Normalize injected account and playlist responses without side effects.

    Accepted track entries are positive integer IDs or provider-shaped
    dictionaries with an ``id`` field. Strings and booleans are never coerced
    to integer IDs. A malformed entry is retained as a position and marked.
    """
    if isinstance(account_response, SourceFailure):
        return account_response
    if isinstance(playlist_response, SourceFailure):
        return playlist_response
    if not _is_positive_int(expected_playlist_id):
        return _unsupported()
    if not isinstance(account_response, dict) or not isinstance(playlist_response, dict):
        return _unsupported()

    if (
        not _is_positive_int(account_response.get("code"))
        or account_response.get("code") != 200
        or not _is_positive_int(playlist_response.get("code"))
        or playlist_response.get("code") != 200
    ):
        return _read_failed()
    account = account_response.get("account")
    if not isinstance(account, dict) or not _is_positive_int(account.get("id")):
        return _unsupported()

    playlist = playlist_response.get("playlist")
    if not isinstance(playlist, dict):
        return _unsupported()
    playlist_id = playlist.get("id")
    if not _is_positive_int(playlist_id) or playlist_id != expected_playlist_id:
        return SourceFailure(status="unsupported", error_code="PLAYLIST_ID_MISMATCH")

    track_count = playlist.get("trackCount")
    track_ids = playlist.get("trackIds")
    if not _is_non_negative_int(track_count) or not isinstance(track_ids, list):
        return _unsupported()
    if track_count > MAX_SOURCE_POSITIONS or len(track_ids) > MAX_SOURCE_POSITIONS:
        return SourceFailure(status="unsupported", error_code="POSITION_LIMIT_EXCEEDED")
    if track_count == MAX_SOURCE_POSITIONS or len(track_ids) == MAX_SOURCE_POSITIONS:
        return SourceFailure(status="unsupported", error_code="SOURCE_LIMIT_REACHED")

    versions: list[tuple[str, int]] = []
    for field in _VERSION_FIELDS:
        if field not in playlist:
            continue
        value = playlist[field]
        if not _is_positive_int(value):
            return _unsupported()
        versions.append((field, value))
    if not versions:
        return _unsupported()

    positions: list[PlaylistPosition] = []
    for index, raw_entry in enumerate(track_ids, start=1):
        if isinstance(raw_entry, dict):
            if "id" not in raw_entry:
                raw_id: Any = raw_entry
            else:
                raw_id = raw_entry["id"]
        else:
            raw_id = raw_entry

        raw_id_json = _canonical_json(raw_id)
        if raw_id_json is None:
            return _unsupported()
        if _is_positive_int(raw_id):
            track_id = raw_id
            error_code = None
        else:
            track_id = None
            error_code = "INVALID_TRACK_ID"
        positions.append(PlaylistPosition(
            position=index,
            track_id=track_id,
            error_code=error_code,
            raw_id_json=raw_id_json,
        ))

    return SourceSnapshot(
        schema_version=SCHEMA_VERSION,
        provider=PROVIDER,
        account_uid=account_response["account"]["id"],
        playlist_id=playlist_id,
        track_count=track_count,
        versions=tuple(versions),
        positions=tuple(positions),
    )


def snapshot_fingerprint(snapshot: SourceSnapshot) -> str:
    """Hash the normalized source identity and ordered positions.

    The digest is a deterministic consistency label only. It is not a provider
    signature, permission token, or evidence that a source response was whole.
    """
    payload = {
        "schema_version": snapshot.schema_version,
        "provider": snapshot.provider,
        "account_uid": snapshot.account_uid,
        "playlist_id": snapshot.playlist_id,
        "track_count": snapshot.track_count,
        "versions": [[name, value] for name, value in snapshot.versions],
        "ordered_ids": [
            position.track_id
            if position.error_code is None
            else {"invalid": position.raw_id_json}
            for position in snapshot.positions
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(
    *,
    status: str,
    error_code: str | None,
    snapshot: SourceSnapshot | None = None,
    positions: tuple[PlaylistPosition, ...] = (),
    fingerprint: str | None = None,
) -> PlaylistManifest:
    items = tuple(
        ManifestPosition(
            position=row.position,
            track_id=row.track_id,
            error_code=row.error_code,
        )
        for row in positions
    )
    return PlaylistManifest(
        status=status,
        error_code=error_code,
        enumeration_complete=status == "complete",
        metadata_complete=False,
        import_ready=False,
        production_ready=False,
        provider_verified=False,
        account_uid=snapshot.account_uid if snapshot else None,
        playlist_id=snapshot.playlist_id if snapshot else None,
        track_count=snapshot.track_count if snapshot else None,
        position_count=len(positions),
        items=items,
        source_fingerprint=fingerprint,
    )


def compare_snapshots(
    before: SourceSnapshot | SourceFailure,
    after: SourceSnapshot | SourceFailure,
) -> PlaylistManifest:
    """Create an offline manifest only when two ordered observations agree."""
    if isinstance(before, SourceFailure):
        return _manifest(status=before.status, error_code=before.error_code)
    if isinstance(after, SourceFailure):
        return _manifest(status=after.status, error_code=after.error_code)
    if (
        not isinstance(before, SourceSnapshot)
        or not isinstance(after, SourceSnapshot)
        or not _well_formed_snapshot(before)
        or not _well_formed_snapshot(after)
    ):
        failure = _unsupported()
        return _manifest(status=failure.status, error_code=failure.error_code)

    failure: str | None = None
    if before.schema_version != after.schema_version or before.provider != after.provider:
        failure = "SOURCE_SCHEMA_CHANGED"
    elif before.account_uid != after.account_uid:
        failure = "ACCOUNT_CHANGED"
    elif before.playlist_id != after.playlist_id:
        failure = "PLAYLIST_CHANGED"
    elif before.versions != after.versions:
        failure = "SOURCE_VERSION_CHANGED"
    elif before.track_count != after.track_count:
        failure = "TRACK_COUNT_CHANGED"
    elif tuple((p.track_id, p.error_code, p.raw_id_json) for p in before.positions) != tuple(
        (p.track_id, p.error_code, p.raw_id_json) for p in after.positions
    ):
        failure = "TRACK_SEQUENCE_CHANGED"
    if failure:
        return _manifest(
            status="source_changed",
            error_code=failure,
            snapshot=after,
            positions=after.positions,
            fingerprint=snapshot_fingerprint(after),
        )

    fingerprint = snapshot_fingerprint(after)
    if after.track_count != len(after.positions):
        return _manifest(
            status="incomplete",
            error_code="TRACK_COUNT_MISMATCH",
            snapshot=after,
            positions=after.positions,
            fingerprint=fingerprint,
        )
    if any(position.error_code for position in after.positions):
        return _manifest(
            status="incomplete",
            error_code="INVALID_TRACK_ID",
            snapshot=after,
            positions=after.positions,
            fingerprint=fingerprint,
        )
    return _manifest(
        status="complete",
        error_code=None,
        snapshot=after,
        positions=after.positions,
        fingerprint=fingerprint,
    )
