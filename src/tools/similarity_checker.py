"""Similarity checker using TF-IDF and embeddings."""
from typing import Dict, List, Tuple

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from utils.openai_client import OpenAIClient
from utils.logger import get_logger
from utils.config_loader import load_config
from archive.content_archive import ContentArchive

logger = get_logger(__name__)


class SimilarityChecker:
    """Computes pairwise similarity using TF-IDF and embeddings."""

    def __init__(self):
        self.config = load_config("brightspot_guide")
        self.openai = OpenAIClient()
        self.archive = ContentArchive()
        
        # Thresholds from requirements
        self.tfidf_threshold = 0.25
        self.embedding_threshold = 0.82

    async def check_pairwise(
        self,
        articles: List[Dict]
    ) -> Dict:
        """Compute pairwise similarity across all articles.

        Args:
            articles: List of {market, html_content} dicts

        Returns:
            Dict: Similarity report with pairs, metrics, and pass/fail status
        """
        n = len(articles)
        if n < 2:
            return {
                "status": "passed",
                "pairs": [],
                "failing_markets": []
            }

        # Extract text and metadata
        markets = [a["market"] for a in articles]
        texts = [self._extract_text(a["html_content"]) for a in articles]
        
        # Compute TF-IDF similarity
        tfidf_matrix = self._compute_tfidf_similarity(texts)
        
        # Compute embedding similarity
        embedding_matrix = await self._compute_embedding_similarity(texts)
        
        # Build pairwise results
        pairs = []
        failing_markets = set()
        
        for i in range(n):
            for j in range(i + 1, n):
                tfidf_sim = tfidf_matrix[i][j]
                embed_sim = embedding_matrix[i][j]
                
                tfidf_pass = tfidf_sim <= self.tfidf_threshold
                embed_pass = embed_sim <= self.embedding_threshold
                pass_gate = tfidf_pass and embed_pass
                
                if not pass_gate:
                    failing_markets.add(markets[i])
                    failing_markets.add(markets[j])
                
                pairs.append({
                    "market_a": markets[i],
                    "market_b": markets[j],
                    "tfidf": round(tfidf_sim, 4),
                    "embedding": round(embed_sim, 4),
                    "pass": pass_gate
                })
        
        # Determine overall status
        all_pass = all(p["pass"] for p in pairs)
        status = "passed" if all_pass else "manual_review_required"
        
        return {
            "status": status,
            "pairs": pairs,
            "failing_markets": sorted(list(failing_markets)),
            "thresholds": {
                "tfidf": self.tfidf_threshold,
                "embedding": self.embedding_threshold
            }
        }

    def _extract_text(self, html_content: str) -> str:
        """Extract plain text from HTML for similarity checking.

        Args:
            html_content: HTML content

        Returns:
            str: Extracted text
        """
        try:
            from bs4 import BeautifulSoup
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Remove script, style, and code elements
            for element in soup(['script', 'style', 'code', 'noscript']):
                element.decompose()
            
            # Get text
            text = soup.get_text(separator=' ')
            
            # Clean up whitespace
            text = ' '.join(text.split())
            
            return text
            
        except Exception as e:
            logger.warning(f"Failed to extract text from HTML: {e}")
            return html_content

    def _compute_tfidf_similarity(
        self,
        texts: List[str]
    ) -> np.ndarray:
        """Compute TF-IDF cosine similarity matrix.

        Args:
            texts: List of text strings

        Returns:
            np.ndarray: Similarity matrix
        """
        # Configure vectorizer for Canadian content
        # Include French stop words for Quebec market
        vectorizer = TfidfVectorizer(
            stop_words='english',
            ngram_range=(1, 2),  # Use unigrams and bigrams
            min_df=1,
            max_df=0.9
        )
        
        try:
            tfidf_matrix = vectorizer.fit_transform(texts)
            similarity_matrix = cosine_similarity(tfidf_matrix)
            
            return similarity_matrix
            
        except Exception as e:
            logger.error(f"Failed to compute TF-IDF similarity: {e}")
            # Return identity matrix on error (no similarity)
            n = len(texts)
            return np.eye(n)

    async def _compute_embedding_similarity(
        self,
        texts: List[str]
    ) -> np.ndarray:
        """Compute embedding similarity matrix.

        Args:
            texts: List of text strings

        Returns:
            np.ndarray: Similarity matrix
        """
        embeddings = []
        
        for text in texts:
            # Truncate text to avoid token limits (keep ~2000 tokens)
            # Rough estimate: 1 token ≈ 4 characters
            truncated = text[:8000] if len(text) > 8000 else text
            
            try:
                embedding = await self.openai.generate_embedding(truncated)
                embeddings.append(embedding)
                
            except Exception as e:
                logger.error(f"Failed to generate embedding: {e}")
                # Use zero vector on error
                embeddings.append([0.0] * 1536)
        
        # Convert to numpy array
        embeddings_array = np.array(embeddings)
        
        # Compute cosine similarity
        norms = np.linalg.norm(embeddings_array, axis=1, keepdims=True)
        normalized_embeddings = embeddings_array / (norms + 1e-10)
        similarity_matrix = np.dot(normalized_embeddings, normalized_embeddings.T)
        
        return similarity_matrix

    def get_text_similarity(self, text1: str, text2: str) -> Dict:
        """Compute similarity between two texts.

        Args:
            text1: First text
            text2: Second text

        Returns:
            Dict: Similarity metrics
        """
        # TF-IDF similarity
        vectorizer = TfidfVectorizer(stop_words='english', ngram_range=(1, 2))
        tfidf_matrix = vectorizer.fit_transform([text1, text2])
        tfidf_sim = cosine_similarity(tfidf_matrix[0:1], tfidf_matrix[1:2])[0][0]
        
        return {
            "tfidf_cosine": round(tfidf_sim, 4),
            "tfidf_pass": tfidf_sim <= self.tfidf_threshold
        }
    
    async def check_against_archive(
        self,
        article: Dict,
        market: str,
        days_back: int = 180
    ) -> Dict:
        """Check article similarity against archived articles for the same market.
        
        This prevents duplicate content across different runs by comparing new articles
        against previously published articles from the archive.
        
        Args:
            article: Article dict with html_content and metadata
            market: Market key (e.g., 'oakville')
            days_back: Number of days to look back in archive (default 180 = 6 months)
            
        Returns:
            Dict: Similarity report with status, matches, and failing status
        """
        html_content = article.get("html_content", "")
        article_text = self._extract_text(html_content)
        
        # Get archived articles for this market
        archived_articles = self.archive.get_articles_for_market(market, days_back=days_back)
        
        if not archived_articles:
            logger.info(f"No archived articles found for {market} (last {days_back} days)")
            return {
                "status": "passed",
                "matches": [],
                "failing_matches": [],
                "archive_checked": True,
                "archived_count": 0
            }
        
        logger.info(f"Checking {market} article against {len(archived_articles)} archived articles")
        
        # P1 Fix: Extract full text from archived articles' HTML content
        archived_texts = []
        archived_metadata = []
        
        for archived in archived_articles:
            # Use full HTML content if available, otherwise fall back to metadata
            archived_html = archived.get("html_content")
            if archived_html:
                archived_text = self._extract_text(archived_html)
            else:
                # Fallback: use title + keywords if HTML not available (backward compatibility)
                title = archived.get("title", "")
                primary_keyword = archived.get("primary_keyword", "")
                secondary_keywords = " ".join(archived.get("secondary_keywords", []))
                archived_text = f"{title} {primary_keyword} {secondary_keywords}"
                logger.warning(
                    f"Archived article {archived.get('title', '')[:50]} has no HTML content, "
                    f"using metadata fallback"
                )
            
            archived_texts.append(archived_text)
            archived_metadata.append(archived)
        
        # Add current article text for comparison
        all_texts = [article_text] + archived_texts
        
        # Compute TF-IDF similarity
        tfidf_matrix = self._compute_tfidf_similarity(all_texts)
        
        # Compute embedding similarity
        embedding_matrix = await self._compute_embedding_similarity(all_texts)
        
        # Compare current article (index 0) against all archived (indices 1+)
        matches = []
        failing_matches = []
        
        for i, archived in enumerate(archived_articles, start=1):
            tfidf_sim = tfidf_matrix[0][i]
            embed_sim = embedding_matrix[0][i]
            
            tfidf_pass = tfidf_sim <= self.tfidf_threshold
            embed_pass = embed_sim <= self.embedding_threshold
            pass_gate = tfidf_pass and embed_pass
            
            match = {
                "run_id": archived.get("run_id"),
                "title": archived.get("title"),
                "published_date": archived.get("published_date"),
                "week_theme": archived.get("week_theme"),
                "primary_keyword": archived.get("primary_keyword"),
                "tfidf": round(tfidf_sim, 4),
                "embedding": round(embed_sim, 4),
                "pass": pass_gate
            }
            
            matches.append(match)
            
            if not pass_gate:
                failing_matches.append(match)
                logger.warning(
                    f"High similarity with archived article: {archived.get('title', '')[:50]} "
                    f"(TF-IDF: {tfidf_sim:.3f}, Embedding: {embed_sim:.3f})"
                )
        
        # Determine overall status
        all_pass = len(failing_matches) == 0
        status = "passed" if all_pass else "manual_review_required"
        
        return {
            "status": status,
            "matches": matches,
            "failing_matches": failing_matches,
            "archive_checked": True,
            "archived_count": len(archived_articles),
            "thresholds": {
                "tfidf": self.tfidf_threshold,
                "embedding": self.embedding_threshold
            }
        }
