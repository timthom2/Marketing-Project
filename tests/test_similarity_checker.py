"""Unit tests for Similarity Checker."""
import pytest

from src.tools.similarity_checker import SimilarityChecker


@pytest.mark.asyncio
class TestSimilarityChecker:
    """Test suite for similarity checking."""

    async def test_different_texts_low_similarity(self):
        """Test that different texts have low similarity."""
        checker = SimilarityChecker()
        similarity = checker.get_text_similarity(
            "Montreal home care services help seniors.",
            "Toronto elderly care programs."
        )

        assert similarity["tfidf_cosine"] < 0.3
        assert similarity["tfidf_pass"] is True

    async def test_identical_texts_high_similarity(self):
        """Test that identical texts have high similarity."""
        checker = SimilarityChecker()
        similarity = checker.get_text_similarity(
            "Montreal home care services help seniors age at home.",
            "Montreal home care services help seniors age at home."
        )

        assert similarity["tfidf_cosine"] > 0.9
        assert similarity["tfidf_pass"] is False

    async def test_similar_texts_moderate_similarity(self):
        """Test that similar texts have moderate similarity."""
        checker = SimilarityChecker()
        similarity = checker.get_text_similarity(
            "Montreal home care provides quality services.",
            "Montreal home care offers quality assistance."
        )

        assert similarity["tfidf_cosine"] > 0.5
        assert similarity["tfidf_cosine"] < 0.9

    async def test_pairwise_similarity_all_pass(self, sample_articles):
        """Test pairwise similarity check with all passing articles."""
        checker = SimilarityChecker()
        report = await checker.check_pairwise(sample_articles)

        assert report["status"] == "passed"
        assert len(report["pairs"]) > 0
        assert len(report["failing_markets"]) == 0

    async def test_pairwise_similarity_with_failures(self):
        """Test pairwise similarity check with failing articles."""
        checker = SimilarityChecker()

        # Create duplicate content
        duplicate_text = """<div class="blog-content-module"><h1>Test</h1>
<p>This is duplicate content that should trigger high similarity.</p>
<p>Another paragraph with similar text structure.</p></div>"""

        articles = [
            {"market": "market1", "html_content": duplicate_text},
            {"market": "market2", "html_content": duplicate_text},
            {"market": "market3", "html_content": "<div><h1>Different</h1><p>Unique content.</p></div>"}
        ]

        report = await checker.check_pairwise(articles)

        assert report["status"] == "manual_review_required"
        assert len(report["failing_markets"]) > 0
        assert "market1" in report["failing_markets"]
        assert "market2" in report["failing_markets"]

    async def test_thresholds_applied_correctly(self):
        """Test that similarity thresholds are applied correctly."""
        checker = SimilarityChecker()

        # Get thresholds
        assert checker.tfidf_threshold == 0.25
        assert checker.embedding_threshold == 0.82

    async def test_empty_article_list(self):
        """Test behavior with empty article list."""
        checker = SimilarityChecker()
        report = await checker.check_pairwise([])

        assert report["status"] == "passed"
        assert len(report["pairs"]) == 0

    async def test_single_article(self):
        """Test behavior with single article."""
        checker = SimilarityChecker()
        articles = [{"market": "test", "html_content": "<h1>Test</h1><p>Content.</p></div>"}]
        report = await checker.check_pairwise(articles)

        assert report["status"] == "passed"
        assert len(report["pairs"]) == 0

    async def test_pairwise_metrics_structure(self, sample_articles):
        """Test structure of pairwise metrics."""
        checker = SimilarityChecker()
        report = await checker.check_pairwise(sample_articles)

        assert "status" in report
        assert "pairs" in report
        assert "failing_markets" in report
        assert "thresholds" in report

        # Check pair structure
        for pair in report["pairs"]:
            assert "market_a" in pair
            assert "market_b" in pair
            assert "tfidf" in pair
            assert "embedding" in pair
            assert "pass" in pair
            assert isinstance(pair["tfidf"], float)
            assert isinstance(pair["embedding"], float)
            assert isinstance(pair["pass"], bool)

    async def test_embedding_similarity_computed(self, sample_articles):
        """Test that embedding similarity is computed."""
        checker = SimilarityChecker()
        report = await checker.check_pairwise(sample_articles)

        # Check that embedding scores are computed
        for pair in report["pairs"]:
            assert pair["embedding"] >= 0.0
            assert pair["embedding"] <= 1.0

    async def test_text_extraction_from_html(self):
        """Test text extraction from HTML."""
        checker = SimilarityChecker()
        html = """<div class="blog-content-module">
<style>.test { color: red; }</style>
<script>console.log('test');</script>
<h1>Title</h1>
<p>Content here.</p>
</div>"""

        text = checker._extract_text(html)

        # Should contain content but not style/script
        assert "Title" in text
        assert "Content here" in text
        assert "color: red" not in text
        assert "console.log" not in text
