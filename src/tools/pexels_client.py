"""Pexels API client for image search."""
import os
from typing import Dict, List, Optional

import aiohttp

from utils.logger import get_logger
from utils.config_loader import get_env_var

logger = get_logger(__name__)


class PexelsClient:
    """Pexels API wrapper for searching royalty-free images."""

    BASE_URL = "https://api.pexels.com/v1"
    
    # Curated fallback images for elderly care themes
    FALLBACK_IMAGES = [
        {
            "id": 666335,
            "url": "https://images.pexels.com/photos/666335/pexels-photo-666335.jpeg",
            "url_large": "https://images.pexels.com/photos/666335/pexels-photo-666335.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/666335/pexels-photo-666335.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "Andrea Piacquadio",
            "photographer_url": "https://www.pexels.com/@olly",
            "alt": "Caregiver assisting elderly person"
        },
        {
            "id": 4226140,
            "url": "https://images.pexels.com/photos/4226140/pexels-photo-4226140.jpeg",
            "url_large": "https://images.pexels.com/photos/4226140/pexels-photo-4226140.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/4226140/pexels-photo-4226140.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "Cottonbro Studio",
            "photographer_url": "https://www.pexels.com/@cottonbro",
            "alt": "Happy elderly person receiving care"
        },
        {
            "id": 5412163,
            "url": "https://images.pexels.com/photos/5412163/pexels-photo-5412163.jpeg",
            "url_large": "https://images.pexels.com/photos/5412163/pexels-photo-5412163.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/5412163/pexels-photo-5412163.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "Kampus Production",
            "photographer_url": "https://www.pexels.com/@kampus",
            "alt": "Professional caregiver providing support"
        },
        {
            "id": 7551442,
            "url": "https://images.pexels.com/photos/7551442/pexels-photo-7551442.jpeg",
            "url_large": "https://images.pexels.com/photos/7551442/pexels-photo-7551442.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/7551442/pexels-photo-7551442.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "SHVETS production",
            "photographer_url": "https://www.pexels.com/@shvets-production",
            "alt": "Senior person with caregiver at home"
        },
        {
            "id": 7551617,
            "url": "https://images.pexels.com/photos/7551617/pexels-photo-7551617.jpeg",
            "url_large": "https://images.pexels.com/photos/7551617/pexels-photo-7551617.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/7551617/pexels-photo-7551617.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "SHVETS production",
            "photographer_url": "https://www.pexels.com/@shvets-production",
            "alt": "Elderly woman smiling with companion"
        },
        {
            "id": 3768131,
            "url": "https://images.pexels.com/photos/3768131/pexels-photo-3768131.jpeg",
            "url_large": "https://images.pexels.com/photos/3768131/pexels-photo-3768131.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/3768131/pexels-photo-3768131.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "Andrea Piacquadio",
            "photographer_url": "https://www.pexels.com/@olly",
            "alt": "Happy senior couple at home"
        },
        {
            "id": 6102008,
            "url": "https://images.pexels.com/photos/6102008/pexels-photo-6102008.jpeg",
            "url_large": "https://images.pexels.com/photos/6102008/pexels-photo-6102008.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/6102008/pexels-photo-6102008.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "RODNAE Productions",
            "photographer_url": "https://www.pexels.com/@rodnae-prod",
            "alt": "Nurse helping elderly patient"
        },
        {
            "id": 3768146,
            "url": "https://images.pexels.com/photos/3768146/pexels-photo-3768146.jpeg",
            "url_large": "https://images.pexels.com/photos/3768146/pexels-photo-3768146.jpeg?auto=compress&cs=tinysrgb&w=1260&h=750",
            "url_medium": "https://images.pexels.com/photos/3768146/pexels-photo-3768146.jpeg?auto=compress&cs=tinysrgb&w=640",
            "photographer": "Andrea Piacquadio",
            "photographer_url": "https://www.pexels.com/@olly",
            "alt": "Elderly man reading at home"
        }
    ]

    def __init__(self):
        self.api_key = get_env_var("PEXELS_API_KEY")
        if not self.api_key:
            logger.warning("PEXELS_API_KEY not set. Using fallback curated images.")

    async def search_images(
        self,
        query: str,
        per_page: int = 10,
        orientation: str = "landscape"
    ) -> List[Dict]:
        """Search for images on Pexels.

        Args:
            query: Search query (e.g., "elderly care", "senior home care")
            per_page: Number of results to return (max 80)
            orientation: Image orientation ("landscape", "portrait", "square")

        Returns:
            List[Dict]: List of image objects with url, photographer, alt, etc.
        """
        if not self.api_key:
            logger.info(f"Using fallback images for query: {query}")
            return self._get_fallback_images(query)

        try:
            headers = {
                "Authorization": self.api_key
            }
            params = {
                "query": query,
                "per_page": min(per_page, 80),
                "orientation": orientation
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/search",
                    headers=headers,
                    params=params
                ) as response:
                    if response.status != 200:
                        logger.error(f"Pexels API error: {response.status}")
                        return self._get_fallback_images(query)

                    data = await response.json()

            photos = data.get("photos", [])
            
            if not photos:
                logger.warning(f"No Pexels results for query: {query}. Using fallback.")
                return self._get_fallback_images(query)

            # Transform to consistent format
            results = []
            for photo in photos:
                results.append({
                    "id": photo["id"],
                    "url": photo["src"]["original"],
                    "url_large": photo["src"]["large"],
                    "url_medium": photo["src"]["medium"],
                    "photographer": photo["photographer"],
                    "photographer_url": photo["photographer_url"],
                    "alt": photo.get("alt", f"Photo by {photo['photographer']}")
                })

            logger.info(f"Found {len(results)} Pexels images for: {query}")
            return results

        except Exception as e:
            logger.error(f"Pexels API request failed: {e}")
            return self._get_fallback_images(query)

    def _get_fallback_images(self, query: str = "") -> List[Dict]:
        """Get fallback curated images.

        Args:
            query: Optional query to filter fallback images (not used currently)

        Returns:
            List[Dict]: Curated list of elderly care images
        """
        return self.FALLBACK_IMAGES.copy()

    async def get_curated(self, per_page: int = 10) -> List[Dict]:
        """Get curated photos from Pexels (editor's choice).

        Args:
            per_page: Number of results to return

        Returns:
            List[Dict]: List of curated image objects
        """
        if not self.api_key:
            return self._get_fallback_images()

        try:
            headers = {
                "Authorization": self.api_key
            }
            params = {
                "per_page": min(per_page, 80)
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.BASE_URL}/curated",
                    headers=headers,
                    params=params
                ) as response:
                    if response.status != 200:
                        logger.error(f"Pexels API error: {response.status}")
                        return self._get_fallback_images()

                    data = await response.json()

            photos = data.get("photos", [])
            
            results = []
            for photo in photos:
                results.append({
                    "id": photo["id"],
                    "url": photo["src"]["original"],
                    "url_large": photo["src"]["large"],
                    "url_medium": photo["src"]["medium"],
                    "photographer": photo["photographer"],
                    "photographer_url": photo["photographer_url"],
                    "alt": photo.get("alt", f"Photo by {photo['photographer']}")
                })

            return results

        except Exception as e:
            logger.error(f"Pexels curated request failed: {e}")
            return self._get_fallback_images()

    def get_credit_html(self, image: Dict) -> str:
        """Generate attribution HTML for a Pexels image.

        Args:
            image: Image object from search_images

        Returns:
            str: HTML credit line
        """
        photographer = image.get("photographer", "Unknown")
        photographer_url = image.get("photographer_url", "https://www.pexels.com")
        
        return (
            f'<a href="{photographer_url}" target="_blank" rel="noopener">'
            f'Photo by {photographer}</a> on '
            f'<a href="https://www.pexels.com" target="_blank" rel="noopener">Pexels</a>'
        )
