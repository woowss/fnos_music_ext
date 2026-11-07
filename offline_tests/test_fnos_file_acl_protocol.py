"""Synthetic tests for the fnOS file-ACL reply protocol candidate."""
from __future__ import annotations

from dataclasses import FrozenInstanceError
import importlib
import importlib.util
import itertools
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import unittest
from unittest import mock

from proxy import fnos_file_acl_protocol as protocol


_REQ_ID = "request-123"
_PATH = "/vol1/1000/music/track.flac"
_IMPORT_PROBE = "proxy._fnos_file_acl_protocol_import_probe"
_DEFAULT_DATA = object()


def _reply_bytes(
    *,
    req_id: object = _REQ_ID,
    code: object = 0,
    message: object = "",
    data: object = _DEFAULT_DATA,
) -> bytes:
    if data is _DEFAULT_DATA:
        data = [{
            "path": _PATH,
            "readable": True,
            "writable": False,
            "deletable": False,
        }]
    return json.dumps(
        {"reqId": req_id, "code": code, "msg": message, "data": data},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _decode(raw: object, *, req_id: object = _REQ_ID, path: object = _PATH):
    return protocol.decode_single_path_acl_reply(
        raw,
        expected_req_id=req_id,
        expected_path=path,
    )


def _entry(**overrides):
    values = {
        "path": _PATH,
        "readable": True,
        "writable": False,
        "deletable": False,
    }
    values.update(overrides)
    return values


def _document(**overrides):
    values = {
        "reqId": _REQ_ID,
        "code": 0,
        "msg": "",
        "data": [_entry()],
    }
    values.update(overrides)
    return values


class FileACLProtocolTests(unittest.TestCase):
    def assert_invalid_redacted(self, raw, *, req_id=_REQ_ID, path=_PATH, markers=()):
        result = _decode(raw, req_id=req_id, path=path)
        self.assertFalse(result.valid)
        self.assertTrue(result.reason)
        self.assertFalse(result.readable)
        self.assertFalse(result.writable)
        self.assertFalse(result.deletable)
        self.assertFalse(result.authenticated)
        self.assertFalse(result.production_ready)
        rendered = repr(result)
        for marker in markers:
            self.assertNotIn(marker, rendered)
        return result

    def test_all_eight_boolean_permission_combinations_are_valid(self):
        for readable, writable, deletable in itertools.product((False, True), repeat=3):
            with self.subTest(readable=readable, writable=writable, deletable=deletable):
                result = _decode(_reply_bytes(data=[_entry(
                    readable=readable,
                    writable=writable,
                    deletable=deletable,
                )]))
                self.assertEqual(
                    result,
                    protocol.FileACLReply(
                        valid=True,
                        reason="reply_valid",
                        readable=readable,
                        writable=writable,
                        deletable=deletable,
                    ),
                )
                self.assertFalse(result.authenticated)
                self.assertFalse(result.production_ready)

    def test_valid_reply_is_immutable_and_has_no_authorization_alias(self):
        result = _decode(_reply_bytes())
        with self.assertRaises(FrozenInstanceError):
            result.writable = True
        self.assertFalse(any(hasattr(result, name) for name in ("allow", "allow_write", "authorized")))
        invalid = _decode(b"{not-json}")
        for reply in (result, invalid):
            with self.subTest(valid=reply.valid):
                with self.assertRaisesRegex(TypeError, r"inspect \.valid explicitly"):
                    bool(reply)

    def test_exact_request_id_and_path_are_required_without_normalization(self):
        self.assertEqual(_decode(_reply_bytes()).reason, "reply_valid")
        self.assertEqual(_decode(_reply_bytes(), req_id="request-124").reason, "request_id_mismatch")
        self.assertEqual(_decode(_reply_bytes(), path="/vol1/1000/music/./track.flac").reason, "path_mismatch")
        unicode_path = "/vol1/1000/music/曲目.flac"
        result = _decode(_reply_bytes(data=[_entry(path=unicode_path)]), path=unicode_path)
        self.assertTrue(result.valid)

    def test_expected_values_are_strict_nonempty_utf8_and_bounded(self):
        req_id_unicode_at_limit = "\u00e9" * (protocol.MAX_REQ_ID_BYTES // 2)
        req_id_unicode_over = req_id_unicode_at_limit + "x"
        self.assertEqual(len(req_id_unicode_at_limit.encode("utf-8")), protocol.MAX_REQ_ID_BYTES)
        self.assertEqual(len(req_id_unicode_over.encode("utf-8")), protocol.MAX_REQ_ID_BYTES + 1)
        invalid = [None, b"request-123", "", "\ud800", "x" * (protocol.MAX_REQ_ID_BYTES + 1), req_id_unicode_over]
        for value in invalid:
            with self.subTest(req_id=value):
                self.assertEqual(_decode(_reply_bytes(), req_id=value).reason, "invalid_expected_value")

        path_unicode_at_limit = "/" + "\u754c" * ((protocol.MAX_PATH_BYTES - 1) // 3)
        path_unicode_over = path_unicode_at_limit + "x"
        self.assertEqual(len(path_unicode_at_limit.encode("utf-8")), protocol.MAX_PATH_BYTES)
        self.assertEqual(len(path_unicode_over.encode("utf-8")), protocol.MAX_PATH_BYTES + 1)
        invalid_paths = [None, b"/path", "", "\udfff", "/" + "x" * protocol.MAX_PATH_BYTES, path_unicode_over]
        for value in invalid_paths:
            with self.subTest(path=value):
                self.assertEqual(_decode(_reply_bytes(), path=value).reason, "invalid_expected_value")

        req_id_at_limit = "r" * protocol.MAX_REQ_ID_BYTES
        path_at_limit = "/" + "p" * (protocol.MAX_PATH_BYTES - 1)
        for request_id, accepted_path in ((req_id_at_limit, path_at_limit), (req_id_unicode_at_limit, path_unicode_at_limit)):
            with self.subTest(multibyte=request_id == req_id_unicode_at_limit):
                result = _decode(
                    _reply_bytes(req_id=request_id, data=[_entry(path=accepted_path)]),
                    req_id=request_id,
                    path=accepted_path,
                )
                self.assertTrue(result.valid)

    def test_reply_type_size_boundary_and_overflow(self):
        self.assertEqual(self.assert_invalid_redacted(bytearray(_reply_bytes())).reason, "invalid_reply_type")
        valid = _reply_bytes()
        exact_limit = valid + (b" " * (protocol.MAX_REPLY_BYTES - len(valid)))
        self.assertTrue(_decode(exact_limit).valid)
        self.assertEqual(self.assert_invalid_redacted(exact_limit + b" ").reason, "reply_too_large")

    def test_invalid_utf8_bom_and_malformed_json_are_rejected(self):
        cases = [
            (b"\xff", "invalid_utf8"),
            (b"\xef\xbb\xbf" + _reply_bytes(), "bom_not_allowed"),
            (b"{not-json}", "invalid_json"),
            (b"{\"reqId\":", "invalid_json"),
        ]
        for raw, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                self.assertEqual(self.assert_invalid_redacted(raw).reason, expected_reason)

    def test_duplicate_json_keys_are_rejected_at_every_object_level(self):
        root = b'{"reqId":"request-123","reqId":"request-123","code":0,"msg":"","data":[]}'
        nested = b'{"reqId":"request-123","code":0,"msg":"","data":[{"path":"/vol1/1000/music/track.flac","path":"/vol1/1000/music/track.flac","readable":true,"writable":false,"deletable":false}]}'
        self.assertEqual(self.assert_invalid_redacted(root).reason, "duplicate_json_key")
        self.assertEqual(self.assert_invalid_redacted(nested).reason, "duplicate_json_key")

    def test_non_finite_constants_and_overflowing_float_are_rejected(self):
        for number in (b"NaN", b"Infinity", b"-Infinity", b"1e999"):
            with self.subTest(number=number):
                raw = _reply_bytes().replace(b'"code":0', b'"code":' + number)
                self.assertEqual(self.assert_invalid_redacted(raw).reason, "non_finite_number")

    def test_json_nesting_is_bounded_and_escaped_quotes_and_backslashes_are_scanned(self):
        deep = _reply_bytes().replace(b'"msg":""', b'"msg":' + (b"[" * (protocol.MAX_JSON_DEPTH + 1)) + b"0" + (b"]" * (protocol.MAX_JSON_DEPTH + 1)))
        self.assertEqual(self.assert_invalid_redacted(deep).reason, "json_too_deep")
        string_value = _reply_bytes(message='brackets [[[{{{]]]}}}, quote " then [[[[]]]], backslashes \\ stay in the string')
        self.assertTrue(_decode(string_value).valid)

    def test_root_schema_request_id_code_and_message_are_strict(self):
        cases = [
            (_reply_bytes(req_id="other"), "request_id_mismatch"),
            (_reply_bytes(code=True), "invalid_response_shape"),
            (_reply_bytes(code=0.0), "invalid_response_shape"),
            (_reply_bytes(code="0"), "invalid_response_shape"),
            (_reply_bytes(code=7, message="PRIVATE_UPSTREAM_TEXT"), "upstream_rejected"),
            (_reply_bytes(message=3), "invalid_response_shape"),
        ]
        for raw, expected_reason in cases:
            with self.subTest(expected_reason=expected_reason):
                result = self.assert_invalid_redacted(raw, markers=("PRIVATE_UPSTREAM_TEXT",))
                self.assertEqual(result.reason, expected_reason)

        for document in (
            {"code": 0, "msg": "", "data": [_entry()]},
            {**_document(), "unexpected": "SENSITIVE_EXTRA"},
            [],
            None,
        ):
            raw = json.dumps(document, separators=(",", ":")).encode()
            result = self.assert_invalid_redacted(raw, markers=("SENSITIVE_EXTRA",))
            self.assertEqual(result.reason, "invalid_response_shape")

        lone_surrogate_message = b'{"reqId":"request-123","code":0,"msg":"\\ud800","data":[]}'
        self.assertEqual(self.assert_invalid_redacted(lone_surrogate_message).reason, "invalid_response_shape")

    def test_data_must_contain_exactly_one_exact_path_entry(self):
        cases = [
            (None, "invalid_response_shape"),
            ([], "invalid_response_shape"),
            ([ _entry(), _entry() ], "invalid_response_shape"),
            (["not-an-object"], "invalid_response_shape"),
            ([{k: v for k, v in _entry().items() if k != "path"}], "invalid_response_shape"),
            ([_entry(extra="EXTRA_MARKER")], "invalid_response_shape"),
            ([_entry(path=42)], "path_mismatch"),
            ([_entry(path="/other/PRIVATE_PATH")], "path_mismatch"),
        ]
        for data, expected_reason in cases:
            with self.subTest(data=data):
                result = self.assert_invalid_redacted(
                    _reply_bytes(data=data), markers=("EXTRA_MARKER", "PRIVATE_PATH"),
                )
                self.assertEqual(result.reason, expected_reason)

    def test_permission_fields_require_exact_bools_and_exact_field_set(self):
        for field in ("readable", "writable", "deletable"):
            for value in (0, 1, "true", "false", None):
                with self.subTest(field=field, value=value):
                    self.assertEqual(
                        self.assert_invalid_redacted(
                            _reply_bytes(data=[_entry(**{field: value})]),
                        ).reason,
                        "invalid_permission_value",
                    )

        for entry in (
            {k: v for k, v in _entry().items() if k != "writable"},
            {**_entry(), "allow": True},
        ):
            self.assertEqual(
                self.assert_invalid_redacted(_reply_bytes(data=[entry])).reason,
                "invalid_response_shape",
            )

    def test_all_failures_are_fixed_and_do_not_echo_sensitive_input(self):
        marker = "PRIVATE_REQUEST_PATH_MARKER"
        result = self.assert_invalid_redacted(
            _reply_bytes(data=[_entry(path=marker)]),
            path="/different/path",
            markers=(marker, "request-123", "PRIVATE_UPSTREAM"),
        )
        self.assertEqual(result.reason, "path_mismatch")

    def test_import_and_call_have_no_io_environment_network_process_or_thread_effect(self):
        source_path = Path(protocol.__file__)
        spec = importlib.util.spec_from_file_location(_IMPORT_PROBE, source_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        assert spec is not None and spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        if _IMPORT_PROBE in sys.modules:
            self.fail("import probe unexpectedly already exists")

        with mock.patch("builtins.open", side_effect=AssertionError("open")), \
             mock.patch("socket.socket", side_effect=AssertionError("socket")), \
             mock.patch("subprocess.Popen", side_effect=AssertionError("process")), \
             mock.patch("os.getenv", side_effect=AssertionError("environment")), \
             mock.patch("threading.Thread", side_effect=AssertionError("thread")):
            sys.modules[_IMPORT_PROBE] = probe
            try:
                spec.loader.exec_module(probe)
                result = probe.decode_single_path_acl_reply(
                    _reply_bytes(),
                    expected_req_id=_REQ_ID,
                    expected_path=_PATH,
                )
            finally:
                sys.modules.pop(_IMPORT_PROBE, None)
        self.assertTrue(result.valid)


if __name__ == "__main__":
    unittest.main()
