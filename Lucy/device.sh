#!/usr/bin/env bash
# Builds and installs the preview app on the connected iPhone, where there is a
# real microphone. The simulator borrows the Mac's, which proves nothing about
# whether offline dictation is installed on the phone.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DERIVED="${DIR}/.build-device"

tmp="$(mktemp -t dc)"
xcrun devicectl list devices --json-output "${tmp}" >/dev/null 2>&1
read -r DEVICE UDID <<<"$(python3 -c "
import json,sys
for d in json.load(open('${tmp}'))['result']['devices']:
    if d['connectionProperties']['pairingState']=='paired':
        print(d['identifier'], d['hardwareProperties']['udid']); break
")"
[ -n "${UDID:-}" ] || { echo "no paired device" >&2; exit 1; }

(cd "${DIR}" && xcodegen generate >/dev/null)
xcodebuild -project "${DIR}/Lucy.xcodeproj" -scheme Lucy \
    -destination "platform=iOS,id=${UDID}" -configuration Debug \
    -derivedDataPath "${DERIVED}" -allowProvisioningUpdates build >/dev/null
xcrun devicectl device install app --device "${DEVICE}" \
    "${DERIVED}/Build/Products/Debug-iphoneos/Lucy.app"
echo "Installed. Launch 'Lucy' on the phone."
