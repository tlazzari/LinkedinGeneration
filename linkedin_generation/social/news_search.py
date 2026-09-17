"""News search module for fetching current news for LinkedIn posts."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Sequence
from urllib.parse import unquote, urlparse

import html as html_mod
import requests

logger = logging.getLogger(__name__)

_DDG_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
_DDG_RESULT = re.compile(
    r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S
)

# News search queries by pillar - dynamically include current year
def get_pillar_search_queries():
    """Get search queries with current year."""
    from datetime import datetime
    year = datetime.now().year
    return {
        "Cross-Border M&A Insights": [
            f"China Europe M&A deal acquisition {year}",
            f"Chinese company European acquisition {year}",
            f"European company China investment deal {year}",
            f"cross-border merger China Europe {year}",
        ],
        "Technology Sector Trends": [
            f"China Europe technology investment {year}",
            f"Chinese tech company Europe expansion {year}",
            f"European tech investment China {year}",
            f"cleantech deeptech China Europe {year}",
        ],
        "Market Intelligence": [
            f"China Europe business investment trends {year}",
            f"China Europe economic relations {year}",
            f"European investment China market {year}",
            f"China Europe trade business news {year}",
        ],
        "M&A Insights": [
            f"China Europe M&A deal acquisition {year}",
            f"Chinese company European acquisition {year}",
            f"European company China investment deal {year}",
            f"cross-border merger China Europe {year}",
        ],
        "Thought Leadership": [
            f"Europe China M&A regulatory outlook {year}",
            f"Chinese industrial capital Europe strategic assets {year}",
            f"family business succession M&A Germany Italy {year}",
            f"Europe China investment banking advisory {year}",
        ],
        "Industry Expertise": [
            f"China Europe EV supply chain M&A {year}",
            f"precision manufacturing cross-border deal {year}",
            f"Chinese automation company Europe acquisition {year}",
            f"industrial technology China Europe investment {year}",
        ],
    }

PILLAR_SEARCH_QUERIES = get_pillar_search_queries()


def get_pillar_search_queries_zh():
    """Chinese-language queries, tried FIRST for every pillar (2026-09-13).

    Chinese outlets carry the Europe-China deal flow that matters to Seta's
    audience days before the Western wires do, and often cover deals the wires
    never pick up at all. A live check on 2026-09-13 returned, in one query:
    the EU state-subsidy review of JD.com/Ceconomy (RFI, DW, 2 days old), Nikkei
    CN on Chinese firms holding 4% of Europe's auto production capacity by 2030,
    and 中金在线 counting 130+ European component makers acquired in 20 years.
    The equivalent English query returned generic advisory marketing.
    """
    return {
        "Cross-Border M&A Insights": [
            "中国 企业 收购 欧洲",
            "中企 跨境并购 欧洲",
            "中国 资本 收购 德国 意大利 企业",
        ],
        "M&A Insights": [
            "中国 企业 收购 欧洲",
            "中企 出海 并购 交易",
            "欧盟 审查 中国 收购",
        ],
        "Thought Leadership": [
            "中企 出海 欧洲 战略",
            "欧盟 外资审查 中国 投资",
            "家族企业 传承 出售 欧洲",
        ],
        "Industry Expertise": [
            "跨境并购 欧洲 制造业",
            "中国 汽车零部件 收购 欧洲",
            "工业自动化 并购 中国 欧洲",
        ],
        "Technology Sector Trends": [
            "中国 科技企业 欧洲 投资",
            "新能源 电池 欧洲 建厂 收购",
        ],
        "Market Intelligence": [
            "中欧 贸易 投资 数据",
            "中国 对欧洲 直接投资 报告",
        ],
    }


PILLAR_SEARCH_QUERIES_ZH = get_pillar_search_queries_zh()

# Articles older than this are dropped: a post that opens on a 4-month-old deal
# reads exactly like the generic filler this pipeline is meant to replace.
MAX_ARTICLE_AGE_DAYS = int(os.getenv("NEWS_MAX_AGE_DAYS", "45"))

# Chinese outlets rank ABOVE the Western wires (2026-09-13, user's call: "news
# from chinese websites would be more interesting"). Seta's LinkedIn audience is
# half Chinese buy-side; citing 新华财经 or 日经中文网 on a Europe deal is both more
# differentiated and closer to where these processes actually start.
PREFERRED_SOURCES_ZH = [
    "cnfin.com",       # 新华财经 (Xinhua Finance)
    "caixin.com",      # 财新
    "yicai.com",       # 第一财经
    "nbd.com.cn",      # 每日经济新闻
    "21jingji.com",    # 21世纪经济报道
    "cn.nikkei.com",   # 日经中文网
    "stcn.com",        # 证券时报
    "cls.cn",          # 财联社
    "jiemian.com",     # 界面新闻
    "chnfund.com",     # 中国基金报
    "cnfol.com",       # 中金在线
    "sina.com.cn",     # 新浪财经
    "dw.com",          # DW 中文
    "rfi.fr",          # RFI 中文
]

# Ranked LAST, never excluded (2026-09-13). Two live runs pulled a Sohu
# aggregator headline ("欧盟对我们下最后通牒…西方就动手") and an EIN Presswire item -
# a paid press-release wire, not journalism - as the anchor for a post. Citing
# those by name in front of an M&A audience costs more credibility than having no
# story at all. They stay available so a thin week still produces a post, but only
# once nothing better is on offer.
DEMOTED_SOURCES = [
    "sohu.com", "163.com", "baijiahao", "toutiao", "ifeng.com",
    "einpresswire.com", "prnewswire", "businesswire", "globenewswire",
    "openpr.com", "accesswire", "medium.com", "linkedin.com", "reddit.com",
    # Portal aggregators - they republish, they do not report. Tencent News was
    # the anchor of a Market Intelligence dry run on 2026-09-14.
    "news.qq.com", "qq.com", "sohu.com", "ifeng.com", "k.sina.com.cn",
    # Market-research report mills. Checking "bearing manufacturer industry" on
    # 2026-09-13 returned "Cold Heading Quality Wire Market Share Analysis",
    # "Wind Power Bearing Market Overview" and "Tool Holder Market Size, Share,
    # Trends & Growth Forecast 2035" in the top five. These are SEO teasers for
    # paid reports, not journalism: there is no event in them, so a post built on
    # one has nothing to say. They crowd out the genuine trade press, which does
    # exist even for narrow topics - the same check surfaced MTDCNC on collets.
    "grandviewresearch", "marketresearchfuture", "marketgrowthreports",
    "marketsandmarkets", "researchandmarkets", "fortunebusinessinsights",
    "precedenceresearch", "imarcgroup", "mordorintelligence", "alliedmarketresearch",
    "verifiedmarket", "skyquestt", "zionmarketresearch", "futuremarketinsights",
    "coherentmarketinsights", "transparencymarketresearch", "openpr",
]

# The genre is recognisable from the headline alone, whatever domain it is on.
_REPORT_MILL_RE = re.compile(
    r"market\s+(size|share|overview|outlook|report|analysis)"
    r"|(size|share)[^.]{0,40}(forecast|cagr)"
    r"|forecast\s+(to\s+)?20\d\d"
    r"|industry\s+report\b",
    re.IGNORECASE,
)

# Trade press that DOES cover narrow engineering topics, ranked with the wires.
# Added because the collet check found MTDCNC carrying a real product story while
# mainstream Google News had nothing at all for the same query.
PREFERRED_TRADE = [
    "mtdcnc.com", "modernmachineshop.com", "americanmachinist.com",
    "themanufacturer.com", "industryweek.com", "canadianmetalworking.com",
    "machinery.co.uk", "aerospacemanufacturinganddesign.com", "etmm-online.com",
    "manufacturingtodayindia.com", "engineering.com", "machinedesign.com",
    "powertransmission.com", "evolution.skf.com", "bearing-news.com",
]

# Outright junk — dropped, not demoted. Chinese "news" results for manufacturing
# terms are salted with gambling and betting sites keyword-stuffing the industry
# vocabulary: a probe for 机床 行业 市场 on 2026-09-13 returned "果博APP怎么样产业化
# 成果发布" and "库博体育怎么让制造业效益可见" in the top ten. Ranking them last is
# not enough, because on a thin topic last still means published.
# Chinese corporate marketing dressed as news. A dry run anchored a TNT post on
# "【广西峰会精彩回顾之孛辰篇】..." and cited "邦德激光以'颠覆性创新'持续领跑" - a
# conference recap and a vendor press release. Neither is an event; both read as
# an advert for someone else.
PROMO_MARKERS = [
    "精彩回顾", "持续领跑", "颠覆性创新", "重磅发布", "荣获", "斩获",
    "强势登陆", "圆满落幕", "闪耀", "引领行业", "赋能", "新范式",
    "award-winning", "proud to announce", "we are excited",
]


def is_promo(title: str) -> bool:
    """True for vendor marketing and conference recaps posing as news."""
    t = (title or "")
    return any(m in t for m in PROMO_MARKERS)


SPAM_MARKERS = [
    "果博", "库博", "太阳城", "威尼斯人", "百家乐", "娱乐城", "博彩", "彩票",
    "开户", "赌场", "下注", "投注", "澳门银河", "新葡京",
    "casino", "betting", "gambling", "포커", "바카라",
]


def is_spam(title: str, url: str = "") -> bool:
    """True for SEO junk dressed as industry news."""
    blob = f"{title} {url}".lower()
    return any(m.lower() in blob for m in SPAM_MARKERS)


# STATE OR PARTY-AFFILIATED OUTLETS (2026-09-13). Not banned - they carry real
# deal and industrial news the Western wires never run, which is why the Chinese
# search earns its place. But their FRAMING is official policy, and a post that
# repeats it unattributed is a European M&A advisory publishing Beijing's line
# under its own name.
#
# This list was written after noticing that the top-ranked Chinese source here
# was cnfin.com - 新华财经, Xinhua - and that a generated post had adopted
# "pan-securitisation erodes market logic" as its own analysis. That is a
# official formulation, not a finding.
#
# The rule is ATTRIBUTE, DON'T ASSERT. Independent and foreign outlets outrank
# these so they are the anchor when both are available.
STATE_AFFILIATED = {
    "cnfin.com": "Xinhua Finance (state news agency)",
    "xinhuanet.com": "Xinhua (state news agency)",
    "people.com.cn": "People's Daily (Communist Party organ)",
    "ifnews.com": "International Finance News (People's Daily group)",
    "gmw.cn": "Guangming Daily (Communist Party organ)",
    "chinanews.com": "China News Service (state-run)",
    "chinanews.com.cn": "China News Service (state-run)",
    "cctv.com": "CCTV (state broadcaster)",
    "globaltimes.cn": "Global Times (Communist Party group)",
    "chinadaily.com.cn": "China Daily (state)",
    "ce.cn": "Economic Daily (state)",
    "stcn.com": "Securities Times (People's Daily group)",
    "21jingji.com": "21st Century Business Herald (Nanfang, party group)",
    "yicai.com": "Yicai (Shanghai state-owned media group)",
    "cs.com.cn": "China Securities Journal (Xinhua group)",
    "sc.chinanews.com.cn": "China News Service (state-run)",
    # Found on a dry run 2026-09-14: both were being used as anchors unlabelled.
    "thepaper.cn": "The Paper 澎湃 (Shanghai state media group)",
    "cnindustry": "China Industry News (ministry-affiliated)",
    "cinn.cn": "China Industry News (ministry-affiliated)",
    "bjnews.com.cn": "Beijing News (Beijing municipal party group)",
    # Non-Chinese state outlets, same rule
    "rt.com": "RT (Russian state)",
    "sputniknews": "Sputnik (Russian state)",
    "presstv": "Press TV (Iranian state)",
}


def state_affiliation(url: str) -> Optional[str]:
    """How an outlet should be labelled, or None if it is independent."""
    u = (url or "").lower()
    for domain, label in STATE_AFFILIATED.items():
        if domain in u:
            return label
    return None


# Preferred news sources (not restricted, just prioritized)
PREFERRED_SOURCES = [
    "bloomberg.com",
    "reuters.com",
    "ft.com",
    "wsj.com",
    "scmp.com",
    "caixin.com",
    "economist.com",
    "cnbc.com",
    "techcrunch.com",
    "dealogic.com",
]


@dataclass
class NewsArticle:
    """Represents a news article with metadata."""

    title: str
    url: str
    source: str
    summary: str
    published_date: Optional[str] = None
    preview_image_url: Optional[str] = None
    language: str = "en"
    age_days: Optional[int] = None
    # Set from the URL, not guessed by the model: an outlet's ownership is a
    # fact about the source, and the post must attribute rather than assert.
    state_label: Optional[str] = None

    def to_context_string(self) -> str:
        """Format article for LLM context.

        The URL is deliberately NOT given to the model. It goes in the first
        comment after publishing instead (LinkedIn demotes posts carrying an
        outbound link in the body), and a model handed a URL pastes it.
        """
        date_str = f", {self.published_date}" if self.published_date else ""
        lang = " [Chinese-language source]" if self.language == "zh" else ""
        state = (f"\n  ⚠ OWNERSHIP: {self.state_label}. Its FRAMING is official "
                 f"policy, not a neutral finding - attribute anything it asserts "
                 f"to the outlet and give the other side of it."
                 if self.state_label else "")
        return (
            f"- HEADLINE: {self.title}\n"
            f"  OUTLET: {self.source}{date_str}{lang}{state}\n"
            f"  REPORTED: {self.summary}"
        )


_REL_DATE_RE = re.compile(
    r"(\d+)\s*(分钟|小时|天|周|个月|月|年|minute|hour|day|week|month|year)",
    re.IGNORECASE,
)
_REL_UNIT_DAYS = {
    "分钟": 0, "minute": 0, "小时": 0, "hour": 0,
    "天": 1, "day": 1, "周": 7, "week": 7,
    "个月": 30, "月": 30, "month": 30, "年": 365, "year": 365,
}


def parse_relative_age(text: str) -> Optional[int]:
    """Turn '4 天前' / '3 weeks ago' / '2026-09-09' into an age in days.

    SerpAPI returns news dates in the locale of the query, so a Chinese search
    comes back as '3 周前'. Without this every article looked undated and the
    recency filter could not run, which is how 4-month-old pieces were reaching
    the prompt alongside today's.
    """
    if not text:
        return None
    m = _REL_DATE_RE.search(text)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        return n * _REL_UNIT_DAYS.get(unit, 1)
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%b %d, %Y"):
        try:
            return max(0, (datetime.now() - datetime.strptime(text.strip()[:11].strip(), fmt)).days)
        except ValueError:
            continue
    return None


def resolve_publisher_url(url: str, timeout: int = 15) -> str:
    """Follow a Google grounding redirect to the real publisher URL.

    Grounded Gemini ALWAYS returns vertexaisearch.cloud.google.com redirect
    links, never the publisher's own. The old code recognised this and threw
    those articles away, which meant Gemini could never contribute a single
    usable result. They resolve fine — verified 2026-09-13 against mining.com,
    theguardian.com and brusselssignal.eu — so follow them instead.
    """
    if "vertexaisearch" not in url and "google.com/grounding" not in url:
        return url
    try:
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; SetaCapitalBot/1.0)"})
        return r.url or url
    except Exception as e:
        logger.debug(f"Could not resolve grounding redirect: {e}")
        return ""


# Once the plan is spent every further call is a wasted round trip and three
# lines of log noise per run. Latched per process, so the next run tries again -
# the quota resets monthly and a restart must not be needed to notice.
_SERPAPI_EXHAUSTED = False



# Provider errors quote the URL they failed on, and for these APIs the credential
# IS a query parameter. A 401 from Google Custom Search printed the whole key;
# under cron that line goes to logs/seta.log, so a single failed call would write
# a live credential to disk. Nothing had leaked yet only because Custom Search has
# no key configured and the SerpAPI quota ran out after the day's cron had already
# run - timing, not design.
_SECRET_PARAM = re.compile(
    r"((?:api_?key|key|token|access_token|password)=)([^&\s\"']{6,})", re.I
)


def _redact(text: object) -> str:
    """An exception message with any credential in it replaced."""
    return _SECRET_PARAM.sub(lambda m: f"{m.group(1)}<redacted:{len(m.group(2))} chars>",
                             str(text))

def search_news_serpapi(
    query: str, num_results: int = 8, lang: str = "zh", max_age: str = "qdr:m"
) -> List[dict]:
    """Google News via SerpAPI — the primary provider since 2026-09-13.

    Uses engine=google&tbm=nws (NOT engine=google_news) because only that form
    accepts `tbs=qdr:*`, and without a date restriction the results are mostly
    months old. `SERP_API_KEY` has been in /opt/linkedin/.env all along; the code
    was looking for `SERPER_API_KEY` (a different product) and so never used it.
    """
    global _SERPAPI_EXHAUSTED
    if _SERPAPI_EXHAUSTED:
        return []
    api_key = os.getenv("SERP_API_KEY")
    if not api_key:
        logger.warning("SERP_API_KEY not set, skipping SerpAPI search")
        return []
    base = os.getenv("SERP_API_BASE", "https://serpapi.com").rstrip("/")
    hl, gl = ("zh-cn", "cn") if lang == "zh" else ("en", "us")
    # SerpAPI reads time out intermittently - 3 of 12 queries in one probe on
    # 2026-09-13, and once it made the suite's LIVE provider check fail for no
    # real reason. A flaky test in the nightly gate is worse than no test: it
    # teaches whoever sees it to ignore a red run. One retry clears it, and it
    # also stops production silently losing a query's results.
    data = None
    for attempt in (1, 2):
        try:
            response = requests.get(
                f"{base}/search",
                params={"engine": "google", "tbm": "nws", "q": query, "tbs": max_age,
                        "hl": hl, "gl": gl, "num": num_results, "api_key": api_key},
                timeout=60,
            )
            response.raise_for_status()
            data = response.json()
            break
        except requests.Timeout:
            logger.info("SerpAPI timed out on '%s' (attempt %d) - retrying", query, attempt)
        except Exception as exc:
            # 429 means the monthly plan is spent. Latch it: every further call
            # this run is a wasted round trip and a log line that buries the one
            # message that matters. The flag is per process, so the next run
            # tries again - a monthly reset must not need a restart to be seen.
            if "429" in str(exc) or "Too Many Requests" in str(exc):
                _SERPAPI_EXHAUSTED = True
                globals()["_SERPAPI_EXHAUSTED"] = True
                logger.warning("SerpAPI plan exhausted (429) - skipping it for this run")
            else:
                logger.warning("SerpAPI news search failed for %r: %s", query, _redact(exc))
            return []
    if data is None:
        logger.warning("SerpAPI timed out twice on '%s' - giving up on this query", query)
        return []
    try:
        if data.get("error"):
            logger.warning(f"SerpAPI returned an error for '{query}': {data['error']}")
            return []
        out = []
        for n in data.get("news_results", []):
            link = n.get("link", "")
            if not link:
                continue
            raw_date = n.get("date", "") or ""
            out.append({
                "title": n.get("title", ""),
                "url": link,
                "snippet": n.get("snippet", "") or n.get("title", ""),
                "image": n.get("thumbnail", ""),
                "date": raw_date,
                "age_days": parse_relative_age(raw_date),
                "language": lang,
                # SerpAPI names the publisher in the locale of the query, which
                # is a better label than a domain guess when the domain is not
                # one we curate.
                "serp_source": (n.get("source") if isinstance(n.get("source"), str)
                                else (n.get("source") or {}).get("name", "")),
            })
        logger.info(f"SerpAPI[{lang}] found {len(out)} articles for: {query}")
        return out
    except Exception as e:
        logger.warning("SerpAPI news search failed for %r: %s", query, _redact(e))
        return []


def search_news_serper(query: str, num_results: int = 5) -> List[dict]:
    """Search news using Serper API (Google Search API)."""
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        logger.warning("SERPER_API_KEY not set, skipping Serper search")
        return []

    try:
        response = requests.post(
            "https://google.serper.dev/news",
            headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
            json={"q": query, "num": num_results},
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("news", [])
    except Exception as e:
        logger.error("Serper news search failed: %s", _redact(e))
        return []


def search_news_tavily(query: str, num_results: int = 5) -> List[dict]:
    """Search news using Tavily API."""
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set, skipping Tavily search")
        return []

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": api_key,
                "query": query,
                "search_depth": "advanced",
                "include_images": True,
                "max_results": num_results,
                "topic": "news",
            },
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("results", [])
    except Exception as e:
        logger.error("Tavily news search failed: %s", _redact(e))
        return []


def search_news_google_custom(query: str, num_results: int = 5) -> List[dict]:
    """Search news using Google Custom Search API."""
    api_key = (os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY")
               or os.getenv("GOOGLE_SEARCH_API_KEY"))
    # GOOGLE_CSE_ID has been in .credentials.env all along; only the API key was
    # ever missing. Accept either name so adding the key is the ONLY step needed.
    cx = os.getenv("GOOGLE_CUSTOM_SEARCH_CX") or os.getenv("GOOGLE_CSE_ID")
    if not api_key or not cx:
        logger.warning("Google Custom Search not configured, skipping")
        return []

    try:
        # Add dateRestrict for recent news (last 30 days)
        response = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": api_key,
                "cx": cx,
                "q": query,
                "num": num_results,
                "dateRestrict": "m1",  # Last month
                "sort": "date",
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json()
        return data.get("items", [])
    except Exception as e:
        logger.error("Google Custom Search failed: %s", _redact(e))
        return []




# A source is a PUBLICATION, not a hostname. DuckDuckGo (added 2026-09-16) has no
# publisher field, so its results carried netloc straight through and a post
# opened "Chejiahao.m.autohome.com.cn reported in 2026" - which reads like a
# broken link in the body, right where the rule says no link may appear. The link
# rule was never violated; the attribution just looked like it was.
_PUBLISHER_NAMES = {
    "yicai.com": "Yicai (第一财经)",
    "autohome.com.cn": "Autohome (汽车之家)",
    "sohu.com": "Sohu (搜狐)",
    "sina.com.cn": "Sina (新浪)",
    "sina.cn": "Sina (新浪)",
    "thepaper.cn": "The Paper (澎湃新闻)",
    "caixin.com": "Caixin (财新)",
    "eastmoney.com": "East Money (东方财富)",
    "stcn.com": "Securities Times (证券时报)",
    "cnstock.com": "China Securities Journal (中国证券报)",
    "21jingji.com": "21st Century Business Herald (21世纪经济报道)",
    "jiemian.com": "Jiemian (界面新闻)",
    "36kr.com": "36Kr (36氪)",
    "guancha.cn": "Guancha (观察者网)",
    "ofweek.com": "OFweek",
    "zhihu.com": "Zhihu (知乎)",
    "toutiao.com": "Toutiao (今日头条)",
    "nikkei.com": "Nikkei",
    "cn.nikkei.com": "Nikkei Chinese (日经中文网)",
    "reuters.com": "Reuters",
    "ft.com": "Financial Times",
    "bloomberg.com": "Bloomberg",
    "theguardian.com": "The Guardian",
    "dw.com": "Deutsche Welle",
    "scmp.com": "South China Morning Post",
    "mckinsey.com.cn": "McKinsey Greater China",
    "mckinsey.com": "McKinsey",
    "axios.com": "Axios",
    "handelsblatt.com": "Handelsblatt",
    "lesechos.fr": "Les Echos",
    "ilsole24ore.com": "Il Sole 24 Ore",
}

# Subdomains that carry no editorial identity and only make the name ugly.
_NOISE_SUBDOMAINS = {"www", "m", "mobile", "amp", "news", "cj", "k", "nev",
                     "finance", "auto", "chejiahao", "zhuanlan", "gongyi", "en"}


def publisher_name(url: str, given: str = "") -> str:
    """A human publication name for a source, never a bare hostname.

    `given` wins when a provider supplied a real name - only DuckDuckGo and the
    legacy providers fall through to deriving one from the host.
    """
    if given and not re.fullmatch(r"[a-z0-9.-]+\.[a-z.]{2,}", given.strip(), re.I):
        return given.strip()

    host = urlparse(url).netloc.lower().split(":")[0]
    parts = [x for x in host.split(".") if x]
    while parts and parts[0] in _NOISE_SUBDOMAINS:
        parts.pop(0)
    host = ".".join(parts)
    if host in _PUBLISHER_NAMES:
        return _PUBLISHER_NAMES[host]
    # Try progressively shorter suffixes: news.x.co.uk -> x.co.uk -> co.uk
    for i in range(len(parts)):
        cand = ".".join(parts[i:])
        if cand in _PUBLISHER_NAMES:
            return _PUBLISHER_NAMES[cand]
    # Fall back to the registrable name, capitalised: autohome.com.cn -> Autohome
    if parts:
        return parts[0].replace("-", " ").title()
    return given or "the press"

def search_news_duckduckgo(query: str, num_results: int = 8,
                           lang: str = "zh") -> List[dict]:
    """Free web search with NO API key and no monthly plan to run out.

    Added 2026-09-16, the day the SerpAPI free plan hit 0 and took the
    Chinese-language search with it. The answer to "is there nothing else free"
    is: this, and the Gemini grounding we already pay for. Everything else
    either needs a signed-up key (Brave 2k/mo, Tavily 1k/mo, Serper) or is gone
    (Bing's free tier retired).

    Google News RSS was the other candidate and looked ideal - 57 Chinese items
    for one query, no key, no quota - but since 2024 its links are opaque
    `news.google.com/rss/articles/CBMi...` tokens that resolve only through an
    undocumented batchexecute call. Verified here: following the redirect returns
    the token URL itself and the payload carries no publisher URL. A post whose
    citation points at news.google.com is not a real link, which is the entire
    point of citing one, so it is not used.

    DuckDuckGo's HTML endpoint returns the publisher URL directly. It is a WEB
    search, not a news index, so results skew towards forums and aggregators -
    the existing DEMOTED_SOURCES and is_spam() filters carry that weight, the
    same as for any other provider.
    """
    region = "cn-zh" if lang == "zh" else "us-en"
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={
                "q": query,
                "kl": region,
                "df": "m",          # past month, matching SerpAPI's tbs=qdr:m
            },
            headers={"User-Agent": _DDG_UA},
            timeout=25,
        )
        resp.raise_for_status()
    except Exception as e:
        logger.warning("DuckDuckGo search failed for %r: %s", query, str(e)[:120])
        return []

    results: List[dict] = []
    for m in _DDG_RESULT.finditer(resp.text):
        url = html_mod.unescape(m.group(1))
        title = html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(2))).strip()
        # DuckDuckGo wraps outbound links in its own redirector.
        if "/l/?uddg=" in url or "uddg=" in url:
            try:
                url = unquote(url.split("uddg=")[1].split("&")[0])
            except Exception:
                continue
        if not url.startswith("http") or not title:
            continue
        results.append({
            "title": title,
            "url": url,
            "source": publisher_name(url),
            "summary": "",
            "provider": "duckduckgo",
        })
        if len(results) >= num_results:
            break
    logger.info("DuckDuckGo returned %d results for %r", len(results), query[:40])
    return results


def search_reference_marginalia(query: str, num_results: int = 5,
                                timeout: int = 10) -> List[dict]:
    """Independent-web search. Reference material, NOT news.

    No key, no quota, no CAPTCHA - and deliberately kept OUT of the news chain.
    Measured 2026-09-16 before deciding where it belongs:

      "china europe acquisition"        -> StackExchange, Wikipedia
      "bearing grease lubrication"      -> brighthubengineering, Wikipedia
      "rolling element bearing fatigue" -> an academic fault-detection paper
      "collet runout machining"         -> TIMED OUT after 45s

    Marginalia's whole design is to demote commercial and SEO-optimised pages,
    which is most of the news web - so it surfaces good durable writing and
    almost no current events. It also has no Chinese coverage and no recency
    filter, the two things the Seta pillars need most. Using it for news would
    make posts worse, not better.

    Where it genuinely helps is TNT's technical pillars, where the subject is
    physics rather than this week. One query in three timed out, so the timeout
    is short and failure returns [] - this may never be load-bearing.
    """
    from urllib.parse import quote
    try:
        r = requests.get(
            "https://api.marginalia.nu/public/search/" + quote(query),
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        logger.info("Marginalia unavailable for %r (%s) - skipping",
                    query[:40], type(e).__name__)
        return []

    out: List[dict] = []
    for item in (data.get("results") or [])[:num_results]:
        url = item.get("url") or ""
        title = (item.get("title") or "").strip()
        if not url.startswith("http") or not title:
            continue
        out.append({
            "title": title,
            "url": url,
            "source": publisher_name(url),
            "summary": (item.get("description") or "")[:300],
            "provider": "marginalia",
        })
    logger.info("Marginalia returned %d reference results for %r",
                len(out), query[:40])
    return out

def search_news_gemini(query: str, num_results: int = 5) -> List[dict]:
    """Search news using Gemini with Google Search grounding."""
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger.warning("GOOGLE_API_KEY not set, skipping Gemini search")
        return []

    try:
        import json as json_module

        # Use Gemini with Google Search grounding
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"

        from datetime import datetime
        current_year = datetime.now().year
        current_month = datetime.now().strftime("%B %Y")

        prompt = f"""Search for recent news about: {query}

IMPORTANT: Today is {current_month}. Only return news from {current_year} (preferably last 3 months).
Do NOT return news from {current_year - 1} or earlier unless explicitly about {current_year} predictions.

Return ONLY a JSON array with the 3-5 most relevant recent news articles. Each article must have:
- "title": the article headline
- "url": the ACTUAL DIRECT article URL from the original publisher (e.g., https://www.reuters.com/..., https://www.ft.com/..., https://www.bloomberg.com/...)
  IMPORTANT: Do NOT use Google redirect URLs or vertexaisearch URLs. Only use the real publisher URL.
- "source": the publication name
- "snippet": a 1-2 sentence summary
- "date": publication date (MUST be from {current_year})

Focus on news from {current_year} from reputable sources like Bloomberg, Reuters, Financial Times, SCMP, etc.

Return ONLY valid JSON array, no other text."""

        payload = {
            "contents": [{"parts": [{"text": prompt}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {
                "temperature": 0.1,
                # 2000 was the whole budget INCLUDING gemini-2.5-flash's internal
                # thinking tokens, which consumed all of it: every response came
                # back finishReason=MAX_TOKENS, truncated mid-URL at ~190 chars,
                # and the JSON parse failed. That is why news search returned zero
                # articles on every run from 2026-08-20 to 2026-09-13 while the
                # cron, the sentinel and the health check all stayed green.
                # thinkingBudget=0 restores finishReason=STOP.
                "maxOutputTokens": 8192,
                "thinkingConfig": {"thinkingBudget": 0},
            }
        }

        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Extract the text response. Grounded replies are split across several
        # parts; reading only parts[0] silently truncated the JSON.
        try:
            candidate = data["candidates"][0]
            parts = candidate.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            logger.warning("Unexpected Gemini response structure")
            return []
        if candidate.get("finishReason") == "MAX_TOKENS":
            logger.warning("Gemini search hit MAX_TOKENS - result will be incomplete")
        if not text.strip():
            logger.warning("Gemini search returned no text")
            return []

        # Parse JSON from response
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        # Find JSON array in response
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1:
            text = text[start:end + 1]

        articles = json_module.loads(text)

        # Convert to standard format, filtering out Google redirect URLs
        results = []
        for article in articles[:num_results]:
            if isinstance(article, dict) and article.get("url"):
                url = article.get("url", "")
                # Grounded Gemini only ever emits vertexaisearch redirect links.
                # Resolve them to the publisher instead of discarding the article.
                if "vertexaisearch" in url or "google.com/grounding" in url:
                    url = resolve_publisher_url(url)
                    if not url:
                        logger.warning("Could not resolve a grounding redirect - dropping")
                        continue
                raw_date = article.get("date", "")
                results.append({
                    "title": article.get("title", ""),
                    "url": url,
                    "snippet": article.get("snippet", ""),
                    "date": raw_date,
                    "age_days": parse_relative_age(raw_date),
                    "source": article.get("source", ""),
                    "language": "en",
                })

        logger.info(f"Gemini search found {len(results)} valid articles for: {query}")
        return results

    except json_module.JSONDecodeError as e:
        logger.warning(f"Failed to parse Gemini search response as JSON (returning empty): {e}")
        return []
    except Exception as e:
        safe_msg = re.sub(r"[?&]key=[^&\s]+", "?key=REDACTED", str(e))
        logger.warning(f"Gemini search failed (returning empty): {safe_msg}")
        return []


def fetch_article_preview_image(url: str) -> Optional[str]:
    """Fetch the Open Graph preview image from an article URL."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; SetaCapitalBot/1.0)"
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()

        # Look for og:image meta tag
        content = response.text

        # Try og:image first
        og_match = re.search(r'<meta[^>]*property=["\']og:image["\'][^>]*content=["\']([^"\']+)["\']', content, re.IGNORECASE)
        if not og_match:
            og_match = re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*property=["\']og:image["\']', content, re.IGNORECASE)

        if og_match:
            return og_match.group(1)

        # Try twitter:image as fallback
        twitter_match = re.search(r'<meta[^>]*name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)["\']', content, re.IGNORECASE)
        if twitter_match:
            return twitter_match.group(1)

        return None
    except Exception as e:
        logger.debug(f"Failed to fetch preview image from {url}: {e}")
        return None


def extract_source_name(url: str) -> str:
    """Extract a clean source name from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        # Remove www. prefix
        if domain.startswith("www."):
            domain = domain[4:]
        # Map common domains to readable names
        source_names = {
            "bloomberg.com": "Bloomberg",
            "reuters.com": "Reuters",
            "ft.com": "Financial Times",
            "wsj.com": "Wall Street Journal",
            "scmp.com": "South China Morning Post",
            "caixin.com": "Caixin",
            "economist.com": "The Economist",
            "cnbc.com": "CNBC",
            "techcrunch.com": "TechCrunch",
            "nytimes.com": "New York Times",
            "theguardian.com": "The Guardian",
            "bbc.com": "BBC",
            "cnn.com": "CNN",
            # Chinese outlets: name them as a Chinese reader would recognise
            # them, with the English gloss, since the post itself is in English.
            "cnfin.com": "Xinhua Finance (新华财经)",
            "caixin.com": "Caixin (财新)",
            "yicai.com": "Yicai (第一财经)",
            "nbd.com.cn": "National Business Daily (每日经济新闻)",
            "21jingji.com": "21st Century Business Herald (21世纪经济报道)",
            "cn.nikkei.com": "Nikkei Chinese (日经中文网)",
            "stcn.com": "Securities Times (证券时报)",
            "cls.cn": "Cailianshe (财联社)",
            "jiemian.com": "Jiemian (界面新闻)",
            "chnfund.com": "China Fund News (中国基金报)",
            "cnfol.com": "CnFol (中金在线)",
            "sina.com.cn": "Sina Finance (新浪财经)",
            "dw.com": "Deutsche Welle",
            "rfi.fr": "RFI",
        }
        if domain in source_names:
            return source_names[domain]
        # Match on the registrable suffix: the live feed returns auto.cnfol.com
        # and finance.sina.com.cn, which an exact lookup missed entirely and
        # labelled "Auto" and "Finance" in the post.
        for known, label in source_names.items():
            if domain.endswith("." + known):
                return label
        parts = [x for x in domain.split(".") if x not in ("www", "m", "cn", "com", "net", "org")]
        return parts[0].title() if parts else "News Source"
    except Exception:
        return "News Source"


def _best_source_label(url: str, result: dict) -> str:
    """Curated bilingual name first, then the publisher name the feed gave us."""
    label = extract_source_name(url)
    fallback = (result.get("serp_source") or result.get("source") or "").strip()
    # extract_source_name returns a Title-cased domain stem when it knows nothing;
    # in that case the feed's own label is better ("每日经济新闻" beats "Nbd").
    if fallback and "(" not in label and label.lower() == label.split()[0].lower():
        known = any(ch in label for ch in "（(") or label in (
            "Bloomberg", "Reuters", "Financial Times", "Wall Street Journal",
            "South China Morning Post", "Caixin", "The Economist", "CNBC",
            "TechCrunch", "New York Times", "The Guardian", "BBC", "CNN",
            "Deutsche Welle", "RFI",
        )
        if not known:
            return fallback
    return label


# Equity-market stories: an IPO, a placement, a results call. For an M&A advisor
# these ARE the subject. For a company selling bearings and toolholders they are
# a dead end - there is no technical connection to draw, so the model invents a
# decorative one. That is exactly what happened on 2026-09-13: a post opened on
# Luoyang Bearing Group's Shenzhen listing and then pivoted to a canned
# "cheapest bearing is the most expensive" argument. The listing had nothing to
# do with the argument; the news was wallpaper.
# Latin terms need word boundaries so "stake" does not match "mistake".
_FINANCE_LATIN_RE = re.compile(
    r"\b(listing|listed|flotation|placement|share sale|stake|"
    r"earnings|results|quarterly|half[- ]year|revenue (?:rose|fell)|"
    r"raises?|raised|fundrais\w*|valuation|shareholder|dividend|"
    r"stock exchange|bourse)\b",
    re.IGNORECASE,
)

# Substring markers: CJK has no word boundaries, and "IPO" is unambiguous enough
# to match anywhere. BOTH were needed - the boundary form silently failed on
# "IPO周报" (O followed by a CJK word character is not a boundary), so a post
# opened on "four Chinese bearing manufacturers are applying for IPOs this week"
# AFTER the finance filter was supposedly in place. Second time the same CJK
# boundary assumption broke a guard; assume it is wrong everywhere.
_FINANCE_SUBSTRINGS = (
    "ipo", "上市", "挂牌", "募资", "融资", "定增", "增发", "业绩", "财报",
    "季报", "年报", "股份", "股价", "投资者", "新股", "申购", "营收", "净利",
    "涨停", "跌停", "市值", "招股",
)


def is_corporate_finance(title: str) -> bool:
    """True when the story is about the money, not the engineering."""
    t = (title or "").lower()
    if any(m in t for m in _FINANCE_SUBSTRINGS):
        return True
    return bool(_FINANCE_LATIN_RE.search(title or ""))


def _is_chinese(text: str) -> bool:
    """Any CJK ideograph means this query belongs to the Chinese search."""
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _rank_key(result: dict):
    """Chinese preferred outlets, then Western preferred, then the rest, then wires."""
    url = (result.get("url") or "").lower()
    age = result.get("age_days") if result.get("age_days") is not None else 999
    title = result.get("title") or ""
    if any(src in url for src in DEMOTED_SOURCES) or _REPORT_MILL_RE.search(title):
        return (4, 0, age)
    for i, src in enumerate(PREFERRED_TRADE):
        if src in url:
            return (1, len(PREFERRED_SOURCES) + i, age)
    for i, src in enumerate(PREFERRED_SOURCES_ZH):
        if src in url:
            # State-affiliated Chinese outlets still rank above the Western
            # wires - they carry the deal flow - but BELOW the independent
            # Chinese and foreign-Chinese ones, so an independent account is the
            # anchor whenever one exists.
            return (0, i + (50 if state_affiliation(url) else 0), age)
    for i, src in enumerate(PREFERRED_SOURCES):
        if src in url:
            return (1, i, age)
    return (3, 0, age)



# ─── Do not write the same story twice ───────────────────────────────────────
# 13 Sep: "Chinese Firms to Control 4% of European Auto Output by 2030".
# 16 Sep: "European Car Factories Transfer to Chinese Ownership by 2030".
# Same figure, same year, same three source URLs byte for byte. The search had
# no memory: `seen_urls` inside search_news_for_pillar dedupes within ONE call
# and is thrown away after it, so an article that is still the top hit next week
# is served again as though it were new.
#
# The memory already exists - every saved post artefact records `news_urls`. So
# the record of what has been written about IS the post archive, and there is no
# second store to drift out of step with it.

_URL_IN_ARTEFACT = re.compile(r"https?://[^\s|]+")


def _norm_url(url: str) -> str:
    """Compare on host+path: tracking parameters must not disguise a repeat."""
    u = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return u.replace("https://", "").replace("http://", "").replace("www.", "").lower()


_STOP = {
    "the", "a", "an", "of", "for", "and", "to", "in", "on", "by", "with", "as",
    "at", "from", "into", "amid", "amidst", "its", "their", "this", "that",
    # House words that appear in half the archive and would make every pair of
    # posts look similar. "Navigating" alone opens five of them.
    "navigating", "europe", "european", "china", "chinese", "m&a", "ma", "deal",
    "deals", "cross-border", "crossborder", "2026", "capital", "seta",
    # Capitalised only because they open a sentence - not names.
    "these", "those", "this", "there", "they", "when", "while", "where",
    "what", "which", "such", "both", "many", "most", "some", "recent",
    "understanding", "higher", "lower", "german", "germany", "italy",
    "italian", "french", "france",
}


def _title_terms(text: str) -> set:
    words = re.findall(r"[\w&%]+", (text or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 2}


def recent_post_history(posts_dir, days: int = 45) -> dict:
    """URLs and headline terms used by saved posts in the last `days`.

    Reads the artefacts rather than a side file, so it can never disagree with
    what was actually written.
    """
    from pathlib import Path
    import json as _json
    from datetime import datetime, timedelta

    urls: set = set()
    headlines: list = []
    posts: list = []
    d = Path(posts_dir)
    if not d.is_dir():
        return {"urls": urls, "headlines": headlines, "posts": posts}

    cutoff = datetime.now() - timedelta(days=days)
    for f in sorted(d.glob("20*.json")):
        stamp = f.name[:8]
        try:
            when = datetime.strptime(stamp, "%Y%m%d")
        except ValueError:
            continue
        if when < cutoff:
            continue
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        # A post that was retracted is not a precedent to avoid repeating; it is
        # one we would rather not repeat at all, so its URLs stay excluded.
        for m in _URL_IN_ARTEFACT.finditer(str(data.get("news_urls", "") or "")):
            urls.add(_norm_url(m.group(0)))
        if data.get("headline"):
            headlines.append(str(data["headline"]))
            posts.append({"headline": str(data["headline"]),
                          "body": str(data.get("body", "") or "")})
    return {"urls": urls, "headlines": headlines, "posts": posts}


def repeats_recent_headline(headline: str, recent: Sequence[str],
                            threshold: float = 0.6) -> Optional[str]:
    """The recent headline this one is a retread of, if any.

    Jaccard overlap on meaningful terms. House vocabulary is stripped first,
    otherwise every Europe-China M&A headline looks like every other one.
    """
    terms = _title_terms(headline)
    if len(terms) < 3:
        return None
    for old in recent:
        old_terms = _title_terms(old)
        if len(old_terms) < 3:
            continue
        union = terms | old_terms
        if not union:
            continue
        if len(terms & old_terms) / len(union) >= threshold:
            return old
    return None


# Headline wording is a weak signal for a repeat. The 13 and 16 Sep posts told
# the SAME story - "Chinese Firms to Control 4% of European Auto Output by 2030"
# and "European Car Factories Transfer to Chinese Ownership by 2030" - and share
# one meaningful word between them. What they share is the FACTS: the figure, the
# year, the publication. So compare those.
_SIG_NUM = re.compile(r"\b\d+(?:[.,]\d+)?\s?%|\b(?:19|20)\d{2}\b|\b\d+(?:[.,]\d+)?\s?(?:billion|million|bn|m)\b", re.I)
_SIG_NAME = re.compile(r"\b[A-Z][\w&.-]+(?:\s+[A-Z][\w&.-]+)*")


def story_signature(headline: str, body: str) -> set:
    """The facts a post is built on: figures, years, and named entities.

    Only the opening of the body: that is where the source and the anchor fact
    are stated. Later paragraphs drift into generic commentary that every post
    in the pillar shares.
    """
    opening = (body or "").strip().split("\n\n")[0]
    text = f"{headline} {opening}"
    sig = {m.group(0).replace(" ", "").lower() for m in _SIG_NUM.finditer(text)}
    for m in _SIG_NAME.finditer(text):
        token = m.group(0).strip().lower()
        if token in _STOP or len(token) < 4:
            continue
        sig.add(token)
    return sig


def _is_distinctive_figure(token: str) -> bool:
    """A figure that identifies a STORY, not one every post happens to carry.

    The current year fails this: "2026" appears in nearly every headline, and
    counting it let two unrelated posts about German and Italian industry look
    like retellings of each other. A percentage, a quantity, or a year that is
    not this one is specific enough to matter.
    """
    from datetime import datetime
    t = token.replace(" ", "")
    if not _SIG_NUM.fullmatch(t):
        return False
    this_year = str(datetime.now().year)
    if t in (this_year, str(int(this_year) - 1)):
        return False
    return True


def repeats_recent_story(headline: str, body: str, recent: Sequence[dict],
                         threshold: float = 0.5) -> Optional[str]:
    """The recent post this one retells, if any.

    `recent` is the list of {'headline','body'} from recent_post_history.
    """
    sig = story_signature(headline, body)
    if len(sig) < 3:
        return None
    for old in recent:
        old_sig = story_signature(old.get("headline", ""), old.get("body", ""))
        if len(old_sig) < 3:
            continue
        shared = sig & old_sig
        if not shared:
            continue
        # Containment, not Jaccard. Two tellings of one story rarely share
        # phrasing, so the union is dominated by each post's own wording: the
        # 13/16 Sep pair shared 4%, 2030 and Nikkei Chinese and still scored
        # only 0.36 against the union. What matters is how much of the SMALLER
        # signature is accounted for.
        containment = len(shared) / min(len(sig), len(old_sig))
        # And it must be the same FACTS, not just the same nouns: a shared
        # figure or year is what distinguishes retelling one story from two
        # posts that happen to discuss the same industry.
        shares_a_figure = any(_is_distinctive_figure(x) for x in shared)
        if containment >= threshold and shares_a_figure:
            return old.get("headline", "")
    return None


# ─── Do not sit on one theme ────────────────────────────────────────────────
# Story-level de-duplication stopped the same ARTICLE being written twice. It did
# not stop three posts in five days on Chinese capital buying European auto parts
# (13, 16 and 17 Sep) - different articles, different facts, same subject. That is
# the "they always say the same things" complaint one level up.
#
# STEER rather than BLOCK, deliberately: on a quiet news week a hard rule would
# mean no post rather than a slightly familiar one, and a missed slot is worse
# than a second piece on a genuinely live theme. So a recently-worked theme sends
# its query to the BACK of the rotation; it is only used if nothing fresher is
# found.

# Coarse subject buckets. Deliberately broad - the aim is "not autos again this
# week", not a taxonomy.
_THEMES = {
    "auto": ("汽车", "零部件", "车企", "auto", "automotive", "car ", "vehicle",
             "supplier", "parts maker"),
    "energy": ("新能源", "电池", "光伏", "battery", "solar", "renewable",
               "energy", "hydrogen"),
    "machinery": ("机械", "机床", "装备", "machinery", "machine tool",
                  "industrial equipment", "robotics", "automation"),
    "chemicals": ("化工", "材料", "chemical", "materials", "polymer", "coating"),
    "pharma": ("医药", "医疗", "pharma", "medical", "biotech", "device"),
    "food": ("食品", "农业", "food", "beverage", "agri"),
    "luxury": ("奢侈品", "时尚", "luxury", "fashion", "leather", "brand"),
    "policy": ("监管", "审查", "政策", "regulation", "screening", "tariff",
               "antitrust", "foreign subsid"),
    "macro": ("汇率", "利率", "gdp", "exchange rate", "yield", "inflation"),
}


def post_theme(text: str) -> Optional[str]:
    """The coarse subject of a post, or None if it fits no bucket."""
    low = (text or "").lower()
    best, best_hits = None, 0
    for theme, markers in _THEMES.items():
        hits = sum(1 for m in markers if m in low)
        if hits > best_hits:
            best, best_hits = theme, hits
    return best


def recent_themes(posts: Sequence[dict], limit: int = 4) -> List[str]:
    """Themes of the last `limit` posts, most recent first."""
    out: List[str] = []
    for post in list(posts)[-limit:][::-1]:
        theme = post_theme(f"{post.get('headline','')} {post.get('body','')}")
        if theme:
            out.append(theme)
    return out


def demote_worked_themes(queries: Sequence[str], recent: Sequence[str]) -> List[str]:
    """Reorder queries so recently-worked themes are tried last.

    Nothing is removed. A theme the pillar is built on must stay reachable, or a
    quiet week produces no post at all.
    """
    if not recent:
        return list(queries)
    tired = set(recent)
    fresh = [q for q in queries if post_theme(q) not in tired]
    stale = [q for q in queries if post_theme(q) in tired]
    if fresh and stale:
        logger.info("Theme rotation: %s worked recently - trying %d other "
                    "quer%s first", ", ".join(sorted(tired)), len(fresh),
                    "y" if len(fresh) == 1 else "ies")
    return fresh + stale

def search_news_for_pillar(
    pillar_name: str,
    num_articles: int = 3,
    fetch_images: bool = True,
    queries: Optional[List[str]] = None,
    avoid_finance: bool = False,
    avoid_companies: Optional[Sequence[str]] = None,
    exclude_urls: Optional[set] = None,
    recent_themes_used: Optional[Sequence[str]] = None,
) -> List[NewsArticle]:
    """Find recent, specific news for a content pillar.

    PROVIDER ORDER, and why each sits where it does (settled 2026-09-16, after
    the SerpAPI free plan ran out and forced the question):

      1. SerpAPI, Chinese then English - MAIN WHEN IT HAS QUOTA.
         The only provider that is a true news index AND searches natively in
         Chinese (hl=zh-cn, gl=cn) AND takes a real recency restriction
         (tbs=qdr:m). Best result per query, so it goes first. Latches off after
         a 429 rather than burning a round trip per query.

      2. DuckDuckGo HTML - MAIN IN PRACTICE TODAY. No key, no quota, no daily
         cap, and it returns the publisher URL directly with no redirect to
         decode. Chinese queries reach Chinese sources. It is a WEB index rather
         than a news one, so more forums and aggregators come back and the spam
         and demotion filters do more work - but it costs nothing and cannot run
         out, which is why it sits above the paid options.

      3. Google Custom Search - DORMANT until GOOGLE_SEARCH_API_KEY exists.
         GOOGLE_CSE_ID is already configured. Free but capped at 100 queries a
         day, so it ranks below the uncapped provider and above the billed one.

      4. Gemini with Google Search grounding - LAST RESORT ONLY. It works well
         and handles Chinese properly, but every call spends tokens on a billed
         key. It fires only when the free providers found NOTHING - not merely
         fewer articles than we wanted, because paying to top up a thin result
         is how a last resort becomes the default.

    DELIBERATELY NOT IN THIS CHAIN:
      - Google News RSS. Free, no key, and by far the richest (57 Chinese items
        for one query) - but since 2024 its links are opaque
        news.google.com/rss/articles/CBMi... tokens. Verified here: following the
        redirect returns the token URL, and the payload contains no publisher
        URL. A citation pointing at news.google.com is not a real link, and real
        links are the point.
      - Marginalia. Measured and kept for TNT's technical pillars instead - see
        search_reference_marginalia(). It demotes commercial pages by design,
        which is most of the news web, has no Chinese coverage and no recency
        filter.
      - Bing. Its free tier was retired; the endpoint returns 401.
      - Brave / Tavily / Serper. Real free tiers, but all need a signed-up key,
        so none is available today.
    """
    # A pillar's OWN topics win over the name-keyed table (2026-09-13). The table
    # is hardcoded China-Europe M&A, so without this every Bolla tenant that cloned
    # the Seta template searched cross-border M&A news whatever its business was.
    # Queries are used as written, in whatever language the company gave them:
    # Chinese ones go to the Chinese search, the rest to the English one.
    if queries:
        zh_queries = [q for q in queries if _is_chinese(q)]
        en_queries = [q for q in queries if not _is_chinese(q)]
    else:
        zh_queries = PILLAR_SEARCH_QUERIES_ZH.get(pillar_name, [])
        en_queries = PILLAR_SEARCH_QUERIES.get(pillar_name, [])
    if not zh_queries and not en_queries:
        logger.warning(f"No search queries defined for pillar: {pillar_name}")
        return []

    # Push a theme worked in the last few posts to the back of each rotation.
    zh_queries = demote_worked_themes(zh_queries, recent_themes_used or [])
    en_queries = demote_worked_themes(en_queries, recent_themes_used or [])

    all_results: List[dict] = []
    seen_urls = set()
    seen_titles = set()
    # Articles this brand has already written about. seen_urls only dedupes
    # WITHIN one call; without this an article that is still the top hit next
    # week comes back as though it were new, which is how the same Nikkei piece
    # produced two posts three days apart (13 and 16 Sep 2026).
    already_used = {_norm_url(u) for u in (exclude_urls or set())}

    def absorb(results: List[dict]) -> None:
        for r in results:
            url = r.get("url", "")
            title = (r.get("title") or "").strip().lower()
            if not url or url in seen_urls or (title and title in seen_titles):
                continue
            if _norm_url(url) in already_used:
                logger.info("Skipping an article already written about: %s", url[:80])
                continue
            title_raw = r.get("title", "") or ""
            if is_spam(title_raw, url):
                logger.info("Dropping SEO spam result: %s", title_raw[:60])
                continue
            # A technical brand cannot build a post on an IPO, and a post built on
            # a competitor's news advertises the competitor. Both are dropped at
            # SELECTION, not argued with in the prompt: an article that should not
            # be the anchor should never be offered as one.
            if is_promo(title_raw):
                logger.info("Dropping vendor marketing / conference recap: %s",
                            title_raw[:60])
                continue
            if avoid_finance and is_corporate_finance(title_raw):
                logger.info("Dropping equity-market story (no technical hook): %s",
                            title_raw[:60])
                continue
            if avoid_companies and any(
                (c in title_raw) if any("\u4e00" <= ch <= "\u9fff" for ch in c)
                else re.search(r"(?<!\w)" + re.escape(c) + r"(?!\w)", title_raw, re.I)
                for c in avoid_companies
            ):
                logger.info("Dropping story about a competitor: %s", title_raw[:60])
                continue
            age = r.get("age_days")
            if age is not None and age > MAX_ARTICLE_AGE_DAYS:
                continue
            seen_urls.add(url)
            if title:
                seen_titles.add(title)
            all_results.append(r)

    # 1. Chinese first — this is where the Europe-China deal flow actually breaks.
    #    For a tenant with no Chinese topics this loop is simply empty and English
    #    carries the pillar, which is the right behaviour for a company whose
    #    market is not China.
    for query in zh_queries[:2]:
        absorb(search_news_serpapi(query, num_results=8, lang="zh"))
        if len(all_results) >= num_articles * 2:
            break

    # 2. English, to widen the pool (and to carry the pillar if Chinese was thin).
    if len(all_results) < num_articles * 2:
        for query in en_queries[:2]:
            absorb(search_news_serpapi(query, num_results=8, lang="en"))
            if len(all_results) >= num_articles * 2:
                break

    # 3. DuckDuckGo: free, keyless, and nothing to run out. Ahead of the other
    #    free options because it has no daily cap at all and returns the
    #    publisher URL directly, with no redirect to decode.
    if len(all_results) < num_articles * 2:
        for query in (zh_queries[:2] + en_queries[:1]):
            absorb(search_news_duckduckgo(
                query, num_results=8, lang="zh" if _is_chinese(query) else "en"))
            if len(all_results) >= num_articles * 2:
                break

    # 4. Google Custom Search, once a key for it exists. Free but capped at 100
    #    queries a day, so it goes after the uncapped one and before the billed
    #    one.
    if len(all_results) < num_articles * 2:
        for query in (zh_queries + en_queries)[:2]:
            absorb(search_news_google_custom(query, num_results=8))
            if len(all_results) >= num_articles * 2:
                break

    # 5. Gemini with Google Search grounding - LAST RESORT (Tom, 2026-09-16).
    #    It works well and searches Chinese properly, but every call spends
    #    tokens on the billed Gemini key, and everything above is free. So it
    #    runs only when the free providers between them could not find a single
    #    usable article, and then on ONE query rather than three.
    #
    #    Note the condition: `not all_results`, not "fewer than we wanted". A
    #    thin result from a free provider is still a result; paying to top it up
    #    is how a last resort quietly becomes the default.
    if not all_results:
        logger.info("No free provider returned an article - falling back to "
                    "grounded Gemini (billed)")
        for query in (zh_queries + en_queries)[:1]:
            absorb(search_news_gemini(query, num_results=5))

    # Theme rotation acts HERE, on results, not on queries. The first cut
    # reordered the pillar's queries - useless, because this pillar's queries
    # (中国 企业 收购 欧洲) carry no theme at all: the auto slant came from what
    # the search returned, not from what was asked. A fresher subject therefore
    # has to win at ranking time.
    #
    # Still a demotion, never a filter: if every article this week is about autos
    # then an auto post is the honest post, and a missed slot would be worse.
    _tired = set(recent_themes_used or [])
    if _tired:
        _before = [r.get("title", "")[:40] for r in all_results[:2]]
        all_results.sort(
            key=lambda r: (post_theme(f"{r.get('title','')} {r.get('summary','')}") in _tired,)
                          + tuple(_rank_key(r))
        )
        _after = [r.get("title", "")[:40] for r in all_results[:2]]
        if _before != _after:
            logger.info("Theme rotation: %s worked recently - promoted a different "
                        "subject to the top", ", ".join(sorted(_tired)))
    else:
        all_results.sort(key=_rank_key)

    articles: List[NewsArticle] = []
    for r in all_results[:num_articles]:
        url = r.get("url", "")
        preview_image = r.get("image", "")
        if fetch_images and not preview_image:
            preview_image = fetch_article_preview_image(url)
        age = r.get("age_days")
        published = r.get("date", "") or ""
        if age is not None:
            # Give the model an absolute date; "3 周前" means nothing in a post.
            published = (datetime.now() - timedelta(days=age)).strftime("%-d %B %Y")
        articles.append(NewsArticle(
            title=r.get("title", "Untitled"),
            url=url,
            source=_best_source_label(url, r),
            summary=(r.get("snippet", "") or "")[:400],
            published_date=published,
            preview_image_url=preview_image,
            language=r.get("language", "en"),
            age_days=age,
            state_label=state_affiliation(url),
        ))

    logger.info(
        "Found %d news articles for pillar '%s' (%d Chinese-language)",
        len(articles), pillar_name, sum(1 for a in articles if a.language == "zh"),
    )
    return articles


def providers_reachable() -> bool:
    """Is the news plumbing working AT ALL, regardless of any one topic?

    Needed to tell two very different situations apart (2026-09-13). A pillar
    that returns nothing might mean the providers are broken - the three-week
    outage - or it might simply mean nobody wrote about that subject this week.
    A live probe on 2026-09-13 found "collet chuck tooling" and "刀柄 夹头 加工"
    return ZERO results in any language, while "bearing manufacturer industry"
    returns 18: some product niches genuinely have no press. Raising NEWS_OUTAGE
    for a narrow topic would cry wolf until the alarm was ignored, which is
    exactly how the real outage survived three weeks.
    """
    try:
        return bool(search_news_serpapi("manufacturing industry", 3, lang="en"))
    except Exception:
        return False



def serpapi_quota() -> dict:
    """SerpAPI searches remaining this month.

    Added 2026-09-16 after the free plan's 250 searches ran out and every
    SerpAPI call started returning 429. Nothing noticed: the chain silently fell
    through to Gemini Google Search grounding, which still returns articles, so
    the pipeline looked healthy while the Chinese-first search - the whole reason
    SerpAPI is first in the chain - was gone.

    That is the same shape as the three-week outage this module was rewritten
    for: a provider failing quietly behind a fallback that covers for it. A
    fallback that hides an outage is not redundancy, it is a blindfold.
    """
    key = os.getenv("SERP_API_KEY")
    if not key:
        return {"configured": False}
    base = os.getenv("SERP_API_BASE", "https://serpapi.com").rstrip("/")
    try:
        r = requests.get(f"{base}/account", params={"api_key": key}, timeout=20)
        r.raise_for_status()
        d = r.json()
        left = d.get("total_searches_left", d.get("plan_searches_left"))
        return {
            "configured": True,
            "plan": d.get("plan_name"),
            "left": left,
            "used_this_month": d.get("this_month_usage"),
            "exhausted": isinstance(left, int) and left <= 0,
        }
    except Exception as e:                      # network or auth problem
        return {"configured": True, "error": str(e)[:160]}

def provider_health(timeout_query: str = "中国 企业 收购 欧洲") -> dict:
    """Live check that the news providers actually return articles.

    Exists because nothing ever verified this: the only test was that
    news_search.py was present on disk, so a total provider outage ran for three
    weeks (2026-08-20 → 2026-09-13) while every post published on schedule with
    no news in it at all. Called by the LinkedIn test suite.
    """
    health = {"serpapi_quota": serpapi_quota()}
    try:
        health["serpapi_zh"] = len(search_news_serpapi(timeout_query, 5, lang="zh"))
    except Exception as e:
        health["serpapi_zh"] = f"ERROR: {e}"
    try:
        health["serpapi_en"] = len(
            search_news_serpapi("Chinese acquisition European manufacturer", 5, lang="en")
        )
    except Exception as e:
        health["serpapi_en"] = f"ERROR: {e}"
    try:
        health["duckduckgo"] = len(search_news_duckduckgo(timeout_query, 5, lang="zh"))
    except Exception as e:
        health["duckduckgo"] = f"ERROR: {e}"
    try:
        health["gemini"] = len(search_news_gemini("China Europe M&A acquisition", 3))
    except Exception as e:
        health["gemini"] = f"ERROR: {e}"
    return health


def build_news_context(articles: List[NewsArticle]) -> str:
    """Build the news block for the LLM prompt.

    Deliberately gives the model NO URLs. The link is published as the first
    comment instead (LinkedIn suppresses reach on posts with an outbound link in
    the body), and a model handed a URL will paste it into the body every time.
    """
    if not articles:
        return ""

    lines = [
        "REAL NEWS FETCHED TODAY — the post MUST be built on this, not on general themes:",
        "",
    ]
    for article in articles:
        lines.append(article.to_context_string())
    lines += [
        "",
        "HOW TO USE IT (non-negotiable):",
        "1. Anchor the post on ONE specific development above — name the companies,",
        "   the sector and what actually happened. Not a theme, an event.",
        "2. Attribute it in the body: the outlet by name and when it was reported",
        "   (e.g. 'Nikkei Chinese reported on 9 September'). Never 'recent reports'.",
        "3. Carry over at least one CONCRETE figure from the reporting above —",
        "   a percentage, a deal value, a count, a date. Invent nothing.",
        "4. Then add what the reporting does NOT say: the operator's read on what",
        "   this means for a European owner or a Chinese buyer in the next 12 months.",
        "   That second half is the whole value of the post.",
        "5. Do NOT paste any URL. The link is published separately as the first",
        "   comment; a URL in the body suppresses the post's reach.",
    ]
    return "\n".join(lines)


__all__ = [
    "NewsArticle",
    "search_news_for_pillar",
    "search_news_serpapi",
    "search_news_gemini",
    "resolve_publisher_url",
    "parse_relative_age",
    "provider_health",
    "providers_reachable",
    "is_spam",
    "is_corporate_finance",
    "is_promo",
    "PROMO_MARKERS",
    "SPAM_MARKERS",
    "build_news_context",
    "fetch_article_preview_image",
    "PILLAR_SEARCH_QUERIES",
    "PILLAR_SEARCH_QUERIES_ZH",
    "_is_chinese",
    "PREFERRED_SOURCES_ZH",
    "DEMOTED_SOURCES",
    "STATE_AFFILIATED",
    "state_affiliation",
    "PREFERRED_TRADE",
    "MAX_ARTICLE_AGE_DAYS",
]
