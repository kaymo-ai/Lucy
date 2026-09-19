#!/usr/bin/env bash
# Archives Lucy, exports a signed IPA, and uploads it to TestFlight.
#
# This is the "send the camp a new build" button. device.sh puts a debug build
# on the one phone that is plugged in; this puts a release build in front of
# everyone in the internal testing group, usually within a few minutes.
#
#   ./release.sh              build, export and upload
#   ./release.sh --export     stop after the IPA, upload nothing
#
# Credentials come from an App Store Connect API key, not a password, so this
# runs unattended and does not trip over two-factor auth:
#
#   ASC_KEY_ID     the key's ID, e.g. 7X2K9ABCDE
#   ASC_ISSUER_ID  the issuer UUID, from the same page
#   and the .p8 itself at ~/.appstoreconnect/private_keys/AuthKey_<ID>.p8
#
# Put those two in Lucy/.env.release (gitignored) and this picks them up.
set -euo pipefail
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="${DIR}/.build-release"
EXPORT="${BUILD}/export"

[ -f "${DIR}/.env.release" ] && source "${DIR}/.env.release"

# The build number has to rise on every upload or App Store Connect refuses it.
# Commit count is monotonic, needs no state file, and ties a build to a commit.
BUILD_NUMBER="$(git -C "${DIR}/.." rev-list --count HEAD)"
echo "==> build ${BUILD_NUMBER} ($(git -C "${DIR}/.." rev-parse --short HEAD))"

# A release build ships whatever is in the database at the moment it is cut.
# Rebuilding it here means a stale enrichment can never quietly go out.
if [ -f "${DIR}/../scripts/build_preview_db.py" ]; then
    echo "==> rebuilding the database"
    (cd "${DIR}/.." && python3 scripts/build_preview_db.py >/dev/null)
fi

# Personal data must not leave this machine. redact() runs inside the build
# script, but the artifact is what ships -- so the artifact is what is checked.
echo "==> checking the database for personal data"
python3 "${DIR}/../scripts/check_shipped_db.py" "${DIR}/../scripts/output/enriched_preview.db"

(cd "${DIR}" && xcodegen generate >/dev/null)

: "${ASC_KEY_ID:?set ASC_KEY_ID (see the header of this script)}"
: "${ASC_ISSUER_ID:?set ASC_ISSUER_ID (see the header of this script)}"
KEY="${ASC_KEY_PATH:-${HOME}/.appstoreconnect/private_keys/AuthKey_${ASC_KEY_ID}.p8}"
[ -f "${KEY}" ] || { echo "no API key at ${KEY}" >&2; exit 1; }

# Authenticating with the API key rather than a signed-in Xcode account is what
# makes this runnable unattended. Without it, exportArchive fails with "No
# Accounts", "No signing certificate iOS Distribution found" and "No profiles
# for ai.kaymo.Lucy" -- all three of which this fixes, because with the key
# xcodebuild will create the certificate and the profile itself.
AUTH=(-authenticationKeyPath "${KEY}"
      -authenticationKeyID "${ASC_KEY_ID}"
      -authenticationKeyIssuerID "${ASC_ISSUER_ID}")

echo "==> archiving"
xcodebuild -project "${DIR}/Lucy.xcodeproj" -scheme Lucy \
    -destination "generic/platform=iOS" -configuration Release \
    -archivePath "${BUILD}/Lucy.xcarchive" \
    -derivedDataPath "${BUILD}" \
    CURRENT_PROJECT_VERSION="${BUILD_NUMBER}" \
    "${AUTH[@]}" -allowProvisioningUpdates archive >/dev/null

# Manual signing, with a profile created once by hand.
#
# Automatic signing cannot work here. It needs either an Apple ID signed into
# Xcode -- xcodebuild reports "No Accounts" whatever the GUI shows -- or an API
# key allowed to mint certificates and profiles, which returns "Cloud signing
# permission error" unless the key is Admin. A downloaded profile sidesteps
# both: the certificate is already in the keychain and the profile names it.
#
# Create it once at developer.apple.com ▸ Certificates, Identifiers & Profiles
# ▸ Profiles ▸ + ▸ App Store Connect ▸ ai.kaymo.Lucy ▸ the Apple Distribution
# certificate ▸ name it exactly PROFILE below ▸ Generate ▸ Download ▸
# double-click. It expires after a year, and this script will say so plainly
# rather than failing deep inside an export.
PROFILE="${ASC_PROFILE:-Lucy App Store}"
if ! ls ~/Library/MobileDevice/Provisioning\ Profiles/*.mobileprovision >/dev/null 2>&1; then
    echo "No provisioning profile installed. Create \"${PROFILE}\" — see the" >&2
    echo "comment above this check in release.sh for the exact steps." >&2
    exit 1
fi

cat > "${BUILD}/ExportOptions.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>method</key><string>app-store-connect</string>
  <key>teamID</key><string>78J6KTETAK</string>
  <key>signingStyle</key><string>manual</string>
  <key>provisioningProfiles</key>
  <dict>
    <key>ai.kaymo.Lucy</key><string>${PROFILE}</string>
  </dict>
  <key>uploadSymbols</key><true/>
  <key>destination</key><string>export</string>
</dict></plist>
PLIST

echo "==> exporting"
rm -rf "${EXPORT}"
xcodebuild -exportArchive -archivePath "${BUILD}/Lucy.xcarchive" \
    -exportOptionsPlist "${BUILD}/ExportOptions.plist" \
    -exportPath "${EXPORT}" "${AUTH[@]}" -allowProvisioningUpdates >/dev/null

IPA="$(find "${EXPORT}" -name '*.ipa' | head -1)"
echo "==> ${IPA}"
[ "${1:-}" = "--export" ] && { echo "Stopping before upload."; exit 0; }

echo "==> uploading to TestFlight"
xcrun altool --upload-app --type ios --file "${IPA}" \
    --apiKey "${ASC_KEY_ID}" --apiIssuer "${ASC_ISSUER_ID}"

echo
echo "Uploaded build ${BUILD_NUMBER}. Processing takes a few minutes; internal"
echo "testers get it as soon as that finishes, with no review."
