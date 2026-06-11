"""News search module for fetching current news for LinkedIn posts."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

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

    def to_context_string(self) -> str:
        """Format article for LLM context."""
        date_str = f" ({self.published_date})" if self.published_date else ""
        return f"- {self.title}{date_str}\n  Source: {self.source}\n  URL: {self.url}\n  Summary: {self.summary}"


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
        logger.error(f"Serper news search failed: {e}")
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
        logger.error(f"Tavily news search failed: {e}")
        return []


def search_news_google_custom(query: str, num_results: int = 5) -> List[dict]:
    """Search news using Google Custom Search API."""
    api_key = os.getenv("GOOGLE_CUSTOM_SEARCH_API_KEY")
    cx = os.getenv("GOOGLE_CUSTOM_SEARCH_CX")
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
        logger.error(f"Google Custom Search failed: {e}")
        return []


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
                "maxOutputTokens": 2000,
            }
        }

        response = requests.post(url, json=payload, timeout=30)
        response.raise_for_status()
        data = response.json()

        # Extract the text response
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError):
            logger.warning("Unexpected Gemini response structure")
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
                # Skip Google redirect URLs - they expire and don't work
                if "vertexaisearch" in url or "google.com/grounding" in url:
                    logger.warning(f"Skipping Google redirect URL: {url[:50]}...")
                    continue
                results.append({
                    "title": article.get("title", ""),
                    "url": url,
                    "snippet": article.get("snippet", ""),
                    "date": article.get("date", ""),
                    "source": article.get("source", ""),
                })

        logger.info(f"Gemini search found {len(results)} valid articles for: {query}")
        return results

    except json_module.JSONDecodeError as e:
        logger.warning(f"Failed to parse Gemini search response as JSON (returning empty): {e}")
        return []
    except Exception as e:
        logger.warning(f"Gemini search failed (returning empty): {e}")
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
        }
        return source_names.get(domain, domain.split(".")[0].title())
    except Exception:
        return "News Source"


def search_news_for_pillar(
    pillar_name: str,
    num_articles: int = 3,
    fetch_images: bool = True,
) -> List[NewsArticle]:
    """
    Search for recent news articles relevant to a content pillar.

    Args:
        pillar_name: Name of the content pillar
        num_articles: Number of articles to return
        fetch_images: Whether to fetch preview images from articles

    Returns:
        List of NewsArticle objects
    """
    queries = PILLAR_SEARCH_QUERIES.get(pillar_name, [])
    if not queries:
        logger.warning(f"No search queries defined for pillar: {pillar_name}")
        return []

    all_results = []
    seen_urls = set()

    # Try each search provider in order of preference
    for query in queries[:2]:  # Use first 2 queries to get variety
        # Try Gemini with Google Search first (uses existing GOOGLE_API_KEY)
        results = search_news_gemini(query, num_results=5)
        if results:
            for r in results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("snippet", ""),
                        "image": "",
                        "date": r.get("date", ""),
                    })
            continue

        # Fallback to Tavily
        results = search_news_tavily(query, num_results=5)
        if results:
            for r in results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("content", r.get("snippet", "")),
                        "image": r.get("image", ""),
                        "date": r.get("published_date", ""),
                    })
            continue

        # Fallback to Serper
        results = search_news_serper(query, num_results=5)
        if results:
            for r in results:
                url = r.get("link", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    all_results.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "snippet": r.get("snippet", ""),
                        "image": r.get("imageUrl", ""),
                        "date": r.get("date", ""),
                    })
            continue

        # Fallback to Google Custom Search
        results = search_news_google_custom(query, num_results=5)
        for r in results:
            url = r.get("link", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                all_results.append({
                    "title": r.get("title", ""),
                    "url": url,
                    "snippet": r.get("snippet", ""),
                    "image": r.get("pagemap", {}).get("cse_image", [{}])[0].get("src", ""),
                    "date": "",
                })

    # Sort by preferred sources
    def source_priority(result):
        url = result.get("url", "").lower()
        for i, source in enumerate(PREFERRED_SOURCES):
            if source in url:
                return i
        return len(PREFERRED_SOURCES)

    all_results.sort(key=source_priority)

    # Convert to NewsArticle objects
    articles = []
    for r in all_results[:num_articles]:
        url = r.get("url", "")
        preview_image = r.get("image", "")

        # Try to fetch preview image if not available and requested
        if fetch_images and not preview_image:
            preview_image = fetch_article_preview_image(url)

        articles.append(NewsArticle(
            title=r.get("title", "Untitled"),
            url=url,
            source=extract_source_name(url),
            summary=r.get("snippet", "")[:300],  # Limit summary length
            published_date=r.get("date", ""),
            preview_image_url=preview_image,
        ))

    logger.info(f"Found {len(articles)} news articles for pillar '{pillar_name}'")
    return articles


def build_news_context(articles: List[NewsArticle]) -> str:
    """Build a context string from news articles for LLM prompt."""
    if not articles:
        return ""

    lines = ["Recent relevant news to reference in your post:"]
    for article in articles:
        lines.append(article.to_context_string())
    lines.append("")
    lines.append("IMPORTANT: Include at least one full URL from the above articles in your post.")
    lines.append("Use the actual news to make your post timely and relevant.")

    return "\n".join(lines)


__all__ = [
    "NewsArticle",
    "search_news_for_pillar",
    "build_news_context",
    "fetch_article_preview_image",
    "PILLAR_SEARCH_QUERIES",
]
