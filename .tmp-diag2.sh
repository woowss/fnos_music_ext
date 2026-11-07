#!/bin/bash
# read-only: extract install attempt outcome from app center logs
echo "=== info.log around install task (18:44-18:50) ==="
grep -aE "2026/11/07 18:4[4-9]|2026/11/07 18:50" /var/log/trim_app_center/info.log 2>/dev/null \
  | grep -aiE "fnmusic|install|fpk|task|fail|error" | head -40

echo
echo "=== error.log tail around that window ==="
grep -aE "2026/11/07 18:4[4-9]" /var/log/trim_app_center/error.log 2>/dev/null | tail -20
