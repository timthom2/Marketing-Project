"""Web cache for search results."""
import hashlib
import json
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta

from src.utils.logger import get_logger

logger = get_logger(__name__)


class WebCache:
    """Cache for web search results."""

    def __init__(self, cache_dir: Optional[Path] = None, ttl_hours: int = 24):
        """Initialize web cache.

        Args:
            cache_dir: Cache directory (default: .cache/web)
            ttl_hours: Time-to-live in hours
        """
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent.parent / ".cache" / "web"

        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)

    def _get_cache_key(self, query: str, domains: Optional[list] = None) -> str:
        """Generate cache key from query and domains.

        Args:
            query: Search query
            domains: Optional list of domains

        Returns:
            str: Cache key
        """
        key_str = query
        if domains:
            key_str += "|".join(sorted(domains))

        return hashlib.md5(key_str.encode()).hexdigest()

    def get(self, query: str, domains: Optional[list] = None) -> Optional[list]:
        """Get cached search results.

        Args:
            query: Search query
            domains: Optional list of domains

        Returns:
            Optional[list]: Cached results or None
        """
        cache_key = self._get_cache_key(query, domains)
        cache_file = self.cache_dir / f"{cache_key}.json"

        if not cache_file.exists():
            logger.debug(f"Web cache miss: {query[:50]}...")
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check TTL
            cached_at = datetime.fromisoformat(data.get("cached_at", ""))
            if datetime.now() - cached_at > self.ttl:
                logger.debug(f"Web cache expired: {query[:50]}...")
                cache_file.unlink()
                return None

            logger.debug(f"Web cache hit: {query[:50]}...")
            return data.get("results")

        except Exception as e:
            logger.warning(f"Failed to read web cache: {e}")
            return None

    def set(self, query: str, results: list, domains: Optional[list] = None):
        """Cache search results.

        Args:
            query: Search query
            results: Search results to cache
            domains: Optional list of domains
        """
        cache_key = self._get_cache_key(query, domains)
        cache_file = self.cache_dir / f"{cache_key}.json"

        try:
            data = {
                "query": query,
                "domains": domains,
                "cached_at": datetime.now().isoformat(),
                "results": results
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            logger.debug(f"Cached web results: {query[:50]}...")

        except Exception as e:
            logger.warning(f"Failed to cache web results: {e}")

    def clear(self):
        """Clear all cached web results."""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()

        logger.info("Cleared web cache")
