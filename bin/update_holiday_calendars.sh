#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir" || exit 1

LOG_FILE="$project_dir/logs/holiday_calendars.log"
mkdir -p "$(dirname "$LOG_FILE")"

if command -v conda >/dev/null 2>&1 && conda env list 2>/dev/null | grep -q "^generative-seo[[:space:]]"; then
    PYTHONPATH="$project_dir" conda run -n generative-seo python linkedin_generation/scripts/update_holiday_calendars.py "$@" >>"$LOG_FILE" 2>&1
else
    PYTHONPATH="$project_dir" python3 linkedin_generation/scripts/update_holiday_calendars.py "$@" >>"$LOG_FILE" 2>&1
fi

echo "Holiday calendars refreshed. See $LOG_FILE for details."
