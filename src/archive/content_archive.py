"""Content Archive System - Tracks published articles to prevent duplicate content across runs."""
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from utils.config_loader import get_env_var
from utils.logger import get_logger

logger = get_logger(__name__)


class ContentArchive:
    """Manages archive of published articles for duplicate content prevention.
    
    Stores article metadata (market, title, keywords, themes, dates) in SQLite
    for fast queries and historical tracking.
    """
    
    def __init__(self, db_path: Optional[Path] = None):
        """Initialize content archive.
        
        Args:
            db_path: Optional path to SQLite database. Defaults to data/archive.db
        """
        if db_path is None:
            app_root = Path(__file__).parent.parent.parent
            archive_dir = app_root / "data" / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            db_path = archive_dir / "content_archive.db"
        
        self.db_path = db_path
        self._init_database()
    
    def _init_database(self) -> None:
        """Initialize SQLite database with required tables."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Articles table - stores published article metadata
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS articles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                market TEXT NOT NULL,
                market_name TEXT NOT NULL,
                title TEXT NOT NULL,
                primary_keyword TEXT NOT NULL,
                secondary_keywords TEXT,  -- JSON array
                week_theme TEXT,
                slug TEXT,
                published_date TEXT NOT NULL,  -- ISO format
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(run_id, market)
            )
        """)
        
        # Keywords table - tracks keyword usage per market
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS keywords (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                keyword TEXT NOT NULL,
                used_date TEXT NOT NULL,  -- ISO format
                run_id TEXT NOT NULL,
                UNIQUE(market, keyword, run_id)
            )
        """)
        
        # Themes table - tracks theme usage per market
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS themes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                week_theme TEXT NOT NULL,
                used_date TEXT NOT NULL,  -- ISO format
                run_id TEXT NOT NULL,
                UNIQUE(market, week_theme, run_id)
            )
        """)
        
        # Sources table - tracks research source URLs per market
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market TEXT NOT NULL,
                source_url TEXT NOT NULL,
                used_date TEXT NOT NULL,  -- ISO format
                run_id TEXT NOT NULL,
                UNIQUE(market, source_url, run_id)
            )
        """)
        
        # Create indexes for fast queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_market_date ON articles(market, published_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_articles_theme ON articles(market, week_theme)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_keywords_market_date ON keywords(market, used_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_themes_market_date ON themes(market, used_date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_sources_market_date ON sources(market, used_date)")
        
        conn.commit()
        conn.close()
        logger.info(f"Content archive database initialized at {self.db_path}")
    
    def archive_article(
        self,
        run_id: str,
        market: str,
        market_name: str,
        title: str,
        primary_keyword: str,
        secondary_keywords: List[str],
        week_theme: Optional[str],
        slug: Optional[str],
        published_date: Optional[str] = None
    ) -> None:
        """Archive a published article.
        
        Args:
            run_id: Run identifier (YYYY-MM-DD format)
            market: Market key (e.g., 'oakville')
            market_name: Market display name (e.g., 'Oakville')
            title: Article title
            primary_keyword: Primary SEO keyword
            secondary_keywords: List of secondary keywords
            week_theme: Week theme used (e.g., 'winter_safety')
            slug: URL slug
            published_date: Publication date (ISO format). Defaults to run_id
        """
        if published_date is None:
            published_date = run_id
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            # Insert article
            cursor.execute("""
                INSERT OR REPLACE INTO articles 
                (run_id, market, market_name, title, primary_keyword, secondary_keywords, 
                 week_theme, slug, published_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                market,
                market_name,
                title,
                primary_keyword,
                json.dumps(secondary_keywords),
                week_theme,
                slug,
                published_date
            ))
            
            # Archive primary keyword
            cursor.execute("""
                INSERT OR IGNORE INTO keywords (market, keyword, used_date, run_id)
                VALUES (?, ?, ?, ?)
            """, (market, primary_keyword, published_date, run_id))
            
            # Archive secondary keywords
            for keyword in secondary_keywords:
                cursor.execute("""
                    INSERT OR IGNORE INTO keywords (market, keyword, used_date, run_id)
                    VALUES (?, ?, ?, ?)
                """, (market, keyword, published_date, run_id))
            
            # Archive theme
            if week_theme:
                cursor.execute("""
                    INSERT OR IGNORE INTO themes (market, week_theme, used_date, run_id)
                    VALUES (?, ?, ?, ?)
                """, (market, week_theme, published_date, run_id))
            
            conn.commit()
            logger.info(f"Archived article: {market} - {title[:50]}...")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to archive article {market}: {e}")
            raise
        finally:
            conn.close()
    
    def archive_sources(self, run_id: str, market: str, source_urls: List[str]) -> None:
        """Archive research source URLs used for a market.
        
        Args:
            run_id: Run identifier
            market: Market key
            source_urls: List of source URLs used
        """
        if not source_urls:
            return
        
        published_date = run_id
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            for url in source_urls:
                cursor.execute("""
                    INSERT OR IGNORE INTO sources (market, source_url, used_date, run_id)
                    VALUES (?, ?, ?, ?)
                """, (market, url, published_date, run_id))
            
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed to archive sources for {market}: {e}")
        finally:
            conn.close()
    
    def get_articles_for_market(
        self,
        market: str,
        days_back: int = 180
    ) -> List[Dict]:
        """Get articles for a market within specified time window.
        
        Args:
            market: Market key
            days_back: Number of days to look back
            
        Returns:
            List of article dictionaries with metadata
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT run_id, market, market_name, title, primary_keyword, 
                   secondary_keywords, week_theme, slug, published_date
            FROM articles
            WHERE market = ? AND published_date >= ?
            ORDER BY published_date DESC
        """, (market, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        articles = []
        for row in rows:
            articles.append({
                "run_id": row["run_id"],
                "market": row["market"],
                "market_name": row["market_name"],
                "title": row["title"],
                "primary_keyword": row["primary_keyword"],
                "secondary_keywords": json.loads(row["secondary_keywords"] or "[]"),
                "week_theme": row["week_theme"],
                "slug": row["slug"],
                "published_date": row["published_date"]
            })
        
        return articles
    
    def get_articles_by_theme(
        self,
        week_theme: str,
        days_back: int = 180
    ) -> List[Dict]:
        """Get articles by theme within specified time window.
        
        Args:
            week_theme: Week theme (e.g., 'winter_safety')
            days_back: Number of days to look back
            
        Returns:
            List of article dictionaries
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT run_id, market, market_name, title, primary_keyword, 
                   secondary_keywords, week_theme, slug, published_date
            FROM articles
            WHERE week_theme = ? AND published_date >= ?
            ORDER BY published_date DESC
        """, (week_theme, cutoff_date))
        
        rows = cursor.fetchall()
        conn.close()
        
        articles = []
        for row in rows:
            articles.append({
                "run_id": row["run_id"],
                "market": row["market"],
                "market_name": row["market_name"],
                "title": row["title"],
                "primary_keyword": row["primary_keyword"],
                "secondary_keywords": json.loads(row["secondary_keywords"] or "[]"),
                "week_theme": row["week_theme"],
                "slug": row["slug"],
                "published_date": row["published_date"]
            })
        
        return articles
    
    def get_recent_keywords(
        self,
        market: str,
        count: int = 10,
        days_back: int = 180
    ) -> List[str]:
        """Get recently used keywords for a market.
        
        Args:
            market: Market key
            count: Maximum number of keywords to return
            days_back: Number of days to look back
            
        Returns:
            List of keyword strings, most recent first
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT keyword
            FROM keywords
            WHERE market = ? AND used_date >= ?
            ORDER BY used_date DESC
            LIMIT ?
        """, (market, cutoff_date, count))
        
        keywords = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return keywords
    
    def get_recent_themes(
        self,
        market: str,
        days_back: int = 180
    ) -> List[str]:
        """Get recently used themes for a market.
        
        Args:
            market: Market key
            days_back: Number of days to look back
            
        Returns:
            List of theme strings, most recent first
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT week_theme
            FROM themes
            WHERE market = ? AND used_date >= ?
            ORDER BY used_date DESC
        """, (market, cutoff_date))
        
        themes = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        return themes
    
    def get_used_sources(
        self,
        market: str,
        days_back: int = 180
    ) -> Set[str]:
        """Get previously used source URLs for a market.
        
        Args:
            market: Market key
            days_back: Number of days to look back
            
        Returns:
            Set of source URLs
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT DISTINCT source_url
            FROM sources
            WHERE market = ? AND used_date >= ?
        """, (market, cutoff_date))
        
        sources = {row[0] for row in cursor.fetchall()}
        conn.close()
        
        return sources
    
    def has_recent_theme(
        self,
        market: str,
        week_theme: str,
        days_back: int = 60
    ) -> bool:
        """Check if a theme was used recently for a market.
        
        Args:
            market: Market key
            week_theme: Week theme to check
            days_back: Number of days to look back
            
        Returns:
            True if theme was used within the time window
        """
        cutoff_date = (datetime.now() - timedelta(days=days_back)).isoformat()
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT COUNT(*) FROM themes
            WHERE market = ? AND week_theme = ? AND used_date >= ?
        """, (market, week_theme, cutoff_date))
        
        count = cursor.fetchone()[0]
        conn.close()
        
        return count > 0
    
    def is_keyword_similar_to_recent(
        self,
        market: str,
        candidate_keyword: str,
        days_back: int = 90
    ) -> bool:
        """Check if a candidate keyword is too similar to recently used keywords.
        
        Uses simple word overlap check - if ≥3 words match, consider it similar.
        
        Args:
            market: Market key
            candidate_keyword: Keyword to check
            days_back: Number of days to look back
            
        Returns:
            True if keyword is too similar to a recent one
        """
        recent_keywords = self.get_recent_keywords(market, count=20, days_back=days_back)
        if not recent_keywords:
            return False
        
        candidate_words = set(candidate_keyword.lower().split())
        
        for recent in recent_keywords:
            recent_words = set(recent.lower().split())
            overlap = len(candidate_words & recent_words)
            # If 3+ words overlap, consider it similar
            if overlap >= 3:
                return True
        
        return False

