"""SERP gap checker using DuckDuckGo results and heading comparison."""
import asyncio
import re
from datetime import datetime
from typing import Dict, List, Optional, Set

import aiohttp
from bs4 import BeautifulSoup
from ddgs import DDGS

from utils.config_loader import get_env_var
from utils.logger import get_logger

logger = get_logger(__name__)


class SerpGapChecker:
    """Check SERP headings against article headings to find gaps."""

    STOPWORDS: Set[str] = {
        "the", "and", "for", "with", "from", "that", "this", "your", "you", "are",
        "was", "were", "will", "can", "how", "what", "why", "when", "where", "who",
        "into", "onto", "about", "over", "under", "between", "after", "before",
        "guide", "tips", "best", "top", "local", "near", "info", "resources"
    }

    def __init__(self):
        self.enabled = get_env_var("SERP_GAP_CHECK", "true").strip().lower() in ("1", "true", "yes", "on")
        self.max_results = int(get_env_var("SERP_MAX_RESULTS", "5"))
        self.max_pages = int(get_env_var("SERP_MAX_PAGES", "3"))
        self.max_headings = int(get_env_var("SERP_MAX_HEADINGS", "8"))
        self.timeout_seconds = int(get_env_var("SERP_TIMEOUT_SECONDS", "12"))

    async def check(
        self,
        query: str,
        article_html: str,
        market_name: str,
    ) -> Optional[Dict]:
        if not self.enabled:
            return None

        if not query:
            return None

        results = self._search(query)
        if not results:
            return None

        results = results[: self.max_results]
        serp_headings = await self._collect_serp_headings(results[: self.max_pages])
        article_headings = self._extract_article_headings(article_html)
        missing = self._find_missing_topics(serp_headings, article_headings)
        coverage_ratio = self._coverage_ratio(serp_headings, missing)

        return {
            "market": market_name,
            "query": query,
            "checked_at": datetime.now().isoformat(),
            "top_results": [{"title": r["title"], "url": r["url"]} for r in results],
            "serp_headings": serp_headings,
            "article_headings": article_headings,
            "missing_topics": missing,
            "coverage_ratio": coverage_ratio,
        }

    def _search(self, query: str) -> List[Dict]:
        results: List[Dict] = []
        try:
            with DDGS() as ddgs:
                search_results = ddgs.text(
                    query,
                    region="ca-en",
                    safesearch="moderate",
                    max_results=self.max_results,
                )
                for r in search_results:
                    url = r.get("href") or ""
                    title = r.get("title") or ""
                    if not url:
                        continue
                    results.append({
                        "url": url,
                        "title": title,
                        "snippet": r.get("body", "") or "",
                    })
        except Exception as exc:
            logger.warning(f"SERP search failed for '{query}': {exc}")
        return results

    async def _collect_serp_headings(self, results: List[Dict]) -> List[str]:
        tasks = [self._fetch_headings(r["url"]) for r in results]
        headings_sets = await asyncio.gather(*tasks, return_exceptions=True)

        combined: List[str] = []
        for entry in headings_sets:
            if isinstance(entry, Exception):
                continue
            for heading in entry:
                if heading not in combined:
                    combined.append(heading)
            if len(combined) >= self.max_headings:
                break

        return combined[: self.max_headings]

    async def _fetch_headings(self, url: str) -> List[str]:
        html = await self._fetch_html(url)
        if not html:
            return []
        try:
            soup = BeautifulSoup(html, "html.parser")
            headings = []
            for tag in soup.find_all(["h1", "h2", "h3"]):
                text = tag.get_text(" ", strip=True)
                if not text or len(text) < 6:
                    continue
                cleaned = re.sub(r"\s+", " ", text).strip()
                headings.append(cleaned)
            return headings[: self.max_headings]
        except Exception:
            return []

    async def _fetch_html(self, url: str) -> Optional[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9",
        }
        try:
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=True) as response:
                    if response.status != 200:
                        return None
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        return None
                    return await response.text()
        except Exception:
            return None

    def _extract_article_headings(self, html: str) -> List[str]:
        headings: List[str] = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup.find_all(["h2", "h3"]):
                text = tag.get_text(" ", strip=True)
                if not text or len(text) < 6:
                    continue
                headings.append(re.sub(r"\s+", " ", text).strip())
        except Exception:
            return []
        return headings

    def _tokenize(self, text: str) -> Set[str]:
        tokens = re.findall(r"[a-z0-9]+", text.lower())
        return {t for t in tokens if t and t not in self.STOPWORDS and len(t) > 2}

    def _is_covered(self, serp_heading: str, article_token_sets: List[Set[str]]) -> bool:
        serp_tokens = self._tokenize(serp_heading)
        if not serp_tokens:
            return True
        required = 1 if len(serp_tokens) <= 2 else max(2, int(len(serp_tokens) * 0.5))
        for tokens in article_token_sets:
            if len(serp_tokens & tokens) >= required:
                return True
        return False

    def _find_missing_topics(self, serp_headings: List[str], article_headings: List[str]) -> List[str]:
        article_token_sets = [self._tokenize(h) for h in article_headings]
        missing = []
        for heading in serp_headings:
            if not self._is_covered(heading, article_token_sets):
                missing.append(heading)
        return missing

    def _coverage_ratio(self, serp_headings: List[str], missing: List[str]) -> float:
        total = len(serp_headings)
        if total == 0:
            return 1.0
        covered = max(0, total - len(missing))
        return round(covered / total, 2)
