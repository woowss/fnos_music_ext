#!/bin/bash
# P1 生产者 → G-X3 校验器 端到端（/tmp only）
set -u
D=/tmp/fnm-p1-e2e
rm -rf "$D"
mkdir -p "$D/lib" "$D/evidence"

python3 - <<'PY'
import wave
with wave.open("/tmp/fnm-p1-e2e/lib/track.wav", "wb") as fh:
    fh.setnchannels(1); fh.setsampwidth(2); fh.setframerate(8000)
    fh.writeframes(b"\x00\x00" * 800)
open("/tmp/fnm-p1-e2e/lib/track.lrc", "w", encoding="utf-8").write("第一行\n第二行\n")
PY

cd /tmp/fnm212 || exit 9
export PYTHONPATH=/tmp/fnm212:/tmp/fnm212/proxy
/tmp/fnm212-venv/bin/python - <<'PY'
import importlib, importlib.util, json
from pathlib import Path

spec = importlib.util.spec_from_file_location("observer", "scripts/observe_delivered_evidence.py")
observer = importlib.util.module_from_spec(spec); spec.loader.exec_module(observer)
capture = importlib.import_module("proxy.acceptance_capture")

ts = "2026-11-07T00:00:00Z"
observed = observer.observe(
    file_path=Path("/tmp/fnm-p1-e2e/lib/track.wav"),
    evidence_dir=Path("/tmp/fnm-p1-e2e/evidence"),
    target_dir=Path("/tmp/fnm-p1-e2e/lib"),
    captured_at=ts,
)
source = capture.build_source_evidence(
    provider_payload={"data": {"level": "lossless", "type": "flac", "size": 11,
                               "md5": "a" * 32, "url": "https://cdn.example/secret?token=SECRET"}},
    captured_at=ts,
    tier_requested="lossless",
    fallback=False,
    info_payload={"songs": [{"name": "验收曲目", "ar": [{"name": "歌手"}], "al": {"name": "专辑"}}]},
    cover_status="not_attempted",
    lyric_status="published",
    api_size=11, api_md5="a" * 32, downloaded_size=11, downloaded_md5="a" * 32,
)
position = capture.build_position_uncaptured(position=1, captured_at=ts)
position["state"] = "published"
position["source_evidence"] = source
position["delivered_evidence"] = observed["delivered_evidence"]
position["record_sha256"] = capture.recompute_record_sha256(position)
document = capture.build_evidence_set(
    environment=capture.build_environment(
        device_ref="kotomi-test", fnos_version="1.2.0903", kernel="6.18.18.c1168-trim",
        fstype="ext4", mount_options_digest="0" * 64,
        library_ref="tmp-copy-tree", staging_ref="tmp-staging",
        deployed_version="2.12.0-8", provenance_status="unknown"),
    job=capture.build_job(job_id="job-p1-e2e", manifest_sha256="1" * 64, positions_total=1),
    positions=[position],
    created_at=ts,
)
Path("/tmp/fnm-p1-e2e/evidence-set.json").write_text(
    json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
leaks = capture.find_sensitive_leaks(document)
print("built evidence set; sensitive leaks:", leaks)
print("overall_verdict:", document["summary"]["overall_verdict"])
PY

echo "=== G-X3 CLI on produced evidence set ==="
/tmp/fnm212-venv/bin/python scripts/verify_acceptance_evidence_semantics.py \
    /tmp/fnm-p1-e2e/evidence-set.json > /tmp/fnm-p1-e2e/gx3.json 2>/tmp/fnm-p1-e2e/gx3.err
echo "rc=$?"
python3 - <<'PY'
import json
result = json.load(open("/tmp/fnm-p1-e2e/gx3.json"))
print("result:", result["result"], "fail_closed:", result["fail_closed"], "exit_code:", result["exit_code"])
print("counts:", result["counts"])
print("violations:", json.dumps(result["violations"], ensure_ascii=False)[:300])
unknowns = result["unknowns"]
print("unknowns count:", len(unknowns))
for item in unknowns[:5]:
    print("  unknown:", item["check"], item["reason"])
PY

echo "=== leaks scan in raw evidence files ==="
grep -rl "token=SECRET\|cdn.example" /tmp/fnm-p1-e2e 2>/dev/null || echo "no leaks found"
