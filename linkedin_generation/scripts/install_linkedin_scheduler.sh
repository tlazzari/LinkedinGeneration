#!/usr/bin/env bash
set -euo pipefail

if [[ "${OSTYPE}" != darwin* ]]; then
  echo "This installer currently supports macOS (launchd) only." >&2
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODULE_ROOT="${PROJECT_ROOT}/linkedin_generation"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found. Set PYTHON_BIN or install Python 3." >&2
  exit 1
fi

PLIST_NAME="com.tntmotion.linkedin.scheduler"
PLIST_PATH="${HOME}/Library/LaunchAgents/${PLIST_NAME}.plist"
LOG_DIR="${PROJECT_ROOT}/logs"
mkdir -p "${LOG_DIR}"

LLM_API_KEY_VALUE="${LLM_API_KEY:-}"
OPENAI_API_KEY_VALUE="${OPENAI_API_KEY:-}"
LINKEDIN_ACCESS_TOKEN_VALUE="${LINKEDIN_ACCESS_TOKEN:-}"
LINKEDIN_OWNER_URN_VALUE="${LINKEDIN_OWNER_URN:-}"

cat >"${PLIST_PATH}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${PLIST_NAME}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${PYTHON_BIN}</string>
        <string>${MODULE_ROOT}/linkedin_post_scheduler.py</string>
        <string>--publish</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${PROJECT_ROOT}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>LLM_API_KEY</key>
        <string>${LLM_API_KEY_VALUE}</string>
        <key>OPENAI_API_KEY</key>
        <string>${OPENAI_API_KEY_VALUE}</string>
        <key>LINKEDIN_ACCESS_TOKEN</key>
        <string>${LINKEDIN_ACCESS_TOKEN_VALUE}</string>
        <key>LINKEDIN_OWNER_URN</key>
        <string>${LINKEDIN_OWNER_URN_VALUE}</string>
    </dict>
    <key>StandardOutPath</key>
    <string>${LOG_DIR}/linkedin_scheduler.out.log</string>
    <key>StandardErrorPath</key>
    <string>${LOG_DIR}/linkedin_scheduler.err.log</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
</dict>
</plist>
PLIST

launchctl unload "${PLIST_PATH}" >/dev/null 2>&1 || true
launchctl load "${PLIST_PATH}"
launchctl start "${PLIST_NAME}" || true

echo "Installed launchd service ${PLIST_NAME}." >&2
echo "Use 'launchctl list | grep ${PLIST_NAME}' to confirm status." >&2
