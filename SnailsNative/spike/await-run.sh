#!/usr/bin/env bash
# Retries the spike run until the phone is actually reachable and unlocked,
# then pulls the log. Exists so a locked screen costs a retry, not a round trip.
#
#   ./await-run.sh 4096 [max_attempts]
set -uo pipefail

SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CTX="${1:-4096}"
MAX="${2:-60}"

for i in $(seq 1 "${MAX}"); do
    OUT="$("${SPIKE_DIR}/run-spike.sh" run "${CTX}" 2>&1)"
    if echo "${OUT}" | grep -q "Locked\|could not be, unlocked"; then
        echo "attempt ${i}: device locked, retrying in 20s"
        sleep 20
        continue
    fi
    echo "${OUT}"
    echo "=== run returned at attempt ${i} (n_ctx=${CTX}) ==="
    exit 0
done

echo "gave up after ${MAX} attempts — device never unlocked" >&2
exit 1
