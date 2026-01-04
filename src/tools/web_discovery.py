"""Web discovery tool for finding relevant research sources."""
import asyncio
from typing import Dict, List
from urllib.parse import urlparse

from ddgs import DDGS

from utils.config_loader import load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class WebDiscovery:
    """Discovers relevant web sources for research using DuckDuckGo search."""

    def __init__(self):
        self.config = load_config("research_sources")
        self.source_policy = self.config.get("source_policy", {})
        
        # Flatten nested domain structures
        self.trusted_domains = self._flatten_domains(self.config.get("trusted_domains", {}))
        self.allowed_domains = self._flatten_domains(self.config.get("allowed_domains", {}))
        self.blocked_domains = self._flatten_domains(self.config.get("blocked_domains", {}))
        
        self.query_templates = self.config.get("query_templates", {})
        self.priority_resources = self.config.get("priority_resources", {})
        self.max_pages = self.source_policy.get("max_pages_per_market", 8)

    def _flatten_domains(self, domains_config) -> set:
        """Flatten nested domain config into a flat set."""
        result = set()
        if isinstance(domains_config, list):
            result.update(domains_config)
        elif isinstance(domains_config, dict):
            for key, value in domains_config.items():
                if isinstance(value, list):
                    result.update(value)
                elif isinstance(value, dict):
                    for subkey, subvalue in value.items():
                        if isinstance(subvalue, list):
                            result.update(subvalue)
        return result

    async def discover_sources(
        self,
        market: str,
        province: str,
        week_theme: str,
        year: int = 2026
    ) -> List[Dict]:
        """Discover relevant web sources for a market and theme.

        Args:
            market: Market name (e.g., "Montreal")
            province: Province name (e.g., "Quebec")
            week_theme: Content calendar theme (e.g., "new_year_care_planning")
            year: Current year for query templates

        Returns:
            List[Dict]: List of discovered sources with url, title, domain, trust_level
        """
        logger.info(f"Discovering sources for {market} ({week_theme})...")

        all_results = []
        
        # STEP 1: Use priority resources first (pre-sampled authoritative sources)
        market_lower = market.lower()
        priority_urls = self.priority_resources.get(market_lower, [])
        for url in priority_urls:
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                all_results.append({
                    "url": url,
                    "title": f"{market} resource",
                    "snippet": f"Pre-sampled authoritative resource for {market}",
                    "domain": domain,
                    "trust_level": "trusted"
                })
            except Exception:
                continue
        
        if priority_urls:
            logger.info(f"Added {len(priority_urls)} priority resources for {market}")

        # STEP 2: Build search queries from templates
        queries = self._build_queries(market, province, week_theme, year)

        # STEP 3: Execute searches and collect URLs (only if we need more)
        if len(all_results) < self.max_pages:
            for query in queries[:3]:  # Limit to 3 queries to avoid rate limiting
                try:
                    # Run sync search in executor to not block event loop
                    results = await asyncio.get_event_loop().run_in_executor(
                        None, self._search_duckduckgo_sync, query
                    )
                    all_results.extend(results)
                    await asyncio.sleep(1)  # Rate limiting between queries
                except Exception as e:
                    logger.warning(f"Search failed for query '{query[:50]}...': {e}")
                    continue

        # Deduplicate and filter by domain policy
        filtered_results = self._filter_and_dedupe(all_results)

        # Sort by trust level (trusted > allowed > other)
        filtered_results.sort(key=lambda x: (
            0 if x["trust_level"] == "trusted" else
            1 if x["trust_level"] == "allowed" else 2
        ))

        # Limit to max pages
        final_results = filtered_results[:self.max_pages]

        logger.info(f"Discovered {len(final_results)} sources for {market}")
        return final_results

    def _build_queries(
        self,
        market: str,
        province: str,
        week_theme: str,
        year: int
    ) -> List[str]:
        """Build search queries from templates."""
        # Get default templates
        templates = self.query_templates.get("default", [])
        
        # Add local_programs and statistics templates
        templates.extend(self.query_templates.get("local_programs", []))
        templates.extend(self.query_templates.get("statistics", []))
        
        # Convert week_theme to readable format
        theme_readable = week_theme.replace("_", " ") if week_theme else "senior care"
        
        queries = []
        for template in templates:
            try:
                # Support multiple placeholder formats
                query = template.format(
                    market=market,
                    city=market,
                    province=province,
                    theme=theme_readable,
                    year=year,
                    health_authority=f"{market} health authority"
                )
                queries.append(query)
            except KeyError:
                # If template has unmatched placeholders, try simpler substitution
                query = template.replace("{market}", market)
                query = query.replace("{city}", market)
                query = query.replace("{province}", province)
                query = query.replace("{theme}", theme_readable)
                query = query.replace("{year}", str(year))
                query = query.replace("{health_authority}", f"{market} health authority")
                queries.append(query)

        # Add fallback queries if none built
        if not queries:
            queries = [
                f"{market} home care seniors {province}",
                f"senior care services {market} {year}",
                f"{province} elderly care programs {market}"
            ]

        return queries

    def _search_duckduckgo_sync(self, query: str) -> List[Dict]:
        """Search DuckDuckGo using the official library (synchronous).

        Args:
            query: Search query string

        Returns:
            List[Dict]: Search results with url, title, snippet
        """
        results = []
        try:
            with DDGS() as ddgs:
                # Search with Canadian region preference
                search_results = ddgs.text(
                    query,
                    region="ca-en",  # Canadian English
                    safesearch="moderate",
                    max_results=10
                )
                
                for r in search_results:
                    results.append({
                        "url": r.get("href", ""),
                        "title": r.get("title", ""),
                        "snippet": r.get("body", "")
                    })
                    
            logger.debug(f"Found {len(results)} results for: {query[:40]}...")
            
        except Exception as e:
            logger.warning(f"DuckDuckGo search error: {e}")
            
        return results

    def _filter_and_dedupe(self, results: List[Dict]) -> List[Dict]:
        """Filter results by domain policy and deduplicate.

        Args:
            results: Raw search results

        Returns:
            List[Dict]: Filtered and deduplicated results with trust_level
        """
        seen_urls = set()
        filtered = []

        for result in results:
            url = result.get("url", "")

            # Skip if already seen
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract domain
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
            except Exception:
                continue

            # Check if blocked
            if self._is_blocked(domain):
                continue

            # Determine trust level
            trust_level = self._get_trust_level(domain)

            # Add to filtered results
            filtered.append({
                "url": url,
                "title": result.get("title", ""),
                "snippet": result.get("snippet", ""),
                "domain": domain,
                "trust_level": trust_level
            })

        return filtered

    def _is_blocked(self, domain: str) -> bool:
        """Check if domain is blocked."""
        for blocked in self.blocked_domains:
            if domain == blocked or domain.endswith(f".{blocked}"):
                return True
        return False

    def _get_trust_level(self, domain: str) -> str:
        """Determine trust level for a domain."""
        # Check trusted domains
        for trusted in self.trusted_domains:
            if domain == trusted or domain.endswith(f".{trusted}"):
                return "trusted"

        # Check allowed domains
        for allowed in self.allowed_domains:
            if domain == allowed or domain.endswith(f".{allowed}"):
                return "allowed"

        return "unknown"
