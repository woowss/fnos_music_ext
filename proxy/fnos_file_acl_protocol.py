"""Decode a single fnOS file-ACL response without trusting its origin.

This protocol-only candidate validates response shape and request correlation.
It does not authenticate the response, identify the caller, or authorize a
filesystem operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import NoReturn


MAX_REPLY_BYTES = 64 * 1024
MAX_REQ_ID_BYTES = 128
MAX_PATH_BYTES = 4096
MAX_JSON_DEPTH = 3

_ROOT_FIELDS = frozenset({"reqId", "code", "msg", "data"})
_DATA_FIELDS = frozenset({"path", "readable", "writable", "deletable"})


@dataclass(frozen=True, slots=True)
class FileACLReply:
    """A redacted protocol result, never an authorization decision."""

    valid: bool
    reason: str
    readable: bool = False
    writable: bool = False
    deletable: bool = False
    authenticated: bool = False
    production_ready: bool = False

    def __bool__(self) -> NoReturn:
        raise TypeError("FileACLReply has no truth value; inspect .valid explicitly")


class _DuplicateJSONKey(ValueError):
    pass


class _NonFiniteJSONNumber(ValueError):
    pass


def _invalid(reason: str) -> FileACLReply:
    return FileACLReply(valid=False, reason=reason)


def _valid_expected_text(value: object, max_bytes: int) -> str | None:
    if type(value) is not str or not value:
        return None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    if len(encoded) > max_bytes:
        return None
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey
        result[key] = value
    return result


def _reject_constant(_value: str) -> NoReturn:
    raise _NonFiniteJSONNumber


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise _NonFiniteJSONNumber
    return parsed


def _check_json_depth(raw: bytes) -> str | None:
    """Bound nesting before json.loads; braces inside quoted strings do not count."""
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # quote
                in_string = False
            continue

        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):  # [ or {
            depth += 1
            if depth > MAX_JSON_DEPTH:
                return "json_too_deep"
        elif byte in (0x5D, 0x7D):  # ] or }
            depth -= 1
            if depth < 0:
                return "invalid_json"
    return None


def decode_single_path_acl_reply(
    raw: object,
    *,
    expected_req_id: object,
    expected_path: object,
) -> FileACLReply:
    """Validate one successful checkUserACL response using fixed errors.

    Exact strings are compared as decoded; paths are never normalized or
    inspected on disk. A structurally valid reply is not proof of who sent it
    or which UID was checked.
    """
    request_id = _valid_expected_text(expected_req_id, MAX_REQ_ID_BYTES)
    path = _valid_expected_text(expected_path, MAX_PATH_BYTES)
    if request_id is None or path is None:
        return _invalid("invalid_expected_value")

    if type(raw) is not bytes:
        return _invalid("invalid_reply_type")
    if len(raw) > MAX_REPLY_BYTES:
        return _invalid("reply_too_large")
    if raw.startswith(b"\xef\xbb\xbf"):
        return _invalid("bom_not_allowed")

    depth_error = _check_json_depth(raw)
    if depth_error is not None:
        return _invalid(depth_error)

    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return _invalid("invalid_utf8")

    try:
        document = json.loads(
            text,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except _DuplicateJSONKey:
        return _invalid("duplicate_json_key")
    except _NonFiniteJSONNumber:
        return _invalid("non_finite_number")
    except (json.JSONDecodeError, RecursionError, ValueError):
        return _invalid("invalid_json")

    if type(document) is not dict or frozenset(document) != _ROOT_FIELDS:
        return _invalid("invalid_response_shape")

    response_id = document["reqId"]
    code = document["code"]
    message = document["msg"]
    entries = document["data"]
    if type(response_id) is not str or response_id != request_id:
        return _invalid("request_id_mismatch")
    if type(code) is not int:
        return _invalid("invalid_response_shape")
    if code != 0:
        return _invalid("upstream_rejected")
    if type(message) is not str or any(0xD800 <= ord(ch) <= 0xDFFF for ch in message):
        return _invalid("invalid_response_shape")
    if type(entries) is not list or len(entries) != 1:
        return _invalid("invalid_response_shape")

    entry = entries[0]
    if type(entry) is not dict or frozenset(entry) != _DATA_FIELDS:
        return _invalid("invalid_response_shape")
    if type(entry["path"]) is not str or entry["path"] != path:
        return _invalid("path_mismatch")

    readable = entry["readable"]
    writable = entry["writable"]
    deletable = entry["deletable"]
    if any(type(value) is not bool for value in (readable, writable, deletable)):
        return _invalid("invalid_permission_value")

    return FileACLReply(
        valid=True,
        reason="reply_valid",
        readable=readable,
        writable=writable,
        deletable=deletable,
    )
