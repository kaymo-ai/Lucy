#!/usr/bin/env bash
# Renders a Lucy PT screen in the simulator and writes a PNG.
#
#   ./render.sh                          home, day face
#   ./render.sh home night
#   SCREEN=entity ENTITY=Lucy ./render.sh
#   SCREEN=entity EXPAND=1 ./render.sh   entity record with evidence open
#   SCREEN=ask ./render.sh
#
# Env: SCREEN=home|ask|entity  ENTITY=<name>  EXPAND=1  SIM="iPhone 17 Pro"
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCREEN="${SCREEN:-${1:-home}}"
FACE="${2:-dark}"
ENTITY="${ENTITY:-Doris}"
SIM="${SIM:-iPhone 17 Pro}"
# Renders build Debug, and Debug is the .dev identifier -- see project.yml.
BUNDLE_ID="ai.kaymo.Lucy.dev"
DERIVED="${DIR}/.build"
OUT="${OUT:-${DIR}/render-${SCREEN}-${FACE}.png}"

(cd "${DIR}" && xcodegen generate >/dev/null)

xcodebuild -project "${DIR}/Lucy.xcodeproj" \
    -scheme Lucy \
    -sdk iphonesimulator \
    -destination "platform=iOS Simulator,name=${SIM}" \
    -derivedDataPath "${DERIVED}" \
    -configuration Debug build CODE_SIGNING_ALLOWED=NO >/dev/null

APP="${DERIVED}/Build/Products/Debug-iphonesimulator/Lucy.app"

xcrun simctl boot "${SIM}" 2>/dev/null || true
xcrun simctl bootstatus "${SIM}" -b >/dev/null 2>&1 || true
# Show the window. simctl boots the device without ever opening Simulator.app,
# so every render happened invisibly -- fine for a PNG, useless for watching a
# change land. HEADLESS=1 puts it back.
[ -z "${HEADLESS:-}" ] && open -a Simulator 2>/dev/null || true
xcrun simctl install "${SIM}" "${APP}"
# Pre-grant, or every render is a screenshot of a permission dialog.
for svc in microphone speech-recognition camera photos; do
    xcrun simctl privacy "${SIM}" grant "$svc" "${BUNDLE_ID}" >/dev/null 2>&1 || true
done
xcrun simctl terminate "${SIM}" "${BUNDLE_ID}" 2>/dev/null || true

ARGS=(--no-prompt --screen "${SCREEN}" --entity "${ENTITY}")
[ -n "${QUERY:-}" ] && ARGS+=(--query "${QUERY}")
[ -n "${NOW:-}" ] && ARGS+=(--now "${NOW}")
[ "${FACE}" = "night" ] && ARGS+=(--night)
[ "${FACE}" = "day" ] && ARGS+=(--day)
[ -n "${EXPAND:-}" ] && ARGS+=(--expand)
[ -n "${SOFT:-}" ] && ARGS+=(--soft)
xcrun simctl launch "${SIM}" "${BUNDLE_ID}" "${ARGS[@]}" >/dev/null

# Give SwiftUI a beat to lay out before capturing.
sleep 2
xcrun simctl io "${SIM}" screenshot "${OUT}" >/dev/null 2>&1
echo "${OUT}"
