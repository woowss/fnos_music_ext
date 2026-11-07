#!/bin/bash
# Single-shot orchestration for the /vol1 Btrfs evidence probes.
# Everything (setup -> scenarios -> cleanup) runs inside one SSH invocation so
# no long-lived tree is left behind for another concurrent operator to remove.
set -u
CODE=/tmp/fnmusic-vol1-probe/code
L=/tmp/fnmusic-vol1-probe/logs
P=/vol1/1000/fnmusic-btrfs-probe-20261107
mkdir -p "$L"
echo "RUN_START $(date -Is)" > /tmp/fnmusic-vol1-probe/run-meta.txt
echo "probe-root-exists-before=$( [ -e "$P" ] && echo yes || echo no )" >> /tmp/fnmusic-vol1-probe/run-meta.txt

run_phase () {
  local name="$1"; shift
  local rc=0
  "$@" > "$L/$name.json" 2>"$L/$name.err"
  rc=$?
  echo "$name rc=$rc" >> /tmp/fnmusic-vol1-probe/run-meta.txt
  echo "### $name rc=$rc"
  return 0
}

run_phase 10-setup       python3 -B "$CODE/scripts/anonymous_staging_btrfs_probe.py" --phase setup --code-root "$CODE"
run_phase 11-root        python3 -B "$CODE/scripts/anonymous_staging_btrfs_probe.py" --phase root  --code-root "$CODE"
run_phase 12-user        runuser -u kurumi -- python3 -B "$CODE/scripts/anonymous_staging_btrfs_probe.py" --phase user --code-root "$CODE"
run_phase 13-btrfs       python3 -B "$CODE/scripts/btrfs_subvol_state_probe.py" --phase btrfs --code-root "$CODE"
run_phase 14-btrfs-user  runuser -u kurumi -- python3 -B "$CODE/scripts/btrfs_subvol_state_probe.py" --phase user --code-root "$CODE"
run_phase 15-btrfs-clean python3 -B "$CODE/scripts/btrfs_subvol_state_probe.py" --phase cleanup --code-root "$CODE"
run_phase 16-clean       python3 -B "$CODE/scripts/anonymous_staging_btrfs_probe.py" --phase cleanup --code-root "$CODE"

echo "RUN_END $(date -Is)" >> /tmp/fnmusic-vol1-probe/run-meta.txt
echo "probe-root-exists-after=$( [ -e "$P" ] && echo yes || echo no )" >> /tmp/fnmusic-vol1-probe/run-meta.txt
echo "== run-meta =="
cat /tmp/fnmusic-vol1-probe/run-meta.txt
echo "== residual =="
ls -ld "$P" 2>&1 || true
btrfs subvolume list /vol1 2>/dev/null | grep -E "fnmusic-btrfs-probe" || echo "no-probe-subvolumes"
