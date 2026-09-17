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

# === Brand registry (social/brand.py) ===
import sys as _sys
_sys.path.insert(0, str(PKG_DIR.parent))
try:
    from linkedin_generation.social.brand import BRANDS, get_brand, Brand, register_brand
    from linkedin_generation.social.base_content import BaseContentGenerator
    _reg_ok = True
except Exception as _e:
    _reg_ok = False
    t.check(f'brand registry imports (err: {_e})', False)

if _reg_ok:
    t.check('brand registry has tnt + seta', {'tnt', 'seta'} <= set(BRANDS))
    for _k, _b in BRANDS.items():
        t.check(f'brand "{_k}" key matches dict key', _b.key == _k)
        t.check(f'brand "{_k}" generator subclasses BaseContentGenerator',
                isinstance(_b.generator, type) and issubclass(_b.generator, BaseContentGenerator))
        t.check(f'brand "{_k}" has capabilities', len(_b.capabilities) > 0)
        _hc = (PKG_DIR.parent / _b.default_holiday_config)
        t.check(f'brand "{_k}" holiday config exists ({_b.default_holiday_config})', _hc.is_file())
    t.check('tnt uses logo_overlay capability', get_brand('tnt').has('logo_overlay'))
    t.check('seta uses news + charts capabilities',
            get_brand('seta').has('news') and get_brand('seta').has('charts'))
    try:
        get_brand('nope')
        t.check('get_brand raises on unknown brand', False)
    except KeyError:
        t.check('get_brand raises on unknown brand', True)

# === RULE: TNT keeps its logo, now passed explicitly per-brand ===
# The GIF provider used to hardcode the TNT logo (which leaked onto Seta posts).
# It is now a parameter — so TNT MUST supply it, or TNT silently loses branding.
tnt_prov_src = (PKG_DIR / 'social' / 'image_providers.py').read_text()
tnt_sched_src = (PKG_DIR / 'linkedin_post_scheduler.py').read_text()

t.check('RULE: TNT logo asset exists',
        (PROJECT_ROOT / 'assets' / 'tnt_motion_logo.png').exists())
t.check('RULE: TNT scheduler defines TNT_LOGO_PATH',
        'TNT_LOGO_PATH' in tnt_sched_src)
t.check('RULE: TNT passes its logo into the image provider',
        'logo_path=str(TNT_LOGO_PATH)' in tnt_sched_src)
t.check('RULE: GIF provider honours a per-brand logo_path',
        'self.logo_path' in tnt_prov_src)

# ============================================================================
# TNT NOW BUILDS ON REAL NEWS (2026-09-13)
# ============================================================================
# TNT was the last brand writing from nothing: every post invented from the
# pillar angle, which is why they read interchangeably. It had been left out on
# the assumption that industrial parts have no press. Measuring it disproved
# that - "bearing manufacturer industry" returns 18 fresh articles and Modern
# Machine Shop covers collets - PROVIDED the search phrases aim at the industry
# or the machine rather than the part number. See rules_seta.md.
import sys as _sys
_sys.path.insert(0, str(PKG_DIR.parent))
_sys.path.insert(0, os.getenv('COMMONLIB_ROOT', '/opt/commonlib'))

from linkedin_generation.social.brand import BRANDS as _BR
from linkedin_generation.social.post_quality import post_issues, TNT_VOICE, SETA_VOICE

_cg = (PKG_DIR / 'social' / 'content_generation.py').read_text()
t.check('RULE: TNT declares the news capability', 'news' in _BR['tnt'].capabilities)
t.check('RULE: TNT actually fetches news', 'search_news_for_pillar(' in _cg)
t.check('RULE: TNT uses its pillar\'s own search phrases',
        'news_queries' in _cg.split('search_news_for_pillar(')[1][:220])
t.check('RULE: fetched news counts as a source, so its figures are not called invented',
        'news_context' in _cg.split('sources =')[1][:200])
t.check('RULE: no URL reaches a TNT body - the link goes in the first comment',
        'def _strip_urls' in _cg)
t.check('RULE: TNT carries the articles onto the post for the source comment',
        'news_articles=news_articles' in _cg)
# Match a fragment on ONE source line: the sentence is split across a
# concatenation, so it never appears contiguously in the file.
t.check('RULE: TNT is told to connect the news to what it actually supplies',
        'The news earns the attention; the product answers' in _cg)
t.check('RULE: TNT must attribute by outlet and date, not "recent reports"',
        "Never 'recent reports'" in _cg)

_ts = (PKG_DIR / 'linkedin_post_scheduler.py').read_text()
t.check('RULE: TNT publishes its sources as the first comment',
        'build_source_comment(' in _ts and 'comment_on_post(' in _ts)

# Every TNT pillar must have phrases, and they must aim at the industry - a
# phrase naming only the part returns nothing (measured: "collet chuck tooling"
# gets zero from mainstream news while "bearing manufacturer industry" gets 18).
_camp = load_tnt_campaign() if 'load_tnt_campaign' in dir() else None
if _camp is None:
    import yaml as _yaml
    _camp = _yaml.safe_load(open(os.getenv(
        'LINKEDIN_CAMPAIGN_CONFIG', str(PKG_DIR.parent / 'config' / 'linkedin_campaign.yaml'))))
# CHANGED 2026-09-13: this asserted that EVERY pillar searches news. Forcing news
# onto an evergreen technical pillar is exactly what produced the Luoyang
# bolt-on, so which pillars are news-led is now a deliberate per-pillar choice
# (asserted individually further down). What every pillar must still have is
# usable phrases, so the choice can be flipped either way without re-research.
for _p in _camp.get('content_pillars', []):
    _n = _p.get('name', '?')[:34]
    t.check(f'RULE: TNT pillar "{_n}" has its own search phrases',
            len(_p.get('news_queries') or []) >= 2)
    t.check(f'RULE: TNT pillar "{_n}" phrases carry no hardcoded year',
            not any(__import__('re').search(r'\b(19|20)\d{2}\b', q)
                    for q in (_p.get('news_queries') or [])))

# TNT is a sales channel: naming itself twice is the intended shape, not a defect.
_promo = {'headline': 'H', 'body': 'TNT Motion makes these. TNT Motion ships fast.',
          'cta': 'Ask TNT Motion. And you?'}
t.check('RULE: a promotional brand may name itself more than once',
        not [i for i in post_issues(_promo, TNT_VOICE) if 'named' in i])
t.check('RULE: a reshareable brand still may not',
        [i for i in post_issues({'headline': 'H', 'body': 'Seta Capital did this.',
                                 'cta': 'Seta Capital again. And you?'}, SETA_VOICE)
         if 'named' in i])
t.check('RULE: the limit is declared per voice, not hardcoded',
        TNT_VOICE.max_brand_mentions > SETA_VOICE.max_brand_mentions)

# ============================================================================
# NEVER ADVERTISE A COMPETITOR (2026-09-13)
# ============================================================================
# Building posts on real news made this urgent: bearing and toolholding trade
# press is very often ABOUT a competitor - an SKF launch, a Schaeffler result, a
# Haimer chuck - and a post opening on one is free advertising for them,
# published from TNT's own page and paid for by TNT.
#
# The rule is NOT "never mention": a competitor's move is frequently the story,
# and reporting it factually is legitimate. It is "never PROMOTE".
from linkedin_generation.social.post_quality import competitor_promotion, TNT_COMPETITORS

t.check('RULE: the competitor list is declared per brand, not hardcoded in the check',
        TNT_VOICE.competitors and 'SKF' in TNT_VOICE.competitors)
t.check('RULE: the list covers bearings AND toolholding',
        all(n in TNT_COMPETITORS for n in ('SKF', 'Schaeffler', 'NSK', 'Timken',
                                           'Haimer', 'Schunk', 'Kennametal')))
t.check('RULE: Seta has no competitor list - it is not selling against anyone',
        not SETA_VOICE.competitors)

t.check('competitor: a name in the HEADLINE is promotion whatever the wording',
        competitor_promotion('SKF launches new spindle bearing\nBody text.', TNT_COMPETITORS)
        == ['SKF'])
t.check('competitor: praise vocabulary in the same sentence is promotion',
        competitor_promotion('Workholding matters\nThe leading Haimer chuck sets the standard.',
                             TNT_COMPETITORS) == ['Haimer'])
# Moved deeper into the body on 2026-09-13. A competitor in the OPENING sentence
# is now promotion whatever the wording - the post is built on them either way.
# Reported factually further down, it is legitimate industry context.
t.check('competitor: a plain factual report deeper in the body is NOT promotion',
        competitor_promotion('Spindle vibration costs uptime\nAlignment drift is the usual '
                             'cause. Schaeffler reported a 4% drop in orders last quarter. '
                             'That matters for lead times.',
                             TNT_COMPETITORS) == [])
t.check('competitor: opening ON a competitor IS promotion, even stated neutrally',
        competitor_promotion('Spindle vibration costs uptime\nSchaeffler reported a 4% drop '
                             'in orders last quarter.', TNT_COMPETITORS) == ['Schaeffler'])
t.check('competitor: a post with no competitor in it is clean',
        competitor_promotion('Pull studs and clamping force\nA worn pull stud loses force.',
                             TNT_COMPETITORS) == [])
t.check('competitor: a substring is not a false match',
        competitor_promotion('Headline\nThe best skfitting we sell.', TNT_COMPETITORS) == [])
t.check('RULE: the gate reports competitor promotion for the retry',
        any('advert' in i for i in post_issues(
            {'headline': 'SKF launches a new bearing', 'body': 'x', 'cta': 'And you?'},
            TNT_VOICE)))
t.check('RULE: the prompt tells the model outright not to advertise a competitor',
        'NEVER ADVERTISE A COMPETITOR' in _cg)
t.check('RULE: the prompt explains WHY (it is an advert TNT paid for)',
        "an advert TNT paid for" in _cg)

# ── Invented engineering figures get their own retry ────────────────────────
# The prompt has said since day one that every number must come from the supplied
# material. A dry run ignored it twice and produced "0.04 mm misalignment can cut
# bearing operating life by 40%" and "improving alignment to 0.01 mm extends life
# by 70%" - plausible, checkable and entirely invented. TNT's readers are
# maintenance engineers who will check.
t.check('RULE: invented figures trigger a focused retry, not just a warning',
        'stat_issues' in _cg and 'focused retry' in _cg)
t.check('RULE: the retry names the offending figures rather than re-asking blindly',
        'Every one of those figures is invented' in _cg)
t.check('RULE: it asks for a qualitative claim instead of a different invented number',
        'an engineer cannot' in _cg)
t.check('RULE: a post that still carries invented figures is loud and greppable',
        'INVENTED_FIGURES' in _cg)
# The phrase is wrapped across two source lines, so match the tail of it.
t.check('RULE: the general "report, do not strip" rule is unchanged',
        'not strip" (deleting every sentence with a number would gut the post)' in _cg)

# ============================================================================
# THE LUOYANG POST (2026-09-13) — three separate failures in one dry run
# ============================================================================
# A TNT post opened on "Luoyang Bearing Group ... recently listed on the Shenzhen
# stock exchange" and then pivoted to a canned "cheapest bearing is the most
# expensive" argument. Every guard reported it clean. Three things were wrong:
#   1. Luoyang is a COMPETITOR and was not in the list - which held only Western
#      and Japanese names, absurd when the news search is Chinese-first.
#   2. The competitor check only looked at the headline, and only for praise
#      words; the competitor was in the opening sentence, described neutrally.
#   3. A stock listing has NO causal link to bearing failure. The news was
#      wallpaper: an opening flourish the rest of the post ignored.
from linkedin_generation.social.news_search import is_corporate_finance

# --- 1. the list must cover where the news actually comes from ---
t.check('LUOYANG: Chinese bearing makers are in the competitor list',
        all(n in TNT_COMPETITORS for n in ('Luoyang Bearing', '洛轴', 'ZWZ', '瓦轴',
                                           'C&U', '五洲新春', 'Wanxiang')))
t.check('LUOYANG: the list is not Western-only any more',
        [x for x in TNT_COMPETITORS if any('一' <= c <= '鿿' for c in x)])

# --- 2. CJK has no word boundaries; the guard assumed it did ---
t.check('LUOYANG: a Chinese competitor name is matched at all',
        competitor_promotion('河南老牌轴承国企上市\n洛轴股份上市仪式，行业领先。',
                             TNT_COMPETITORS) == ['洛轴'])
t.check('LUOYANG: Chinese praise vocabulary counts as praise',
        competitor_promotion('标题\n这家公司是行业龙头。洛轴技术领先。', TNT_COMPETITORS))

# --- 3. the anchor is the headline AND the opening sentence ---
_luoyang = ('THE CHEAPEST BEARING IS OFTEN THE MOST EXPENSIVE.\n'
            'Luoyang Bearing Group, with over 70 years of history, recently listed on '
            'the Shenzhen stock exchange. But for distributors, price hides cost.')
t.check('LUOYANG: the exact post that shipped clean is now flagged',
        competitor_promotion(_luoyang, TNT_COMPETITORS) == ['Luoyang Bearing'])
t.check('LUOYANG: a competitor deep in the body, reported factually, is still allowed',
        competitor_promotion('Spindle vibration costs uptime\nAlignment matters. '
                             'Schaeffler reported a 4% drop in orders last quarter. '
                             'That matters for lead times.', TNT_COMPETITORS) == [])

# --- 4. equity stories are not a hook for a company selling components ---
t.check('LUOYANG: an IPO story is recognised as corporate finance',
        is_corporate_finance('河南老牌轴承国企上市 传统制造业焕新'))
t.check('LUOYANG: "IPO周报" is caught - CJK broke the word boundary a SECOND time',
        is_corporate_finance('IPO周报｜本周4只新股申购，国内轴承制造龙头来了'))
t.check('LUOYANG: a technical story is not mistaken for finance',
        not is_corporate_finance('Bearing failures: When normal readings hide the risk')
        and not is_corporate_finance('A costly mistake in spindle alignment'))
t.check('LUOYANG: both filters run at SELECTION, so the model never sees the article',
        'avoid_finance=True' in _cg and 'avoid_companies=TNT_VOICE.competitors' in _cg)

# --- 5. the connection must be causal, not decorative ---
t.check('LUOYANG: the prompt demands a causal connection',
        'CONNECTION MUST BE REAL' in _cg and 'decoration, not a' in _cg)
t.check('LUOYANG: the prompt says no news beats bolted-on news',
        'worse than one' in _cg)

# --- 6. not every pillar is news-shaped ---
# Forcing news onto an evergreen technical pillar is what produced the bolt-on.
_by_name = {p.get('name'): p for p in _camp.get('content_pillars', [])}
for _n, _want in (('Myth Busters', False), ('When Bearings Fail', False),
                  ('Extreme Applications', False), ('Product Spotlight', False),
                  ('Inside the Factory', True), ('Workholding Precision', True)):
    _p = next((v for k, v in _by_name.items() if k.startswith(_n)), None)
    if _p:
        t.check(f'LUOYANG: "{_n}" is {"news-led" if _want else "evergreen"}',
                bool(_p.get('use_news_search')) is _want)
t.check('LUOYANG: an evergreen pillar KEEPS its phrases so it can be flipped back',
        all(_p.get('news_queries') for _p in _by_name.values()))

# ============================================================================
# DESCRIBE THE MECHANISM, DO NOT INVENT THE CUSTOMER (2026-09-14)
# ============================================================================
# A ten-pillar dry run found TNT narrating customer work that never happened:
#   "Our TNT Motion engineer, David, visited a client in Saudi Arabia last year"
#   "A major steel mill in Alexandria, Egypt... failures every 3 months"
#   "An Eastern European food processing plant... cost over EUR 18,000"
# A named colleague who may not exist and customers that certainly do not. The
# invented-figures rule only polices NUMBERS, so the stories sailed through.
#
# These pillars are called "Forensic Stories from the Field" - narrative is the
# point. What is true and useful is the MECHANISM; naming a country, a customer
# or a colleague turns an explanation into a testimonial for work never done.
from linkedin_generation.social.post_quality import invented_case_studies

_INVENTED = [
    'Our TNT Motion engineer, David, visited a client in Saudi Arabia last year.',
    'A major steel mill in Alexandria, Egypt, faced critical bearing failures.',
    'The plant lost two full shifts of production. This cost over EUR 18,000.',
    'Our engineers at TNT Motion see this often in South American water treatment plants.',
    'A major Eastern European food processing plant faced a complete shutdown.',
]
_LEGITIMATE = [
    'In a washdown environment, moisture works past a worn lip seal and the race corrodes.',
    'A worn pull stud can lose up to 30% of its drawbar clamping force.',
    'High-duty pump cycles build internal heat inside sealed bearings.',
    'Replacing a EUR 40 bearing three times a year is EUR 120, plus three shutdowns.',
    'Chinese automakers will use 4% of European car production capacity by 2030.',
    'Ceramic balls are 60% lighter than steel, cutting friction heat.',
    'Sealed bearings in wet environments need seal inspection on a set interval.',
]
for _t in _INVENTED:
    t.check(f'CASE: flagged - "{_t[:46]}..."', bool(invented_case_studies(_t)))
for _t in _LEGITIMATE:
    t.check(f'CASE: clean - "{_t[:46]}..."', not invented_case_studies(_t))

t.check('CASE: a named colleague is called out specifically',
        any('named colleague' in h for h in invented_case_studies(
            'Our engineer, David, found the fault.')))
t.check('CASE: a cost pinned on a customer is called out specifically',
        any('cost or saving' in h for h in invented_case_studies(
            'It cost them EUR 18,000 in lost product.')))

# Scoped by voice: Seta DELIBERATELY describes parties by what they are, backed
# by the real mandate record and policed by the confidentiality gate. Applying
# this there would block its intended output.
t.check('CASE: the rule is a voice flag, not global',
        TNT_VOICE.ban_invented_cases and not SETA_VOICE.ban_invented_cases)
t.check('CASE: the gate reports it for the retry',
        any('did not happen' in i for i in post_issues(
            {'headline': 'H',
             'body': 'A major steel mill in Alexandria, Egypt, lost three shifts.',
             'cta': 'Call us. And you?'}, TNT_VOICE)))
t.check('CASE: the prompt shows what to write INSTEAD, not just what to avoid',
        'DESCRIBE THE MECHANISM' in _cg and 'washdown environment' in _cg)
t.check('CASE: a real case from the supplied material may still be used',
        'use it exactly as given' in _cg)


# ─── The cleanroom post, 17 Sep ─────────────────────────────────────────────
# "TNT Motion engineers developed a unique solution... This combination stops
# particle generation completely. Our bearings extend robot arm uptime by 3x."
# Tom: "the TNT post claim is absurd and unsubstantiated by anything".
#
# FOUR separate checks had to miss it for that to publish, and the root cause was
# upstream of all of them: the pillar's own proof_points were being passed as
# EVIDENCE. They are marketing copy in TNT's voice ("a ceramic hybrid solution
# that cut vibration by 70%"), so a post repeating them was judged sourced by the
# copy that invented them. A brand cannot be its own citation.
from linkedin_generation.social.post_quality import (
    absolute_claims, unbacked_first_person_claims, unsupported_statistics,
    statistic_values, post_issues, blocking_issues, TNT_VOICE,
)

_CLEANROOM = ("TNT Motion engineers developed a unique solution. We used advanced "
              "polymer cages. This combination stops particle generation completely. "
              "Our bearings extend robot arm uptime by 3x.")

t.check('CLAIM: an engineering multiplier is a statistic (3x was invisible)',
        '3x' in " ".join(statistic_values("uptime by 3x")))
t.check('CLAIM: 3x and 300% are judged the same way',
        bool(unsupported_statistics("uptime by 3x", ""))
        and bool(unsupported_statistics("uptime by 300%", "")))
t.check('CLAIM: hours and temperatures count too',
        bool(statistic_values("80 000 hours at 280 C")))
t.check('CLAIM: an absolute with no standard behind it is caught',
        bool(absolute_claims("This combination stops particle generation completely.")))
t.check('CLAIM: an absolute measured against a standard is allowed',
        not absolute_claims("Particle release stays below ISO 14644 Class 5 limits."))
t.check('CLAIM: a first-person feat with no record is caught',
        bool(unbacked_first_person_claims("We used advanced polymer cages.", "", "TNT Motion")))
t.check('CLAIM: the brand in third person is the same claim',
        bool(unbacked_first_person_claims(
            "TNT Motion engineers developed a unique solution.", "", "TNT Motion")))
t.check('CLAIM: ordinary product copy is NOT a feat claim',
        not unbacked_first_person_claims(
            "TNT Motion offers bearings engineered for harsh environments.", "", "TNT Motion")
        and not unbacked_first_person_claims(
            "TNT Motion provides technical support.", "", "TNT Motion"))
t.check('CLAIM: describing a mechanism generically stays clean',
        not unbacked_first_person_claims(
            "Water corrodes the inner race and the bearing seizes.", "", "TNT Motion"))

_iss = post_issues({'headline': 'X', 'body': _CLEANROOM, 'cta': 'Call us. And you?'},
                   TNT_VOICE, sources='', post_type='promotional', verified='')
t.check('CLAIM: the whole cleanroom post is REFUSED, not warned about',
        len(blocking_issues(_iss)) >= 3)

# The structural fix: a pillar's own copy may steer, never prove.
_marketing = ("Robotic arms in a Class-10 cleanroom required near-zero particle "
              "generation - special polymer cages and dry-film lubrication made it possible")
# Check for the SPECIFIC issue, not any issue. The first version asserted that
# post_issues returned SOMETHING - which it always does, since the body is a
# single block - so it passed while the statistics gate was still reading
# proof_points. A test that cannot fail for the reason it names is not a test.
_pp_issues = post_issues({'headline': 'X', 'body': 'Vibration fell by 70%.',
                          'cta': 'Call us. And you?'},
                         TNT_VOICE, sources=_marketing, post_type='promotional',
                         verified='')
t.check('EVIDENCE: proof_points passed as `sources` no longer license a figure',
        any('no source' in i for i in _pp_issues))
t.check('EVIDENCE: a real fetched article still supports its own figure',
        not [i for i in post_issues(
            {'headline': 'X', 'body': 'Vibration fell by 70%.', 'cta': 'Call us. And you?'},
            TNT_VOICE, sources='', post_type='promotional',
            verified='the trial showed vibration fell by 70% in testing')
            if 'no source' in i])

# And the prompt that asked for the invention in the first place.
_yaml = (PKG_DIR.parent / 'config' / 'linkedin_campaign.yaml').read_text()
# The phrase survives in the comment explaining its removal, so check the ANGLE
# the model actually receives rather than the raw file.
from linkedin_generation.social.campaign_config import CampaignConfig as _CC2
import pathlib as _pl2
_extreme = [q.angle for q in _CC2.from_yaml(
    _pl2.Path('/opt/linkedin/config/linkedin_campaign.yaml')).pillars
    if 'Extreme' in q.name][0]
t.check('PILLAR: Extreme Applications no longer asks for "real performance numbers"',
        'real performance numbers' not in _extreme)
t.check('PILLAR: it asks for the mechanism instead',
        'Start from the physics' in _yaml)


# The false positives matter as much as the catches: TNT is openly a sales
# channel, so blocking its ordinary product copy would be a worse failure than
# the one this gate exists to prevent. "prov\\w*" originally matched PROVIDE and
# flagged "We provide hybrid solutions for CNC spindles" on a live run.
for _sentence, _should in [
    ("We provide hybrid solutions for CNC spindles.", False),
    ("We provide technical support.", False),
    ("TNT Motion offers bearings engineered for harsh environments.", False),
    ("Proven technology is widely used in CNC spindles.", False),
    ("Water corrodes the inner race and the bearing seizes.", False),
    ("We developed a unique cage.", True),
    ("We used advanced polymer cages.", True),
    ("We proved the design at -45 C.", True),
    ("Our approach is proven in the field.", True),
]:
    _got = bool(unbacked_first_person_claims(_sentence, "", "TNT Motion"))
    t.check(f'FEAT: {"flags" if _should else "allows"} - {_sentence[:46]}',
            _got == _should)


# An absolute the post REPORTS in order to knock down is not a claim it makes.
# Flagging "Many believe sealed bearings are maintenance-free" made the whole
# Myth Busters pillar unpublishable - the gate was fighting the pillar's purpose.
for _s, _flag in [
    ("Many believe sealed bearings are maintenance-free.", False),
    ("Myth: grease colour indicates quality.", False),
    ("Conventional wisdom says ceramic bearings never wear.", False),
    ("This combination stops particle generation completely.", True),
    ("Our bearings never fail.", True),
    ("Particle release stays below ISO 14644 Class 5 limits.", False),
]:
    t.check(f'ABSOLUTE: {"flags" if _flag else "allows"} - {_s[:44]}',
            bool(absolute_claims(_s)) == _flag)


# Tom, 2026-09-17, on two borderline phrases in a live draft: "they can be
# admitted". Both describe TNT's ordinary working voice - no customer, no
# country, no figure, no claimed achievement - and this pillar is called
# Forensic Stories from the Field. Pinned so a later tightening of the feat gate
# cannot quietly take them out.
for _allowed in [
    "Our engineers found clear signs of moisture.",
    "We prevent such costly failures.",
    "Our technical team helps select the right sealing solutions.",
]:
    t.check(f'VOICE: admitted by decision - {_allowed[:44]}',
            not unbacked_first_person_claims(_allowed, "", "TNT Motion"))

sys.exit(t.summary())
