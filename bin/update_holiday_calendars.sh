#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir" || exit 1

LOG_FILE="$project_dir/logs/holiday_calendars.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Use the project venv interpreter — the 2025 refresh failed because it ran
# under an old system python that rejected `from __future__ import annotations`.
PY=/opt/venv/bin/python
[ -x "$PY" ] || PY=python3

# Year to generate: default next year (matches the .py default). Override via $1.
YEAR="${1:-$(date -d '+1 year' +%Y)}"

PYTHONPATH="$project_dir" "$PY" \
    linkedin_generation/scripts/update_holiday_calendars.py \
    --year "$YEAR" --output-dir "$project_dir/config/holidays" >>"$LOG_FILE" 2>&1

# Self-heal the yaml year references so holiday matching never rots: after a
# successful generation, point both holiday configs at the freshly written year.
for yaml in config/holiday_campaign.yaml config/seta_holiday_campaign.yaml; do
    [ -f "$yaml" ] || continue
    sed -i -E "s|holidays/(italy\|china)_[0-9]{4}\.json|holidays/\1_${YEAR}.json|g" "$yaml"
done

echo "Holiday calendars refreshed for ${YEAR} and yaml refs updated. See $LOG_FILE for details."
