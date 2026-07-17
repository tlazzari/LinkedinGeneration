# LinkedIn Pipeline — Cron Schedule

**Source of truth = root crontab on Vultr Tokyo** (`crontab -l`, also mirrored in SYSTEM_SNAPSHOT.md on every commit). This file documents the intended schedule next to the code; if it disagrees with the live crontab, the crontab wins — fix whichever is wrong and keep both in sync.

| When (UTC) | Cron line | What |
|---|---|---|
| Daily 07:00 | `0 7 * * * cd /opt/linkedin && . .env && bin/run_daily_tnt.sh --publish >> logs/tnt.log 2>&1` | TNT Motion daily post |
| Daily 06:00 | `0 6 * * * cd /opt/linkedin && . .env && bin/run_daily_seta.sh --publish >> logs/seta.log 2>&1` | Seta Capital — the SCRIPT decides whether to post (day/pillar/holiday logic lives in code, not in the cron day-field) |
| Weekly Sun 05:00 | `0 5 * * 0 bash /opt/linkedin/bin/refresh_seta_token.sh >> /opt/linkedin/logs/seta_token_refresh.log 2>&1` | Seta LinkedIn token refresh (if expiry < 12 days) |
| Yearly Dec 1 03:00 | `0 3 1 12 * cd /opt/linkedin && . .env && bin/update_holiday_calendars.sh >> logs/holiday.log 2>&1` | Refresh holiday JSON calendars (Nager.Date) for next year |

Rules:
- Posting-day logic belongs in the scripts (APScheduler/pillar config), never in the cron day-field.
- Edit crontab only via `/opt/scripts/safe_crontab.sh` (see rules_testing.md in claude-memory).
- Success sentinel: `seta_linkedin_daily.log` touched on success; health check threshold 30h.
