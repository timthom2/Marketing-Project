"""Researcher Agent - Evidence-driven research with web discovery and LLM synthesis."""
import json
import random
from typing import Dict, List, Optional
from datetime import datetime

from agents.base_agent import BaseAgent
from tools.web_discovery import WebDiscovery
from tools.web_fetch_extract import WebFetchExtract
from utils.config_loader import load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class ResearcherAgent(BaseAgent):
    """Agent responsible for evidence-driven research with web discovery and LLM synthesis."""

    # Class-level tracking for story lead rotation across markets in a run
    _story_lead_rotation = ["scene", "question", "statistic"]
    _lead_index = 0

    def __init__(self):
        super().__init__()
        self.content_calendar = load_config("content_calendar")
        self.research_sources_config = load_config("research_sources")
        self.model_config = load_config("model_routing")
        self.web_discovery = WebDiscovery()
        self.web_fetch_extract = WebFetchExtract()
        # Load vetted claims
        self.claims = self._load_claims()
        # Import archive for theme variation tracking
        from archive.content_archive import ContentArchive
        self.archive = ContentArchive()

    @classmethod
    def reset_story_lead_rotation(cls):
        """Reset story lead rotation for new run."""
        cls._lead_index = 0
        logger.info("Story lead rotation reset for new run")

    @classmethod
    def get_next_story_lead_type(cls) -> str:
        """Get next story lead type in rotation."""
        lead_type = cls._story_lead_rotation[cls._lead_index % len(cls._story_lead_rotation)]
        cls._lead_index += 1
        return lead_type

    def _load_claims(self) -> Dict:
        """Load vetted claims from claims.yaml."""
        try:
            from pathlib import Path
            import yaml
            claims_path = Path(__file__).parent.parent.parent / "data" / "claims" / "claims.yaml"
            if claims_path.exists():
                with open(claims_path, 'r') as f:
                    return yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Failed to load claims file: {e}")
        return {}

    def _normalize_theme_key(self, week_theme: str) -> str:
        """Normalize week theme to the closest claims/theme key."""
        theme = (week_theme or "").strip().lower()
        theme_map = {
            "dementia_awareness": "dementia",
            "winter_isolation": "companion_care",
            "valentines_companionship": "companion_care",
            "new_year_care_planning": "aging_in_place",
            "spring_preparation": "falls",
            "parkinsons_awareness": "parkinsons",
            "stroke_awareness": "stroke",
            "palliative_care": "palliative"
        }
        return theme_map.get(theme, theme)

    def _render_theme_keywords(self, week_config: Dict, market_config: Dict) -> List[str]:
        """Render theme keywords with market-specific placeholders."""
        if not week_config:
            return []

        theme_keywords = week_config.get("theme_keywords", {}).get("long_tail", [])
        if not theme_keywords:
            return []

        replacements = {
            "{city}": market_config.get("name", ""),
            "{province}": market_config.get("province", ""),
            "{health_authority}": market_config.get("health_authority", "")
        }

        rendered = []
        for keyword in theme_keywords:
            if not isinstance(keyword, str):
                continue
            value = keyword
            for token, replacement in replacements.items():
                if replacement:
                    value = value.replace(token, replacement)
            value = " ".join(value.split()).strip()
            if "{" in value or not value:
                continue
            rendered.append(value)

        return rendered

    def _get_theme_focus_topics(self, week_theme: str, market_name: str) -> List[str]:
        """Provide topic checklist per theme to keep content on-theme."""
        topics = {
            "new_year_care_planning": [
                "annual care plan review checklist (medications, safety, support)",
                "how to schedule a home care assessment",
                "set 1-2 measurable goals for the year"
            ],
            "winter_safety": [
                "fall prevention in winter (ice, stairs, lighting)",
                "hypothermia warning signs and prevention",
                "heating safety and emergency planning"
            ],
            "winter_isolation": [
                "signs of social isolation in seniors",
                "local community or volunteer programs",
                "companionship care benefits"
            ],
            "dementia_awareness": [
                "early signs and the diagnosis pathway",
                "types of dementia and how they differ",
                "caregiver support and respite options"
            ],
            "heart_health": [
                "post-cardiac event recovery at home",
                "medication adherence and warning signs",
                "cardiac rehab or heart-healthy routines"
            ],
            "valentines_companionship": [
                "emotional wellbeing and social connection",
                "local senior programs or events",
                "companionship care benefits"
            ],
            "tax_season_prep": [
                "eligible home care expenses and credits",
                "what documents to keep for filing",
                "how to estimate care costs for the year"
            ],
            "hospital_to_home": [
                "discharge checklist and medication reconciliation",
                "30-day recovery plan and follow-up visits",
                "how to reduce readmission risk"
            ],
            "spring_preparation": [
                "spring home safety hazards (thaw, wet floors)",
                "mobility and outdoor readiness",
                "home modification or maintenance checklist"
            ],
            "parkinsons_awareness": [
                "motor and non-motor symptoms",
                "daily living support strategies",
                "caregiver tips for routines and safety"
            ],
            "stroke_awareness": [
                "F.A.S.T. warning signs and emergency response",
                "rehab timeline and therapy needs",
                "home adaptations for safety"
            ],
            "palliative_care": [
                "difference between palliative and hospice care",
                "symptom management and comfort focus",
                "advance care planning and family support"
            ]
        }

        return topics.get(week_theme, [f"local guidance for {market_name} families"])

    def _get_vetted_stats(self, province: str, theme: str) -> List[Dict]:
        """Get vetted statistics for province and theme.
        
        Args:
            province: Province name (e.g., 'Quebec', 'Ontario')
            theme: Week theme (e.g., 'winter_safety', 'dementia')
            
        Returns:
            List of vetted stat dicts with source URLs
        """
        stats = []
        
        # Map province names to keys
        province_key = province.lower().replace(" ", "_")
        
        # Get province-specific stats
        province_stats = self.claims.get(province_key, {})
        for category, stat_list in province_stats.items():
            if isinstance(stat_list, list):
                for stat in stat_list[:2]:  # Max 2 per category
                    if isinstance(stat, dict) and stat.get("source_url"):
                        stats.append(stat)
        
        # Get theme-specific stats (normalized to claims theme keys)
        theme_key = self._normalize_theme_key(theme)
        theme_stats = self.claims.get("themes", {}).get(theme_key, [])
        if not theme_stats:
            theme_stats = self.claims.get("national", {}).get(theme_key, [])
        for stat in theme_stats[:2]:
            if isinstance(stat, dict) and stat.get("source_url"):
                stats.append(stat)
        
        # Get national stats as fallback
        if len(stats) < 2:
            national = self.claims.get("national", {})
            for category, stat_list in national.items():
                if isinstance(stat_list, list):
                    for stat in stat_list[:1]:
                        if isinstance(stat, dict) and stat.get("source_url"):
                            stats.append(stat)
                        if len(stats) >= 4:
                            break
                if len(stats) >= 4:
                    break
        
        return stats[:4]  # Return max 4 vetted stats

    async def run(self, market_key: str, market_config: Dict) -> Dict:
        """Generate evidence-driven research pack with web discovery.

        Args:
            market_key: Market key (e.g., 'montreal')
            market_config: Market configuration from markets.yaml

        Returns:
            Dict: Comprehensive evidence-driven research pack
        """
        self.log_info(f"Generating evidence-driven research for {market_config['name']}...")

        # Determine current week theme and context (with variation tracking)
        current_week = self._get_current_week_theme(market_key=market_key)
        week_theme = current_week.get('theme', 'general')

        # Step 1: Discover web sources (with source deduplication)
        sources = await self._discover_sources(
            market_config['name'],
            market_config['province'],
            week_theme,
            market_key=market_key
        )

        # Step 2: Fetch and extract content from sources
        extracted_sources = []
        if sources:
            extracted_sources = await self.web_fetch_extract.fetch_and_extract_batch(sources)

        # Step 3: Compress extracted content into brief chunks
        compressed_briefs = []
        if extracted_sources:
            compressed_briefs = await self._compress_sources(extracted_sources)

        # Step 4: Synthesize into evidence-driven research pack
        research_pack = await self._synthesize_research(
            market_key, market_config, week_theme, current_week, compressed_briefs
        )
        
        # Phase 5: Store discovered source URLs in research pack for archiving
        # This allows coordinator to archive web discovery URLs
        if sources:
            research_pack["discovered_sources"] = [s.get("url", "") for s in sources if s.get("url")]

        self.log_info(f"✓ Evidence-driven research complete for {market_config['name']}")
        return research_pack

    def _get_current_week_theme(self, market_key: Optional[str] = None) -> Dict:
        """Get current week theme from content calendar with variation tracking.
        
        Args:
            market_key: Optional market key to check theme history for variation
            
        Returns:
            Dict: Week theme configuration, potentially adjusted for variation
        """
        # Check for WEEK_OVERRIDE environment variable
        import os
        week_override = os.getenv("WEEK_OVERRIDE", "").strip()
        if week_override:
            week_key = f"week_{week_override}"
            if week_key in self.content_calendar["rotation_schedule"]:
                base_theme = self.content_calendar["rotation_schedule"][week_key]
            else:
                base_theme = self.content_calendar["rotation_schedule"]["week_1"]
        else:
            # Calculate based on current date
            from datetime import date
            today = date.today()
            week_of_year = today.isocalendar()[1]
            
            # Map to 12-week rotation (updated from 16-week)
            rotation_week = ((week_of_year - 1) % 12) + 1
            week_key = f"week_{rotation_week}"
            
            base_theme = self.content_calendar["rotation_schedule"].get(
                week_key, 
                self.content_calendar["rotation_schedule"]["week_1"]
            )
        
        # Phase 3: Check theme variation if market is provided
        ignore_variation = os.getenv("IGNORE_THEME_VARIATION", "").strip().lower() in ("1", "true", "yes", "on")
        if ignore_variation:
            return base_theme

        if market_key and base_theme.get("theme"):
            week_theme = base_theme.get("theme")
            
            # Check if theme should be blocked (used 3+ times in 6 months)
            if self.archive.should_block_theme(market_key, week_theme, max_uses_in_6_months=3):
                self.log_warning(
                    f"Theme '{week_theme}' blocked for {market_key} (used 3+ times in 6 months). "
                    f"Using fallback theme."
                )
                return self._get_fallback_theme(base_theme)
            
            # Check if theme used within 60 days (force different angle)
            if self.archive.has_recent_theme(market_key, week_theme, days_back=60):
                self.log_warning(
                    f"Theme '{week_theme}' used recently for {market_key} (within 60 days). "
                    f"Using alternative theme to ensure variation."
                )
                return self._get_alternative_theme(base_theme, market_key)
            
            # Check if theme used within 90 days (warn but allow with different angle)
            if self.archive.has_recent_theme(market_key, week_theme, days_back=90):
                self.log_warning(
                    f"Theme '{week_theme}' used for {market_key} within 90 days. "
                    f"Article should use significantly different angle."
                )
                # Mark theme for different angle in research pack
                base_theme["_requires_different_angle"] = True
        
        return base_theme
    
    def _get_alternative_theme(self, base_theme: Dict, market_key: str) -> Dict:
        """Get alternative theme when base theme was recently used.
        
        Strategy: Use a semantically different theme from the rotation.
        
        Args:
            base_theme: Original theme configuration
            market_key: Market key for context
            
        Returns:
            Dict: Alternative theme configuration
        """
        # Get themes that are semantically different
        # For winter themes, prefer non-winter alternatives
        base_theme_name = base_theme.get("theme", "")
        
        # Define theme alternatives (semantically different themes)
        alternatives = {
            "winter_safety": ["dementia_awareness", "heart_health", "hospital_to_home"],
            "winter_isolation": ["dementia_awareness", "heart_health", "parkinsons_awareness"],
            "dementia_awareness": ["heart_health", "hospital_to_home", "spring_preparation"],
            "heart_health": ["dementia_awareness", "stroke_awareness", "hospital_to_home"],
            "hospital_to_home": ["dementia_awareness", "heart_health", "parkinsons_awareness"],
        }
        
        # Try to find alternative theme
        alt_themes = alternatives.get(base_theme_name, [])
        recent_themes = self.archive.get_recent_themes(market_key, days_back=90)
        
        for alt_theme_name in alt_themes:
            if alt_theme_name not in recent_themes:
                # Find week config for this theme
                for week_key, week_config in self.content_calendar["rotation_schedule"].items():
                    if week_config.get("theme") == alt_theme_name:
                        self.log_info(f"Using alternative theme: {alt_theme_name}")
                        return week_config
        
        # Fallback: use a different week from rotation
        return self._get_fallback_theme(base_theme)
    
    def _get_fallback_theme(self, base_theme: Dict) -> Dict:
        """Get fallback theme when base theme is blocked.
        
        Args:
            base_theme: Original theme configuration
            
        Returns:
            Dict: Fallback theme configuration (typically week_1 or week_4)
        """
        # Use a stable, year-round theme as fallback
        fallback_themes = ["dementia_awareness", "hospital_to_home", "heart_health"]
        
        for fallback_name in fallback_themes:
            for week_key, week_config in self.content_calendar["rotation_schedule"].items():
                if week_config.get("theme") == fallback_name:
                    self.log_info(f"Using fallback theme: {fallback_name}")
                    return week_config
        
        # Ultimate fallback: week_1
        return self.content_calendar["rotation_schedule"]["week_1"]

    async def _discover_sources(
        self,
        market: str,
        province: str,
        week_theme: str,
        market_key: Optional[str] = None
    ) -> List[Dict]:
        """Discover relevant web sources, prioritizing new sources over previously used ones.
        
        Args:
            market: Market name (e.g., 'Oakville')
            province: Province name (e.g., 'Ontario')
            week_theme: Week theme string
            market_key: Optional market key for checking source history
            
        Returns:
            List of source dictionaries, with new sources prioritized
        """
        try:
            all_sources = await self.web_discovery.discover_sources(
                market=market,
                province=province,
                week_theme=week_theme,
                year=datetime.now().year
            )
            
            # Phase 5: Prioritize new sources over previously used ones
            if market_key and all_sources:
                used_sources = self.archive.get_used_sources(market_key, days_back=180)
                
                # Separate new and used sources
                new_sources = [s for s in all_sources if s.get('url', '') not in used_sources]
                used_sources_list = [s for s in all_sources if s.get('url', '') in used_sources]
                
                self.log_info(
                    f"Discovered {len(all_sources)} sources for {market}: "
                    f"{len(new_sources)} new, {len(used_sources_list)} previously used"
                )
                
                # Prioritize new sources, fallback to used if needed
                # Use up to 6 sources, preferring new ones
                if new_sources:
                    prioritized = new_sources[:6]
                    if len(prioritized) < 6 and used_sources_list:
                        # Fill remaining slots with used sources if needed
                        remaining = 6 - len(prioritized)
                        prioritized.extend(used_sources_list[:remaining])
                    return prioritized
                else:
                    # No new sources available, use previously used ones
                    self.log_warning(
                        f"No new sources found for {market}, using previously used sources"
                    )
                    return used_sources_list[:6]
            else:
                # No market_key provided or no sources, return as-is
                self.log_info(f"Discovered {len(all_sources)} sources for {market}")
                return all_sources[:6] if all_sources else []
                
        except Exception as e:
            self.log_warning(f"Web discovery failed for {market}: {e}")
            return []

    async def _compress_sources(self, sources: List[Dict]) -> List[Dict]:
        """Compress extracted sources into brief chunks using LLM."""
        model_config = self.model_config["models"]["web_extraction"]
        compressed = []

        for source in sources[:6]:  # Limit to 6 sources to control costs
            content = source.get("content", "")
            if not content or len(content) < 100:
                continue

            prompt = f"""Extract the key facts from this content about senior care/home care. Focus on:
- Statistics and numbers (percentages, costs, timelines)
- Named programs, initiatives, or services
- Policy updates or recent changes
- Actionable information for families

Content from {source.get('title', 'Unknown')} ({source.get('domain', 'Unknown')}):
{content[:4000]}

Return a JSON object with:
{{
  "key_facts": ["fact 1", "fact 2", ...],
  "statistics": ["stat with number 1", ...],
  "programs": ["program name 1", ...],
  "actionable_tips": ["tip 1", ...]
}}

Return ONLY valid JSON."""

            try:
                response = await self.openai.generate(
                    model=model_config["model"],
                    prompt=prompt,
                    max_tokens=model_config["max_tokens"],
                    temperature=model_config["temperature"],
                    response_format={"type": "json_object"}
                )

                # Parse JSON response
                import re
                json_match = re.search(r'\{.*\}', response, re.DOTALL)
                if json_match:
                    brief = json.loads(json_match.group(0))
                    compressed.append({
                        "source": {
                            "title": source.get("title", ""),
                            "url": source.get("url", ""),
                            "domain": source.get("domain", ""),
                            "trust_level": source.get("trust_level", "unknown")
                        },
                        "brief": brief
                    })
            except Exception as e:
                self.log_warning(f"Failed to compress source {source.get('url', '')}: {e}")
                continue

        self.log_info(f"Compressed {len(compressed)} sources into briefs")
        return compressed

    async def _synthesize_research(
        self,
        market_key: str,
        market_config: Dict,
        week_theme: str,
        week_config: Dict,
        compressed_briefs: List[Dict]
    ) -> Dict:
        """Synthesize compressed briefs into an evidence-driven research pack."""
        seasonal_context = week_config.get("seasonal_context", "")
        target_dates = week_config.get("target_dates", "")
        week_requirements = week_config.get("must_include", [])
        system_context_mode = self._system_context_mode(week_theme)

        # Build base research structure
        research_pack = {
            "market": market_key,
            "market_name": market_config["name"],
            "province": market_config["province"],
            "location_url": market_config["location_url"],
            "week_theme": week_theme,
            "week_theme_description": week_config.get("description", ""),
            "seasonal_context": seasonal_context,
            "target_dates": target_dates,
            "week_requirements": week_requirements,
            "system_context_mode": system_context_mode,
            "generated_at": datetime.now().isoformat(),
        }

        # Generate evidence-driven components using LLM synthesis if we have briefs
        if compressed_briefs:
            synthesized = await self._llm_synthesize(
                market_config, week_theme, week_config, compressed_briefs
            )
            research_pack.update(synthesized)
        else:
            # Fallback to enhanced static generation
            research_pack.update(self._generate_fallback_research(
                market_config, week_theme, week_config
            ))

        # Always add core components
        research_pack["keywords"] = self._generate_keywords(
            market_config,
            week_theme,
            week_config,
            market_key=market_key
        )
        research_pack["faqs"] = self._generate_faqs(market_config, week_theme)
        research_pack["local_resources"] = self._generate_local_resources(market_config, week_theme)
        research_pack["medical_sources"] = self._generate_medical_sources(market_config, week_theme)
        research_pack["theme_focus_topics"] = self._get_theme_focus_topics(
            week_theme,
            market_config.get("name", "")
        )

        # Add anti-generic constraints
        research_pack["anti_generic"] = self.research_sources_config.get("anti_generic", {})

        # Add vetted stats from claims.yaml
        research_pack["vetted_stats"] = self._get_vetted_stats(
            market_config.get("province", ""),
            week_theme
        )
        
        # Add H2 seeds from market config (prefer h2_seeds, fallback to h2_prompts)
        h2_seeds = market_config.get("h2_seeds", market_config.get("h2_prompts", []))
        research_pack["h2_seeds"] = self._filter_h2_seeds(h2_seeds, system_context_mode)
        
        # Add must-include entities from market config
        research_pack["must_include_entities"] = market_config.get("must_include_entities", [])
        
        # Assign story lead type for this market (rotates across markets)
        research_pack["assigned_story_lead_type"] = self.get_next_story_lead_type()
        
        # Add local authority links for inline use
        research_pack["local_authority_links"] = market_config.get("local_resources", [])[:2]

        return research_pack

    def _system_context_mode(self, week_theme: str) -> str:
        system_focus = {
            "new_year_care_planning",
            "tax_season_prep",
            "hospital_to_home",
        }
        return "full" if week_theme in system_focus else "brief"

    def _filter_h2_seeds(self, h2_seeds: List[str], system_context_mode: str) -> List[str]:
        if system_context_mode == "full" or not h2_seeds:
            return h2_seeds
        return [seed for seed in h2_seeds if not self._is_system_h2(seed)]

    def _is_system_h2(self, seed: str) -> bool:
        if not seed:
            return False
        lowered = seed.lower()
        system_keywords = (
            "clsc",
            "ramq",
            "ohip",
            "hccss",
            "msp",
            "csil",
            "ahcip",
            "home and community care",
            "home support",
            "continuing care",
            "health authority",
            "coverage",
            "tax credit",
            "soutien",
        )
        return any(keyword in lowered for keyword in system_keywords)

    async def _llm_synthesize(
        self,
        market_config: Dict,
        week_theme: str,
        week_config: Dict,
        compressed_briefs: List[Dict]
    ) -> Dict:
        """Use LLM to synthesize compressed briefs into research components."""
        model_config = self.model_config["models"]["research_synthesis"]

        # Format briefs for prompt
        briefs_text = ""
        for i, cb in enumerate(compressed_briefs[:5], 1):
            source = cb["source"]
            brief = cb["brief"]
            briefs_text += f"\n\nSOURCE {i}: {source['title']} ({source['url']})\n"
            briefs_text += f"Trust: {source['trust_level']}\n"
            briefs_text += f"Key facts: {json.dumps(brief.get('key_facts', []))}\n"
            briefs_text += f"Statistics: {json.dumps(brief.get('statistics', []))}\n"
            briefs_text += f"Programs: {json.dumps(brief.get('programs', []))}\n"

        prompt = f"""You are synthesizing research for a news-worthy article about senior home care in {market_config['name']}, {market_config['province']}.

THEME: {week_theme.replace('_', ' ').title()}
THEME CONTEXT: {week_config.get('description', '')}
SEASONAL CONTEXT: {week_config.get('seasonal_context', '')}
TARGET DATES: {week_config.get('target_dates', '')}
CALENDAR MUST-INCLUDE: {", ".join(week_config.get("must_include", [])) if week_config.get("must_include") else "None specified"}

EXTRACTED RESEARCH:
{briefs_text}

Generate an evidence-driven research brief that will help a writer create engaging, specific, non-generic content.

Return JSON with these components:

{{
  "story_leads": [
    {{
      "type": "scene",
      "lead": "A vivid opening scenario that hooks readers",
      "why_compelling": "Explanation of emotional hook"
    }},
    {{
      "type": "question",
      "lead": "A thought-provoking question that resonates",
      "why_compelling": "Why this question matters"
    }},
    {{
      "type": "statistic",
      "lead": "A surprising statistic that grabs attention",
      "source": "Citation for the stat",
      "why_compelling": "Why this stat matters"
    }}
  ],
  "news_pegs": [
    {{
      "hook": "What's timely/newsworthy about this topic now",
      "source_url": "URL if from research",
      "relevance": "Why this matters to {market_config['name']} families"
    }}
  ],
  "evidence_cards": [
    {{
      "fact": "Specific fact or statistic",
      "source": "Source name and URL",
      "how_to_use": "How writer should incorporate this"
    }}
  ],
  "actionable_takeaways": [
    {{
      "action": "What families can do this week",
      "why_now": "Why timing matters",
      "local_resource": "Local resource to mention if applicable"
    }}
  ],
  "local_hook": {{
    "title": "Local angle for {market_config['name']}",
    "summary": "2-3 sentence summary with specific local relevance",
    "citation": "Source if applicable"
  }},
  "content_suggestions": {{
    "must_include": ["Required element 1", "Required element 2"],
    "angle_options": ["Possible angle 1", "Possible angle 2"],
    "avoid": ["Generic phrasing to avoid"]
  }}
}}

REQUIREMENTS:
- All statistics must include sources
- Story leads must be specific to {market_config['name']} or {market_config['province']} when possible
- News pegs should feel timely (reference current season, policy, or initiative)
- Evidence cards should be concrete and citable
- Actionable takeaways should be practical for families this week

Return ONLY valid JSON."""

        try:
            response = await self.openai.generate(
                model=model_config["model"],
                prompt=prompt,
                max_tokens=model_config["max_tokens"],
                temperature=model_config["temperature"],
                response_format={"type": "json_object"}
            )

            import re
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                synthesized = json.loads(json_match.group(0))
                self.log_info(f"LLM synthesis complete: {len(synthesized.get('evidence_cards', []))} evidence cards")
                return synthesized
        except Exception as e:
            self.log_warning(f"LLM synthesis failed: {e}")

        # Fallback
        return self._generate_fallback_research(market_config, week_theme, week_config)

    def _generate_fallback_research(
        self,
        market_config: Dict,
        week_theme: str,
        week_config: Dict
    ) -> Dict:
        """Generate research components when web discovery fails."""
        seasonal_context = week_config.get("seasonal_context", "this time of year")
        theme_readable = week_theme.replace("_", " ")
        
        story_leads = [
            {
                "type": "scene",
                "lead": f"Picture this: Your mother, who once managed the entire household, now struggles to remember if she took her morning medication. You're 500 kilometers away in {market_config['name']}, wondering how to help.",
                "why_compelling": "Universal scenario that creates emotional connection"
            },
            {
                "type": "question",
                "lead": f"What if the key to your parent's independence isn't less freedom, but smarter support?",
                "why_compelling": "Reframes the conversation about aging care"
            },
            {
                "type": "statistic",
                "lead": f"Over 90% of Canadian seniors want to age at home, yet only 15% have a formal care plan in place.",
                "source": "Canadian Institute for Health Information",
                "why_compelling": "Shows gap between desire and preparation"
            }
        ]

        news_pegs = [
            {
                "hook": f"{seasonal_context} makes {theme_readable} especially relevant for {market_config['name']} families.",
                "source_url": "",
                "relevance": f"Timing and local conditions affect care decisions in {market_config['name']}"
            }
        ]

        evidence_cards = [
            {
                "fact": "Home care can delay or prevent the need for long-term care placement by up to 2 years.",
                "source": "Canadian Institute for Health Information",
                "how_to_use": "Use to emphasize preventive value of early planning"
            },
            {
                "fact": f"{market_config['province']}'s publicly funded home care covers medical services, but families often supplement with private companion care.",
                "source": f"{market_config['province']} Health",
                "how_to_use": "Explain the public-private care landscape"
            }
        ]

        actionable_takeaways = [
            {
                "action": "Schedule a family meeting to discuss care preferences before a crisis occurs",
                "why_now": seasonal_context,
                "local_resource": f"{market_config['name']} CLSC or local health authority"
            },
            {
                "action": "Request a home safety assessment from your local health authority",
                "why_now": seasonal_context,
                "local_resource": f"{market_config['province']} home care services"
            }
        ]

        local_hook = {
            "title": f"{market_config['name']} Senior Care Planning Initiative",
            "summary": f"{market_config['name']} offers resources to help families navigate the transition to home care. Local health authorities provide free assessments and care coordination services through {market_config.get('healthcare_context', 'the provincial healthcare system')}.",
            "citation": f"{market_config['province']} Health — accessed {datetime.now().strftime('%Y-%m-%d')}"
        }

        content_suggestions = {
            "must_include": [
                f"Reference to {market_config['province']}'s healthcare system by name",
                "At least one concrete statistic with source",
                "A practical action families can take this week",
                f"Local context specific to {market_config['name']}"
            ],
            "angle_options": [
                "The hidden costs of waiting too long to plan",
                "How to start the care conversation without causing conflict",
                "What most families don't know about home care options"
            ],
            "avoid": [
                "Many families...",
                "In today's world...",
                "It's no secret that...",
                "As we all know...",
                "Generic opening paragraphs"
            ]
        }

        return {
            "story_leads": story_leads,
            "news_pegs": news_pegs,
            "evidence_cards": evidence_cards,
            "actionable_takeaways": actionable_takeaways,
            "local_hook": local_hook,
            "content_suggestions": content_suggestions
        }

    def _generate_medical_sources(self, market_config: Dict, week_theme: str) -> List[Dict]:
        """Generate verified medical sources based on province and theme."""
        
        provincial_health_urls = {
            "Quebec": {
                "title": "Santé Québec - Home Care Services",
                "url": "https://www.quebec.ca/en/health/health-system-and-services/service-organization/home-support-services",
                "publisher": "Government of Quebec",
                "summary": "Quebec's official information on home support and care services for seniors."
            },
            "Ontario": {
                "title": "Ontario Health - Home and Community Care",
                "url": "https://www.ontario.ca/page/homecare-seniors",
                "publisher": "Government of Ontario",
                "summary": "Information about home care services and support for seniors in Ontario."
            },
            "British Columbia": {
                "title": "BC Health - Home and Community Care",
                "url": "https://www2.gov.bc.ca/gov/content/health/accessing-health-care/home-community-care",
                "publisher": "Government of British Columbia",
                "summary": "BC's home and community care program information for seniors."
            },
            "Manitoba": {
                "title": "Manitoba Health - Home Care Program",
                "url": "https://www.gov.mb.ca/health/homecare/",
                "publisher": "Manitoba Health",
                "summary": "Manitoba's home care program for seniors and those with health conditions."
            },
            "Alberta": {
                "title": "Alberta Health Services - Home Care",
                "url": "https://www.albertahealthservices.ca/findhealth/Service.aspx?id=1001178",
                "publisher": "Alberta Health Services",
                "summary": "Information about home care and continuing care services in Alberta."
            }
        }
        
        province = market_config.get("province", "")
        base_source = provincial_health_urls.get(province, {
            "title": "Health Canada - Seniors",
            "url": "https://www.canada.ca/en/public-health/services/health-promotion/aging-seniors.html",
            "publisher": "Public Health Agency of Canada",
            "summary": "Federal resources on aging and senior health in Canada."
        })
        
        base_sources = [base_source]

        theme_key = self._normalize_theme_key(week_theme)
        theme_sources = {
            "new_year_care_planning": [
                {
                    "title": "Health Canada - Caring for Seniors",
                    "url": "https://www.canada.ca/en/public-health/services/health-promotion/aging-seniors/publications/caring-for-seniors.html",
                    "publisher": "Public Health Agency of Canada",
                    "summary": "Resources for families planning care for aging loved ones."
                }
            ],
            "winter_safety": [
                {
                    "title": "Health Canada - Cold Weather Safety",
                    "url": "https://www.canada.ca/en/health-canada/services/healthy-living/your-health/environment/extreme-cold.html",
                    "publisher": "Health Canada",
                    "summary": "Guidelines for preventing cold-related health issues in seniors."
                }
            ],
            "winter_isolation": [
                {
                    "title": "Public Health Agency of Canada - Aging and Seniors",
                    "url": "https://www.canada.ca/en/public-health/services/health-promotion/aging-seniors.html",
                    "publisher": "Public Health Agency of Canada",
                    "summary": "Resources on healthy aging, mental health, and social connection."
                }
            ],
            "dementia": [
                {
                    "title": "Alzheimer Society of Canada - About Dementia",
                    "url": "https://alzheimer.ca/en/about-dementia/what-dementia",
                    "publisher": "Alzheimer Society of Canada",
                    "summary": "Overview of dementia types, symptoms, and support resources."
                }
            ],
            "heart_health": [
                {
                    "title": "Heart and Stroke Foundation - Heart Disease",
                    "url": "https://www.heartandstroke.ca/heart-disease",
                    "publisher": "Heart and Stroke Foundation of Canada",
                    "summary": "Information on heart conditions, risk factors, and prevention."
                }
            ],
            "companion_care": [
                {
                    "title": "Public Health Agency of Canada - Aging and Seniors",
                    "url": "https://www.canada.ca/en/public-health/services/health-promotion/aging-seniors.html",
                    "publisher": "Public Health Agency of Canada",
                    "summary": "Resources on healthy aging, social connection, and wellbeing."
                }
            ],
            "tax_season_prep": [
                {
                    "title": "Canada Revenue Agency - Medical Expense Tax Credit",
                    "url": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/tax-return/completing-a-tax-return/deductions-credits-expenses/lines-33099-33199-eligible-medical-expenses-you-claim-on-your-tax-return.html",
                    "publisher": "Canada Revenue Agency",
                    "summary": "Official guide to claiming medical expense tax credits including home care."
                }
            ],
            "hospital_to_home": [
                {
                    "title": "CIHI - Home Care",
                    "url": "https://www.cihi.ca/en/home-care",
                    "publisher": "Canadian Institute for Health Information",
                    "summary": "Home care context and system performance across Canada."
                }
            ],
            "spring_preparation": [
                {
                    "title": "Public Health Agency of Canada - Seniors Falls",
                    "url": "https://www.canada.ca/en/public-health/services/health-promotion/aging-seniors/publications/publications-general-public/seniors-falls-canada-second-report.html",
                    "publisher": "Public Health Agency of Canada",
                    "summary": "Falls prevention guidance and risk factors for seniors."
                }
            ],
            "parkinsons": [
                {
                    "title": "Parkinson Canada - What is Parkinson's",
                    "url": "https://www.parkinson.ca/what-is-parkinsons/",
                    "publisher": "Parkinson Canada",
                    "summary": "Overview of Parkinson's symptoms, progression, and resources."
                }
            ],
            "stroke": [
                {
                    "title": "Heart and Stroke Foundation - What is Stroke",
                    "url": "https://www.heartandstroke.ca/stroke/what-is-stroke",
                    "publisher": "Heart and Stroke Foundation of Canada",
                    "summary": "Stroke warning signs, causes, and recovery information."
                }
            ],
            "palliative": [
                {
                    "title": "Canadian Hospice Palliative Care Association",
                    "url": "https://www.chpca.ca/",
                    "publisher": "CHPCA",
                    "summary": "Information on palliative care, hospice services, and family support."
                }
            ]
        }

        additional_sources = theme_sources.get(theme_key, []) or theme_sources.get(week_theme, [])
        selected_additional = random.sample(additional_sources, min(len(additional_sources), random.randint(0, 2)))
        all_sources = base_sources + selected_additional

        for source in all_sources:
            source["accessed_date"] = datetime.now().strftime("%Y-%m-%d")
            source["citation"] = f"{source['publisher']} — accessed {source['accessed_date']}"

        return all_sources

    def _generate_local_resources(self, market_config: Dict, week_theme: str) -> List[Dict]:
        """Generate verified local resources for Canadian markets."""
        
        provincial_resources = {
            "Quebec": [
                {
                    "title": "Info-Santé 811",
                    "url": "https://www.quebec.ca/en/health/finding-a-resource/info-sante-811",
                    "publisher": "Government of Quebec",
                    "description": "Free health consultation line available 24/7 for seniors and caregivers.",
                    "is_local": True
                },
                {
                    "title": "Régie de l'assurance maladie du Québec (RAMQ)",
                    "url": "https://www.ramq.gouv.qc.ca/en/citizens/health-insurance",
                    "publisher": "RAMQ",
                    "description": "Quebec's public health insurance information and coverage details.",
                    "is_local": True
                }
            ],
            "Ontario": [
                {
                    "title": "Ontario Seniors Secretariat",
                    "url": "https://www.ontario.ca/page/programs-and-services-seniors",
                    "publisher": "Government of Ontario",
                    "description": "Comprehensive guide to senior programs and services in Ontario.",
                    "is_local": True
                },
                {
                    "title": "Seniors Care at Home Tax Credit",
                    "url": "https://www.ontario.ca/page/ontario-seniors-care-at-home-tax-credit",
                    "publisher": "Government of Ontario",
                    "description": "Information about Ontario's tax credit for senior care expenses.",
                    "is_local": True
                }
            ],
            "British Columbia": [
                {
                    "title": "BC Seniors' Guide",
                    "url": "https://www2.gov.bc.ca/gov/content/family-social-supports/seniors",
                    "publisher": "Government of British Columbia",
                    "description": "Provincial resources and programs for seniors and their families.",
                    "is_local": True
                },
                {
                    "title": "HealthLink BC",
                    "url": "https://www.healthlinkbc.ca/services-and-resources/home-and-community-care",
                    "publisher": "HealthLink BC",
                    "description": "Information about home and community care options in BC.",
                    "is_local": True
                }
            ],
            "Manitoba": [
                {
                    "title": "Manitoba Health Seniors and Long Term Care",
                    "url": "https://www.gov.mb.ca/health/homecare/",
                    "publisher": "Government of Manitoba",
                    "description": "Home care program information for Manitoba residents.",
                    "is_local": True
                },
                {
                    "title": "Age-Friendly Manitoba",
                    "url": "https://www.gov.mb.ca/seniors/",
                    "publisher": "Government of Manitoba",
                    "description": "Resources and initiatives supporting seniors in Manitoba.",
                    "is_local": True
                }
            ],
            "Alberta": [
                {
                    "title": "Alberta Seniors and Housing",
                    "url": "https://www.alberta.ca/seniors-and-housing",
                    "publisher": "Government of Alberta",
                    "description": "Programs and services for seniors in Alberta.",
                    "is_local": True
                },
                {
                    "title": "Alberta Health Services Continuing Care",
                    "url": "https://www.albertahealthservices.ca/cc/Page15339.aspx",
                    "publisher": "Alberta Health Services",
                    "description": "Information about continuing care and home support services.",
                    "is_local": True
                }
            ]
        }
        
        national_resources = [
            {
                "title": "Government of Canada Seniors Programs",
                "url": "https://www.canada.ca/en/services/benefits/seniors.html",
                "publisher": "Government of Canada",
                "description": "Federal programs and benefits available to Canadian seniors.",
                "is_local": False
            },
            {
                "title": "Canada Revenue Agency Medical Expenses",
                "url": "https://www.canada.ca/en/revenue-agency/services/tax/individuals/topics/about-your-tax-return/tax-return/completing-a-tax-return/deductions-credits-expenses/lines-33099-33199-eligible-medical-expenses-you-claim-on-your-tax-return.html",
                "publisher": "Canada Revenue Agency",
                "description": "Guide to claiming medical expenses including home care on your tax return.",
                "is_local": False
            }
        ]
        
        province = market_config.get("province", "")
        local = provincial_resources.get(province, [])
        all_resources = local + national_resources
        selected = random.sample(all_resources, min(len(all_resources), random.randint(2, 3)))
        
        for resource in selected:
            resource["accessed_date"] = datetime.now().strftime("%Y-%m-%d")
        
        return selected

    def _generate_keywords(
        self,
        market_config: Dict,
        week_theme: str,
        week_config: Optional[Dict] = None,
        market_key: Optional[str] = None
    ) -> Dict:
        """Generate contextual keywords based on theme, avoiding recently used keywords.
        
        Args:
            market_config: Market configuration dict
            week_theme: Week theme string
            week_config: Week configuration (for theme keywords)
            market_key: Optional market key for checking keyword history
            
        Returns:
            Dict with primary, secondary, and theme_keywords
        """
        primary_pool = market_config["primary_keyword_pool"]
        secondary_pool = market_config["secondary_keyword_pool"]

        # Phase 4: Get recent keywords once to avoid per-keyword DB queries (P2 Fix)
        recent_keywords = []
        if market_key:
            recent_keywords = self.archive.get_recent_keywords(market_key, count=20, days_back=90)
            self.log_info(f"Found {len(recent_keywords)} recent keywords for {market_key}")

        # Filter primary keywords to avoid recent ones (using cached recent_keywords)
        filtered_primary_pool = []
        for keyword in primary_pool:
            if not market_key or not self.archive.is_keyword_similar_to_recent(
                market_key, keyword, days_back=90, recent_keywords=recent_keywords
            ):
                filtered_primary_pool.append(keyword)
        
        # If all primary keywords are filtered out, use original pool with warning
        if not filtered_primary_pool:
            self.log_warning(
                f"All primary keywords filtered for {market_key}. Using original pool."
            )
            filtered_primary_pool = primary_pool
        
        theme_specific = self._render_theme_keywords(week_config or {}, market_config)
        primary = None
        theme_term_map = {
            "new_year_care_planning": ["plan", "planning", "assessment", "aging in place", "care review"],
            "winter_safety": ["winter", "fall", "hypothermia", "heating", "ice", "snow"],
            "winter_isolation": ["loneliness", "isolation", "companion", "social"],
            "dementia_awareness": ["dementia", "alzheimer", "memory"],
            "heart_health": ["heart", "cardiac"],
            "valentines_companionship": ["companion", "loneliness", "social"],
            "tax_season_prep": ["tax", "credit", "deduction"],
            "hospital_to_home": ["hospital", "discharge", "readmission", "transition"],
            "spring_preparation": ["spring", "thaw", "fall", "safety"],
            "parkinsons_awareness": ["parkinson"],
            "stroke_awareness": ["stroke"],
            "palliative_care": ["palliative", "hospice", "end of life"]
        }
        theme_terms = [t for t in theme_term_map.get(week_theme, []) if t]

        # Prefer primary keywords aligned to theme terms
        themed_primary_pool = [
            kw for kw in filtered_primary_pool
            if any(term in kw.lower() for term in theme_terms)
        ]
        if themed_primary_pool:
            primary = random.choice(themed_primary_pool)
        elif theme_specific:
            localized_theme_terms = [
                kw for kw in theme_specific
                if market_config.get("name", "").lower() in kw.lower()
                or market_config.get("province", "").lower() in kw.lower()
            ]
            if localized_theme_terms:
                primary = random.choice(localized_theme_terms)
        if not primary:
            primary = random.choice(filtered_primary_pool)
        available_secondary = secondary_pool + theme_specific
        
        # Filter secondary keywords to avoid recent ones (using cached recent_keywords)
        filtered_secondary = []
        for keyword in available_secondary:
            if not market_key or not self.archive.is_keyword_similar_to_recent(
                market_key, keyword, days_back=90, recent_keywords=recent_keywords
            ):
                filtered_secondary.append(keyword)
        
        # If too many filtered out, use original pool with warning
        if len(filtered_secondary) < 4:
            self.log_warning(
                f"Too few secondary keywords after filtering for {market_key}. Using original pool."
            )
            filtered_secondary = available_secondary

        secondary_count = random.randint(4, 6)
        secondary = random.sample(filtered_secondary, min(len(filtered_secondary), secondary_count))

        return {
            "primary": primary,
            "secondary": secondary,
            "theme_keywords": theme_specific
        }

    def _generate_faqs(self, market_config: Dict, week_theme: str) -> List[Dict]:
        """Generate contextual FAQs based on theme."""
        base_faqs = [
            {
                "question": f"How do I find quality home care in {market_config['name']}?",
                "answer": f"Start by contacting {market_config.get('healthcare_context', 'your local health authority')} for public options, then explore private providers like TheKey for personalized care plans."
            },
            {
                "question": f"What home care services are covered by {market_config['province']} health insurance?",
                "answer": f"Publicly funded services typically include nursing care and some personal care. Private providers offer additional services like companion care and specialized support."
            },
            {
                "question": "How much does private home care cost in Canada?",
                "answer": "Costs vary by service type and hours needed. Many families combine public coverage with private care. Ask about tax credits that may offset expenses."
            }
        ]

        theme_faqs = {
            "new_year_care_planning": [
                {
                    "question": "What should a yearly care plan review include?",
                    "answer": "Focus on medications, safety risks, support needs, and backup plans. A simple checklist helps families spot changes early."
                },
                {
                    "question": f"How do we start a home care assessment in {market_config['province']}?",
                    "answer": "Begin with your local health authority for a public assessment, then compare private options for consistent support."
                }
            ],
            "winter_safety": [
                {
                    "question": "How can we prevent falls for seniors in winter?",
                    "answer": "Improve lighting, use proper footwear, clear ice quickly, and add grab bars or handrails where needed."
                },
                {
                    "question": "What are the warning signs of hypothermia in seniors?",
                    "answer": "Look for shivering, confusion, slow speech, and fatigue. Keep homes warm and limit time outdoors in extreme cold."
                }
            ],
            "winter_isolation": [
                {
                    "question": "What are common signs of winter isolation in older adults?",
                    "answer": "Reduced phone calls, missed appointments, and low mood can signal isolation. Regular check-ins help."
                },
                {
                    "question": "How can companion care help in winter?",
                    "answer": "Companion care supports routines, social connection, and safe outings when family cannot be present."
                }
            ],
            "dementia_awareness": [
                {
                    "question": "What are early signs of dementia?",
                    "answer": "Common signs include memory changes, confusion with time or place, and difficulty with familiar tasks."
                },
                {
                    "question": "How is dementia diagnosed in Canada?",
                    "answer": "Diagnosis typically involves a family doctor, cognitive screening, and referral to specialists when needed."
                }
            ],
            "heart_health": [
                {
                    "question": "What does safe cardiac recovery at home look like?",
                    "answer": "Follow discharge instructions, manage medications, track symptoms, and attend cardiac rehab if available."
                },
                {
                    "question": "What warning signs should families watch after a cardiac event?",
                    "answer": "New chest pain, shortness of breath, dizziness, or swelling should be discussed with a clinician promptly."
                }
            ],
            "valentines_companionship": [
                {
                    "question": "Why is social connection important for seniors?",
                    "answer": "Regular connection supports mental health, cognitive health, and daily motivation during long winters."
                },
                {
                    "question": "What are simple ways to reduce loneliness?",
                    "answer": "Schedule weekly calls, community programs, or companion care visits to keep routines consistent."
                }
            ],
            "tax_season_prep": [
                {
                    "question": "Can I claim home care expenses on my taxes?",
                    "answer": "Many home care expenses qualify for the Medical Expense Tax Credit. Keep receipts and check CRA rules."
                },
                {
                    "question": "What documents should caregivers keep for tax season?",
                    "answer": "Keep invoices, receipts, and care schedules to support eligible credits or deductions."
                }
            ],
            "hospital_to_home": [
                {
                    "question": "What should we do in the first 48 hours after discharge?",
                    "answer": "Review medications, confirm follow-up appointments, and arrange help with mobility and daily tasks."
                },
                {
                    "question": "How can families reduce readmission risk?",
                    "answer": "Track symptoms, follow care instructions, and coordinate support for meals, mobility, and hygiene."
                }
            ],
            "spring_preparation": [
                {
                    "question": "What spring hazards increase fall risk for seniors?",
                    "answer": "Wet floors, uneven pavement, and clutter from spring cleaning can increase falls during thaw season."
                },
                {
                    "question": "How can we prepare the home for spring?",
                    "answer": "Check lighting, remove trip hazards, and consider small home modifications for safer movement."
                }
            ],
            "parkinsons_awareness": [
                {
                    "question": "What are early signs of Parkinson's?",
                    "answer": "Early signs include tremor, stiffness, and slowed movement. Non-motor changes can appear too."
                },
                {
                    "question": "How can home care support daily living with Parkinson's?",
                    "answer": "Support can include mobility help, medication reminders, and routines that reduce fatigue."
                }
            ],
            "stroke_awareness": [
                {
                    "question": "What are the FAST warning signs of stroke?",
                    "answer": "Face drooping, Arm weakness, Speech trouble, Time to call emergency services."
                },
                {
                    "question": "What does stroke recovery at home involve?",
                    "answer": "Rehab exercises, speech or mobility therapy, and home adaptations are common parts of recovery."
                }
            ],
            "palliative_care": [
                {
                    "question": "What is the difference between palliative and hospice care?",
                    "answer": "Palliative care can begin earlier in illness, while hospice focuses on end-of-life comfort."
                },
                {
                    "question": "When should families ask about palliative care?",
                    "answer": "Early conversations help align care with comfort goals and reduce stress during health changes."
                }
            ]
        }

        additional_faqs = theme_faqs.get(week_theme, [])
        all_faqs = base_faqs + additional_faqs
        
        return random.sample(all_faqs, min(len(all_faqs), random.randint(3, 5)))
