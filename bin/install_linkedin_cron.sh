#!/usr/bin/env bash
# Installs both daily LinkedIn cron entries (Seta 08:00 + TNT 09:00 Rome time).
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
seta_entry="0 8 * * * TZ=Europe/Rome ${project_dir}/bin/run_daily_seta.sh --publish"
tnt_entry="0 9 * * * TZ=Europe/Rome ${project_dir}/bin/run_daily_tnt.sh --publish"
holiday_entry="0 3 1 12 * TZ=Europe/Rome ${project_dir}/bin/update_holiday_calendars.sh"
tmp_file="$(mktemp)"
trap "rm -f $tmp_file" EXIT
if crontab -l >/dev/null 2>&1; then
    # remove any prior LinkedIn entries (old or new path) so we never double-install
    crontab -l | grep -vE "run_daily_linkedin|run_daily_tnt|run_daily_seta|update_holiday_calendars" >"$tmp_file"
else
    : >"$tmp_file"
fi
{
    printf "%s\n" "# === LinkedIn daily posts (managed by LinkedinGeneration/bin/install_linkedin_cron.sh) ==="
    printf "%s\n" "$seta_entry"
    printf "%s\n" "$tnt_entry"
    printf "%s\n" "$holiday_entry"
} >>"$tmp_file"
crontab "$tmp_file"
echo "Installed cron entries:"
echo "  $seta_entry"
echo "  $tnt_entry"
echo "  $holiday_entry"
echo
echo "Current crontab:"
crontab -l
