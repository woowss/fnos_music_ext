"""Test-only Python write-intent audit hook.

This module is imported by offline tests only; it is not product code and is
never imported by the application or the candidates under test.

A Python audit hook observes Python-level API calls (``open``, ``os.mkdir``,
``os.link`` ...) but not C-level opens made inside extension modules, so this
check complements (does not replace) a real directory-residue check.
"""

from __future__ import annotations

import os
import sys

_O_TMPFILE = getattr(os, "O_TMPFILE", 0)
_WRITE_OPEN_FLAGS = (
    getattr(os, "O_WRONLY", 0)
    | getattr(os, "O_RDWR", 0)
    | getattr(os, "O_CREAT", 0)
    | getattr(os, "O_TRUNC", 0)
    | getattr(os, "O_APPEND", 0)
)
_WRITE_EVENTS = frozenset({
    "os.mkdir",
    "os.rmdir",
    "os.remove",
    "os.unlink",
    "os.rename",
    "os.replace",
    "os.link",
    "os.symlink",
    "os.truncate",
})


class WriteAudit:
    """Collect Python-level file writes; inspect them with ``violations()``.

    Python audit hooks cannot be removed once added, so each instance records
    only while ``active`` is true. Anonymous staging is allowed: an ``open``
    flagged with ``O_TMPFILE`` creates no directory entry, and an ``os.chmod``
    whose first argument is an integer descriptor can only change the mode of
    the already-open anonymous inode, not a named path.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, tuple]] = []
        self.active = False
        sys.addaudithook(self._hook)

    def _hook(self, event: str, args: tuple) -> None:
        if self.active:
            self.events.append((event, args))

    def reset(self) -> None:
        self.events.clear()

    def violations(self) -> list[list[str]]:
        found: list[list[str]] = []
        for event, args in self.events:
            if event == "open":
                path, mode, flags = args
                if isinstance(flags, int) and flags & _O_TMPFILE:
                    continue
                if isinstance(mode, str) and any(character in mode for character in "wax+"):
                    found.append(["open", os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else repr(path)])
                elif isinstance(flags, int) and flags & _WRITE_OPEN_FLAGS:
                    found.append(["open", os.fspath(path) if isinstance(path, (str, bytes, os.PathLike)) else repr(path)])
            elif event == "os.chmod":
                target = args[0] if args else None
                if isinstance(target, int):
                    continue
                found.append(["os.chmod", repr(target)[:120]])
            elif event in _WRITE_EVENTS:
                found.append([event, repr(args)[:120]])
        return found
