"""Researcher Agent - Evidence-driven research with web discovery and LLM synthesis."""
import json
import random
from typing import Dict, List
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
        
        # Get theme-specific stats
        theme_stats = self.claims.get("themes", {}).get(theme, [])
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

        # Determine current week theme and context
        current_week = self._get_current_week_theme()
        week_theme = current_week.get('theme', 'general')

        # Step 1: Discover web sources
        sources = await self._discover_sources(
            market_config['name'],
            market_config['province'],
            week_theme
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

        self.log_info(f"✓ Evidence-driven research complete for {market_config['name']}")
        return research_pack

    def _get_current_week_theme(self) -> Dict:
        """Get current week theme from content calendar."""
        # Check for WEEK_OVERRIDE environment variable
        import os
        week_override = os.getenv("WEEK_OVERRIDE", "").strip()
        if week_override:
            week_key = f"week_{week_override}"
            if week_key in self.content_calendar["rotation_schedule"]:
                return self.content_calendar["rotation_schedule"][week_key]
        
        # Calculate based on current date
        from datetime import date
        today = date.today()
        week_of_year = today.isocalendar()[1]
        
        # Map to 12-week rotation (updated from 16-week)
        rotation_week = ((week_of_year - 1) % 12) + 1
        week_key = f"week_{rotation_week}"
        
        return self.content_calendar["rotation_schedule"].get(
            week_key, 
            self.content_calendar["rotation_schedule"]["week_1"]
        )

    async def _discover_sources(
        self,
        market: str,
        province: str,
        week_theme: str
    ) -> List[Dict]:
        """Discover relevant web sources."""
        try:
            sources = await self.web_discovery.discover_sources(
                market=market,
                province=province,
                week_theme=week_theme,
                year=datetime.now().year
            )
            self.log_info(f"Discovered {len(sources)} sources for {market}")
            return sources
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
        
        # Build base research structure
        research_pack = {
            "market": market_key,
            "market_name": market_config["name"],
            "province": market_config["province"],
            "location_url": market_config["location_url"],
            "week_theme": week_theme,
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
        research_pack["keywords"] = self._generate_keywords(market_config, week_theme)
        research_pack["faqs"] = self._generate_faqs(market_config, week_theme)
        research_pack["local_resources"] = self._generate_local_resources(market_config, week_theme)
        research_pack["medical_sources"] = self._generate_medical_sources(market_config, week_theme)

        # Add anti-generic constraints
        research_pack["anti_generic"] = self.research_sources_config.get("anti_generic", {})

        # Add vetted stats from claims.yaml
        research_pack["vetted_stats"] = self._get_vetted_stats(
            market_config.get("province", ""),
            week_theme
        )
        
        # Add H2 seeds from market config (prefer h2_seeds, fallback to h2_prompts)
        research_pack["h2_seeds"] = market_config.get("h2_seeds", market_config.get("h2_prompts", []))
        
        # Add must-include entities from market config
        research_pack["must_include_entities"] = market_config.get("must_include_entities", [])
        
        # Assign story lead type for this market (rotates across markets)
        research_pack["assigned_story_lead_type"] = self.get_next_story_lead_type()
        
        # Add local authority links for inline use
        research_pack["local_authority_links"] = market_config.get("local_resources", [])[:2]

        return research_pack

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
                "hook": f"With {market_config['province']}'s latest home care funding announcement, families have new options to explore.",
                "source_url": "",
                "relevance": f"Local funding affects {market_config['name']} families directly"
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
                "why_now": "New Year is ideal time for planning conversations",
                "local_resource": f"{market_config['name']} CLSC or local health authority"
            },
            {
                "action": "Request a home safety assessment from your local health authority",
                "why_now": "Winter hazards make this especially timely",
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

        theme_sources = {
            "winter_safety": [
                {
                    "title": "Health Canada - Cold Weather Safety",
                    "url": "https://www.canada.ca/en/health-canada/services/healthy-living/your-health/environment/extreme-cold.html",
                    "publisher": "Health Canada",
                    "summary": "Guidelines for preventing cold-related health issues in seniors."
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
            "new_year_care_planning": [
                {
                    "title": "Health Canada - Caring for Seniors",
                    "url": "https://www.canada.ca/en/public-health/services/health-promotion/aging-seniors/publications/caring-for-seniors.html",
                    "publisher": "Public Health Agency of Canada",
                    "summary": "Resources for families planning care for aging loved ones."
                }
            ]
        }

        additional_sources = theme_sources.get(week_theme, [])
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

    def _generate_keywords(self, market_config: Dict, week_theme: str) -> Dict:
        """Generate contextual keywords based on theme."""
        primary_pool = market_config["primary_keyword_pool"]
        secondary_pool = market_config["secondary_keyword_pool"]

        primary = random.choice(primary_pool)

        theme_keywords = {
            "new_year_care_planning": ["senior care planning", "aging at home", "care plan review", "senior wellness"],
            "winter_safety": ["winter senior safety", "cold weather care", "home heating safety", "falls prevention"],
            "valentines_companionship": ["senior companionship", "elderly social connection", "loneliness prevention", "senior social activities"],
            "tax_season_prep": ["home care tax credits", "medical expense deduction", "senior tax planning", "care cost savings"]
        }

        theme_specific = theme_keywords.get(week_theme, [])
        available_secondary = secondary_pool + theme_specific

        secondary_count = random.randint(4, 6)
        secondary = random.sample(available_secondary, min(len(available_secondary), secondary_count))

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
                    "question": "When should families start planning for home care?",
                    "answer": "Ideally before a crisis occurs. The new year is an excellent time to have family discussions about care preferences and explore local options."
                },
                {
                    "question": "What should a senior care plan include?",
                    "answer": "A comprehensive plan covers daily living assistance, medical needs, emergency contacts, financial considerations, and your loved one's personal preferences."
                }
            ],
            "winter_safety": [
                {
                    "question": "How can we prevent falls for seniors in winter?",
                    "answer": "Ensure proper footwear, keep walkways clear, install grab bars, and consider companion care for outdoor activities during icy conditions."
                }
            ],
            "tax_season_prep": [
                {
                    "question": "Can I claim home care expenses on my taxes?",
                    "answer": "Yes, many home care expenses qualify for the Medical Expense Tax Credit. Keep receipts and consult CRA guidelines or a tax professional."
                }
            ]
        }

        additional_faqs = theme_faqs.get(week_theme, [])
        all_faqs = base_faqs + additional_faqs
        
        return random.sample(all_faqs, min(len(all_faqs), random.randint(3, 5)))
