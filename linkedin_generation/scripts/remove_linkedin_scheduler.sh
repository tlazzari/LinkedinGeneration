#!/usr/bin/env bash
set -euo pipefail

if [[ "${OSTYPE}" != darwin* ]]; then
  echo "This removal helper currently supports macOS (launchd) only." >&2
  exit 1
fi

PLIST_NAME="com.tntmotion.linkedin.scheduler"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true

if [[ -f "${PLIST_PATH}" ]]; then
  rm "${PLIST_PATH}"
  echo "Removed ${PLIST_PATH}." >&2
else
  echo "No plist found at ${PLIST_PATH}." >&2
fi

echo "LaunchAgent ${PLIST_NAME} unloaded." >&2
