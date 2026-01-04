"""Research cache for research packs."""
import json
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timedelta

from src.utils.logger import get_logger

logger = get_logger(__name__)


class ResearchCache:
    """Cache for research packs to reduce duplicate work."""

    def __init__(self, cache_dir: Optional[Path] = None, ttl_hours: int = 24):
        """Initialize research cache.

        Args:
            cache_dir: Cache directory (default: .cache/research)
            ttl_hours: Time-to-live in hours
        """
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent.parent / ".cache" / "research"

        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = timedelta(hours=ttl_hours)

    def get(self, market: str, primary_keyword: str) -> Optional[Dict]:
        """Get cached research pack.

        Args:
            market: Market name
            primary_keyword: Primary keyword

        Returns:
            Optional[Dict]: Cached research pack or None
        """
        cache_key = f"{market}_{primary_keyword}".replace(" ", "_").lower()
        cache_file = self.cache_dir / f"{cache_key}.json"

        if not cache_file.exists():
            logger.debug(f"Research cache miss: {cache_key}")
            return None

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Check TTL
            cached_at = datetime.fromisoformat(data.get("cached_at", ""))
            if datetime.now() - cached_at > self.ttl:
                logger.debug(f"Research cache expired: {cache_key}")
                cache_file.unlink()
                return None

            logger.debug(f"Research cache hit: {cache_key}")
            return data.get("research_pack")

        except Exception as e:
            logger.warning(f"Failed to read research cache {cache_key}: {e}")
            return None

    def set(self, market: str, primary_keyword: str, research_pack: Dict):
        """Cache research pack.

        Args:
            market: Market name
            primary_keyword: Primary keyword
            research_pack: Research pack to cache
        """
        cache_key = f"{market}_{primary_keyword}".replace(" ", "_").lower()
        cache_file = self.cache_dir / f"{cache_key}.json"

        try:
            data = {
                "market": market,
                "primary_keyword": primary_keyword,
                "cached_at": datetime.now().isoformat(),
                "research_pack": research_pack
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            logger.debug(f"Cached research pack: {cache_key}")

        except Exception as e:
            logger.warning(f"Failed to cache research pack {cache_key}: {e}")

    def clear(self):
        """Clear all cached research packs."""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()

        logger.info("Cleared research cache")
