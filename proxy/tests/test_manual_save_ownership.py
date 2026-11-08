"""Ownership/permission tagging for the manual-save publish path.

The production gate runs the save as root. A published O_TMPFILE inode therefore
stays ``root:root 0600`` and a normal fnOS user cannot open the saved FLAC/LRC in
the file manager. Both storage adapters retag the *already published* inode,
best effort, to the destination directory's owner/group with mode 0640.

These tests are platform-independent: the ``os`` callables are injected, so the
success and every denied/failed branch are exercised on Windows too. One real
``fchown`` round-trip test is Linux-only and skipped elsewhere.
"""
from __future__ import annotations

import os
import stat
import sys
from types import SimpleNamespace

import pytest

from proxy import manual_save_anonymous as anonymous
from proxy import manual_save_posix as posix


TAG_MODULES = (anonymous, posix)
MODULE_IDS = ("anonymous", "posix")


def test_published_file_mode_is_0640():
    for module in TAG_MODULES:
        assert module.PUBLISHED_FILE_MODE == 0o640
        assert module.OWNERSHIP_TAGGED == "tagged"
        assert module.OWNERSHIP_UNAVAILABLE == "unavailable"


class _FakeOs:
    """Small injectable stand-in for the three descriptor calls under test."""

    def __init__(self, *, uid=1000, gid=1000, mode=0o640,
                 chown_error=None, chmod_error=None, stat_error=None,
                 stat_uid=None, stat_gid=None, stat_mode=None):
        self.uid = uid
        self.gid = gid
        self.mode = mode
        self.chown_error = chown_error
        self.chmod_error = chmod_error
        self.stat_error = stat_error
        self.stat_uid = uid if stat_uid is None else stat_uid
        self.stat_gid = gid if stat_gid is None else stat_gid
        self.stat_mode = mode if stat_mode is None else stat_mode
        self.chown_calls = []
        self.chmod_calls = []
        self.stat_calls = []

    def fchown(self, fd, uid, gid):
        self.chown_calls.append((fd, uid, gid))
        if self.chown_error is not None:
            raise self.chown_error

    def fchmod(self, fd, mode):
        self.chmod_calls.append((fd, mode))
        if self.chmod_error is not None:
            raise self.chmod_error

    def fstat(self, fd):
        self.stat_calls.append(fd)
        if self.stat_error is not None:
            raise self.stat_error
        return SimpleNamespace(
            st_uid=self.stat_uid,
            st_gid=self.stat_gid,
            st_mode=stat.S_IFREG | self.stat_mode,
        )


def _tag(module, fake, *, uid=1000, gid=1000):
    return module._tag_published_ownership(
        uid + 7,
        uid,
        gid,
        fchown=fake.fchown,
        fchmod=fake.fchmod,
        fstat=fake.fstat,
    )


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_tagging_applies_destination_owner_group_and_0640(module):
    fake = _FakeOs(uid=1000, gid=1000)
    assert _tag(module, fake) == module.OWNERSHIP_TAGGED
    assert fake.chown_calls == [(1007, 1000, 1000)]
    assert fake.chmod_calls == [(1007, 0o640)]
    assert fake.stat_calls == [1007]


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_denied_chown_keeps_publishing_but_reports_unavailable(module):
    fake = _FakeOs(uid=1000, gid=1000, chown_error=PermissionError("denied"))
    assert _tag(module, fake) == module.OWNERSHIP_UNAVAILABLE
    # The owner is unchanged, but the group-readable mode is still attempted.
    assert fake.chmod_calls == [(1007, 0o640)]


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_denied_chmod_reports_unavailable(module):
    fake = _FakeOs(uid=1000, gid=1000, chmod_error=OSError("denied"))
    assert _tag(module, fake) == module.OWNERSHIP_UNAVAILABLE


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_acl_override_that_does_not_stick_reports_unavailable(module):
    # The calls succeed but an ACL-inheriting filesystem reports the old mode.
    fake = _FakeOs(uid=1000, gid=1000, stat_mode=0o600)
    assert _tag(module, fake) == module.OWNERSHIP_UNAVAILABLE


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_group_that_does_not_stick_reports_unavailable(module):
    fake = _FakeOs(uid=1000, gid=1000, stat_gid=555)
    assert _tag(module, fake) == module.OWNERSHIP_UNAVAILABLE


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_owner_that_does_not_stick_reports_unavailable(module):
    fake = _FakeOs(uid=1000, gid=1000, stat_uid=0)
    assert _tag(module, fake) == module.OWNERSHIP_UNAVAILABLE


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_missing_fchown_reports_unavailable(module):
    fake = _FakeOs()
    result = module._tag_published_ownership(
        1, 1000, 1000,
        fchown=False,
        fchmod=fake.fchmod,
        fstat=fake.fstat,
    )
    assert result == module.OWNERSHIP_UNAVAILABLE


@pytest.mark.parametrize("module", TAG_MODULES, ids=MODULE_IDS)
def test_fstat_failure_reports_unavailable(module):
    fake = _FakeOs(stat_error=OSError("gone"))
    assert _tag(module, fake) == module.OWNERSHIP_UNAVAILABLE


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not hasattr(os, "fchown"),
    reason="a real fchown round-trip requires Linux",
)
def test_real_fchown_and_fchmod_round_trip_on_published_file(tmp_path):
    path = tmp_path / "published.flac"
    path.write_bytes(b"synthetic")
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    try:
        uid = os.geteuid()
        gid = os.getegid()
        status = anonymous._tag_published_ownership(descriptor, uid, gid)
        info = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    assert status == "tagged"
    assert (info.st_uid, info.st_gid) == (uid, gid)
    assert stat.S_IMODE(info.st_mode) == 0o640
