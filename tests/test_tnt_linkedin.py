#!/usr/bin/env python3
"""Tests for TNT Bearings LinkedIn — exercises the ACTIVE scheduler (linkedin_post_scheduler.py)."""
import os, sys, subprocess
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_common import TestRun, PROJECT_ROOT, SEO_ROOT, PKG_DIR, PYTHON_BIN, CAMPAIGN_YAML, py_help_ok, load_campaign, py_compile_ok, bash_n_ok

t = TestRun('TNT')

# === Source file presence ===
t.check('linkedin_post_scheduler.py exists', (PKG_DIR / 'linkedin_post_scheduler.py').is_file())
t.check('token_manager.py exists',           (PKG_DIR / 'token_manager.py').is_file())
t.check('social/image_providers.py exists',  (PKG_DIR / 'social' / 'image_providers.py').is_file())
t.check('holiday/scheduler.py exists',       (PKG_DIR / 'holiday' / 'scheduler.py').is_file())

# === Compile / import ===
t.check('compile linkedin_post_scheduler', py_compile_ok('linkedin_post_scheduler.py'))
t.check('compile token_manager',           py_compile_ok('token_manager.py'))

# === CLI ===
t.check('--help exits 0',                  py_help_ok('linkedin_post_scheduler.py'))

# === Required functions present (via grep) ===
src = (PKG_DIR / 'linkedin_post_scheduler.py').read_text()
t.check('daily_runner function present', 'def daily_runner' in src)
t.check('parse_args function present',    'def parse_args' in src)
t.check('--daily flag present',           '"--daily"' in src)
t.check('--publish flag present',         '"--publish"' in src)

# === Campaign yaml — pillars + required fields ===
campaign = load_campaign()
t.check('campaign yaml has tone in defaults',   'defaults' in campaign and 'tone' in campaign['defaults'])
t.check('campaign yaml has content_pillars',            'content_pillars' in campaign and isinstance(campaign['content_pillars'], list))
if 'pillars' in campaign and campaign['pillars']:
    p0 = campaign['content_pillars'][0]
    t.check('pillar has name',     'name' in p0)
    t.check('pillar has prompt',   'prompt' in p0 or 'description' in p0)

# === Bin script ===
bin_script = PROJECT_ROOT / 'bin' / 'run_daily_tnt.sh'
t.check('run_daily_tnt.sh exists',     bin_script.is_file())
t.check('run_daily_tnt.sh executable', os.access(bin_script, os.X_OK))
t.check('run_daily_tnt.sh syntax',     bash_n_ok(bin_script))
t.check('script references --daily',   '--daily' in bin_script.read_text())
t.check('script sets PYTHONPATH',      'PYTHONPATH' in bin_script.read_text())

sys.exit(t.summary())
