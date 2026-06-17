#!/usr/bin/env bash
# Daily Seta Capital LinkedIn post: cron fires daily, --daily flag enforces Tue/Thu + holiday logic.
# Skip days are handled internally by HolidayAwareScheduler.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
seo_dir="${SEO_PROJECT_ROOT:-/opt/seo}"
commonlib_dir="${COMMONLIB_ROOT:-/opt/commonlib}"
cd "$project_dir"
LOG_FILE="$project_dir/logs/seta.log"
mkdir -p "$(dirname "$LOG_FILE")"
PYTHON_BIN=${PYTHON_BIN:-"/opt/venv/bin/python"}
[ -x "$PYTHON_BIN" ] || { echo "$(date -Is) ERROR python not found at $PYTHON_BIN" >>"$LOG_FILE"; exit 1; }
[ -f "$project_dir/.env" ] && { set -a; source "$project_dir/.env"; set +a; }
PYTHONPATH="$project_dir:$seo_dir:$commonlib_dir" "$PYTHON_BIN" linkedin_generation/seta_post_scheduler.py --daily "$@" >>"$LOG_FILE" 2>&1
touch "$project_dir/logs/seta_linkedin_daily.log"
