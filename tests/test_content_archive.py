"""Tests for Content Archive System."""
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from archive.content_archive import ContentArchive


@pytest.fixture
def temp_archive():
    """Create a temporary archive for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_archive.db"
        archive = ContentArchive(db_path=db_path)
        yield archive


def test_archive_article(temp_archive):
    """Test archiving an article."""
    temp_archive.archive_article(
        run_id="2026-01-09",
        market="oakville",
        market_name="Oakville",
        title="Test Article",
        primary_keyword="home care Oakville",
        secondary_keywords=["elderly care", "senior support"],
        week_theme="winter_safety",
        slug="test-article"
    )
    
    articles = temp_archive.get_articles_for_market("oakville", days_back=365)
    assert len(articles) == 1
    assert articles[0]["title"] == "Test Article"
    assert articles[0]["primary_keyword"] == "home care Oakville"
    assert articles[0]["week_theme"] == "winter_safety"


def test_get_recent_keywords(temp_archive):
    """Test retrieving recent keywords."""
    temp_archive.archive_article(
        run_id="2026-01-09",
        market="oakville",
        market_name="Oakville",
        title="Article 1",
        primary_keyword="home care Oakville",
        secondary_keywords=["elderly care"],
        week_theme="winter_safety",
        slug="article-1"
    )
    
    temp_archive.archive_article(
        run_id="2026-01-13",
        market="oakville",
        market_name="Oakville",
        title="Article 2",
        primary_keyword="Oakville senior care",
        secondary_keywords=["companion care"],
        week_theme="winter_isolation",
        slug="article-2"
    )
    
    keywords = temp_archive.get_recent_keywords("oakville", count=10, days_back=365)
    assert "home care Oakville" in keywords
    assert "Oakville senior care" in keywords
    assert "elderly care" in keywords
    assert "companion care" in keywords


def test_get_recent_themes(temp_archive):
    """Test retrieving recent themes."""
    temp_archive.archive_article(
        run_id="2026-01-09",
        market="oakville",
        market_name="Oakville",
        title="Article 1",
        primary_keyword="home care Oakville",
        secondary_keywords=[],
        week_theme="winter_safety",
        slug="article-1"
    )
    
    temp_archive.archive_article(
        run_id="2026-01-13",
        market="oakville",
        market_name="Oakville",
        title="Article 2",
        primary_keyword="Oakville senior care",
        secondary_keywords=[],
        week_theme="winter_isolation",
        slug="article-2"
    )
    
    themes = temp_archive.get_recent_themes("oakville", days_back=365)
    assert "winter_safety" in themes
    assert "winter_isolation" in themes


def test_has_recent_theme(temp_archive):
    """Test checking if theme was used recently."""
    temp_archive.archive_article(
        run_id="2026-01-09",
        market="oakville",
        market_name="Oakville",
        title="Article 1",
        primary_keyword="home care Oakville",
        secondary_keywords=[],
        week_theme="winter_safety",
        slug="article-1"
    )
    
    assert temp_archive.has_recent_theme("oakville", "winter_safety", days_back=365)
    assert not temp_archive.has_recent_theme("oakville", "summer_wellness", days_back=365)


def test_is_keyword_similar(temp_archive):
    """Test keyword similarity detection."""
    temp_archive.archive_article(
        run_id="2026-01-09",
        market="oakville",
        market_name="Oakville",
        title="Article 1",
        primary_keyword="Ontario home care Oakville",
        secondary_keywords=[],
        week_theme="winter_safety",
        slug="article-1"
    )
    
    # Similar keyword (3+ words overlap)
    assert temp_archive.is_keyword_similar_to_recent(
        "oakville",
        "home care Oakville winter",
        days_back=365
    )
    
    # Different keyword (less overlap)
    assert not temp_archive.is_keyword_similar_to_recent(
        "oakville",
        "dementia care services",
        days_back=365
    )


def test_archive_sources(temp_archive):
    """Test archiving source URLs."""
    temp_archive.archive_sources(
        run_id="2026-01-09",
        market="oakville",
        source_urls=[
            "https://ontario.ca/home-care",
            "https://halton.ca/seniors"
        ]
    )
    
    used_sources = temp_archive.get_used_sources("oakville", days_back=365)
    assert "https://ontario.ca/home-care" in used_sources
    assert "https://halton.ca/seniors" in used_sources

