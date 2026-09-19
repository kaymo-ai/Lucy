#!/usr/bin/env bash
# Builds llama.xcframework (device + simulator, Metal + mtmd) from the pinned submodule.
# The xcframework is a build artifact and is NOT committed; run this after a fresh clone.
set -euo pipefail

VENDOR_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_DIR="${VENDOR_DIR}/llama.cpp"
EXPECTED_SHA="$(cat "${VENDOR_DIR}/llama.cpp.pinned-sha")"
ACTUAL_SHA="$(git -C "${SRC_DIR}" rev-parse HEAD)"

if [ "${EXPECTED_SHA}" != "${ACTUAL_SHA}" ]; then
  echo "ERROR: llama.cpp submodule is at ${ACTUAL_SHA}, expected ${EXPECTED_SHA}" >&2
  echo "Run: git submodule update --init --recursive" >&2
  exit 1
fi

cd "${SRC_DIR}"
./build-xcframework.sh

rm -rf "${VENDOR_DIR}/llama.xcframework"
cp -R "${SRC_DIR}/build-apple/llama.xcframework" "${VENDOR_DIR}/llama.xcframework"

echo "Built ${VENDOR_DIR}/llama.xcframework from ${ACTUAL_SHA}"
