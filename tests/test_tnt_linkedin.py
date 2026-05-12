#!/usr/bin/env python3
"""Tests for TNT Bearings LinkedIn — exercises the ACTIVE scheduler (linkedin_post_scheduler.py).

IMPORTANT: Several tests here are RULE ENFORCERS. They prevent accidental introduction of
generic corporate imagery, removal of animated GIF support, or non-industrial image prompts.
TNT Motion is an engineering brand — every image must be technical/industrial, never a boardroom.
"""
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_common import (
    TestRun, PROJECT_ROOT, SEO_ROOT, PKG_DIR, PYTHON_BIN,
    CAMPAIGN_YAML, py_help_ok, load_campaign, py_compile_ok, bash_n_ok,
)

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
t.check('--help exits 0', py_help_ok('linkedin_post_scheduler.py'))

# === Required functions present ===
src = (PKG_DIR / 'linkedin_post_scheduler.py').read_text()
t.check('daily_runner function present', 'def daily_runner' in src)
t.check('parse_args function present',   'def parse_args' in src)
t.check('--daily flag present',          '"--daily"' in src)
t.check('--publish flag present',        '"--publish"' in src)

# === Campaign yaml — pillars + required fields ===
campaign = load_campaign()
t.check('campaign yaml has tone in defaults',
        'defaults' in campaign and 'tone' in campaign.get('defaults', {}))
t.check('campaign yaml has content_pillars',
        'content_pillars' in campaign and isinstance(campaign['content_pillars'], list))
pillars = campaign.get('content_pillars', [])
t.check('at least 4 TNT pillars defined', len(pillars) >= 4)
if pillars:
    t.check('first pillar has name', 'name' in pillars[0])

# === RULE: Animated GIF must remain enabled for TNT ===
# TNT images are animated GIFs (6 AI-generated frames), not static photos.
# This is a core part of the brand — do not disable without deliberate decision.
img_provider = campaign.get('image_provider', {})
t.check('RULE: use_animated_gif=true in TNT campaign',
        img_provider.get('use_animated_gif') is True)
t.check('RULE: gif_num_frames >= 4',
        int(img_provider.get('gif_num_frames', 0)) >= 4)
t.check('RULE: AnimatedGIFProvider wired in scheduler',
        'AnimatedGIF' in src or 'animated_gif' in src or 'use_animated_gif' in src)

# === RULE: Image prompts must be industrial/engineering-focused ===
# TNT is an engineering brand. Images must depict bearings, machinery, engineers,
# workshops, or industrial environments. Never generic offices or boardrooms.
INDUSTRIAL_MARKERS = [
    'bearing', 'engineer', 'machinery', 'industrial', 'mechanical',
    'workshop', 'manufacturing', 'motor', 'pump', 'spindle', 'precision',
    'factory', 'lathe', 'cnc', 'compressor', 'turbine', 'rotor',
    'inspection', 'diagnostic', 'grease', 'lubrication', 'metallic',
    'technical', 'instrument',
]
BANNED_CORPORATE_PHRASES = [
    'boardroom', 'executive in suit', 'city skyline', 'glass office',
    'corporate meeting', 'handshake in office',
]
for p in pillars:
    name = p.get('name', 'unknown')
    prompt = p.get('image_prompt', '') or ''
    prompt_lower = prompt.lower()

    t.check(f'RULE: TNT image_prompt for "{name}" is specific (>= 60 chars)',
            len(prompt.strip()) >= 60)

    has_industrial = any(marker in prompt_lower for marker in INDUSTRIAL_MARKERS)
    t.check(f'RULE: TNT image_prompt for "{name}" is industrial/engineering-focused',
            has_industrial)

    for banned in BANNED_CORPORATE_PHRASES:
        t.check(f'RULE: TNT image_prompt for "{name}" not generic corporate ("{banned}")',
                banned not in prompt_lower)

# === RULE: TNT pillars must NOT have use_veo — TNT uses animated GIF, not video ===
veo_pillars = [p for p in pillars if p.get('use_veo')]
t.check('RULE: TNT pillars do not use Veo (TNT uses animated GIF)',
        len(veo_pillars) == 0)

# === Bin script ===
bin_script = PROJECT_ROOT / 'bin' / 'run_daily_tnt.sh'
t.check('run_daily_tnt.sh exists',      bin_script.is_file())
t.check('run_daily_tnt.sh executable',  os.access(bin_script, os.X_OK))
t.check('run_daily_tnt.sh syntax',      bash_n_ok(bin_script))
t.check('script references --daily',    '--daily' in bin_script.read_text())
t.check('script sets PYTHONPATH',       'PYTHONPATH' in bin_script.read_text())

sys.exit(t.summary())
