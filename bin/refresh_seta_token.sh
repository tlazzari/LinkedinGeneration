#!/usr/bin/env bash
# Auto-refresh Seta Capital LinkedIn access token before it expires.
# Reads SETA_LINKEDIN_TOKEN_EXPIRY from .env; refreshes if within 12 days.
set -euo pipefail

ENV_FILE="/opt/linkedin/.env"
LOG="/opt/linkedin/logs/seta_token_refresh.log"
DAYS_BEFORE=12

log() { echo "$(date -Is) $*" | tee -a "$LOG"; }

# Load env
set -a; source "$ENV_FILE"; set +a

# Check expiry
if [ -z "${SETA_LINKEDIN_TOKEN_EXPIRY:-}" ]; then
  log "ERROR: SETA_LINKEDIN_TOKEN_EXPIRY not set in .env"
  exit 1
fi

TODAY=$(date +%Y-%m-%d)
EXPIRY="$SETA_LINKEDIN_TOKEN_EXPIRY"
DAYS_LEFT=$(( ( $(date -d "$EXPIRY" +%s) - $(date -d "$TODAY" +%s) ) / 86400 ))

log "Token expires $EXPIRY — $DAYS_LEFT days remaining"

if [ "$DAYS_LEFT" -gt "$DAYS_BEFORE" ]; then
  log "No refresh needed (threshold: $DAYS_BEFORE days)"
  exit 0
fi

log "Refreshing Seta LinkedIn token (expires in $DAYS_LEFT days)..."

RESPONSE=$(curl -s -X POST 'https://www.linkedin.com/oauth/v2/accessToken' \
  --data-urlencode 'grant_type=refresh_token' \
  --data-urlencode "refresh_token=${SETA_LINKEDIN_REFRESH_TOKEN}" \
  --data-urlencode "client_id=${SETA_LINKEDIN_CLIENT_ID}" \
  --data-urlencode "client_secret=${SETA_LINKEDIN_CLIENT_SECRET}")

NEW_TOKEN=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['access_token'])" 2>/dev/null || true)
EXPIRES_IN=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['expires_in'])" 2>/dev/null || true)

if [ -z "$NEW_TOKEN" ]; then
  log "ERROR: Token refresh failed — response: $RESPONSE"
  exit 1
fi

# Calculate new expiry date
NEW_EXPIRY=$(date -d "+$((EXPIRES_IN / 86400)) days" +%Y-%m-%d)

# Update .env
sed -i "s|^SETA_LINKEDIN_ACCESS_TOKEN=.*|SETA_LINKEDIN_ACCESS_TOKEN=${NEW_TOKEN}|" "$ENV_FILE"
sed -i "s|^SETA_LINKEDIN_TOKEN_EXPIRY=.*|SETA_LINKEDIN_TOKEN_EXPIRY=${NEW_EXPIRY}|" "$ENV_FILE"

log "Token refreshed successfully. New expiry: $NEW_EXPIRY"
