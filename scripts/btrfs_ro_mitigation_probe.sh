#!/bin/bash
# Follow-up: mitigation candidates for the trimacl RO-enforcement bypass.
#   E4: reflink (FICLONE) COW independence on /vol1 (larger file, extent + content proof)
#   E5: btrfs send -> receive into an independent loop btrfs (no trimacl) -> RO enforced
# All writes confined to /tmp and one disposable /vol1 probe tree; trap cleanup.
set -u
IMG=/tmp/fnm-m2-loop.img
MNT=/tmp/fnm-m2-loop-mnt
PROBE=/vol1/1000/fnmusic-btrfs-ro-probe2-20261107
STREAM=/tmp/fnm-m2.stream
LOGF=/tmp/fnm-ro-mitigation.log
: > "$LOGF"
say() { echo "$@" | tee -a "$LOGF"; }

cleanup() {
  set +e
  umount "$MNT" 2>/dev/null
  rm -rf "$MNT" 2>/dev/null
  rm -f "$IMG" "$STREAM" 2>/dev/null
  if [ -d "$PROBE" ]; then
    for sv in "$PROBE"/*; do
      [ -d "$sv" ] && btrfs subvolume delete "$sv" >/dev/null 2>&1
    done
    rm -rf "$PROBE"
  fi
}
trap cleanup EXIT

say "== BEGIN $(date -Is) kernel=$(uname -r) =="
if [ -e "$PROBE" ]; then say "ABORT: probe tree exists"; exit 1; fi
mkdir -p "$PROBE" && chmod 755 "$PROBE"

# ---------- E4: reflink COW independence on /vol1 ----------
say ""
say "== E4: reflink COW on /vol1 =="
btrfs subvolume create "$PROBE/src" >/dev/null 2>&1
dd if=/dev/urandom of="$PROBE/src/big.bin" bs=1M count=8 status=none 2>>"$LOGF"; say "dd rc=$? bytes=$(stat -c %s "$PROBE/src/big.bin" 2>/dev/null)"
cp --reflink=always "$PROBE/src/big.bin" "$PROBE/copy.bin" 2>>"$LOGF"; say "reflink cp rc=$?"
H_SRC_BEFORE=$(sha256sum "$PROBE/src/big.bin" | cut -d' ' -f1)
H_COPY_BEFORE=$(sha256sum "$PROBE/copy.bin" | cut -d' ' -f1)
say "hashes equal after copy: $([ "$H_SRC_BEFORE" = "$H_COPY_BEFORE" ] && echo yes || echo no)"
if command -v filefrag >/dev/null; then
  say "--- filefrag src (first extents) ---"
  filefrag -v "$PROBE/src/big.bin" 2>&1 | sed -n '1,6p' | tee -a "$LOGF"
  say "--- filefrag copy (first extents) ---"
  filefrag -v "$PROBE/copy.bin" 2>&1 | sed -n '1,6p' | tee -a "$LOGF"
fi
# modify the original; the reflink copy must stay byte-identical
python3 - "$PROBE/src/big.bin" <<'PY' | tee -a "$LOGF"
import os, sys
p = sys.argv[1]
with open(p, "r+b") as f:
    f.seek(0); f.write(b"MODIFIED")
    f.flush(); os.fsync(f.fileno())
print("E4 original modified")
PY
H_COPY_AFTER=$(sha256sum "$PROBE/copy.bin" | cut -d' ' -f1)
say "copy unchanged after original modified: $([ "$H_COPY_BEFORE" = "$H_COPY_AFTER" ] && echo yes || echo NO)"
# modify the copy; the original must not change further
python3 - "$PROBE/copy.bin" <<'PY' | tee -a "$LOGF"
import os, sys
p = sys.argv[1]
with open(p, "r+b") as f:
    f.seek(4096); f.write(b"COPYMOD")
    f.flush(); os.fsync(f.fileno())
print("E4 copy modified")
PY
H_SRC_AFTER=$(sha256sum "$PROBE/src/big.bin" | cut -d' ' -f1)
H_SRC_EXPECT=$(python3 -c "
import hashlib,sys
p='$PROBE/src/big.bin'
d=bytearray(open(p,'rb').read())
print(hashlib.sha256(bytes(d)).hexdigest())
")
say "original stable after copy modified: $([ "$H_SRC_AFTER" = "$H_SRC_EXPECT" ] && echo yes || echo NO)"

# ---------- E5: send -> receive -> independent btrfs -> RO enforced ----------
say ""
say "== E5: send/receive into independent loop btrfs =="
btrfs subvolume snapshot -r "$PROBE/src" "$PROBE/snapshot-ro" >/dev/null 2>&1; say "snapshot rc=$?"
SNAPID=$(btrfs subvolume show "$PROBE/snapshot-ro" 2>/dev/null | awk '/Subvolume ID:/{print $3}')
say "snapid=$SNAPID prop=$(btrfs property get -ts "$PROBE/snapshot-ro" ro 2>&1)"
btrfs send "$PROBE/snapshot-ro" > "$STREAM" 2>>"$LOGF"; say "send rc=$? bytes=$(stat -c %s "$STREAM" 2>/dev/null) sha=$(sha256sum "$STREAM" | cut -d' ' -f1)"

truncate -s 128M "$IMG"; mkfs.btrfs -f -q "$IMG" >>"$LOGF" 2>&1; say "mkfs rc=$?"
mkdir -p "$MNT"; mount -o loop "$IMG" "$MNT" >>"$LOGF" 2>&1; say "mount rc=$? opts=$(findmnt -no OPTIONS "$MNT")"
btrfs receive "$MNT" < "$STREAM" >>"$LOGF" 2>&1; say "receive rc=$?"
RECV="$MNT/snapshot-ro"
if [ -d "$RECV" ]; then
  say "recv payload sha=$(sha256sum "$RECV/big.bin" | cut -d' ' -f1) (source-before-mod is not comparable; content check below)"
  btrfs property set -ts "$RECV" ro true >>"$LOGF" 2>&1; say "set received ro rc=$? prop=$(btrfs property get -ts "$RECV" ro 2>&1)"
  say "--- received subvol on non-trimacl mount: write test (expect EROFS) ---"
  python3 /tmp/fnm-writecheck.py "$RECV/e5-recv-ro-write.txt" | tee -a "$LOGF"
  # remount the loop image read-only and test again
  umount "$MNT" >>"$LOGF" 2>&1
  mount -o loop,ro "$IMG" "$MNT" >>"$LOGF" 2>&1; say "remount ro rc=$? opts=$(findmnt -no OPTIONS "$MNT")"
  python3 /tmp/fnm-writecheck.py "$RECV/e5-recv-ro-write2.txt" | tee -a "$LOGF"
  umount "$MNT" >>"$LOGF" 2>&1
else
  say "received subvolume MISSING; entries=$(ls -A "$MNT" | tr '\n' ' ')"
  umount "$MNT" >>"$LOGF" 2>&1
fi

# compare received content against the *snapshot* content (after E4 modified src, snapshot was taken later)
say ""
say "== E5 content parity =="
mount -o loop,ro "$IMG" "$MNT" >>"$LOGF" 2>&1
if [ -d "$RECV" ]; then
  A=$(sha256sum "$RECV/big.bin" 2>/dev/null | cut -d' ' -f1)
  B=$(sha256sum "$PROBE/snapshot-ro/big.bin" 2>/dev/null | cut -d' ' -f1)
  say "received=$A snapshot=$B equal=$([ "$A" = "$B" ] && echo yes || echo no)"
fi
umount "$MNT" >>"$LOGF" 2>&1

say ""
say "== END $(date -Is) =="
