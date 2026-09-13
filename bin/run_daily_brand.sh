#!/usr/bin/env bash
# Daily LinkedIn post for ONE brand — the generic runner (2026-09-13).
#
# Until now there were only run_daily_seta.sh and run_daily_tnt.sh, so a Bolla
# tenant could be configured on the Social page and would then never post: no
# runner existed for it and no cron called one. This is that runner.
#
#   run_daily_brand.sh t10105_eratrend           # honours Tue/Thu + holidays
#   run_daily_brand.sh t10105_eratrend --run-once # post now, ignore the calendar
#
# The brand supplies its own pillars, news topics, output directory, credentials
# and delivery choice. Only the house rules are shared — see
# social/brand_store.INHERITED_RULES.
set -euo pipefail

BRAND="${1:-}"
[ -n "$BRAND" ] || { echo "usage: $(basename "$0") <brand-key> [extra args]" >&2; exit 2; }
shift || true

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
commonlib_dir="${COMMONLIB_ROOT:-/opt/commonlib}"
cd "$project_dir"

LOG_FILE="$project_dir/logs/${BRAND}.log"
mkdir -p "$(dirname "$LOG_FILE")"
PYTHON_BIN=${PYTHON_BIN:-"/opt/venv/bin/python"}
[ -x "$PYTHON_BIN" ] || { echo "$(date -Is) ERROR python not found at $PYTHON_BIN" >>"$LOG_FILE"; exit 1; }
[ -f "$project_dir/.env" ] && { set -a; source "$project_dir/.env"; set +a; }

# --daily unless the caller asked for something else, so the Tue/Thu + holiday
# logic in the scheduler decides whether today is a posting day.
MODE="--daily"
for arg in "$@"; do [ "$arg" = "--run-once" ] && MODE=""; done

PYTHONPATH="$project_dir:$commonlib_dir" "$PYTHON_BIN" \
  linkedin_generation/seta_post_scheduler.py \
  --brand "$BRAND" $MODE --publish "$@" >>"$LOG_FILE" 2>&1

touch "$project_dir/logs/${BRAND}_daily.log"
