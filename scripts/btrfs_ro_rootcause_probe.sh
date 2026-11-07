#!/bin/bash
# RO-subvolume write-enforcement root-cause experiment (all writes confined to
# /tmp and one disposable /vol1 probe tree; atomic single run with trap cleanup).
set -u
IMG=/tmp/fnm-ro-loop.img
MNT=/tmp/fnm-ro-loop-mnt
PROBE=/vol1/1000/fnmusic-btrfs-ro-probe-20261107
NSMNT=/tmp/fnm-ro-nsmnt
LOGF=/tmp/fnm-ro-rootcause.log
STREAM1=/tmp/fnm-send1.stream
STREAM2=/tmp/fnm-send2.stream
: > "$LOGF"
say() { echo "$@" | tee -a "$LOGF"; }
run() { "$@" 2>&1 | tee -a "$LOGF"; }

cleanup() {
  set +e
  umount "$MNT" 2>/dev/null
  rm -rf "$MNT" 2>/dev/null
  rm -f "$IMG" 2>/dev/null
  if [ -d "$PROBE" ]; then
    for sv in "$PROBE/recv/snapshot-ro" "$PROBE/recv" "$PROBE/snapshot-ro" "$PROBE/subvol-src"; do
      [ -e "$sv" ] && btrfs subvolume delete "$sv" >/dev/null 2>&1
    done
    rm -rf "$PROBE"
  fi
  rm -rf "$NSMNT" 2>/dev/null
  rm -f "$STREAM1" "$STREAM2" 2>/dev/null
}
trap cleanup EXIT

say "== BEGIN $(date -Is) kernel=$(uname -r) =="
if [ -e "$PROBE" ]; then say "ABORT: probe tree already exists"; exit 1; fi

# ---------- E1: loop btrfs, NO trimacl option ----------
say ""
say "== E1: loop btrfs without trimacl =="
truncate -s 512M "$IMG" 2>>"$LOGF" && say "truncate ok" || say "truncate FAILED"
mkfs.btrfs -f -q "$IMG" >>"$LOGF" 2>&1 && say "mkfs ok" || say "mkfs FAILED"
mkdir -p "$MNT"
if mount -o loop "$IMG" "$MNT" >>"$LOGF" 2>&1; then
  say "mount ok options=$(findmnt -no OPTIONS "$MNT")"
  btrfs subvolume create "$MNT/src" >/dev/null 2>&1
  printf 'payload\n' > "$MNT/src/payload.txt"
  btrfs subvolume snapshot -r "$MNT/src" "$MNT/snap" >/dev/null 2>&1
  say "prop=$(btrfs property get -ts "$MNT/snap" ro 2>&1)"
  python3 /tmp/fnm-writecheck.py "$MNT/snap/e1-write.txt" | tee -a "$LOGF"
  say "entries=$(ls -A "$MNT/snap" | tr '\n' ' ')"
  umount "$MNT" >>"$LOGF" 2>&1 && say "unmounted"
else
  say "E1 mount FAILED"
fi

# ---------- E2: loop btrfs, WITH trimacl option ----------
say ""
say "== E2: loop btrfs with trimacl =="
if mount -o loop,trimacl "$IMG" "$MNT" >>"$LOGF" 2>&1; then
  say "mount ok options=$(findmnt -no OPTIONS "$MNT")"
  say "prop=$(btrfs property get -ts "$MNT/snap" ro 2>&1)"
  python3 /tmp/fnm-writecheck.py "$MNT/snap/e2-write.txt" | tee -a "$LOGF"
  say "entries=$(ls -A "$MNT/snap" | tr '\n' ' ')"
  umount "$MNT" >>"$LOGF" 2>&1 && say "unmounted"
else
  say "E2 mount with trimacl FAILED (option rejected or other)"
fi

# ---------- E3: real /vol1 + RO mount in private namespace + send/receive ----------
say ""
say "== E3: /vol1 disposable tree =="
mkdir -p "$PROBE" && chmod 755 "$PROBE" && say "probe tree created"
btrfs subvolume create "$PROBE/subvol-src" >/dev/null 2>&1
printf 'payload\n' > "$PROBE/subvol-src/payload.txt"
btrfs subvolume snapshot -r "$PROBE/subvol-src" "$PROBE/snapshot-ro" >/dev/null 2>&1
SNAPID=$(btrfs subvolume show "$PROBE/snapshot-ro" 2>/dev/null | awk '/Subvolume ID:/{print $3}')
DEV=$(findmnt -no SOURCE /vol1)
say "snapid=$SNAPID dev=$DEV prop=$(btrfs property get -ts "$PROBE/snapshot-ro" ro 2>&1)"

# E3a: direct write through the normal RW /vol1 path
python3 /tmp/fnm-writecheck.py "$PROBE/snapshot-ro/e3a-direct-write.txt" | tee -a "$LOGF"

# E3b: mount the snapshot read-only without trimacl, inside a private mount namespace
mkdir -p "$NSMNT"
unshare -m bash -c "mount -t btrfs -o ro,subvolid=$SNAPID $DEV $NSMNT 2>&1; echo RO_MOUNT_OPTIONS=\$(findmnt -no OPTIONS $NSMNT 2>&1); python3 /tmp/fnm-writecheck.py $NSMNT/e3b-ro-mount-write.txt; umount $NSMNT 2>&1" 2>&1 | tee -a "$LOGF"

# E3c: btrfs send stream determinism + receive into loop image
say ""
say "== E3c: btrfs send / receive =="
btrfs send "$PROBE/snapshot-ro" > "$STREAM1" 2>>"$LOGF"; say "send1 rc=$? bytes=$(stat -c %s "$STREAM1" 2>/dev/null) sha1=$(sha256sum "$STREAM1" 2>/dev/null | cut -d' ' -f1)"
btrfs send "$PROBE/snapshot-ro" > "$STREAM2" 2>>"$LOGF"; say "send2 bytes=$(stat -c %s "$STREAM2" 2>/dev/null) sha2=$(sha256sum "$STREAM2" 2>/dev/null | cut -d' ' -f1)"
if mount -o loop "$IMG" "$MNT" >>"$LOGF" 2>&1; then
  btrfs receive "$MNT" < "$STREAM1" >>"$LOGF" 2>&1; say "receive rc=$?"
  if [ -f "$MNT/snapshot-ro/payload.txt" ]; then
    say "received payload sha=$(sha256sum "$MNT/snapshot-ro/payload.txt" | cut -d' ' -f1) source sha=$(sha256sum "$PROBE/snapshot-ro/payload.txt" | cut -d' ' -f1)"
  else
    say "received payload MISSING; entries=$(ls -A "$MNT" | tr '\n' ' ')"
  fi
  umount "$MNT" >>"$LOGF" 2>&1
fi

# E3d: reflink (FICLONE) support on /vol1
say ""
say "== E3d: reflink on /vol1 =="
cp --reflink=always "$PROBE/subvol-src/payload.txt" "$PROBE/reflink-copy.txt" 2>>"$LOGF"
say "reflink cp rc=$?"
say "E3d stats: $(stat -c '%n size=%s blocks=%b' "$PROBE/subvol-src/payload.txt" "$PROBE/reflink-copy.txt" 2>&1 | tr '\n' '|')"
command -v filefrag >/dev/null && filefrag -v "$PROBE/reflink-copy.txt" 2>&1 | head -6 | tee -a "$LOGF"

say ""
say "== END $(date -Is) =="
