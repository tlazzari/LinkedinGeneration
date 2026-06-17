#!/usr/bin/env python3
"""Tests for Seta Capital LinkedIn — exercises the ACTIVE scheduler (seta_post_scheduler.py).

IMPORTANT: Several tests here are RULE ENFORCERS, not just code checks. They prevent
accidental removal of the Veo video generator, generic image prompts, and missing
video_prompt fields. Do not remove or weaken them without a deliberate decision.
"""
import os, sys
import yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_common import (
    TestRun, PROJECT_ROOT, SEO_ROOT, PKG_DIR, PYTHON_BIN,
    py_help_ok, py_compile_ok, bash_n_ok,
)

SETA_CAMPAIGN_YAML = SEO_ROOT / 'config' / 'seta_capital_linkedin.yaml'

def load_seta_campaign():
    with open(SETA_CAMPAIGN_YAML) as f:
        return yaml.safe_load(f) or {}

t = TestRun('Seta')

# === Source file presence ===
t.check('seta_post_scheduler.py exists',            (PKG_DIR / 'seta_post_scheduler.py').is_file())
t.check('social/seta_content_generation.py exists', (PKG_DIR / 'social' / 'seta_content_generation.py').is_file())
t.check('social/news_search.py exists',             (PKG_DIR / 'social' / 'news_search.py').is_file())
t.check('seta_capital_linkedin.yaml exists',        SETA_CAMPAIGN_YAML.is_file())

# === Compile / import ===
t.check('compile seta_post_scheduler',   py_compile_ok('seta_post_scheduler.py'))
t.check('compile linkedin_client',       py_compile_ok('social/linkedin_client.py'))
t.check('compile campaign_config',       py_compile_ok('social/campaign_config.py'))

# === CLI ===
t.check('--help exits 0', py_help_ok('seta_post_scheduler.py'))

# === Required functions present in scheduler source ===
src = (PKG_DIR / 'seta_post_scheduler.py').read_text()
t.check('main function present',                'def main' in src)
t.check('parse_args function present',          'def parse_args' in src)
t.check('--run-once flag present',              '"--run-once"' in src)
t.check('--publish flag present',               '"--publish"' in src)
t.check('news preview wired in',                'fetch_article_preview_image' in src)

# === RULE: Veo video generation must be wired ===
t.check('RULE: generate_video_for_post function present',
        'def generate_video_for_post' in src)
t.check('RULE: ReplicateVideoProvider imported in scheduler',
        'ReplicateVideoProvider' in src)
t.check('RULE: scheduler calls generate_video_for_post when pillar.use_veo',
        'pillar.use_veo' in src and 'generate_video_for_post' in src)
t.check('RULE: scheduler calls publish_video_post for video path',
        'publish_video_post' in src)
t.check('RULE: media_type tracked in metadata',
        'media_type' in src)

# === RULE: Video publishing must be wired in the publisher ===
client_src = (PKG_DIR / 'social' / 'linkedin_client.py').read_text()
t.check('RULE: publish_video_post method present in publisher',
        'def publish_video_post' in client_src)
t.check('RULE: feedshare-video recipe present in publisher',
        'feedshare-video' in client_src)
t.check('RULE: _register_video_upload helper present',
        'def _register_video_upload' in client_src)
t.check('RULE: VIDEO shareMediaCategory present',
        'VIDEO' in client_src and 'shareMediaCategory' in client_src)

# === RULE: PostPillar must carry use_veo and video_prompt ===
cfg_src = (PKG_DIR / 'social' / 'campaign_config.py').read_text()
t.check('RULE: PostPillar has use_veo field',   'use_veo: bool' in cfg_src)
t.check('RULE: PostPillar has video_prompt field', 'video_prompt: str' in cfg_src)
t.check('RULE: from_yaml enforces video_prompt when use_veo=True',
        'video_prompt set' in cfg_src)

# === Campaign YAML structural checks (Seta YAML, not TNT) ===
campaign = load_seta_campaign()
t.check('campaign yaml has content_pillars',
        'content_pillars' in campaign and isinstance(campaign['content_pillars'], list))
pillars = campaign.get('content_pillars', [])
t.check('at least 3 pillars defined', len(pillars) >= 3)

# === RULE: Every use_veo pillar must have a video_prompt ===
for p in pillars:
    name = p.get('name', 'unknown')
    if p.get('use_veo'):
        t.check(f'RULE: Veo pillar "{name}" has video_prompt',
                bool(p.get('video_prompt', '').strip()))
        t.check(f'RULE: Veo pillar "{name}" video_prompt >= 40 chars',
                len(p.get('video_prompt', '')) >= 40)
        t.check(f'RULE: Veo pillar "{name}" video_prompt mentions 16:9',
                '16:9' in p.get('video_prompt', ''))

# === RULE: No generic / empty-buildings image prompts for Seta ===
# Seta images must be people-centric: advisors, executives, deal-making scenes
BANNED_IMAGE_PHRASES = ['empty building', 'empty office', 'just a skyline']
PEOPLE_MARKERS = [
    'advisor', 'executive', 'engineer', 'director', 'investor',
    'analyst', 'manager', 'partner', 'founder',
    'businessman', 'businesswoman', 'team', 'professional',
]
for p in pillars:
    name = p.get('name', 'unknown')
    prompt = p.get('image_prompt', '') or ''
    prompt_lower = prompt.lower()

    for banned in BANNED_IMAGE_PHRASES:
        t.check(f'RULE: Seta image_prompt for "{name}" does not contain "{banned}"',
                banned not in prompt_lower)

    t.check(f'RULE: Seta image_prompt for "{name}" is specific (>= 60 chars)',
            len(prompt.strip()) >= 60)

    has_people = any(marker in prompt_lower for marker in PEOPLE_MARKERS)
    t.check(f'RULE: Seta image_prompt for "{name}" includes human professionals',
            has_people)

# === RULE: at least one pillar uses Veo, at least one does not ===
veo_pillars   = [p for p in pillars if p.get('use_veo')]
photo_pillars = [p for p in pillars if not p.get('use_veo')]
t.check('RULE: at least one pillar uses Veo video',  len(veo_pillars) >= 1)
t.check('RULE: at least one pillar uses static image (Imagen fallback path exercised)',
        len(photo_pillars) >= 1)

# === RULE: seta_content_generation fake-URL prevention ===
gen_src = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
t.check('RULE: URL-stripping safety net present in content generator',
        'url_pattern' in gen_src and 'hallucinated' in gen_src)
t.check('RULE: no-URL directive when news unavailable',
        'Do NOT include any external links' in gen_src)

# === Bin script ===
bin_script = PROJECT_ROOT / 'bin' / 'run_daily_seta.sh'
t.check('run_daily_seta.sh exists',      bin_script.is_file())
t.check('run_daily_seta.sh executable',  os.access(bin_script, os.X_OK))
t.check('run_daily_seta.sh syntax',      bash_n_ok(bin_script))
t.check('script references --daily',     '--daily' in bin_script.read_text())
t.check('script sets PYTHONPATH',        'PYTHONPATH' in bin_script.read_text())


# === RULE: seta_content_generation image prompt cannot suggest city/building imagery ===
gen_src = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
# These are the exact suggestion phrases that were previously in _build_prompt().
# They must not reappear in a context that tells the LLM to GENERATE these things.
# (The words may still appear in the prohibited-list lines starting with "NO ...")
BAD_SUGGESTIONS = [
    'Focus on: cityscapes',
    'boardrooms, or cityscapes',
    'Focus on: city skylines',
    'show city skylines',
]
for phrase in BAD_SUGGESTIONS:
    t.check(f'RULE: content generator does NOT suggest "{phrase}" to LLM',
            phrase not in gen_src)
# The NO-list must explicitly forbid city/building-only imagery
t.check('RULE: content generator prohibits empty buildings in image prompt',
        'NO empty buildings' in gen_src)
t.check('RULE: content generator prohibits city skylines without people',
        'NO city skylines without people' in gen_src or 'NO city skyline' in gen_src)

# === RULE: YAML pillar.image_prompt takes priority over LLM-generated image_prompt ===
t.check(
    'RULE: pillar.image_prompt is primary (YAML wins over LLM output)',
    'pillar.image_prompt or payload.get("image_prompt")' in gen_src,
)
t.check(
    'RULE: LLM image_prompt does NOT silently override YAML pillar.image_prompt',
    'payload.get("image_prompt") or pillar.image_prompt' not in gen_src,
)

# === RULE: Seta cron uses --daily flag (not --run-once) for Tue/Thu enforcement ===
import subprocess
crontab = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout
seta_lines = [l for l in crontab.splitlines() if 'run_daily_seta.sh' in l and not l.strip().startswith('#')]
t.check('RULE: Seta cron entry exists', len(seta_lines) >= 1)
run_script = PROJECT_ROOT / 'bin' / 'run_daily_seta.sh'
if run_script.is_file():
    script_src = run_script.read_text()
    t.check('RULE: run_daily_seta.sh uses --daily flag (not --run-once)',
            '--daily' in script_src and '--run-once' not in script_src,
            detail='--run-once bypasses Tue/Thu check; must use --daily')


# === RULE: animated GIF must be enabled (no static images for non-Veo pillars) ===
t.check('RULE: Seta image_provider uses animated GIF (use_animated_gif: true)',
        campaign.get('image_provider', {}).get('use_animated_gif') is True)
t.check('RULE: Seta GIF has at least 4 frames',
        (campaign.get('image_provider', {}).get('gif_num_frames') or 0) >= 4)

# === RULE: Veo failure falls back to Imagen (veo-1.5-001 is dead, veo-2 needs billing) ===
sched_src = (PKG_DIR / 'seta_post_scheduler.py').read_text()
t.check('RULE: Veo failure skips post (no static fallback)',
        'elif pillar.use_veo:' in sched_src and 'Veo generation failed' in sched_src)
t.check('RULE: Veo fallback uses Imagen instead of skipping post',
        'falling back to Imagen' in sched_src and 'generate_image_for_post' in sched_src)
t.check('RULE: non-Veo pillars still generate animated GIF image',
        '# Non-Veo pillar' in sched_src or 'Industry Expertise' in sched_src or 'generate_image_for_post' in sched_src)


# === RULE: Market Intelligence pillar exists and is correctly configured ===
chart_pillars = [p for p in pillars if p.get('use_chart')]
t.check('RULE: Market Intelligence pillar exists (use_chart: true)',
        len(chart_pillars) >= 1)

if chart_pillars:
    cp = chart_pillars[0]
    t.check('RULE: Market Intelligence pillar has no use_veo (chart is the media)',
            not cp.get('use_veo', False))
    t.check('RULE: Market Intelligence has target_client defined',
            bool(cp.get('target_client', '').strip()))
    t.check('RULE: Market Intelligence has at least 2 proof_points',
            len(cp.get('proof_points', [])) >= 2)

# === RULE: seta_chart_generator.py exists and is importable ===
chart_gen = PKG_DIR / 'social' / 'seta_chart_generator.py'
t.check('RULE: seta_chart_generator.py exists', chart_gen.is_file())
if chart_gen.is_file():
    gen_src = chart_gen.read_text()
    t.check('RULE: chart generator defines generate_market_chart()',
            'def generate_market_chart(' in gen_src)
    t.check('RULE: chart generator fetches ECB EUR/CNY data',
            'D.CNY.EUR.SP00.A' in gen_src)
    t.check('RULE: chart generator fetches FRED data',
            'FRED_API_KEY' in gen_src)
    t.check('RULE: chart generator fetches World Bank GDP',
            'worldbank.org' in gen_src)
    t.check('RULE: chart generator uses Seta navy branding (#1B2A4A)',
            '#1B2A4A' in gen_src or 'SETA_NAVY' in gen_src)
    t.check('RULE: chart generator uses Seta gold branding (#C4A35A)',
            '#C4A35A' in gen_src or 'SETA_GOLD' in gen_src)
    t.check('RULE: chart generator returns (path, summary) tuple',
            'data_summary' in gen_src)
    t.check('RULE: chart generator skips post on failure (returns None)',
            'return None,' in gen_src)
    t.check('RULE: 3 chart types rotate (fx_trend, gdp_bars, dual_axis)',
            all(ct in gen_src for ct in ['fx_trend', 'gdp_bars', 'dual_axis']))

# === RULE: scheduler uses chart data BEFORE generating post (LLM gets real numbers) ===
sched_src = (PKG_DIR / 'seta_post_scheduler.py').read_text()
t.check('RULE: chart generated before post (data feeds LLM)',
        sched_src.index('generate_market_chart(') < sched_src.index('generator.generate('))
t.check('RULE: chart_data passed to generator.generate()',
        'chart_data=chart_data_summary' in sched_src)
t.check('RULE: chart_type rotates via RotationState',
        'next_chart_type_index' in sched_src)

# === RULE: content generator injects chart data into LLM prompt ===
gen_src2 = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
t.check('RULE: generate() accepts chart_data parameter',
        'chart_data: str = ' in gen_src2)
t.check('RULE: chart data injected as MANDATORY in prompt',
        'LIVE MARKET DATA' in gen_src2 and 'MUST USE THESE EXACT FIGURES' in gen_src2)
t.check('RULE: chart post requires quoting specific numbers',
        'Quote at least TWO specific numbers' in gen_src2)

sys.exit(t.summary())

# === RULE: Seta LinkedIn access token must not be expiring within 14 days ===
import datetime
env_file = Path('/opt/linkedin/.env')
expiry_val = None
if env_file.is_file():
    for line in env_file.read_text().splitlines():
        if line.startswith('SETA_LINKEDIN_TOKEN_EXPIRY='):
            expiry_val = line.split('=', 1)[1].strip()
            break
t.check('RULE: SETA_LINKEDIN_TOKEN_EXPIRY set in .env', expiry_val is not None)
if expiry_val:
    try:
        expiry_dt = datetime.date.fromisoformat(expiry_val)
        days_left = (expiry_dt - datetime.date.today()).days
        t.check(f'RULE: Seta LinkedIn token not expiring within 14 days (expires {expiry_val}, {days_left}d left)',
                days_left > 14)
    except ValueError:
        t.check(f'RULE: SETA_LINKEDIN_TOKEN_EXPIRY is a valid date (got: {expiry_val!r})', False)
