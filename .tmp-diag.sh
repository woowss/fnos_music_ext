#!/bin/bash
# read-only diagnostics for app center install attempt
echo "=== install errors 18:45-18:52 ==="
journalctl -u trim_app_center --since "2026-11-07 18:45:25" --until "2026-11-07 18:52:00" --no-pager 2>/dev/null \
  | grep -viE "GIN|SELECT|UPDATE|INSERT" | tail -25

echo "=== app center log files ==="
ls -lt /var/log/trim_app_center* /usr/trim/var/log/*app* 2>/dev/null | head -5

echo "=== front-end bundle hints ==="
JS=/usr/trim/www/assets/appCenter-D4hearCD.js
ls -l "$JS" 2>/dev/null
grep -o "install/task[^\"']\{0,80\}" "$JS" 2>/dev/null | head -5
grep -o "\"/rpc/v1/[a-z/-]*\"" "$JS" 2>/dev/null | sort -u | head -40

echo "=== any install task record ==="
find /usr/trim/var /var/trim -maxdepth 4 -iname "*install*" -newermt "2026-11-07 18:40" 2>/dev/null | head -10
