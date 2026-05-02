#!/usr/bin/env bash
# Daily TNT Bearings LinkedIn post.
# Reads campaign yaml, picks pillar, generates post, publishes if --publish.
set -euo pipefail
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
seo_dir="${SEO_PROJECT_ROOT:-/home/d1yjs0joned1/GenerativeSEOProject}"
commonlib_dir="${COMMONLIB_ROOT:-/home/d1yjs0joned1/CommonLib}"
cd "$project_dir"
LOG_FILE="$project_dir/logs/tnt_linkedin_daily.log"
mkdir -p "$(dirname "$LOG_FILE")"
PYTHON_BIN=${PYTHON_BIN:-"$HOME/miniconda/envs/generative-seo/bin/python"}
[ -x "$PYTHON_BIN" ] || { echo "$(date -Is) ERROR python not found at $PYTHON_BIN" >>"$LOG_FILE"; exit 1; }
# Load .env from this project (falls back to SEO project for shared keys)
[ -f "$project_dir/.env" ] && { set -a; source "$project_dir/.env"; set +a; }
PYTHONPATH="$project_dir:$seo_dir:$commonlib_dir" "$PYTHON_BIN" linkedin_generation/linkedin_post_scheduler.py --daily "$@" >>"$LOG_FILE" 2>&1
