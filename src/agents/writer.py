"""Writer Agent: Generates Brightspot-ready HTML articles."""
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from agents.base_agent import BaseAgent
from utils.config_loader import load_config
from utils.logger import get_logger

logger = get_logger(__name__)


class WriterAgent(BaseAgent):
    """Agent responsible for writing HTML articles and metadata.
    
    Note: Image selection is handled separately by the coordinator
    after all articles are finalized, to ensure unique images per market.
    """

    def __init__(self):
        super().__init__()
        self.brand_config = load_config("brand")
        self.brightspot_config = load_config("brightspot_guide")
        self.markets_config = load_config("markets")

    async def run(
        self,
        tone_profile: Dict,
        research_pack: Dict,
        markets_config: Dict
    ) -> Dict:
        """Generate HTML article and metadata for a market.

        Args:
            tone_profile: Extracted tone profile from US Learning Center
            research_pack: Research data for this market
            markets_config: Full markets configuration

        Returns:
            Dict: Article metadata with HTML content and metadata JSON
        """
        market = research_pack["market"]
        market_name = research_pack["market_name"]
        
        self.log_info(f"Generating article for {market_name}...")
        
        # Generate article content with validation loop
        max_attempts = 2
        article_data = None
        
        for attempt in range(max_attempts):
            article_data = await self._generate_article(
                tone_profile,
                research_pack,
                markets_config
            )
            
            # Validate critical requirements
            issues = self._validate_article_requirements(article_data, research_pack)
            
            if not issues:
                break
            elif attempt < max_attempts - 1:
                self.log_warning(f"Validation issues for {market_name} (attempt {attempt + 1}): {issues[:2]}")
                # Fix meta description if too short
                self._fix_meta_description(article_data, research_pack, markets_config)
            else:
                self.log_warning(f"Using article despite issues: {issues[:2]}")

        # Enforce locality and linking before HTML build
        self._ensure_local_h2(article_data, research_pack["market_name"])
        self._ensure_internal_links(article_data, research_pack, markets_config)
        self._ensure_cta_localization(article_data, research_pack, markets_config)
        self._force_meta_description(article_data, research_pack, markets_config)
        
        # Note: Image selection is now handled by coordinator after all articles
        # are finalized to ensure unique images per market. Use placeholder here.
        placeholder_image = {
            "url": self.brightspot_config.get("placeholder_image", ""),
            "url_large": self.brightspot_config.get("placeholder_image", ""),
            "alt_text": f"Home care in {market_name}",
            "photographer": "Pending",
            "credit": "",
            "relevance_score": 0,
            "match_description": "Image will be selected after article finalization"
        }
        
        # Build Brightspot HTML with placeholder image
        html_content = self._build_brightspot_html(
            article_data,
            research_pack,
            markets_config,
            placeholder_image
        )
        
        # Placeholder image metadata - will be updated by coordinator
        images = [
            {
                "url": self.brightspot_config.get("placeholder_image", ""),
                "credit": "",
                "photographer": "Pending",
                "recommended_filename": f"{market}-hero-pexels.jpg",
                "alt_text": f"Home care in {market_name}",
                "is_recommended": False,
                "relevance_score": 0,
                "match_description": "Pending - image selection deferred to coordinator"
            }
        ]
        
        # Generate metadata
        metadata = {
            "market": market,
            "market_name": market_name,
            "title": article_data["title"],
            "suggested_slug": self._generate_slug(article_data["title"]),
            "meta_title": self._generate_meta_title(article_data["title"]),
            "meta_description": article_data.get("meta_description", ""),
            "primary_keyword": research_pack["keywords"]["primary"],
            "secondary_keywords": research_pack["keywords"]["secondary"],
            "internal_links": article_data["internal_links"],
            "citations": self._extract_citations(research_pack),
            "images": images,
            "word_count": self._count_words(html_content),
            "generated_at": datetime.now().isoformat()
        }
        
        self.log_info(f"✓ Article generated for {market_name}")
        
        return {
            "market": market,
            "market_name": market_name,
            "title": article_data["title"],
            "primary_keyword": research_pack["keywords"]["primary"],
            "html_content": html_content,
            "metadata": metadata,
            "image_filename": f"{market}-hero-pexels.jpg",  # Will be updated by coordinator
            "html_filename": f"{market}.html",
            "json_filename": f"{market}.json"
        }

    async def _generate_article(
        self,
        tone_profile: Dict,
        research_pack: Dict,
        markets_config: Dict
    ) -> Dict:
        """Generate article content using evidence-driven brief from researcher."""
        model_config = load_config("model_routing")["models"]["writing"]
        market_config = markets_config["markets"][research_pack["market"]]
        
        brand_colors = self.brand_config.get('color_palette', {})
        seo_reqs = self.brand_config.get('content_guidelines', {}).get('seo_requirements', {})
        
        # Extract evidence-driven components from research pack
        story_leads = research_pack.get("story_leads", [])
        news_pegs = research_pack.get("news_pegs", [])
        evidence_cards = research_pack.get("evidence_cards", [])
        actionable_takeaways = research_pack.get("actionable_takeaways", [])
        content_suggestions = research_pack.get("content_suggestions", {})
        anti_generic = research_pack.get("anti_generic", {})
        
        # New anti-duplication components
        vetted_stats = research_pack.get("vetted_stats", [])
        h2_seeds = research_pack.get("h2_seeds", [])
        must_include_entities = research_pack.get("must_include_entities", [])
        assigned_story_lead_type = research_pack.get("assigned_story_lead_type", "scene")
        local_authority_links = research_pack.get("local_authority_links", [])
        
        # Format story leads for prompt
        story_leads_text = ""
        for i, lead in enumerate(story_leads[:3], 1):
            story_leads_text += f"\n{i}. [{lead.get('type', 'general').upper()}]: {lead.get('lead', '')}"
            if lead.get('source'):
                story_leads_text += f" (Source: {lead['source']})"
        
        # Format evidence cards for prompt
        evidence_text = ""
        for card in evidence_cards[:6]:
            evidence_text += f"\n- FACT: {card.get('fact', '')}"
            evidence_text += f"\n  SOURCE: {card.get('source', 'N/A')}"
            evidence_text += f"\n  USE: {card.get('how_to_use', '')}"
        
        # Format news pegs
        news_pegs_text = ""
        for peg in news_pegs[:2]:
            news_pegs_text += f"\n- {peg.get('hook', '')}"
            if peg.get('source_url'):
                news_pegs_text += f" [{peg['source_url']}]"
        
        # Format actionable takeaways
        actions_text = ""
        for action in actionable_takeaways[:4]:
            actions_text += f"\n- ACTION: {action.get('action', '')}"
            actions_text += f"\n  WHY NOW: {action.get('why_now', '')}"
        
        # Format banned openers
        banned_openers = anti_generic.get("banned_openers", [])
        banned_text = ", ".join(f'"{b}"' for b in banned_openers[:5])
        if not banned_text:
            banned_text = '"Many families...", "In today\'s world...", "It\'s no secret that..."'
        
        # Format local resources (avoid nested f-strings)
        local_resources_text = ""
        for r in research_pack['local_resources'][:4]:
            local_resources_text += f"- {r['title']}: {r['url']} - {r.get('description', '')}\n"
        
        # Format vetted stats for prompt
        vetted_stats_text = ""
        for stat in vetted_stats[:4]:
            vetted_stats_text += f"\n- STAT: {stat.get('stat', '')}"
            vetted_stats_text += f"\n  SOURCE: {stat.get('source', '')} ({stat.get('source_url', '')})"
            vetted_stats_text += f"\n  YEAR: {stat.get('year', 'N/A')}"
        
        # Format H2 seeds
        h2_seeds_text = "\n".join(f"- {h2}" for h2 in h2_seeds[:3]) if h2_seeds else "- None specified"
        
        # Format must-include entities
        entities_text = ", ".join(must_include_entities[:5]) if must_include_entities else "None specified"
        
        # Format local authority links for inline use
        local_authority_text = ""
        for link in local_authority_links[:2]:
            local_authority_text += f"\n- {link.get('anchor', '')}: {link.get('url', '')}"
        
        # Build JSON example as a separate string to avoid f-string issues
        json_example = '{"title": "H1 title with primary keyword", "meta_description": "150-160 char SEO description", "include_faqs": false, "internal_links": ["link1", "link2"], "sections": [{"type": "h1", "content": "Title"}, {"type": "deck", "content": "Subheadline"}, {"type": "h2", "content": "Section heading"}, {"type": "content", "content": "Paragraph with evidence"}, {"type": "callout", "title": "Local Hook", "content": "Content"}, {"type": "h2", "content": "What You Can Do This Week"}, {"type": "content", "content": "Checklist"}, {"type": "resources", "content": "Resources"}, {"type": "cta", "content": "CTA", "button_text": "Button", "link": "https://thekey.ca/getting-started"}], "faqs": []}'
        
        prompt = f"""Write a NEWS-WORTHY, evidence-driven article for {research_pack['market_name']} home care that will engage readers and rank well for SEO.

=== CORE IDENTITY ===
MARKET: {research_pack['market_name']}, {research_pack['province']}
PRIMARY KEYWORD: {research_pack['keywords']['primary']}
SECONDARY KEYWORDS: {', '.join(research_pack['keywords']['secondary'][:6])}
HEALTHCARE CONTEXT: {market_config['healthcare_context']}

=== ASSIGNED STORY LEAD TYPE: {assigned_story_lead_type.upper()} ===
Your opening paragraph MUST use a {assigned_story_lead_type} lead type.
Use one of these as inspiration:
{story_leads_text if story_leads_text else "- Create a compelling opening matching the assigned lead type"}

=== NEWS PEGS (MAKE IT TIMELY) ===
Include at least one of these timely hooks in your article:
{news_pegs_text if news_pegs_text else "- Reference current season and its relevance to senior care"}

=== VETTED STATISTICS (USE THESE - DO NOT INVENT) ===
You MUST include at least 2 of these vetted statistics WITH their source URLs:
{vetted_stats_text if vetted_stats_text else "- No vetted stats available - skip statistics rather than inventing"}

=== ADDITIONAL EVIDENCE CARDS ===
Optionally include these supporting facts:
{evidence_text if evidence_text else "- Include at least one statistic about senior care with a credible Canadian source"}

=== ACTIONABLE TAKEAWAYS (REQUIRED) ===
Include a "What You Can Do This Week" or "Practical Next Steps" section with:
{actions_text if actions_text else "- Provide 2-3 concrete actions families can take immediately"}

=== LOCAL HOOK ===
Title: {research_pack['local_hook']['title']}
Summary: {research_pack['local_hook']['summary']}
Citation: {research_pack['local_hook'].get('citation', '')}

=== H2 HEADINGS (MUST USE AT LEAST ONE) ===
You MUST use at least ONE of these city-specific H2 headings in your article:
{h2_seeds_text}

=== MUST-INCLUDE ENTITIES ===
Your article MUST mention these entities by name:
{entities_text}

=== LOCAL AUTHORITY LINKS (INLINE - NOT IN SEPARATE BLOCK) ===
Weave these local authority links INLINE within relevant paragraphs (not in a separate resources section):
{local_authority_text if local_authority_text else "- None specified"}

=== ANTI-GENERIC REQUIREMENTS (CRITICAL) ===
NEVER start with: {banned_text}
MUST INCLUDE:
- At least TWO vetted statistics with source URLs (from VETTED STATISTICS section above)
- At least ONE named local program or provincial initiative
- At least ONE concrete "this week" action for families
- Reference the provincial healthcare system by name
- At least TWO inline local authority links (not in a separate block)

=== SEO REQUIREMENTS ===
1. Primary keyword in H1 title (naturally integrated)
2. Primary keyword density: 1.5-2.5 percent
3. Meta description: 150-160 characters with keyword + value prop
4. Internal links: 2-4 total
5. Semantic variations throughout (elderly care, senior support, aging in place, etc.)

=== CONTENT STRUCTURE ===
TARGET: 900-1200 words of HIGH-QUALITY, specific content
REQUIRED SECTIONS:
1. Compelling H1 with primary keyword
2. Deck/subheadline that promises value
3. Opening paragraph using {assigned_story_lead_type} lead type (NO generic openings)
4. "Why This Matters Now" or news peg section (2-3 paragraphs with vetted statistics)
5. 3-4 H2 sections (at least ONE from H2 SEEDS above)
6. Callout box with local hook and citation
7. "What You Can Do This Week" actionable checklist
8. Warm, helpful CTA (not salesy)
9. FAQ section (5 questions)
10. Medical disclaimer

CRITICAL: Do NOT create a separate "Local Resources" section. Instead, weave local authority links INLINE within relevant paragraphs throughout the article.

=== LOCAL RESOURCES ===
{local_resources_text}

=== INTERNAL LINKS (LOCATION PAGE MUST BE FIRST) ===
1. Location page (MUST be the first internal link): {market_config['location_url']}
   - Use anchor text like: "home care in {market_config['name']}" or "{market_config['name']} senior care services"
2. Service pages (choose 1 most relevant to theme):
   - Dementia: {markets_config['service_pages']['dementia']['url']}
   - Hospital to Home: {markets_config['service_pages']['hospital_to_home']['url']}
   - Use city-specific anchor text (e.g., "dementia care in {market_config['name']}")

=== CANADIAN ENGLISH (MANDATORY) ===
Use Canadian spelling: colour, centre, behaviour, organise, recognise, analyse
Reference: {market_config['healthcare_context']}
Use "home care" NOT "home health care"

=== BRAND VOICE ===
Warm, empathetic, professional. Solution-focused. Never pushy or salesy.
TheKey positioning: premium, white-glove care that helps seniors age with dignity.

Return ONLY valid JSON:

{json_example}"""

        response = await self.openai.generate(
            model=model_config["model"],
            prompt=prompt,
            max_tokens=model_config["max_tokens"],
            temperature=model_config["temperature"]
        )

        # Parse JSON response with better error handling
        article_data = None
        try:
            # Try direct JSON parsing first
            article_data = json.loads(response.strip())
        except json.JSONDecodeError:
            # Try to extract JSON from response if wrapped in other text
            import re
            json_match = re.search(r'\{.*\}', response.strip(), re.DOTALL)
            if json_match:
                try:
                    article_data = json.loads(json_match.group())
                except json.JSONDecodeError:
                    pass

        if article_data is None:
            # Final fallback: return basic structure
            self.log_warning(f"Failed to parse JSON response for {research_pack['market']}. Response: {response[:200]}...")
            article_data = {
                "title": f"{research_pack['market_name']} Home Care Services",
                "meta_description": f"Professional home care services in {research_pack['market_name']}",
                "include_faqs": False,
                "internal_links": [market_config['location_url']],
                "sections": [
                    {
                        "type": "h1",
                        "content": f"{research_pack['market_name']} Home Care Services"
                    },
                    {
                        "type": "deck",
                        "content": f"Professional home care services designed to help seniors in {research_pack['market_name']} age with dignity and independence."
                    },
                    {
                        "type": "content",
                        "content": f"At TheKey, we provide premium, white-glove home care designed to help your loved one age with dignity and independence. Our services in {research_pack['market_name']} include personalized care planning, companion care, and specialized support for various health conditions."
                    },
                    {
                        "type": "cta",
                        "content": f"Discover how TheKey can help your loved one thrive at home in {research_pack['market_name']}.",
                        "button_text": "Get Started Today",
                        "link": markets_config['cta_base_url']
                    }
                ],
                "faqs": []
            }

        # Enforce FAQ presence using research-pack FAQs as fallback
        fallback_faqs = research_pack.get("faqs", [])
        if not article_data.get("faqs"):
            article_data["faqs"] = fallback_faqs[:5]
        else:
            article_data["faqs"] = article_data.get("faqs", [])[:5]
        article_data["include_faqs"] = True if article_data.get("faqs") else False

        return article_data

    def _build_brightspot_html(
        self,
        article_data: Dict,
        research_pack: Dict,
        markets_config: Dict,
        selected_image: Optional[Dict] = None
    ) -> str:
        """Build Brightspot-compliant HTML with TheKey brand styling.
        
        Args:
            article_data: Article content data
            research_pack: Research pack with market info
            markets_config: Markets configuration
            selected_image: Selected hero image from ImageSelectorAgent
            
        Returns:
            str: Brightspot-compliant HTML
        """
        market = research_pack["market"].lower()
        market_name = research_pack["market_name"]
        title = article_data["title"]
        import re

        def _sanitize_inline_sources(text: str) -> str:
            """Remove inline '(Source: ...)' snippets; move sourcing to Sources block instead."""
            cleaned = re.sub(r"\(Source:[^)]+\)", "", text, flags=re.IGNORECASE)
            cleaned = re.sub(r"Source:\s*[^<\n]+", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\(.*?source:.*?\)", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
            cleaned = re.sub(r"<span[^>]*>[^<]*source:[^<]*</span>", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"Source:\s*<a[^>]+>.*?</a>", "", cleaned, flags=re.IGNORECASE | re.DOTALL)
            cleaned = re.sub(r"Source:\s*[^.<]+(?:\.\s*|$)", "", cleaned, flags=re.IGNORECASE)
            return cleaned

        def _split_wrapped_lists(text: str) -> str:
            """Unwrap lists incorrectly nested inside <p> tags."""
            text = re.sub(r"<p>([^<]*?)(<ol[^>]*>.*?</ol>)</p>", r"<p>\1</p>\n\2", text, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r"<p>([^<]*?)(<ul[^>]*>.*?</ul>)</p>", r"<p>\1</p>\n\2", text, flags=re.IGNORECASE | re.DOTALL)
            return text

        def _remove_banned_phrases(text: str) -> str:
            banned = self.brand_config.get("anti_generic_requirements", {}).get("banned_openers", [])
            if not banned:
                banned = [
                    "Many families",
                    "As we age",
                    "In today's world",
                    "It's no secret that",
                    "When it comes to",
                    "There's no doubt that",
                    "In recent years",
                    "As we all know",
                    "For many people",
                    "It goes without saying",
                    "In this article"
                ]
            for phrase in banned:
                text = re.sub(rf"\b{re.escape(phrase)}\b", "Families", text, flags=re.IGNORECASE)
            return text
        
        # Get brand colors and typography
        colors = self.brand_config.get('color_palette', {})
        typo = self.brand_config.get('typography', {})
        styling = self.brand_config.get('styling_patterns', {})
        
        # CSS class prefix
        prefix = f"bs-{market}-"
        
        # Build HTML with TheKey brand styling
        html_lines = [
            f"<!-- Brightspot Content Block: {title} -->",
            f'<div class="blog-content-module">',
        ]
        
        # Hero image with selected Pexels image
        hero_style = styling.get('hero_image', {})
        
        # Get image URL and alt text from selected image
        if selected_image:
            image_url = selected_image.get("url_large", selected_image.get("url", ""))
            image_alt = selected_image.get("alt_text", selected_image.get("alt", f"{market_name} home care"))
            photographer = selected_image.get("photographer", "")
            credit_html = selected_image.get("credit", "")
        else:
            # Fallback to placeholder if no image selected
            image_url = self.brightspot_config.get("placeholder_image", "")
            image_alt = f"{market_name} home care"
            photographer = ""
            credit_html = ""
        
        html_lines.append(
            f'<div style="margin-bottom: {hero_style.get("margin_bottom", "32px")};">'
        )
        html_lines.append(
            f'<img src="{image_url}" '
            f'alt="{image_alt}" '
            f'style="width: {hero_style.get("width", "100%")}; height: {hero_style.get("height", "auto")}; '
            f'border-radius: {hero_style.get("border_radius", "8px")}; '
            f'box-shadow: {hero_style.get("box_shadow", "0 4px 6px -1px rgba(0, 0, 0, 0.1)")};">'
        )
        # Add image credit if available
        if credit_html:
            html_lines.append(
                f'<p style="font-size: 12px; color: #999; margin-top: 8px; text-align: right;">'
                f'{credit_html}</p>'
            )
        html_lines.append('</div>')
        
        # Build sections with inline styles matching TheKey brand
        for section in article_data.get("sections", []):
            if section["type"] == "h1":
                h1_style = typo.get('h1', {})
                html_lines.append(
                    f'<h1 style="font-family: {h1_style.get("font_family", "Times New Roman, serif")}; '
                    f'font-size: {h1_style.get("font_size", "36px")}; '
                    f'color: {h1_style.get("color", colors.get("everest", "#06262D"))}; '
                    f'margin-bottom: {h1_style.get("margin_bottom", "10px")};">'
                    f'{section["content"]}</h1>'
                )
            elif section["type"] == "deck":
                deck_style = typo.get('deck_subheadline', {})
                html_lines.append(
                    f'<p style="font-size: {deck_style.get("font_size", "20px")}; '
                    f'line-height: {deck_style.get("line_height", "28px")}; '
                    f'color: {deck_style.get("color", colors.get("muted_text", "#475569"))}; '
                    f'font-weight: {deck_style.get("font_weight", "500")}; '
                    f'font-style: {deck_style.get("font_style", "italic")}; '
                    f'margin-top: {deck_style.get("margin_top", "-0.5rem")}; '
                    f'margin-bottom: {deck_style.get("margin_bottom", "32px")};">'
                    f'{section["content"]}</p>'
                )
            elif section["type"] == "h2":
                h2_style = typo.get('h2', {})
                html_lines.append(
                    f'<h2 style="font-family: {h2_style.get("font_family", "Times New Roman, serif")}; '
                    f'font-size: {h2_style.get("font_size", "24px")}; '
                    f'color: {h2_style.get("color", colors.get("everest", "#06262D"))}; '
                    f'margin-top: {h2_style.get("margin_top", "30px")}; '
                    f'margin-bottom: {h2_style.get("margin_bottom", "16px")};">'
                    f'{section["content"]}</h2>'
                )
            elif section["type"] == "content":
                body_style = typo.get('body', {})
                content_html = _remove_banned_phrases(_split_wrapped_lists(_sanitize_inline_sources(section["content"])))
                # Avoid wrapping block elements in <p> to prevent invalid nesting
                block_indicators = ("<ul", "<ol", "<div", "<table", "<blockquote", "<h2", "<h3", "<h4", "<p>")
                if any(tag in content_html.lower() for tag in block_indicators):
                    html_lines.append(content_html)
                else:
                    html_lines.append(
                        f'<p style="font-family: {body_style.get("font_family", "Helvetica Neue, Helvetica, Arial, sans-serif")}; '
                        f'font-size: {body_style.get("font_size", "16px")}; '
                        f'line-height: {body_style.get("line_height", "1.6")}; '
                        f'color: {body_style.get("color", "#333")}; '
                        f'margin-bottom: {body_style.get("margin_bottom", "16px")};">'
                        f'{content_html}</p>'
                    )
            elif section["type"] == "callout":
                callout_style = styling.get('callout_box', {})
                callout_title = section.get("title", "") or research_pack.get("local_hook", {}).get("title", "Did you know?")
                if "local hook" in callout_title.lower():
                    callout_title = research_pack.get("local_hook", {}).get("title", callout_title)
                callout_content = _remove_banned_phrases(_split_wrapped_lists(_sanitize_inline_sources(section.get("content", ""))))
                html_lines.append(
                    f'<div style="background-color: {callout_style.get("background_color", colors.get("light_gold", "#F0EEDC"))}; '
                    f'padding: {callout_style.get("padding", "24px")}; '
                    f'border-left: {callout_style.get("border_left", "4px solid " + colors.get("gold", "#D1B886"))}; '
                    f'margin: {callout_style.get("margin", "24px 0")};">'
                )
                html_lines.append(
                    f'<h3 style="margin-top: {callout_style.get("h3_margin_top", "0")}; '
                    f'color: {callout_style.get("h3_color", colors.get("everest", "#06262D"))};">'
                    f'{callout_title}</h3>'
                )
                html_lines.append(f'<p style="margin-bottom: 0;">{callout_content}</p>')
                html_lines.append('</div>')
            elif section["type"] == "cta":
                cta_style = styling.get('cta_box', {})
                html_lines.append(
                    f'<div style="background-color: {cta_style.get("background_color", colors.get("everest", "#06262D"))}; '
                    f'color: {cta_style.get("color", "white")}; '
                    f'padding: {cta_style.get("padding", "32px")}; '
                    f'border-radius: {cta_style.get("border_radius", "8px")}; '
                    f'margin-top: {cta_style.get("margin_top", "32px")}; '
                    f'text-align: {cta_style.get("text_align", "center")};">'
                )
                html_lines.append(
                    f'<h3 style="color: {cta_style.get("h3_color", "white")}; '
                    f'margin-top: {cta_style.get("h3_margin_top", "0")};">'
                    f'{section["content"]}</h3>'
                )
                html_lines.append(
                    f'<p style="color: #e5e7eb;">{section.get("supporting_text", "Speak with a care expert today.")}</p>'
                )
                html_lines.append(
                    f'<a href="{section["link"]}" '
                    f'style="display: inline-block; '
                    f'background-color: {cta_style.get("button_background", colors.get("gold", "#D1B886"))}; '
                    f'color: {cta_style.get("button_color", colors.get("everest", "#06262D"))}; '
                    f'font-weight: {cta_style.get("button_font_weight", "bold")}; '
                    f'padding: {cta_style.get("button_padding", "12px 24px")}; '
                    f'border-radius: {cta_style.get("button_border_radius", "4px")}; '
                    f'text-decoration: none; margin-top: 16px;">'
                    f'{section["button_text"]}</a>'
                )
                html_lines.append('</div>')
            elif section["type"] == "resources":
                # Convert markdown-style links to proper HTML and format as list
                body_style = typo.get('body', {})
                content = section["content"]
                # Convert markdown links [text](url) to HTML <a> tags
                content = self._convert_markdown_links_to_html(content, colors)
                html_lines.append(
                    f'<div style="font-family: {body_style.get("font_family", "Helvetica Neue, Helvetica, Arial, sans-serif")}; '
                    f'font-size: {body_style.get("font_size", "16px")}; '
                    f'line-height: {body_style.get("line_height", "1.8")}; '
                    f'color: {body_style.get("color", "#333")}; '
                    f'margin-bottom: {body_style.get("margin_bottom", "16px")};">'
                    f'{content}</div>'
                )
        
        # Add FAQ section conditionally
        include_faqs = article_data.get("include_faqs", False)
        faqs = article_data.get("faqs", [])

        self.log_info(f"FAQ decision: include_faqs={include_faqs}, faq_count={len(faqs)}")

        if include_faqs and faqs:
            self.log_info("Including FAQ section in HTML")
            h2_style = typo.get('h2', {})
            html_lines.append(
                f'<h2 style="font-family: {h2_style.get("font_family", "Times New Roman, serif")}; '
                f'font-size: {h2_style.get("font_size", "24px")}; '
                f'color: {h2_style.get("color", colors.get("everest", "#06262D"))}; '
                f'margin-top: {h2_style.get("margin_top", "30px")}; '
                f'margin-bottom: {h2_style.get("margin_bottom", "16px")};">'
                f'Frequently Asked Questions</h2>'
            )

            body_style = typo.get('body', {})
            for faq in faqs:
                # Handle different FAQ structures safely
                question = faq.get("question") or faq.get("q") or "Question not available"
                answer = faq.get("answer") or faq.get("a") or "Answer not available"

                html_lines.append(
                    f'<p style="font-family: {body_style.get("font_family", "Helvetica Neue, Helvetica, Arial, sans-serif")}; '
                    f'font-size: {body_style.get("font_size", "16px")}; '
                    f'line-height: {body_style.get("line_height", "1.6")}; '
                    f'color: {body_style.get("color", "#333")}; '
                    f'margin-bottom: {body_style.get("margin_bottom", "16px")};">'
                    f'<strong>Q: {question}</strong></p>'
                )
                html_lines.append(
                    f'<p style="font-family: {body_style.get("font_family", "Helvetica Neue, Helvetica, Arial, sans-serif")}; '
                    f'font-size: {body_style.get("font_size", "16px")}; '
                    f'line-height: {body_style.get("line_height", "1.6")}; '
                    f'color: {body_style.get("color", "#333")}; '
                    f'margin-bottom: {body_style.get("margin_bottom", "16px")};">'
                    f'A: {answer}</p>'
                )
        else:
            self.log_info("Skipping FAQ section - not requested or no FAQs available")
        
        # Add internal links section (Related Services)
        internal_links = article_data.get("internal_links", [])
        if internal_links:
            h2_style = typo.get('h2', {})
            body_style = typo.get('body', {})
            html_lines.append(
                f'<h2 style="font-family: {h2_style.get("font_family", "Times New Roman, serif")}; '
                f'font-size: {h2_style.get("font_size", "24px")}; '
                f'color: {h2_style.get("color", colors.get("everest", "#06262D"))}; '
                f'margin-top: {h2_style.get("margin_top", "30px")}; '
                f'margin-bottom: {h2_style.get("margin_bottom", "16px")};">'
                f'Related TheKey Services</h2>'
            )
            
            # Create service link labels from URLs
            service_labels = {
                "dementia": "Dementia & Alzheimer's Care",
                "hospital-to-home": "Hospital to Home Transition Care",
                "parkinsons": "Parkinson's Disease Care",
                "stroke": "Stroke Recovery Care",
                "end-of-life": "Palliative & End-of-Life Care",
                "heart-health": "Heart Health & Cardiac Care",
                "cancer": "Cancer Care Support",
                "locations": "Find Care in Your Area"
            }
            
            html_lines.append(
                f'<ul style="font-family: {body_style.get("font_family", "Helvetica Neue, Helvetica, Arial, sans-serif")}; '
                f'font-size: {body_style.get("font_size", "16px")}; '
                f'line-height: 1.8; color: {body_style.get("color", "#333")}; '
                f'margin-bottom: 24px; padding-left: 20px;">'
            )
            
            for link in internal_links:
                # Determine label from URL
                label = "Learn More"
                for key, value in service_labels.items():
                    if key in link.lower():
                        label = value
                        break
                
                html_lines.append(
                    f'<li style="margin-bottom: 8px;">'
                    f'<a href="{link}" style="color: {colors.get("everest", "#06262D")}; '
                    f'text-decoration: underline; font-weight: 600;">{label}</a></li>'
                )
            
            html_lines.append('</ul>')
        
        # Add sources section using citations extracted from research
        citations = self._extract_citations(research_pack)
        if citations:
            h2_style = typo.get('h2', {})
            body_style = typo.get('body', {})
            html_lines.append(
                f'<h2 style="font-family: {h2_style.get("font_family", "Times New Roman, serif")}; '
                f'font-size: {h2_style.get("font_size", "24px")}; '
                f'color: {h2_style.get("color", colors.get("everest", "#06262D"))}; '
                f'margin-top: {h2_style.get("margin_top", "30px")}; '
                f'margin-bottom: {h2_style.get("margin_bottom", "12px")};">'
                f'Sources</h2>'
            )
            html_lines.append(
                f'<ul style="font-family: {body_style.get("font_family", "Helvetica Neue, Helvetica, Arial, sans-serif")}; '
                f'font-size: {body_style.get("font_size", "16px")}; '
                f'line-height: 1.6; color: {body_style.get("color", "#333")}; '
                f'margin-bottom: 16px; padding-left: 20px;">'
            )
            link_color = colors.get("everest", "#06262D")
            for cite in citations:
                # If citation already has an href, keep it; otherwise render plain text
                if "http" in cite:
                    parts = cite.split(", ")
                    text = parts[0]
                    url = parts[-1] if parts[-1].startswith("http") else None
                    if url:
                        html_lines.append(
                            f'<li style="margin-bottom: 8px;"><a href="{url}" '
                            f'style="color: {link_color}; text-decoration: underline; font-weight: 600;" '
                            f'target="_blank" rel="noopener">{text}</a></li>'
                        )
                    else:
                        html_lines.append(f'<li style="margin-bottom: 8px;">{cite}</li>')
                else:
                    html_lines.append(f'<li style="margin-bottom: 8px;">{cite}</li>')
            html_lines.append('</ul>')
        
        # Add disclaimer
        html_lines.append(
            f'<p style="font-size: 12px; color: #999; margin: 32px 0 0 0; padding-top: 16px; border-top: 1px solid #eee;">'
            f'{self.brand_config["medical_disclaimer"]["text"]}</p>'
        )
        
        # Close wrapper
        html_lines.append('</div>')
        
        html_output = '\n'.join(html_lines)
        html_output = _remove_banned_phrases(html_output)
        return html_output

    def _generate_image_suggestions(
        self,
        article_data: Dict,
        research_pack: Dict
    ) -> List[Dict]:
        """Generate 2-3 image suggestions."""
        market = research_pack["market"]
        market_name = research_pack["market_name"]
        
        # Simulated image search (in production, use Pexels/Unsplash API)
        base_suggestions = [
            {
                "url": "https://images.pexels.com/photos/666335/pexels-photo-666335.jpeg",
                "credit": "Photo by Andrea Piacquadio on Pexels",
                "recommended_filename": f"{market}-elderly-care-pexels-1.jpg",
                "alt_text": f"Caregiver assisting senior at home in {market_name}",
                "is_recommended": False
            },
            {
                "url": "https://images.pexels.com/photos/4226140/pexels-photo-4226140.jpeg",
                "credit": "Photo by Cottonbro Studio on Pexels",
                "recommended_filename": f"{market}-senior-happiness-pexels-2.jpg",
                "alt_text": f"Happy elderly person receiving care in {market_name}",
                "is_recommended": False
            },
            {
                "url": "https://images.pexels.com/photos/5412163/pexels-photo-5412163.jpeg",
                "credit": "Photo by Kampus Production on Pexels",
                "recommended_filename": f"{market}-home-care-support-pexels-3.jpg",
                "alt_text": f"Professional caregiver providing support in {market_name}",
                "is_recommended": False
            }
        ]
        
        return base_suggestions

    def _generate_slug(self, title: str) -> str:
        """Generate URL slug from title."""
        # Simple slug generation
        slug = title.lower()
        slug = slug.replace(' ', '-')
        slug = ''.join(c for c in slug if c.isalnum() or c == '-')
        return slug

    def _generate_meta_title(self, title: str) -> str:
        """Generate SEO meta title without ellipsis."""
        # Keep title under 60 characters, trimming without adding "..."
        if len(title) <= 60:
            return title
        trimmed = title[:60].rstrip()
        # Avoid cutting mid-word if possible
        last_space = trimmed.rfind(" ")
        if last_space > 40:
            trimmed = trimmed[:last_space]
        return trimmed

    def _get_meta_description_authority(self, research_pack: Dict, markets_config: Dict) -> str:
        market_key = research_pack.get("market", "")
        market_config = markets_config.get("markets", {}).get(market_key, {})
        authority = (market_config.get("health_authority") or "").strip()
        if authority:
            return authority
        province = (market_config.get("province") or research_pack.get("province") or "").strip().lower()
        if province == "ontario":
            return "Ontario Health atHome"
        return "local health authority"

    def _force_meta_description(self, article_data: Dict, research_pack: Dict, markets_config: Dict) -> None:
        """Ensure meta description lands in 150-160 char range with keyword and value prop."""
        meta_desc = article_data.get("meta_description", "") or ""
        primary_keyword = research_pack.get("keywords", {}).get("primary", "")
        market_name = research_pack.get("market_name", "")
        authority = self._get_meta_description_authority(research_pack, markets_config)
        if 150 <= len(meta_desc) <= 160:
            return
        template = (
            f"{market_name} {primary_keyword.lower()} guide: use {authority}, local resources, "
            f"and practical steps to keep aging in place safe and supported this year."
        ).strip()
        if len(template) < 150:
            pad = " Learn how to schedule assessments, use tax credits, and make winter home safety improvements."
            template = (template + pad)[:200]  # temporary extension before trim
        if len(template) > 160:
            template = template[:157].rsplit(" ", 1)[0].rstrip(".,;:") + "."
        article_data["meta_description"] = template

    def _ensure_local_h2(self, article_data: Dict, market_name: str) -> None:
        """Ensure at least one H2 explicitly references the market for uniqueness."""
        sections = article_data.get("sections", [])
        market_lower = market_name.lower()
        has_local_h2 = any(
            (s.get("type") == "h2" and market_lower in s.get("content", "").lower())
            for s in sections
        )
        if not has_local_h2:
            sections.insert(
                0,
                {
                    "type": "h2",
                    "content": f"Local Home Care in {market_name}: What Families Should Know"
                }
            )
        article_data["sections"] = sections

    def _ensure_internal_links(self, article_data: Dict, research_pack: Dict, markets_config: Dict) -> None:
        """Guarantee location + theme-relevant service links exist."""
        market_config = markets_config["markets"][research_pack["market"]]
        links = article_data.get("internal_links", []) or []
        location_url = market_config["location_url"]
        service_url = self._map_theme_to_service_url(research_pack.get("week_theme", ""), markets_config)
        ordered: List[str] = []
        for candidate in [location_url, service_url] + links:
            if candidate and candidate not in ordered:
                ordered.append(candidate)
        article_data["internal_links"] = ordered[:4]

    def _ensure_cta_localization(self, article_data: Dict, research_pack: Dict, markets_config: Dict) -> None:
        """Localize CTA content and link."""
        market_name = research_pack["market_name"]
        service_url = self._map_theme_to_service_url(research_pack.get("week_theme", ""), markets_config)
        location_url = markets_config["markets"][research_pack["market"]]["location_url"]
        preferred_link = service_url or location_url
        for section in article_data.get("sections", []):
            if section.get("type") == "cta":
                section["link"] = preferred_link
                if not section.get("content"):
                    section["content"] = f"Talk to a {market_name} care expert today."
                # Keep supporting text but ensure locality
                section["supporting_text"] = section.get("supporting_text") or f"Connect with a local {market_name} care team for next steps."
                if not section.get("button_text"):
                    section["button_text"] = f"Get Care in {market_name}"

    def _map_theme_to_service_url(self, week_theme: str, markets_config: Dict) -> str:
        """Map weekly theme to the most relevant service URL."""
        services = markets_config.get("service_pages", {})
        theme = (week_theme or "").lower()
        if any(k in theme for k in ["hospital", "discharge", "post-hospital", "home"]):
            return services.get("hospital_to_home", {}).get("url", "")
        if any(k in theme for k in ["dementia", "memory", "alzheim"]):
            return services.get("dementia", {}).get("url", "")
        if any(k in theme for k in ["fall", "winter", "safety"]):
            return services.get("hospital_to_home", {}).get("url", "")
        if any(k in theme for k in ["tax", "financial"]):
            return markets_config.get("cta_base_url", "")
        return services.get("hospital_to_home", {}).get("url", "")

    def _extract_citations(self, research_pack: Dict) -> List[str]:
        """Extract citations from research pack."""
        citations = []
        
        # Local hook citation
        if "local_hook" in research_pack:
            citations.append(research_pack["local_hook"].get("citation", ""))
        
        # Medical source citations
        for source in research_pack.get("medical_sources", []):
            citations.append(source.get("citation", ""))
        
        # Local resource citations
        for resource in research_pack.get("local_resources", []):
            if resource.get("citation"):
                citations.append(resource["citation"])
        
        return [c for c in citations if c]

    def _convert_markdown_links_to_html(self, content: str, colors: Dict) -> str:
        """Convert markdown-style links to HTML anchor tags.
        
        Args:
            content: Text that may contain markdown links [text](url)
            colors: Brand color palette
            
        Returns:
            str: HTML with proper anchor tags
        """
        import re
        
        link_color = colors.get("everest", "#06262D")
        
        # Pattern to match markdown links: [text](url)
        pattern = r'\[([^\]]+)\]\(([^)]+)\)'
        
        def replace_link(match):
            text = match.group(1)
            url = match.group(2)
            return f'<a href="{url}" style="color: {link_color}; text-decoration: underline; font-weight: 600;" target="_blank" rel="noopener">{text}</a>'
        
        converted = re.sub(pattern, replace_link, content)
        
        # Also handle bare URLs that start with http
        url_pattern = r'(?<!["\'>])https?://[^\s<>"\')]+(?!["\'])'
        
        def replace_bare_url(match):
            url = match.group(0)
            # Don't convert if already inside an href
            return f'<a href="{url}" style="color: {link_color}; text-decoration: underline;" target="_blank" rel="noopener">{url}</a>'
        
        # Only apply if there are bare URLs not already converted
        if 'href="http' not in converted:
            converted = re.sub(url_pattern, replace_bare_url, converted)
        
        # Convert line breaks and list markers to proper HTML
        lines = converted.split('\n')
        formatted_lines = []
        for line in lines:
            line = line.strip()
            if line.startswith('- '):
                # Convert to list item style
                formatted_lines.append(f'<p style="margin: 8px 0; padding-left: 16px;">• {line[2:]}</p>')
            elif line:
                formatted_lines.append(f'<p style="margin: 8px 0;">{line}</p>')
        
        return ''.join(formatted_lines) if formatted_lines else converted

    def _count_words(self, html_content: str) -> int:
        """Count words in HTML content."""
        from bs4 import BeautifulSoup
        
        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['style', 'script', 'code']):
            element.decompose()
        
        text = soup.get_text(separator=' ')
        words = [w for w in text.split() if w.strip()]
        
        return len(words)

    def _validate_article_requirements(self, article_data: Dict, research_pack: Dict) -> List[str]:
        """Validate critical article requirements and return list of issues."""
        issues = []
        
        # Check meta description length (150-160 chars)
        meta_desc = article_data.get("meta_description", "")
        if len(meta_desc) < 150:
            issues.append(f"Meta description too short ({len(meta_desc)} chars, need 150+)")
        elif len(meta_desc) > 160:
            issues.append(f"Meta description too long ({len(meta_desc)} chars, max 160)")
        
        # Check for banned openers in content
        banned_openers = self.brand_config.get("anti_generic_requirements", {}).get("banned_openers", [])
        sections = article_data.get("sections", [])
        for section in sections:
            content = section.get("content", "")
            for banned in banned_openers:
                if content.lower().startswith(banned.lower()):
                    issues.append(f"Banned opener found: '{banned}'")
                    break
        
        # Check that title doesn't exceed 60 characters
        title = article_data.get("title", "")
        if len(title) > 70:  # Allow some flexibility
            issues.append(f"Title too long ({len(title)} chars)")
        
        return issues

    def _fix_meta_description(self, article_data: Dict, research_pack: Dict, markets_config: Dict) -> None:
        """Fix common article issues inline."""
        meta_desc = article_data.get("meta_description", "")
        market_name = research_pack.get("market_name", "")
        primary_keyword = research_pack.get("keywords", {}).get("primary", "")
        authority = self._get_meta_description_authority(research_pack, markets_config)
        
        # Fix too-short meta description
        if len(meta_desc) < 150:
            # Expand with relevant content
            suffix = f" Learn about local programs, {primary_keyword.lower()}, and aging in place."
            if len(meta_desc + suffix) <= 160:
                article_data["meta_description"] = meta_desc.rstrip(".") + "." + suffix
            else:
                # Truncate suffix to fit
                available = 160 - len(meta_desc) - 2
                if available > 20:
                    article_data["meta_description"] = meta_desc.rstrip(".") + ". " + suffix[:available]
        
        # Fix too-long meta description
        elif len(meta_desc) > 160:
            # Truncate at word boundary
            truncated = meta_desc[:157].rsplit(" ", 1)[0]
            if not truncated.endswith("."):
                truncated = truncated.rstrip(".,;:") + "."
            article_data["meta_description"] = truncated

        # Final guard: ensure 150-160 chars using a fresh template if still short
        meta_desc = article_data.get("meta_description", "")
        if len(meta_desc) < 150:
            template = (
                f"{market_name} {primary_keyword.lower()} guide: use {authority}, local resources, and practical steps "
                f"to keep aging at home safe and supported this year."
            )
            if len(template) > 160:
                template = template[:157].rsplit(" ", 1)[0].rstrip(".,;:") + "."
            article_data["meta_description"] = template
