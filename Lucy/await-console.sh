#!/usr/bin/env bash
# Retries the device launch until the phone is unlocked, then captures the
# LUCY: diagnostic lines. Exists so a locked screen costs a retry, not a
# round trip to Marcus.
set -uo pipefail
DEV=8C4805DF-BE7A-51FA-94BF-658CD51E704A
LOG="${1:-/tmp/lucy-console.log}"
for i in $(seq 1 60); do
    xcrun devicectl device process launch --device "$DEV" --console --terminate-existing \
        ai.kaymo.Lucy.dev --screen voice --soft > "$LOG" 2>&1 &
    PID=$!
    sleep 15
    kill $PID 2>/dev/null
    if grep -q "LUCY:" "$LOG"; then
        echo "=== captured on attempt $i ==="
        grep "LUCY:" "$LOG"
        exit 0
    fi
    echo "attempt $i: locked or no output, retrying"
    sleep 15
done
echo "gave up" >&2; exit 1
