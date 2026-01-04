"""Prompt cache for system prompts."""
import json
from pathlib import Path
from typing import Dict, Optional

from src.utils.logger import get_logger

logger = get_logger(__name__)


class PromptCache:
    """Cache for system prompts to reduce API costs."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize prompt cache.

        Args:
            cache_dir: Cache directory (default: .cache/)
        """
        if cache_dir is None:
            cache_dir = Path(__file__).parent.parent.parent / ".cache" / "prompts"

        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get(self, key: str) -> Optional[str]:
        """Get cached prompt.

        Args:
            key: Cache key

        Returns:
            Optional[str]: Cached prompt or None
        """
        cache_file = self.cache_dir / f"{key}.json"

        if cache_file.exists():
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                logger.debug(f"Cache hit for: {key}")
                return data.get("prompt")

            except Exception as e:
                logger.warning(f"Failed to read cache {key}: {e}")
                return None

        logger.debug(f"Cache miss for: {key}")
        return None

    def set(self, key: str, prompt: str):
        """Cache prompt.

        Args:
            key: Cache key
            prompt: Prompt to cache
        """
        cache_file = self.cache_dir / f"{key}.json"

        try:
            data = {
                "key": key,
                "prompt": prompt,
                "cached_at": str(Path(__file__).stat().st_mtime)
            }

            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)

            logger.debug(f"Cached prompt: {key}")

        except Exception as e:
            logger.warning(f"Failed to cache prompt {key}: {e}")

    def clear(self):
        """Clear all cached prompts."""
        for cache_file in self.cache_dir.glob("*.json"):
            cache_file.unlink()

        logger.info("Cleared prompt cache")
