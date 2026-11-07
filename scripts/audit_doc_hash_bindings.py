#!/usr/bin/env python3
"""Audit hash bindings and self-check stamps across the project documentation.

Two different hash semantics appear in this repository:

1. **File binding** — a document cites another file as `<name>.md` + SHA-256 of
   the whole file. Checked by hashing every file and matching tokens.
2. **Self-check stamp** — a document ends with a comment line declaring
   `sha256(all bytes before this line, UTF-8) = <hex>`. This value is *not* the
   file hash (the line itself is excluded), so it must be verified by
   recomputing the prefix hash, not by matching the file index.

For every 64-hex token found in a document this tool reports one of:

  active-binding  token equals the current SHA-256 of an indexed file
  valid-stamp     token is a declared self-check stamp and recomputation matches
  STALE-STAMP     token is a declared self-check stamp and recomputation FAILS
  historical      token matches no current file and is not a stamp — expected
                  for audit-trail entries ("已作废"/"中间态") and for hashes of
                  NAS-side artifacts never copied into the repository

Read-only: never writes into the repository, never contacts the device.
Exit code is 0 unless a stale stamp is found (then 1), so it can gate CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
STAMP_LINE = re.compile(
    r"^\s*(?:<!--\s*)?(?P<name>[A-Z0-9_-]*HASH-STAMP[A-Z0-9_-]*)"
    r"[^\n]*?=\s*(?P<hash>[0-9a-f]{64})",
)
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".venv", "venv"}
AUDIT_SUFFIXES = {".md", ".py", ".sh", ".json", ".txt", ".service", ".example", ".in"}


def iter_files(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            if os.path.splitext(name)[1].lower() in AUDIT_SUFFIXES or name in {"VERSION", ".env.example"}:
                yield path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_stamps(raw: bytes) -> list[dict]:
    """Return declared self-check stamps with their recomputed value."""
    text = raw.decode("utf-8", errors="replace")
    stamps = []
    offset = 0
    for lineno, line in enumerate(text.splitlines(keepends=True), 1):
        match = STAMP_LINE.match(line)
        if match:
            prefix = raw[:offset]
            stamps.append({
                "line": lineno,
                "name": match.group("name"),
                "declared": match.group("hash"),
                "recomputed": sha256_bytes(prefix),
            })
        offset += len(line.encode("utf-8"))
    return stamps


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repository root (default: cwd)")
    parser.add_argument("--json-out", default="", help="optional path for the JSON report")
    parser.add_argument("--quiet", action="store_true", help="only print the summary")
    args = parser.parse_args()

    root = os.path.abspath(args.root)
    paths = list(iter_files(root))

    index: dict[str, list[str]] = {}
    for path in paths:
        try:
            index.setdefault(sha256_of(path), []).append(os.path.relpath(path, root))
        except OSError as exc:
            print(f"WARN cannot hash {path}: {exc}", file=sys.stderr)

    doc_paths = [p for p in paths if p.lower().endswith(".md")]
    active: list[dict] = []
    stamp_valid: list[dict] = []
    stamp_stale: list[dict] = []
    historical: list[dict] = []

    for doc in doc_paths:
        rel = os.path.relpath(doc, root)
        try:
            with open(doc, "rb") as stream:
                raw = stream.read()
        except OSError as exc:
            print(f"WARN cannot read {rel}: {exc}", file=sys.stderr)
            continue
        stamps = find_stamps(raw)
        declared = {(s["line"], s["declared"]) for s in stamps}
        for stamp in stamps:
            entry = {"doc": rel, **stamp}
            if stamp["declared"] == stamp["recomputed"]:
                stamp_valid.append(entry)
            else:
                stamp_stale.append(entry)

        text = raw.decode("utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in HEX64.finditer(line):
                token = match.group(0)
                if (lineno, token) in declared:
                    continue  # already classified as a stamp
                if token in index:
                    active.append({"doc": rel, "line": lineno, "hash": token,
                                   "matches": index[token]})
                else:
                    historical.append({"doc": rel, "line": lineno, "hash": token})

    report = {
        "root": root,
        "indexed_files": len(index),
        "documents_scanned": len(doc_paths),
        "counts": {
            "active_bindings": len(active),
            "valid_stamps": len(stamp_valid),
            "stale_stamps": len(stamp_stale),
            "historical_tokens": len(historical),
        },
        "stale_stamps": stamp_stale,
        "valid_stamps": stamp_valid,
        "historical": historical,
        "duplicate_content": {digest: hits for digest, hits in index.items() if len(hits) > 1},
    }

    print(f"root                : {root}")
    print(f"indexed files       : {report['indexed_files']}")
    print(f"documents scanned   : {report['documents_scanned']}")
    print(f"active bindings     : {len(active)}")
    print(f"valid self-stamps   : {len(stamp_valid)}")
    print(f"STALE self-stamps   : {len(stamp_stale)}")
    print(f"historical tokens   : {len(historical)}")

    if stamp_stale:
        print("\n-- STALE self-check stamps (recomputation failed) --")
        for entry in stamp_stale:
            print(f"  {entry['doc']}:{entry['line']}  {entry['name']}  "
                  f"declared={entry['declared'][:16]}... recomputed={entry['recomputed'][:16]}...")

    if report["duplicate_content"]:
        print("\n-- duplicate file contents --")
        for digest, hits in sorted(report["duplicate_content"].items()):
            print(f"  {digest[:16]}... -> {', '.join(hits)}")

    if historical and not args.quiet:
        print(f"\n-- historical tokens ({len(historical)}; expected for audit-trail "
              f"entries and NAS-side artifacts) --")
        for entry in historical:
            print(f"  {entry['doc']}:{entry['line']}  {entry['hash'][:16]}...")

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
        print(f"\nJSON report written to {args.json_out}")

    return 1 if stamp_stale else 0


if __name__ == "__main__":
    raise SystemExit(main())
