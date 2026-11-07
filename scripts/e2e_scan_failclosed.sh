#!/bin/bash
# 扫描器候选能力 e2e（/tmp only, unprivileged）
set -u
D=/tmp/fnm-scan-e2e
rm -rf "$D"
mkdir -p "$D/lib"

python3 - <<'PY'
import hashlib, json, os
root = "/tmp/fnm-scan-e2e/lib"
rows = []
for i, name in enumerate(["a.flac", "b.mp3", "c.wav"], 1):
    payload = ("payload-%d" % i).encode() * (100 * i)
    with open(os.path.join(root, name), "wb") as fh:
        fh.write(payload)
    rows.append({
        "position": i,
        "published_sha256": hashlib.sha256(payload).hexdigest(),
        "published_size": len(payload),
    })
with open("/tmp/fnm-scan-e2e/positions.json", "w") as fh:
    json.dump({"positions": rows}, fh)
print("positions:", len(rows))
PY

cd /tmp/fnm212 || exit 9
echo "=== independent-copy ==="
python3 scripts/verify_position_file_evidence.py --root "$D" --evidence-profile independent-copy \
    < "$D/positions.json" > "$D/out.copy.json" 2>"$D/err.copy.txt"
echo "rc=$?"
head -c 500 "$D/out.copy.json"; echo
echo "stderr:"; head -c 300 "$D/err.copy.txt"; echo

echo "=== btrfs-ro-snapshot on ext4 (expect fail-closed) ==="
python3 scripts/verify_position_file_evidence.py --root "$D" --evidence-profile btrfs-ro-snapshot \
    < "$D/positions.json" > "$D/out.ro.json" 2>"$D/err.ro.txt"
echo "rc=$?"
head -c 400 "$D/out.ro.json"; echo
echo "stderr:"; head -c 300 "$D/err.ro.txt"; echo

echo "=== tree unchanged (sha256) ==="
sha256sum "$D"/lib/* | sort
echo "=== evidence files ==="
ls -la "$D"
