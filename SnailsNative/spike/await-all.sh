#!/usr/bin/env bash
# Runs all three spike configurations, retrying each until the phone is
# unlocked. The device auto-locks between commands, and every earlier attempt
# died on that -- silently, because cmd_run ends in `|| true`.
set -uo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

run_one() {
    local ctx="$1" extra="${2:-}"
    for i in $(seq 1 80); do
        OUT="$("${DIR}/run-spike.sh" run "${ctx}" "${extra}" 2>&1)"
        if echo "${OUT}" | grep -q "Locked\|could not be, unlocked"; then
            sleep 20; continue
        fi
        if echo "${OUT}" | grep -q "ERROR"; then
            echo "n_ctx=${ctx} ${extra}: launch error"; echo "${OUT}" | head -5; return 1
        fi
        echo "n_ctx=${ctx} ${extra}: ran on attempt ${i}"
        return 0
    done
    echo "n_ctx=${ctx} ${extra}: gave up, device never unlocked"; return 1
}

run_one 4096
run_one 32768
run_one 4096 --nommap
echo "=== all configurations attempted ==="
"${DIR}/run-spike.sh" log 2>&1 | tail -40
