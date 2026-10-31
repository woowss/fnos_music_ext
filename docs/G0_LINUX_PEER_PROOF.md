# G0 Linux peer-credential proof candidate

`proxy/tests/test_gateway_identity_linux.py` exercises the existing
`read_peer_credentials()` helper against a real Linux `AF_UNIX` stream listener
and its accepted socket. A child process sends deliberately forged
`X-Trim-*` header bytes. The test checks that `SO_PEERCRED` reports that
process's kernel PID/UID/GID, and that reading those credentials neither
consumes its pending request bytes nor closes the accepted socket.

Run on Linux with:

```text
python -B -m pytest -q proxy/tests/test_gateway_identity_linux.py
```

Non-Linux platforms explicitly skip this test. A passing Linux run proves only
the local kernel/socket mechanism and this helper's behavior. It does not prove
that fnOS uses this socket, authenticate an official gateway peer, validate
header sanitization, authorize an administrator, or establish a supported
service lifecycle. It is not a G0 or deployment pass and must not be used to
change NAS socket permissions or enable privileged routes.
