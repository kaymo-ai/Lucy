#!/usr/bin/env bash
# Drives the MemorySpike run on a physical device end to end.
#
#   ./run-spike.sh build     build + install the app on the device
#   ./run-spike.sh push      copy the models and test image into Documents (~4 GB, slow)
#   ./run-spike.sh check     confirm the increased-memory-limit entitlement is live
#   ./run-spike.sh run [ctx] launch an auto-run at that context size (default 4096)
#   ./run-spike.sh log       pull Documents/spike-log.txt back off the device
#   ./run-spike.sh all       build, check, push
#
# Fully automated -- `run` launches with --autorun so no tap is needed, and each
# context size gets a fresh process. The device must be paired and trusted, with
# Developer Mode enabled.
set -euo pipefail

SPIKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJ_DIR="${SPIKE_DIR}/MemorySpike"
MODELS_DIR="${SPIKE_DIR}/models"
BUNDLE_ID="ai.kaymo.spike.MemorySpike"
DERIVED="${SPIKE_DIR}/.build"

# Two different identifiers are needed and they are NOT interchangeable:
#   devicectl  wants the CoreDevice UUID   (e.g. 8C4805DF-...)
#   xcodebuild wants the hardware UDID     (e.g. 00008140-...)
# Passing one where the other belongs fails with a misleading
# "no available devices matched the request".
device_ids() {
    # `devicectl list devices --quiet` prints nothing, and the plain table's
    # column layout is not stable enough to awk. Go through the JSON.
    local tmp
    tmp="$(mktemp -t devicectl)"
    xcrun devicectl list devices --json-output "${tmp}" >/dev/null 2>&1 || true
    python3 - "${tmp}" <<'PY'
import json, sys
try:
    devices = json.load(open(sys.argv[1]))["result"]["devices"]
except Exception:
    sys.exit(0)
for d in devices:
    if d["connectionProperties"]["pairingState"] == "paired":
        print(d["identifier"], d["hardwareProperties"]["udid"])
        break
PY
    rm -f "${tmp}"
}

read -r DEVICE UDID <<<"$(device_ids)"
DEVICE="${DEVICE:-}"
UDID="${UDID:-}"
if [ -z "${DEVICE}" ]; then
    echo "ERROR: no device found. Plug the iPhone in, unlock it, trust this Mac," >&2
    echo "       and enable Settings > Privacy & Security > Developer Mode." >&2
    exit 1
fi

APP="${DERIVED}/Build/Products/Debug-iphoneos/MemorySpike.app"

cmd_build() {
    # The .xcodeproj is a generated artifact and is not committed.
    (cd "${PROJ_DIR}" && xcodegen generate)
    xcodebuild -project "${PROJ_DIR}/MemorySpike.xcodeproj" \
        -scheme MemorySpike \
        -destination "platform=iOS,id=${UDID}" \
        -configuration Debug \
        -derivedDataPath "${DERIVED}" \
        -allowProvisioningUpdates \
        build
    xcrun devicectl device install app --device "${DEVICE}" "${APP}"
}

cmd_check() {
    echo "--- entitlements baked into the signed app ---"
    ENTS="$(codesign -d --entitlements - --xml "${APP}" 2>/dev/null | plutil -p -)"
    echo "${ENTS}"
    echo
    # Hard failure, not a warning. Without this entitlement the app runs under
    # the ordinary jetsam limit and every number below is measuring the wrong
    # thing -- and a free personal team cannot grant it at all. Better to stop
    # here than to push 4 GB and record a bogus No-go.
    # plutil renders booleans as `=> true`, not `=> 1` as the plan's snippet implies.
    if echo "${ENTS}" | grep -qE '"com\.apple\.developer\.kernel\.increased-memory-limit" => (1|true)'; then
        echo "OK: increased-memory-limit is active."
    else
        echo "ERROR: com.apple.developer.kernel.increased-memory-limit is NOT active." >&2
        echo "       Every number this spike produces would be wrong. Stopping." >&2
        echo "       Likely cause: the signing team is a free personal team, which" >&2
        echo "       cannot enable this capability." >&2
        exit 1
    fi
}

push_one() {
    echo "pushing $1 ..."
    xcrun devicectl device copy to --device "${DEVICE}" \
        --domain-type appDataContainer \
        --domain-identifier "${BUNDLE_ID}" \
        --source "${MODELS_DIR}/$1" \
        --destination "Documents/$1"
}

cmd_push() {
    push_one gemma-4-E2B-it-Q4_K_M.gguf
    push_one mmproj-F16.gguf
    push_one test.jpg
}

cmd_run() {
    CTX="${2:-4096}"
    EXTRA="${3:-}"     # pass --nommap for the pessimistic (dirty-memory) bound
    echo "=== launching run at n_ctx=${CTX} ${EXTRA} ==="
    # --console blocks until the process exits and streams its stdout, so a
    # jetsam kill shows up as the stream ending rather than as a silent pass.
    xcrun devicectl device process launch --device "${DEVICE}" \
        --console --terminate-existing \
        "${BUNDLE_ID}" --autorun "${CTX}" ${EXTRA} || true
}

cmd_log() {
    OUT="${SPIKE_DIR}/spike-log.txt"
    xcrun devicectl device copy from --device "${DEVICE}" \
        --domain-type appDataContainer \
        --domain-identifier "${BUNDLE_ID}" \
        --source Documents/spike-log.txt \
        --destination "${OUT}"
    echo "--- ${OUT} ---"
    cat "${OUT}"
}

case "${1:-all}" in
    build) cmd_build ;;
    push)  cmd_push ;;
    check) cmd_check ;;
    run)   cmd_run "$@" ;;
    log)   cmd_log ;;
    all)   cmd_build; cmd_check; cmd_push ;;
    *) echo "usage: $0 {build|push|check|run [n_ctx]|log|all}" >&2; exit 1 ;;
esac
