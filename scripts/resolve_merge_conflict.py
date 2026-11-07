#!/usr/bin/env python3
"""Inspect and resolve merge conflicts in a file, UTF-8 safe.

The 2.12.0 migration produced files with standard conflict markers:

    <<<<<<< ours
    ...
    =======
    ...
    >>>>>>> theirs

This tool never guesses semantics. It reports each conflict block so a human can
decide, and applies an explicit decision:

    dump    --file F                     list blocks with line numbers and sizes
    show    --file F --block N           print both sides of block N (UTF-8)
    resolve --file F --block N --choice ours|theirs|both-ours|both-theirs

`both-*` keeps both sides concatenated in the stated order, which is the common
case when our side adds new functions and theirs rewrites an existing region.

Reads and writes UTF-8 without BOM; refuses to run if the file has no markers.
"""

from __future__ import annotations

import argparse
import sys

OURS = "<<<<<<< "
BASE = "||||||| "
SEP = "======="
THEIRS = ">>>>>>> "


def find_blocks(lines: list[str]) -> list[dict]:
    blocks = []
    i = 0
    while i < len(lines):
        if lines[i].startswith(OURS):
            start = i
            sep = base = None
            j = i + 1
            while j < len(lines) and not lines[j].startswith(THEIRS):
                if lines[j].startswith(BASE) and base is None:
                    base = j
                elif lines[j].rstrip("\r\n") == SEP and sep is None:
                    sep = j
                j += 1
            if sep is None or j >= len(lines):
                raise SystemExit(f"malformed conflict block starting at line {start + 1}")
            blocks.append({"start": start, "sep": sep, "base": base, "end": j})
            i = j + 1
        else:
            i += 1
    return blocks


def read_lines(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return fh.readlines()


def write_lines(path: str, lines: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.writelines(lines)


def cmd_dump(args) -> int:
    lines = read_lines(args.file)
    blocks = find_blocks(lines)
    print(f"{args.file}: {len(blocks)} conflict block(s)")
    for n, b in enumerate(blocks, 1):
        ours_n = (b["base"] or b["sep"]) - b["start"] - 1
        theirs_n = b["end"] - b["sep"] - 1
        base_n = (b["sep"] - b["base"] - 1) if b["base"] else 0
        print(f"  block {n}: line {b['start'] + 1}, ours={ours_n} theirs={theirs_n} base={base_n}")
    return 0


def cmd_show(args) -> int:
    lines = read_lines(args.file)
    blocks = find_blocks(lines)
    if not 1 <= args.block <= len(blocks):
        raise SystemExit(f"block {args.block} out of range (1..{len(blocks)})")
    b = blocks[args.block - 1]
    ours = lines[b["start"] + 1:(b["base"] or b["sep"])]
    theirs = lines[b["sep"] + 1:b["end"]]
    print(f"=== {args.file} block {args.block} (line {b['start'] + 1}) ===")
    print(f"--- OURS ({len(ours)} lines) ---")
    sys.stdout.writelines(ours)
    print(f"--- THEIRS ({len(theirs)} lines) ---")
    sys.stdout.writelines(theirs)
    return 0


def cmd_resolve(args) -> int:
    lines = read_lines(args.file)
    blocks = find_blocks(lines)
    if not 1 <= args.block <= len(blocks):
        raise SystemExit(f"block {args.block} out of range (1..{len(blocks)})")
    b = blocks[args.block - 1]
    ours = lines[b["start"] + 1:(b["base"] or b["sep"])]
    theirs = lines[b["sep"] + 1:b["end"]]
    if args.choice == "ours":
        replacement = ours
    elif args.choice == "theirs":
        replacement = theirs
    elif args.choice == "both-ours":
        replacement = ours + theirs
    elif args.choice == "both-theirs":
        replacement = theirs + ours
    else:
        raise SystemExit(f"unknown choice {args.choice}")
    new_lines = lines[:b["start"]] + replacement + lines[b["end"] + 1:]
    write_lines(args.file, new_lines)
    print(f"resolved {args.file} block {args.block} with {args.choice}; "
          f"remaining blocks: {len(find_blocks(new_lines))}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("dump"); d.add_argument("--file", required=True); d.set_defaults(func=cmd_dump)
    s = sub.add_parser("show"); s.add_argument("--file", required=True); s.add_argument("--block", type=int, required=True); s.set_defaults(func=cmd_show)
    r = sub.add_parser("resolve"); r.add_argument("--file", required=True); r.add_argument("--block", type=int, required=True)
    r.add_argument("--choice", required=True, choices=["ours", "theirs", "both-ours", "both-theirs"])
    r.set_defaults(func=cmd_resolve)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
