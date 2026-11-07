#!/bin/bash
# Corrected reflink (FICLONE) bidirectional COW test on /vol1.
# Addresses review F2: capture each hash BEFORE the mutating operation so the
# opposite-side comparison is not a tautology.
set -u
D=/vol1/1000/fnm-reflink-cow-test-20261107
LOGF=/tmp/fnm-reflink-cow.log
: > "$LOGF"
say() { echo "$@" | tee -a "$LOGF"; }
cleanup() {
  set +e
  [ -e "$D/src" ] && btrfs subvolume delete "$D/src" >/dev/null 2>&1
  rm -rf "$D" 2>/dev/null
}
trap cleanup EXIT
if [ -e "$D" ]; then say "ABORT dir exists"; exit 1; fi
mkdir -p "$D" && chmod 755 "$D"
btrfs subvolume create "$D/src" >/dev/null 2>&1
dd if=/dev/urandom of="$D/src/big.bin" bs=1M count=8 status=none
cp --reflink=always "$D/src/big.bin" "$D/copy.bin"
sha() { sha256sum "$1" | cut -d' ' -f1; }
say "== date $(date -Is) =="
H0=$(sha "$D/src/big.bin"); C0=$(sha "$D/copy.bin")
say "H0 source after copy   = $H0"
say "C0 copy after copy     = $C0"
if command -v filefrag >/dev/null; then
  say "-- filefrag source --"; filefrag -v "$D/src/big.bin" 2>&1 | sed -n '1,6p' | tee -a "$LOGF"
  say "-- filefrag copy --";   filefrag -v "$D/copy.bin" 2>&1 | sed -n '1,6p' | tee -a "$LOGF"
fi
# direction 1: mutate source, copy must stay at C0
python3 - "$D/src/big.bin" <<'PY' | tee -a "$LOGF"
import os, sys
with open(sys.argv[1], "r+b") as f:
    f.seek(0); f.write(b"SRC-MOD"); f.flush(); os.fsync(f.fileno())
print("mutated source")
PY
H1=$(sha "$D/src/big.bin"); C1=$(sha "$D/copy.bin")
say "H1 source after src-mod  = $H1"
say "C1 copy   after src-mod  = $C1"
say "dir1: copy unchanged C1==C0 ? $([ "$C1" = "$C0" ] && echo yes || echo NO)"
say "dir1: source changed H1!=H0 ? $([ "$H1" != "$H0" ] && echo yes || echo NO)"
# direction 2: mutate copy, source must stay at H1 (captured before mutating copy)
python3 - "$D/copy.bin" <<'PY' | tee -a "$LOGF"
import os, sys
with open(sys.argv[1], "r+b") as f:
    f.seek(8192); f.write(b"COPY-MOD"); f.flush(); os.fsync(f.fileno())
print("mutated copy")
PY
H2=$(sha "$D/src/big.bin"); C2=$(sha "$D/copy.bin")
say "H2 source after copy-mod = $H2"
say "C2 copy   after copy-mod = $C2"
say "dir2: source unchanged H2==H1 ? $([ "$H2" = "$H1" ] && echo yes || echo NO)"
say "dir2: copy changed C2!=C1 ? $([ "$C2" != "$C1" ] && echo yes || echo NO)"
say ""
say "== done =="
