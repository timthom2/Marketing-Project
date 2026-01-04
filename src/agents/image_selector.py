"""Image Selector Agent: Searches and selects best matching images for articles.

Note: This agent is now called sequentially by the coordinator (not in parallel)
after all articles are finalized. This eliminates race conditions and ensures
each market gets a unique image.
"""
import asyncio
import json
from pathlib import Path
from typing import Dict, List, Optional

from agents.base_agent import BaseAgent
from tools.pexels_client import PexelsClient
from utils.config_loader import load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class ImageSelectorAgent(BaseAgent):
    """Agent responsible for selecting the best matching image for articles.
    
    Called sequentially by coordinator after QA to ensure unique images per market.
    """

    # Class-level tracking of used image IDs to prevent duplicates across markets
    _used_image_ids: set = set()
    _used_images_file = Path("logs/used_images.json")

    def __init__(self):
        super().__init__()
        self.pexels_client = PexelsClient()
        self.model_config = load_config("model_routing")
        # Load persisted used images to avoid reuse across runs
        self._load_used_images()

    @classmethod
    def reset_used_images(cls):
        """Reset to persisted set (do not clear historical use across runs)."""
        cls._load_used_images()
        logger.info(f"Image deduplication tracker restored ({len(cls._used_image_ids)} historical IDs)")

    @classmethod
    def mark_image_used(cls, image_id: int):
        """Mark an image as used to prevent reuse across markets."""
        cls._used_image_ids.add(image_id)
        cls._save_used_images()

    @classmethod
    def is_image_used(cls, image_id: int) -> bool:
        """Check if an image has already been used."""
        return image_id in cls._used_image_ids

    @classmethod
    def _load_used_images(cls):
        """Load persisted used image IDs from disk."""
        try:
            if cls._used_images_file.exists():
                data = json.loads(cls._used_images_file.read_text())
                if isinstance(data, list):
                    cls._used_image_ids = set(int(i) for i in data if isinstance(i, (int, str)))
        except Exception as e:
            logger.warning(f"Failed to load used images list: {e}")

    @classmethod
    def _save_used_images(cls):
        """Persist used image IDs to disk."""
        try:
            cls._used_images_file.parent.mkdir(parents=True, exist_ok=True)
            cls._used_images_file.write_text(json.dumps(sorted(cls._used_image_ids)))
        except Exception as e:
            logger.warning(f"Failed to save used images list: {e}")

    async def run(
        self,
        article_data: Dict,
        research_pack: Dict,
        excluded_image_ids: Optional[List[int]] = None
    ) -> Dict:
        """Select the best matching image for an article.

        Args:
            article_data: Article content with title, sections, etc.
            research_pack: Research data including market, keywords, themes
            excluded_image_ids: Optional list of image IDs to exclude (already used)

        Returns:
            Dict: Selected image with url, credit, alt_text, relevance_score
        """
        market_name = research_pack.get("market_name", "")
        primary_keyword = research_pack.get("keywords", {}).get("primary", "")
        week_theme = research_pack.get("week_theme", "")
        
        self.log_info(f"Selecting image for {market_name} article...")

        # Build search queries based on article content
        search_queries = self._build_search_queries(article_data, research_pack)
        
        # Search for candidate images
        candidates = await self._search_candidates(search_queries)
        
        # Filter out already-used images
        if excluded_image_ids:
            original_count = len(candidates)
            candidates = [c for c in candidates if c.get("id") not in excluded_image_ids]
            if len(candidates) < original_count:
                self.log_info(f"Filtered out {original_count - len(candidates)} already-used images")
        
        # Also filter out class-level tracked used images
        original_count = len(candidates)
        candidates = [c for c in candidates if not self.is_image_used(c.get("id", 0))]
        if len(candidates) < original_count:
            self.log_info(f"Filtered out {original_count - len(candidates)} previously-used images")
        
        if not candidates:
            self.log_warning("No candidate images found after filtering. Using default fallback.")
            return self._get_fallback_image(market_name)

        self.log_info(f"Found {len(candidates)} candidate images. Analyzing with vision...")

        # Analyze each candidate with vision model
        scored_images = await self._analyze_candidates(candidates, article_data, research_pack)

        if not scored_images:
            self.log_warning("Vision analysis failed. Selecting first candidate.")
            fallback = self._format_selected_image(candidates[0], market_name, 5)
            # Mark fallback image as used
            if fallback.get("id"):
                self.mark_image_used(fallback["id"])
            return fallback

        # Sort by relevance score and select best
        scored_images.sort(key=lambda x: x["relevance_score"], reverse=True)
        best_image = scored_images[0]

        # Mark selected image as used to prevent reuse in other markets
        if best_image.get("id"):
            self.mark_image_used(best_image["id"])
            self.log_info(f"Marked image {best_image['id']} as used")

        self.log_info(
            f"✓ Selected image (score: {best_image['relevance_score']}/10): "
            f"{best_image['match_description'][:60]}..."
        )

        return best_image

    def _build_search_queries(self, article_data: Dict, research_pack: Dict) -> List[str]:
        """Build diverse search queries for Pexels based on article content.

        Args:
            article_data: Article data
            research_pack: Research pack

        Returns:
            List[str]: Search queries to try
        """
        queries = []

        # Primary query based on keyword and theme
        primary_keyword = research_pack.get("keywords", {}).get("primary", "")
        week_theme = research_pack.get("week_theme", "")
        market_name = research_pack.get("market_name", "")
        province = research_pack.get("province", "")
        
        # Diverse core queries - rotate based on market to get different results
        core_queries = [
            "elderly care home caregiver",
            "senior home care support",
            "caregiver helping elderly person",
            "nurse with elderly patient home",
            "grandmother grandfather family",
            "senior citizen happy home",
            "elderly woman smiling care",
            "elderly man help walking",
            "home healthcare worker senior",
            "retirement family support",
            "older adult independence living",
            "aging parent care assistance",
            "senior wellness happiness",
            "grandparent love family home"
        ]
        
        # Use market name hash to rotate which queries we try first
        market_hash = hash(market_name) % len(core_queries)
        rotated_queries = core_queries[market_hash:] + core_queries[:market_hash]
        queries.extend(rotated_queries[:4])
        
        # Market-specific query
        if market_name:
            queries.insert(0, f"{market_name} senior care family")
        
        # Province-specific fallback
        if province:
            queries.append(f"{province} elderly care home")
        
        # Theme-specific queries
        if week_theme:
            theme_lower = week_theme.lower()
            if "dementia" in theme_lower or "alzheimer" in theme_lower:
                queries.extend(["dementia care elderly support", "memory care senior"])
            elif "companion" in theme_lower:
                queries.extend(["elderly companionship happiness", "senior friendship joy"])
            elif "winter" in theme_lower or "safety" in theme_lower:
                queries.extend(["elderly safety home winter", "senior warm cozy"])
            elif "valentine" in theme_lower or "love" in theme_lower:
                queries.extend(["elderly couple love companionship", "senior romance"])
            elif "hospital" in theme_lower or "recovery" in theme_lower:
                queries.extend(["elderly recovery home care", "senior rehabilitation"])
            elif "palliat" in theme_lower or "end of life" in theme_lower:
                queries.extend(["compassionate elderly care comfort", "peaceful senior"])
            elif "new_year" in theme_lower or "planning" in theme_lower:
                queries.extend(["senior new year family", "elderly planning future"])
            elif "spring" in theme_lower or "outdoor" in theme_lower:
                queries.extend(["elderly outdoor garden", "senior spring sunshine"])
            elif "summer" in theme_lower or "heat" in theme_lower:
                queries.extend(["senior summer cooling", "elderly staying cool"])
            elif "fall" in theme_lower or "autumn" in theme_lower:
                queries.extend(["elderly fall prevention safety", "senior autumn"])
            elif "tax" in theme_lower or "financial" in theme_lower:
                queries.extend(["senior financial planning", "elderly paperwork help"])

        # Diverse fallback queries for variety
        fallback_queries = [
            "happy senior home",
            "elderly person smiling",
            "caring for aging parent",
            "senior citizen independence",
            "multigenerational family care",
            "older adult quality life"
        ]
        queries.extend(fallback_queries)

        return queries[:8]  # Return more queries for diversity

    async def _search_candidates(self, queries: List[str]) -> List[Dict]:
        """Search Pexels for candidate images.

        Args:
            queries: List of search queries

        Returns:
            List[Dict]: Unique candidate images
        """
        seen_ids = set()
        candidates = []
        
        # Also exclude already-used images from seen_ids upfront
        seen_ids.update(self._used_image_ids)

        for query in queries:
            try:
                results = await self.pexels_client.search_images(query, per_page=8)
                
                for img in results:
                    img_id = img.get("id")
                    if img_id and img_id not in seen_ids:
                        seen_ids.add(img_id)
                        candidates.append(img)

                # Continue searching to build larger pool
                if len(candidates) >= 15:
                    break

            except Exception as e:
                self.log_warning(f"Search failed for query '{query}': {e}")
                continue

        return candidates[:15]  # Max 15 candidates for better selection

    async def _analyze_candidates(
        self,
        candidates: List[Dict],
        article_data: Dict,
        research_pack: Dict
    ) -> List[Dict]:
        """Analyze candidate images with vision model.

        Args:
            candidates: List of candidate images
            article_data: Article data
            research_pack: Research pack

        Returns:
            List[Dict]: Images with relevance scores
        """
        # Get article context for vision prompt
        title = article_data.get("title", "")
        deck = ""
        main_topics = []
        
        for section in article_data.get("sections", []):
            if section.get("type") == "deck":
                deck = section.get("content", "")
            elif section.get("type") == "h2":
                main_topics.append(section.get("content", ""))

        primary_keyword = research_pack.get("keywords", {}).get("primary", "")
        market_name = research_pack.get("market_name", "")

        # Vision model config
        vision_config = self.model_config.get("models", {}).get("image_analysis", {})
        model = vision_config.get("model", "gpt-4o")
        max_tokens = vision_config.get("max_tokens", 300)
        temperature = vision_config.get("temperature", 0.3)

        scored_images = []

        # Analyze images in parallel (batch of 3 at a time to avoid rate limits)
        for i in range(0, len(candidates), 3):
            batch = candidates[i:i+3]
            
            tasks = [
                self._analyze_single_image(
                    img, title, deck, primary_keyword, main_topics, market_name,
                    model, max_tokens, temperature
                )
                for img in batch
            ]
            
            try:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                for result in results:
                    if isinstance(result, dict) and "relevance_score" in result:
                        scored_images.append(result)

            except Exception as e:
                self.log_error(f"Batch vision analysis failed: {e}")

        return scored_images

    async def _analyze_single_image(
        self,
        image: Dict,
        title: str,
        deck: str,
        primary_keyword: str,
        main_topics: List[str],
        market_name: str,
        model: str,
        max_tokens: int,
        temperature: float
    ) -> Dict:
        """Analyze a single image with vision model.

        Args:
            image: Image object from Pexels
            title: Article title
            deck: Article deck/subheadline
            primary_keyword: Primary keyword
            main_topics: List of H2 topics
            market_name: Market name
            model: Vision model to use
            max_tokens: Max tokens for response
            temperature: Sampling temperature

        Returns:
            Dict: Image with analysis results
        """
        image_url = image.get("url_medium", image.get("url", ""))
        
        prompt = f"""Analyze this image and determine how well it matches the article content.

ARTICLE CONTEXT:
Title: {title}
Deck: {deck}
Primary Theme: {primary_keyword}
Key Topics: {', '.join(main_topics[:3])}
Location: {market_name}, Canada

EVALUATION CRITERIA:
1. Does the image show relevant subject matter? (elderly care, home setting, caregiver, etc.)
2. Is the tone appropriate? (warm, professional, empathetic - not clinical, sterile, or sad)
3. Does it match the article's focus? (home care, companionship, support, independence)
4. Is it visually appealing and high quality?
5. Would it enhance the article for readers and feel welcoming?
6. Does it show positive, dignified representation of seniors?

IMPORTANT:
- Prefer images showing warm, positive interactions
- Avoid clinical/hospital settings unless article is about hospital-to-home transition
- Prefer home environments over institutional settings
- Look for natural, authentic moments

Return ONLY valid JSON:
{{
  "relevance_score": 8,
  "match_description": "Brief description of why image matches or doesn't match",
  "suitable": true,
  "concerns": []
}}"""

        try:
            result = await self.openai.analyze_image(
                image_url=image_url,
                prompt=prompt,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature
            )

            # Add image metadata to result
            return {
                **image,
                "relevance_score": result.get("relevance_score", 5),
                "match_description": result.get("match_description", ""),
                "suitable": result.get("suitable", True),
                "concerns": result.get("concerns", []),
                "credit": self.pexels_client.get_credit_html(image)
            }

        except Exception as e:
            self.log_warning(f"Vision analysis failed for image {image.get('id')}: {e}")
            # Return with default score on error
            return {
                **image,
                "relevance_score": 5,
                "match_description": "Vision analysis unavailable",
                "suitable": True,
                "concerns": ["Analysis failed"],
                "credit": self.pexels_client.get_credit_html(image)
            }

    def _format_selected_image(self, image: Dict, market_name: str, score: int = 5) -> Dict:
        """Format selected image with all required fields.

        Args:
            image: Image object
            market_name: Market name for alt text
            score: Relevance score

        Returns:
            Dict: Formatted image object
        """
        return {
            "id": image.get("id"),
            "url": image.get("url", ""),
            "url_large": image.get("url_large", image.get("url", "")),
            "url_medium": image.get("url_medium", ""),
            "photographer": image.get("photographer", "Unknown"),
            "photographer_url": image.get("photographer_url", ""),
            "alt": image.get("alt", f"Home care support in {market_name}"),
            "alt_text": image.get("alt", f"Home care support in {market_name}"),
            "credit": self.pexels_client.get_credit_html(image),
            "relevance_score": score,
            "match_description": "Default selection",
            "suitable": True,
            "concerns": []
        }

    def _get_fallback_image(self, market_name: str) -> Dict:
        """Get a fallback image when search/analysis fails.

        Args:
            market_name: Market name for alt text

        Returns:
            Dict: Fallback image object
        """
        # Find first unused fallback image
        for fallback in self.pexels_client.FALLBACK_IMAGES:
            if not self.is_image_used(fallback.get("id", 0)):
                self.mark_image_used(fallback["id"])
                self.log_info(f"Using fallback image {fallback['id']} for {market_name}")
                return self._format_selected_image(fallback, market_name, 5)
        
        # If all fallbacks used, use first one anyway (shouldn't happen with 8 fallbacks and 8 markets)
        self.log_warning("All fallback images already used, reusing first fallback")
        fallback = self.pexels_client.FALLBACK_IMAGES[0]
        return self._format_selected_image(fallback, market_name, 5)
