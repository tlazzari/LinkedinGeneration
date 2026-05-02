#!/usr/bin/env python3
"""Tests for Seta Capital LinkedIn — exercises the ACTIVE scheduler (seta_post_scheduler.py)."""
import os, sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_common import TestRun, PROJECT_ROOT, SEO_ROOT, PKG_DIR, PYTHON_BIN, CAMPAIGN_YAML, py_help_ok, load_campaign, py_compile_ok, bash_n_ok

t = TestRun('Seta')

# === Source file presence ===
t.check('seta_post_scheduler.py exists',          (PKG_DIR / 'seta_post_scheduler.py').is_file())
t.check('social/seta_content_generation.py exists', (PKG_DIR / 'social' / 'seta_content_generation.py').is_file())
t.check('social/news_search.py exists',           (PKG_DIR / 'social' / 'news_search.py').is_file())

# === Compile / import ===
t.check('compile seta_post_scheduler', py_compile_ok('seta_post_scheduler.py'))

# === CLI ===
t.check('--help exits 0',              py_help_ok('seta_post_scheduler.py'))

# === Required functions present ===
src = (PKG_DIR / 'seta_post_scheduler.py').read_text()
t.check('main function present',       'def main' in src)
t.check('parse_args function present', 'def parse_args' in src)
t.check('--run-once flag present',     '"--run-once"' in src)
t.check('--publish flag present',      '"--publish"' in src)
# Seta uses news article preview images, so news_search must be wired
t.check('news preview wired in',       'fetch_article_preview_image' in src)

# === Campaign yaml ===
campaign = load_campaign()
t.check('campaign yaml has content_pillars',   'content_pillars' in campaign and isinstance(campaign['content_pillars'], list))

# === Bin script ===
bin_script = PROJECT_ROOT / 'bin' / 'run_daily_seta.sh'
t.check('run_daily_seta.sh exists',    bin_script.is_file())
t.check('run_daily_seta.sh executable', os.access(bin_script, os.X_OK))
t.check('run_daily_seta.sh syntax',    bash_n_ok(bin_script))
t.check('script references --run-once', '--run-once' in bin_script.read_text())
t.check('script sets PYTHONPATH',      'PYTHONPATH' in bin_script.read_text())

sys.exit(t.summary())
