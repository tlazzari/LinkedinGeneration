#!/usr/bin/env bash
# Run every ACTIVATED tenant brand, one after another (2026-09-13).
#
# One cron line drives all tenants: adding a company on the Social page is then
# enough to make it post, which is what "activate" has to mean. A tenant that is
# disabled, has no pillars, or has nowhere to send its post is skipped with a
# reason rather than half-run.
#
# Built-in brands are NOT touched here — TNT and Seta keep their own cron lines
# and their own campaign YAMLs.
set -uo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
commonlib_dir="${COMMONLIB_ROOT:-/opt/commonlib}"
BRAND_DIR="${LINKEDIN_BRAND_DIR:-$project_dir/config/brands}"
PYTHON_BIN=${PYTHON_BIN:-"/opt/venv/bin/python"}
cd "$project_dir"
[ -f "$project_dir/.env" ] && { set -a; source "$project_dir/.env"; set +a; }

log() { echo "[$(date -u +%FT%TZ)] $*"; }

shopt -s nullglob
configs=("$BRAND_DIR"/*.json)
if [ ${#configs[@]} -eq 0 ]; then
  log "no tenant brands configured — nothing to do"
  exit 0
fi

ran=0 skipped=0
for cfg in "${configs[@]}"; do
  key="$(basename "$cfg" .json)"
  # Built-ins are run by their own crons; a config file for them only overrides voice.
  case "$key" in seta|tnt) continue;; esac

  reason="$(PYTHONPATH="$project_dir:$commonlib_dir" "$PYTHON_BIN" - "$key" <<'PY'
import json, sys, os
key = sys.argv[1]
d = os.getenv("LINKEDIN_BRAND_DIR", "/opt/linkedin/config/brands")
try:
    c = json.load(open(f"{d}/{key}.json"))
except Exception as e:
    print(f"unreadable config ({e})"); sys.exit(0)
if not c.get("enabled", True):
    print("not active"); sys.exit(0)
if not (c.get("pillars") or []):
    print("no pillars defined yet"); sys.exit(0)
mode = str(c.get("delivery") or "linkedin").lower()
if mode == "email":
    if "@" not in str(c.get("notify_email") or ""):
        print("email delivery with no address"); sys.exit(0)
else:
    stem = "".join(ch if ch.isalnum() else "_" for ch in key).upper().strip("_")
    if not (os.getenv(f"{stem}_LINKEDIN_OWNER_URN") and os.getenv(f"{stem}_LINKEDIN_ACCESS_TOKEN")):
        print("no LinkedIn credentials and delivery is not set to email"); sys.exit(0)
print("")
PY
)"

  if [ -n "$reason" ]; then
    log "skip $key — $reason"
    skipped=$((skipped+1))
    continue
  fi

  log "running $key"
  bash "$project_dir/bin/run_daily_brand.sh" "$key" || log "  $key FAILED (see logs/${key}.log)"
  ran=$((ran+1))
done

log "tenant run complete: $ran run, $skipped skipped"
