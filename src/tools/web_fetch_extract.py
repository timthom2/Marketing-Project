"""Web fetch and extract tool for gathering content from discovered sources."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
import trafilatura

from utils.config_loader import load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class SimpleWebCache:
    """Simple file-based cache for web content."""
    
    def __init__(self, ttl_hours: int = 168):
        self.cache_dir = Path(__file__).parent.parent.parent / ".cache" / "web_content"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)
    
    def _get_key(self, url: str) -> str:
        return hashlib.md5(url.encode()).hexdigest()
    
    def get(self, url: str) -> Optional[str]:
        cache_file = self.cache_dir / f"{self._get_key(url)}.json"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            cached_at = datetime.fromisoformat(data.get("cached_at", ""))
            if datetime.now() - cached_at > self.ttl:
                cache_file.unlink()
                return None
            return data.get("content")
        except Exception:
            return None
    
    def set(self, url: str, content: str):
        cache_file = self.cache_dir / f"{self._get_key(url)}.json"
        try:
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"url": url, "content": content, "cached_at": datetime.now().isoformat()}, f)
        except Exception:
            pass


class WebFetchExtract:
    """Fetches and extracts readable content from web pages."""

    def __init__(self):
        self.config = load_config("research_sources")
        self.source_policy = self.config.get("source_policy", {})
        self.max_content_length = self.source_policy.get("max_content_length", 15000)
        self.cache = SimpleWebCache(ttl_hours=self.source_policy.get("cache_ttl_hours", 168))

    async def fetch_and_extract_batch(
        self,
        sources: List[Dict]
    ) -> List[Dict]:
        """Fetch and extract content from multiple sources.

        Args:
            sources: List of source dicts with url, title, domain, trust_level

        Returns:
            List[Dict]: Sources with added 'content' field
        """
        logger.info(f"Fetching content from {len(sources)} sources...")

        tasks = [self._fetch_and_extract_single(source) for source in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out failed extractions
        extracted = []
        for source, result in zip(sources, results):
            if isinstance(result, Exception):
                logger.warning(f"Failed to extract {source['url']}: {result}")
                continue
            if result and result.get("content"):
                extracted.append(result)

        logger.info(f"Successfully extracted content from {len(extracted)} sources")
        return extracted

    async def _fetch_and_extract_single(self, source: Dict) -> Optional[Dict]:
        """Fetch and extract content from a single source.

        Args:
            source: Source dict with url, title, domain, trust_level

        Returns:
            Dict: Source with added 'content' field, or None if failed
        """
        url = source.get("url", "")

        # Check cache first
        cached = self.cache.get(url)
        if cached:
            logger.debug(f"Cache hit for {url[:50]}...")
            return {
                **source,
                "content": cached,
                "from_cache": True
            }

        # Fetch the page
        try:
            html = await self._fetch_page(url)
            if not html:
                return None

            # Extract readable content using trafilatura
            content = self._extract_content(html)
            if not content:
                logger.debug(f"No content extracted from {url[:50]}...")
                return None

            # Truncate if too long
            if len(content) > self.max_content_length:
                content = content[:self.max_content_length] + "..."

            # Cache the result
            self.cache.set(url, content)

            return {
                **source,
                "content": content,
                "from_cache": False
            }

        except Exception as e:
            logger.warning(f"Error processing {url[:50]}...: {e}")
            return None

    async def _fetch_page(self, url: str) -> Optional[str]:
        """Fetch a web page.

        Args:
            url: URL to fetch

        Returns:
            str: HTML content or None if failed
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.8,fr;q=0.7"
        }

        try:
            timeout = aiohttp.ClientTimeout(total=20)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=True) as response:
                    if response.status != 200:
                        logger.debug(f"HTTP {response.status} for {url[:50]}...")
                        return None

                    # Check content type
                    content_type = response.headers.get("Content-Type", "")
                    if "text/html" not in content_type and "application/xhtml" not in content_type:
                        logger.debug(f"Non-HTML content type for {url[:50]}...")
                        return None

                    html = await response.text()
                    return html

        except asyncio.TimeoutError:
            logger.debug(f"Timeout fetching {url[:50]}...")
            return None
        except aiohttp.ClientError as e:
            logger.debug(f"Client error fetching {url[:50]}...: {e}")
            return None

    def _extract_content(self, html: str) -> Optional[str]:
        """Extract readable content from HTML using trafilatura.

        Args:
            html: Raw HTML content

        Returns:
            str: Extracted text content or None if failed
        """
        try:
            # Use trafilatura for content extraction
            content = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                include_links=False,
                include_images=False,
                favor_precision=True,
                deduplicate=True
            )

            if content:
                # Clean up whitespace
                content = " ".join(content.split())

            return content

        except Exception as e:
            logger.debug(f"Trafilatura extraction error: {e}")
            return None
