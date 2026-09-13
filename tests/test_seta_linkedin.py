#!/usr/bin/env python3
"""Tests for Seta Capital LinkedIn — exercises the ACTIVE scheduler (seta_post_scheduler.py).

IMPORTANT: Several tests here are RULE ENFORCERS, not just code checks. They prevent
accidental removal of the Veo video generator, generic image prompts, and missing
video_prompt fields. Do not remove or weaken them without a deliberate decision.
"""
import os, re, sys
import yaml
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from test_common import (
    TestRun, PROJECT_ROOT, SEO_ROOT, PKG_DIR, PYTHON_BIN,
    py_help_ok, py_compile_ok, bash_n_ok,
)

SETA_CAMPAIGN_YAML = Path(os.getenv('SETA_CAMPAIGN_CONFIG', str(PKG_DIR.parent / 'config' / 'seta_capital_linkedin.yaml')))

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
# Updated 2026-09-13: the safety net used to run only when NO news was found,
# because the prompt asked for a link in the body whenever news existed. The
# link now goes in the first comment (LinkedIn demotes posts with an outbound
# link), so NO url may ever reach the body and the net always runs. Same intent,
# stricter rule.
gen_src = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
t.check('RULE: URL-stripping safety net present in content generator',
        'url_pattern' in gen_src and 'Strip URLs from the post body' in gen_src)
t.check('RULE: the body is told never to paste a URL, news or no news',
        'Do NOT paste any URL in the post body' in gen_src)

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


# === RULE: a failed image must SKIP the post, never crash the run ===
# 2026-08-20: Google withdrew imagen-4.0 from the v1beta predict endpoint, image
# generation returned None, and the None travelled all the way into publish_post()
# -> image_path.read_bytes() -> AttributeError. The Seta post did not just lose its
# picture, it died with a stack trace and was silently never published. Every
# publish_post() call site must therefore be preceded by a None check (or sit inside
# a try/except), so any future image failure -- 429 quota, 503, network -- skips.
def _publish_sites_are_guarded(src: str):
    """Return (total_call_sites, unguarded_line_numbers)."""
    lines = src.splitlines()
    total, unguarded = 0, []
    for i, ln in enumerate(lines):
        if 'publisher.publish_post(' not in ln:
            continue
        total += 1
        window = '\n'.join(lines[max(0, i - 15):i])
        if ('local_image_path is None' in window
                or re.search(r'^\s*try:\s*$', window, re.M)):
            continue
        unguarded.append(i + 1)
    return total, unguarded

for _name, _src in (('seta_post_scheduler.py', sched_src),
                    ('linkedin_post_scheduler.py',
                     (PKG_DIR / 'linkedin_post_scheduler.py').read_text())):
    _total, _bad = _publish_sites_are_guarded(_src)
    t.check('RULE: %s has publish_post call sites to check' % _name, _total > 0)
    t.check('RULE: every publish_post in %s guards a None image (skip, not crash)' % _name,
            not _bad,
            'unguarded call sites at line(s): %s' % _bad if _bad else '')


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

# === RULE: Seta posts must NEVER carry another brand's logo ===
# 2026-05-12 -> 2026-07-23: AnimatedGIFProvider hardcoded assets/tnt_motion_logo.png
# and stamped it on every frame regardless of brand, so 8 published Seta Capital
# posts went out with the TNT Motion logo on them. The logo is now per-brand.
prov_src = (PKG_DIR / 'social' / 'image_providers.py').read_text()
seta_sched_src = (PKG_DIR / 'seta_post_scheduler.py').read_text()

t.check('RULE: AnimatedGIFProvider takes a logo_path parameter',
        'logo_path' in prov_src and 'self.logo_path' in prov_src)
t.check('RULE: image_providers.py does NOT hardcode the TNT logo',
        'tnt_motion_logo' not in prov_src)
t.check('RULE: Seta scheduler passes its own logo_path (never TNT)',
        'logo_path=' in seta_sched_src)
t.check('RULE: Seta scheduler never references the TNT logo asset',
        'tnt_motion_logo' not in seta_sched_src)
t.check('RULE: Seta logo asset absent => Seta posts carry no logo',
        (PROJECT_ROOT / 'assets' / 'seta_capital_logo.png').exists() is False
        or 'SETA_LOGO_PATH' in seta_sched_src)

# === RULE: follower analytics must survive LinkedIn's Rest.li quirks ===
# Three separate 400/403s were hit building this: requests percent-encodes the
# timeIntervals parens (400), networkSizes is v2-only with edgeType
# CompanyFollowedByMember (400), and a token without rw_organization_admin is
# refused (403) — the last must surface as an actionable error, not a traceback.
analytics_src = (PKG_DIR / 'social' / 'follower_analytics.py').read_text()

t.check('follower_analytics.py compiles', py_compile_ok('social/follower_analytics.py'))
t.check('RULE: timeIntervals is passed as raw_query, never through requests params',
        'raw_query=f"timeIntervals=' in analytics_src)
t.check('RULE: networkSizes uses the v2 base and CompanyFollowedByMember edgeType',
        'V2_BASE' in analytics_src and 'CompanyFollowedByMember' in analytics_src)
t.check('RULE: v2 calls omit the LinkedIn-Version header',
        'versioned=False' in analytics_src)
t.check('RULE: 403 raises MissingAnalyticsScope naming rw_organization_admin',
        'class MissingAnalyticsScope' in analytics_src
        and 'rw_organization_admin' in analytics_src)
t.check('follower_report.py compiles',
        py_compile_ok('scripts/follower_report.py'))

# === RULE: posts must be repost-worthy, not advertisements ===
# Measured 2026-08-31 over 67 posts (Dec 2025 - Aug 2026): 1 comment, 0 reposts,
# median reach 227/post in March collapsing to 70 by August. Every post closed
# on "Connect with us to discuss your strategic objectives" - nothing to reply
# to - and 61 of 67 headlines contained "cross-border". The gate below is
# enforced in code because a prompt alone cannot guarantee any of it.
sys.path.insert(0, str(PROJECT_ROOT))
from linkedin_generation.social.post_quality import (   # noqa: E402
    SETA_VOICE, TNT_VOICE, apply_fixes, post_issues, promotional_hits, reflow_paragraphs,
)

t.check('post_quality.py compiles', py_compile_ok('social/post_quality.py'))

_old_style = {
    'headline': 'Precision in Cross-Border M&A: Navigating Complexity',
    'body': ('The EUR/CNY rate moved to 7.8422, a 2.06% appreciation. This raises the '
             'effective acquisition cost for European buyers. The U.S. 10-Year yield is '
             'now 4.49%, up from 4.05%. Financing costs are rising for leveraged buyouts. '
             'GDP differentials still drive where capital is deployed.'),
    'cta': ('Seta Capital specializes in advising C-suite leaders on transactions between '
            'Europe and China. Connect with us to discuss your strategic objectives.'),
}
_issues = post_issues(_old_style, SETA_VOICE)
t.check('RULE: gate rejects the old promotional closing',
        any('promotional' in i for i in _issues), str(_issues))
t.check('RULE: gate rejects a closing with no question',
        any('no question' in i for i in _issues), str(_issues))
t.check('RULE: gate rejects house-cliche headlines',
        any('cliche' in i for i in _issues), str(_issues))
t.check('RULE: gate rejects a wall-of-text body',
        any('single block' in i for i in _issues), str(_issues))

_fixed = apply_fixes(_old_style, SETA_VOICE)
t.check('RULE: deterministic fix removes every promotional sentence',
        promotional_hits(_fixed['body'] + ' ' + _fixed['cta']) == [],
        repr(_fixed['cta']))
t.check('RULE: deterministic fix breaks the body into paragraphs',
        len([x for x in _fixed['body'].split('\n\n') if x.strip()]) >= 2)

_good = {
    'headline': 'Mittelstand Succession Is Repricing German Industrial Assets',
    'body': ('The EUR/CNY rate moved to 7.8422 this quarter.\n\n'
             'That shifts the arithmetic for European buyers.\n\n'
             'Minority stakes are absorbing the difference.'),
    'cta': ('Seta Capital reads this as a structural shift rather than a cycle. '
            'What are you seeing on valuations in your own pipeline?'),
}
t.check('RULE: a clean insight-led post passes the gate',
        post_issues(_good, SETA_VOICE) == [], str(post_issues(_good, SETA_VOICE)))

# decimals and initialisms must not be mistaken for sentence ends
t.check('RULE: paragraph reflow does not split decimals or initialisms',
        '7.8422' in reflow_paragraphs('Rates hit 7.8422 today. The U.S. Fed held. Buyers paused.')
        and 'U.S. Fed held' in reflow_paragraphs('Rates hit 7.8422 today. The U.S. Fed held. Buyers paused.'))

_gen_src = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
t.check('RULE: generator runs the quality gate before publishing',
        'post_issues(payload' in _gen_src and 'apply_fixes(payload' in _gen_src)
t.check('RULE: generator retries once with the gate feedback',
        'quality_feedback=issues' in _gen_src)
t.check('RULE: prompt forbids the promotional register',
        "'specializes in'" in _gen_src and 'connect with us' in _gen_src.lower())
t.check('RULE: prompt demands a genuine closing question',
        'ENDS WITH A GENUINE OPEN QUESTION' in _gen_src)
t.check('RULE: prompt demands short paragraphs, not one block',
        'BODY FORMATTING' in _gen_src and 'see more' in _gen_src)
t.check('RULE: prompt bans the worn-out headline vocabulary',
        "avoid 'cross-border'" in _gen_src)

# === RULE: every statistic must trace back to the fetched data ===
# The 2026-08-25 post asserted "Q2 2026 saw a 12% increase in minority stake
# investments" citing "recent H1 2026 data from leading industry reports". The
# pipeline fetches only ECB FX, FRED yields, World Bank GDP and news headlines -
# that figure was invented, in front of an audience of M&A professionals.
from linkedin_generation.social.post_quality import (   # noqa: E402
    unsupported_statistics, vague_source_hits,
)

_sources = ('EUR/CNY 7.8422 from 8.0071, change -2.06 percent. '
            'US 10Y 4.49 from 4.05. Germany GDP 0.2, Italy 0.5.')

t.check('RULE: invented statistics are caught',
        unsupported_statistics('Q2 2026 saw a 12% increase in minority stakes.', _sources) == ['12%'])
t.check('RULE: real fetched figures are accepted',
        unsupported_statistics('EUR/CNY moved to 7.8422 while the 10-year hit 4.49%.', _sources) == [])
t.check('RULE: a rounded quote of a fetched figure is accepted',
        unsupported_statistics('EUR/CNY is around 7.84 today.', _sources) == [])
t.check('RULE: years and labels are not treated as statistics',
        unsupported_statistics('In H1 2026 the 10-Year Treasury mattered.', _sources) == [])
t.check('RULE: vague attribution to unfetched sources is caught',
        vague_source_hits('Recent reports indicate a shift.') != []
        and vague_source_hits('Analysts estimate further tightening.') != [])
t.check('RULE: sourcing check is skipped when no sources are supplied',
        post_issues({'headline': 'A Specific Claim About German Assets',
                     'body': 'Deals rose 12%.\n\nBuyers paused.',
                     'cta': 'Seta Capital sees a shift. What do you see?'}, SETA_VOICE) == [])

_gen_src2 = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
t.check('RULE: generator passes fetched sources into the gate',
        'post_issues(payload, SETA_VOICE, sources=sources' in _gen_src2)
t.check('RULE: sources include chart data, news context and proof points',
        'chart_data, news_context' in _gen_src2 and 'pillar.proof_points' in _gen_src2)
t.check('RULE: prompt forbids inventing statistics',
        'SOURCING (non-negotiable)' in _gen_src2
        and 'Do NOT invent deal statistics' in _gen_src2)

# === RULE: the gate is shared, but each brand keeps its own policy ===
# TNT's prompt deliberately asks for a direct CTA (call, WhatsApp, email,
# catalogue download) because that page is a sales channel; Seta's posts exist
# to be reshared from a personal profile, where a pitch disqualifies them.
_tnt_sales = {'headline': 'Preload Error Cost A Plant Two Days Of Output',
              'body': 'A 2mm error stopped the line.\n\nPreload was the cause.',
              'cta': 'Our team can size it for you - WhatsApp us. What failure mode bites you most?'}
t.check('RULE: TNT keeps its direct sales CTA',
        post_issues(_tnt_sales, TNT_VOICE, sources='2mm preload') == [],
        str(post_issues(_tnt_sales, TNT_VOICE, sources='2mm preload')))
t.check('RULE: the same CTA is rejected for Seta',
        any('promotional' in i for i in post_issues(_tnt_sales, SETA_VOICE)))
t.check('RULE: apply_fixes never strips TNT\'s CTA',
        'WhatsApp' in apply_fixes(_tnt_sales, TNT_VOICE)['cta'])
t.check('RULE: invented currency amounts are caught',
        unsupported_statistics('A 2mm error cost the plant EUR200,000.', '2mm preload')
        == ['200,000'] or
        unsupported_statistics('A 2mm error cost the plant \u20ac200,000.', '2mm preload') != [])
t.check('RULE: a sourced currency amount is accepted',
        unsupported_statistics('The repair cost 200000 euros.', 'repair bill 200000') == [])
t.check('RULE: TNT vocabulary list is enforced, all-caps is not',
        any('myth' in i for i in post_issues(
            {'headline': 'BEARING MYTH BUSTED', 'body': 'A.\n\nB.', 'cta': 'Which one? Call us.'},
            TNT_VOICE))
        and not any('caps' in i.lower() for i in post_issues(
            {'headline': 'PRELOAD FAILURE STOPPED A LINE', 'body': 'A.\n\nB.',
             'cta': 'Which one bites you? Call us.'}, TNT_VOICE)))

_tnt_src = (PKG_DIR / 'social' / 'content_generation.py').read_text()
t.check('RULE: TNT generator runs the shared gate',
        'post_issues(payload, TNT_VOICE' in _tnt_src and 'apply_fixes(payload, TNT_VOICE)' in _tnt_src)
t.check('RULE: TNT prompt asks for a closing question as well as the CTA',
        'end with a genuine open question' in _tnt_src)
t.check('RULE: TNT prompt forbids invented failure costs',
        'invent failure costs' in _tnt_src)

t.check('RULE: TNT may name itself in the body, Seta may not',
        post_issues({'headline': 'PRELOAD ERROR STOPPED A LINE',
                     'body': 'Engineers at TNT Motion traced it.\n\nPreload was wrong.',
                     'cta': 'WhatsApp us. Which failure bites you most?'},
                    TNT_VOICE, sources='preload') == []
        and any('closing paragraph only' in i for i in post_issues(
            {'headline': 'A Claim', 'body': 'Seta Capital sees a shift.\n\nTwo.',
             'cta': 'What do you see?'}, SETA_VOICE)))

# === RULE: posts must be concrete, and holidays play by different rules ===
# Measured across Seta's 32-post archive: every single post used more than 3
# filler terms per 100 words (median 5.40, max 8.33), "strategic" alone
# averaging 3.19 uses per post. Abstraction is what made them interchangeable.
from linkedin_generation.social.post_quality import (   # noqa: E402
    FILLER_TERMS, filler_density, filler_hits,
)
from linkedin_generation.social.brand import get_brand   # noqa: E402

_waffle = {'headline': 'A Claim About German Assets',
           'body': ('The strategic landscape is complex and evolving.\n\n'
                    'Robust dynamic leverage underscores nuanced intricate value.'),
           'cta': 'Seta Capital sees a shift. What do you see?'}
t.check('RULE: consultant filler is measured, not guessed',
        filler_density('strategic complex dynamic landscape') == 100.0)
t.check('RULE: an abstract post is rejected',
        any('filler' in i for i in post_issues(_waffle, SETA_VOICE)),
        str(post_issues(_waffle, SETA_VOICE)))
t.check('RULE: the rejection names the worst offenders',
        any("'strategic'" in i or "'complex'" in i
            for i in post_issues(_waffle, SETA_VOICE) if 'filler' in i))
t.check('RULE: a concrete post passes',
        not any('filler' in i for i in post_issues(
            {'headline': 'Mittelstand Succession Is Repricing German Machine Tools',
             'body': ('Three Baden-Wurttemberg toolmakers changed hands this quarter.\n\n'
                      'Each sold a minority stake to a Chinese buyer.'),
             'cta': 'Seta Capital reads it as succession, not appetite. What do you see?'},
            SETA_VOICE)))

_holiday = {'headline': 'Happy Mid-Autumn Festival',
            'body': 'Wishing you a restful break.\n\nSee you after.',
            'cta': 'Enjoy the festival.'}
t.check('RULE: holiday posts need no discussion question',
        post_issues(_holiday, SETA_VOICE, post_type='holiday') == [],
        str(post_issues(_holiday, SETA_VOICE, post_type='holiday')))
t.check('RULE: the same post as analysis still needs one',
        any('question' in i for i in post_issues(_holiday, SETA_VOICE, post_type='market')))
t.check('RULE: holiday posts are exempt from the cliche list',
        post_issues({'headline': 'Happy Holidays From A Cross-Border Team',
                     'body': 'One.\n\nTwo.', 'cta': 'Enjoy.'},
                    SETA_VOICE, post_type='holiday') == [])

# --- productisation: one registry entry per company ---
t.check('RULE: each brand carries its voice in the registry',
        get_brand('seta').voice is SETA_VOICE and get_brand('tnt').voice is TNT_VOICE)
t.check('RULE: adding a company needs only a Brand entry',
        'BrandVoice' in (PKG_DIR / 'social' / 'brand.py').read_text()
        and 'Give it a `BrandVoice`' in (PKG_DIR / 'social' / 'brand.py').read_text())
import inspect  # noqa: E402
from linkedin_generation.social import post_quality as _pq   # noqa: E402
_generic = ''.join(inspect.getsource(f) for f in
                   (_pq.post_issues, _pq.apply_fixes, _pq.filler_density,
                    _pq.promotional_hits, _pq.unsupported_statistics))
t.check('RULE: the gate logic names no company (only the voices do)',
        'Seta' not in _generic and 'TNT' not in _generic, _generic[:120])
for _b in ('seta', 'tnt'):
    _v = get_brand(_b).voice
    t.check(f'RULE: {_b} voice is complete',
            bool(_v.name) and len(_v.overused_headline_terms) >= 3
            and _v.max_filler_per_100_words > 0)

t.check('RULE: both prompts demand concrete writing',
        'WRITE CONCRETELY' in (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
        and 'WRITE CONCRETELY' in (PKG_DIR / 'social' / 'content_generation.py').read_text())
t.check('RULE: post_type reaches the gate so holidays are exempt in production',
        'post_type=post_type' in (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
        and 'post_type=post_type' in (PKG_DIR / 'social' / 'content_generation.py').read_text())

# === RULE: pages configured in the CRM reach the pipeline intact ===
# /social-standalone/ writes one JSON file per page into config/brands/; the
# pipeline merges them over the code registry at import. A malformed or
# half-written file must never take down an 08:00 scheduled run.
import json as _json, tempfile as _tf, importlib as _il   # noqa: E402
from dataclasses import FrozenInstanceError   # noqa: E402
from linkedin_generation.social import brand_store as _bs   # noqa: E402
from linkedin_generation.social.brand import BRANDS   # noqa: E402

_orig_dir = _bs.BRAND_DIR
_tmp = Path(_tf.mkdtemp(prefix='brandstore_'))
_bs.BRAND_DIR = _tmp
try:
    # tone presets are the contract the CRM radio buttons rely on
    t.check('RULE: reshareable tone bans sales language',
            _bs.TONE_PRESETS['reshareable']['ban_promotional'] is True
            and _bs.TONE_PRESETS['reshareable']['require_closing_question'] is True)
    t.check('RULE: promotional tone allows a direct CTA',
            _bs.TONE_PRESETS['promotional']['ban_promotional'] is False
            and _bs.TONE_PRESETS['promotional']['brand_in_closing_only'] is False)
    t.check('RULE: announcement tone requires no question',
            _bs.TONE_PRESETS['announcement']['require_closing_question'] is False)

    _v = _bs.voice_from_config({'display_name': 'Acme Srl', 'tone': 'promotional',
                                'voice': {'overused_headline_terms': ['Myth'],
                                          'max_filler_per_100_words': 2.0}})
    t.check('RULE: a preset overrides saved switches',
            _v.ban_promotional is False and _v.name == 'Acme Srl')
    t.check('RULE: banned terms are normalised to lowercase',
            _v.overused_headline_terms == ('myth',))

    _c = _bs.voice_from_config({'display_name': 'Acme', 'tone': 'custom',
                                'voice': {'ban_promotional': False,
                                          'brand_in_closing_only': True,
                                          'require_closing_question': False}})
    t.check('RULE: custom tone keeps each switch as saved',
            _c.ban_promotional is False and _c.brand_in_closing_only is True
            and _c.require_closing_question is False)

    # a new page appears in the registry without any code change
    (_tmp / 'acme.json').write_text(_json.dumps({
        'key': 'acme', 'display_name': 'Acme Bearings', 'template': 'tnt',
        'tone': 'promotional', 'enabled': True,
        'example_posts': ['An exemplar.'], 'strategy': 'Bearings for CNC.',
        'proof_points': ['48h from Turin'], 'material': [{'name': 'p.pdf', 'text': 'Founded 1998.'}],
    }))
    # a disabled page must NOT appear
    (_tmp / 'dormant.json').write_text(_json.dumps({
        'key': 'dormant', 'display_name': 'Dormant Co', 'template': 'tnt', 'enabled': False}))
    # a half-written file must be skipped, not raise
    (_tmp / 'broken.json').write_text('{"key": "broken", "display_nam')

    _applied = _bs.apply_configs()
    t.check('RULE: a CRM-configured page joins the registry',
            'acme' in _applied and 'acme' in BRANDS, str(_applied))
    t.check('RULE: a paused page is not registered',
            'dormant' not in _applied and 'dormant' not in BRANDS)
    t.check('RULE: a half-written config is skipped, not fatal',
            'broken' not in _applied)
    t.check('RULE: a new page clones its template pipeline',
            BRANDS['acme'].generator is BRANDS['tnt'].generator
            and BRANDS['acme'].rotation_state_file == 'acme_scheduler_state.json')
    t.check('RULE: the new page gets its own tone',
            BRANDS['acme'].voice.ban_promotional is False
            and BRANDS['acme'].voice.name == 'Acme Bearings')

    _ctx = _bs.extra_context('acme')
    t.check('RULE: example posts lead the material (strongest voice anchor)',
            _ctx.index('exemplar') < _ctx.index('Bearings for CNC'), _ctx[:80])
    t.check('RULE: all four material kinds reach the prompt',
            all(x in _ctx for x in ('An exemplar.', 'Bearings for CNC', '48h from Turin', 'Founded 1998')))
    t.check('RULE: material is truncated to protect the prompt budget',
            len(_bs.extra_context('acme', max_chars=50)) < 120)
    t.check('RULE: an unknown page yields no material',
            _bs.extra_context('nosuchbrand') == '')

    # overriding a built-in changes its voice but not its pipeline
    (_tmp / 'seta.json').write_text(_json.dumps({
        'key': 'seta', 'display_name': 'Seta Capital', 'tone': 'announcement', 'enabled': True}))
    _before = BRANDS['seta'].generator
    _bs.apply_configs()
    t.check('RULE: overriding a built-in changes tone, not pipeline',
            BRANDS['seta'].generator is _before
            and BRANDS['seta'].voice.require_closing_question is False)
finally:
    _bs.BRAND_DIR = _orig_dir
    import shutil as _sh
    _sh.rmtree(_tmp, ignore_errors=True)
    for _k in ('acme', 'dormant'):
        BRANDS.pop(_k, None)
    _bs.apply_configs()   # restore real state for anything downstream

t.check('RULE: the store is loaded by the package, not by each caller',
        'apply_configs()' in (PKG_DIR / 'social' / '__init__.py').read_text())
t.check('RULE: both generators feed brand material into the prompt',
        'brand_material' in (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
        and 'brand_material' in (PKG_DIR / 'social' / 'content_generation.py').read_text())

# ============================================================================
# NEWS PIPELINE — added 2026-09-13 after a three-week silent outage
# ============================================================================
# From 2026-08-20 to 2026-09-13 every news-driven Seta post was generated with
# ZERO news articles. The posts still published, the cron sentinel was still
# touched and the health check still passed, so from outside it looked like a
# working system producing increasingly generic copy. The only news test in this
# file was that news_search.py existed on disk.
#
# Two faults, both invisible without a live call:
#   1. gemini-2.5-flash spent the whole maxOutputTokens=2000 budget on thinking
#      tokens, so every reply was finishReason=MAX_TOKENS, truncated mid-URL,
#      and the JSON parse failed.
#   2. Grounded Gemini only ever returns vertexaisearch redirect URLs, which the
#      code deliberately discarded — so even an untruncated reply yielded none.
# The fallbacks could not save it: they look for SERPER_API_KEY / TAVILY_API_KEY
# / Google Custom Search, none of which are configured, while the SERP_API_KEY
# that IS configured was never read.
#
# So: the tests below make a REAL call. They are the point of this section.

news_src = (PKG_DIR / 'social' / 'news_search.py').read_text()

# --- the specific regressions, pinned mechanically ---
t.check('RULE: news search reads SERP_API_KEY (the key that is actually set)',
        'os.getenv("SERP_API_KEY")' in news_src)
t.check('RULE: Gemini search does not spend its whole budget on thinking tokens',
        '"thinkingConfig": {"thinkingBudget": 0}' in news_src)
t.check('RULE: Gemini maxOutputTokens is no longer the 2000 that always truncated',
        '"maxOutputTokens": 2000,' not in news_src)
t.check('RULE: grounding redirects are resolved, never discarded',
        'def resolve_publisher_url' in news_src
        and 'Skipping Google redirect URL' not in news_src)
t.check('RULE: grounded replies are read from ALL parts, not parts[0]',
        'for p in parts' in news_src)
t.check('RULE: Chinese queries exist and are tried first',
        'PILLAR_SEARCH_QUERIES_ZH' in news_src and 'PREFERRED_SOURCES_ZH' in news_src)
t.check('RULE: stale articles are dropped',
        'MAX_ARTICLE_AGE_DAYS' in news_src)

# --- unit behaviour, no network ---
# run_daily_seta.sh puts BOTH the project and commonlib on PYTHONPATH; add them
# here too so the suite works when invoked bare (run_all_tests.sh does not set
# PYTHONPATH). Without this the scheduler import below raised ModuleNotFoundError,
# the suite died before printing its summary, and run_all_tests.sh scored the
# whole Seta suite as "0 passed, 0 failed" WITH A GREEN TICK.
sys.path.insert(0, str(PKG_DIR.parent))
sys.path.insert(0, os.getenv('COMMONLIB_ROOT', '/opt/commonlib'))
from linkedin_generation.social.news_search import (
    parse_relative_age, extract_source_name, build_news_context, NewsArticle,
    provider_health,
)

t.check('date parse: Chinese relative dates ("4 天前", "3 周前")',
        parse_relative_age('4 天前') == 4 and parse_relative_age('3 周前') == 21)
t.check('date parse: English relative dates',
        parse_relative_age('2 days ago') == 2 and parse_relative_age('1 week ago') == 7)
t.check('date parse: nonsense is None, not 0',
        parse_relative_age('') is None and parse_relative_age('whenever') is None)
t.check('source names: subdomains resolve to the real outlet',
        extract_source_name('http://auto.cnfol.com/x') == 'CnFol (中金在线)'
        and extract_source_name('https://finance.sina.com.cn/y') == 'Sina Finance (新浪财经)')
t.check('source names: Western wires still resolve',
        extract_source_name('https://www.reuters.com/a') == 'Reuters')

_a = NewsArticle(title='T', url='https://example.com/story', source='S',
                 summary='body', published_date='9 September 2026', language='zh')
_ctx = build_news_context([_a])
t.check('RULE: the prompt is never given a URL (links go in the first comment)',
        'https://example.com/story' not in _ctx)
t.check('RULE: the prompt demands outlet + date attribution',
        'OUTLET' in _ctx and "Never 'recent reports'" in _ctx)

from linkedin_generation.seta_post_scheduler import build_source_comment
_comment = build_source_comment([_a])
t.check('RULE: the first comment carries the REAL article link',
        'https://example.com/story' in _comment and 'in Chinese' in _comment)
t.check('RULE: no articles means no empty comment is posted',
        build_source_comment([]) == '')

gen_src = (PKG_DIR / 'social' / 'seta_content_generation.py').read_text()
t.check('RULE: the prompt no longer asks for a URL in the post body',
        'Include the FULL URL' not in gen_src)
t.check('RULE: URLs are stripped from the body even when news WAS found',
        'if post_type == "holiday":\n            return payload' in gen_src)
t.check('RULE: the post must open on an event, not a theme',
        'is a failed post' in gen_src)

client_src = (PKG_DIR / 'social' / 'linkedin_client.py').read_text()
t.check('RULE: the publisher can post the source link as a first comment',
        'def comment_on_post' in client_src and 'socialActions' in client_src)
t.check('RULE: the comment URN is path-encoded',
        'quote(share_urn, safe="")' in client_src)
t.check('RULE: a failed comment never fails the run',
        'must NEVER fail the run' in client_src)

sched_src = (PKG_DIR / 'seta_post_scheduler.py').read_text()
t.check('RULE: a news pillar that found nothing raises NEWS_OUTAGE',
        'NEWS_OUTAGE' in sched_src)
t.check('RULE: the source comment is posted after publishing',
        'comment_on_post(' in sched_src)

# --- the outage alarm: did a recent run publish generic filler? ---
_seta_log = PROJECT_ROOT / 'logs' / 'seta.log'
if _seta_log.exists():
    import time as _time
    _cutoff = _time.time() - 21 * 86400
    _recent_outage = False
    if _seta_log.stat().st_mtime > _cutoff:
        _tail = _seta_log.read_text(errors='replace')[-400_000:]
        _recent_outage = 'NEWS_OUTAGE' in _tail
    t.check('no NEWS_OUTAGE in the recent Seta log (a post went out with no news)',
            not _recent_outage)

# --- THE LIVE CHECK: do the tools actually work right now? ---
# Skipped only when the keys are absent (e.g. a checkout without .env); when they
# are present this MUST make a real call. A structural test cannot catch a model
# changing its token accounting, which is exactly what broke this pipeline.
if os.getenv('SERP_API_KEY') or os.getenv('GOOGLE_API_KEY'):
    _health = provider_health()
    _counts = {k: v for k, v in _health.items() if isinstance(v, int)}
    t.check('LIVE: at least one news provider returns articles right now (%s)' % _health,
            any(v > 0 for v in _counts.values()))
    if os.getenv('SERP_API_KEY'):
        t.check('LIVE: the Chinese-language search returns articles (%s)' % _health.get('serpapi_zh'),
                isinstance(_health.get('serpapi_zh'), int) and _health['serpapi_zh'] > 0)

    from linkedin_generation.social.news_search import search_news_for_pillar
    _arts = search_news_for_pillar('M&A Insights', num_articles=3)
    t.check('LIVE: the M&A pillar gets real articles end to end (%d)' % len(_arts), len(_arts) > 0)
    if _arts:
        t.check('LIVE: every article carries a usable link',
                all(a.url.startswith('http') for a in _arts))
        t.check('LIVE: no article is older than the staleness limit',
                all(a.age_days is None or a.age_days <= 45 for a in _arts))
else:
    t.check('LIVE news check SKIPPED - no SERP_API_KEY/GOOGLE_API_KEY in env', True)

sys.exit(t.summary())
