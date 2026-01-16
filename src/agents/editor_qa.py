"""Editor/QA Agent: Ensures editorial quality, reader engagement, and compliance."""
import asyncio
import json
import re
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

from agents.base_agent import BaseAgent
from tools.html_validator import HTMLValidator
from tools.similarity_checker import SimilarityChecker
from utils.config_loader import load_config
from utils.logger import get_logger

logger = get_logger(__name__)

# Known valid TheKey URLs (skip validation for these)
THEKEY_VALID_URLS = {
    "https://thekey.ca/getting-started",
    "https://thekey.ca/locations/canada/montreal",
    "https://thekey.ca/locations/canada/toronto",
    "https://thekey.ca/locations/canada/vancouver",
    "https://thekey.ca/locations/canada/calgary",
    "https://thekey.ca/locations/canada/winnipeg",
    "https://thekey.ca/locations/canada/victoria",
    "https://thekey.ca/locations/canada/oakville",
    "https://thekey.ca/locations/canada/surrey",
    "https://thekey.ca/our-services/alzheimers-and-dementia",
    "https://thekey.ca/our-services/hospital-to-home",
    "https://thekey.ca/our-services/parkinsons",
    "https://thekey.ca/our-services/stroke",
    "https://thekey.ca/our-services/end-of-life",
    "https://thekey.ca/our-services/heart-health",
    "https://thekey.ca/our-services/cancer",
}

# Trusted domains that don't need validation (image sources, government sites, etc.)
TRUSTED_DOMAINS = {
    "pexels.com",
    "www.pexels.com",
    "images.pexels.com",
    "canada.ca",
    "www.canada.ca",
    "ontario.ca",
    "www.ontario.ca",
    "quebec.ca",
    "www.quebec.ca",
    "gov.bc.ca",
    "www2.gov.bc.ca",
    "gov.mb.ca",
    "www.gov.mb.ca",
    "alberta.ca",
    "www.alberta.ca",
    "albertahealthservices.ca",
    "www.albertahealthservices.ca",
    "healthlinkbc.ca",
    "www.healthlinkbc.ca",
    "ramq.gouv.qc.ca",
    "www.ramq.gouv.qc.ca",
}

FALLBACK_LINKS = {
    "https://www.quebec.ca/en/health/finding-a-resource/clsc": (
        "https://www.quebec.ca/en/health/finding-a-resource"
    ),
}


class EditorQAAgent(BaseAgent):
    """Agent responsible for editorial quality, reader engagement, and compliance."""

    def __init__(self):
        super().__init__()
        self.similarity_checker = SimilarityChecker()
        self.html_validator = HTMLValidator()
        self.model_config = load_config("model_routing")
        self.max_rewrites = self.model_config["cost_controls"]["max_rewrites"]
        self.brand_config = load_config("brand")
        
        # Banned phrases for linting (check entire body, not just opener)
        self.banned_phrases = self.brand_config.get("anti_generic_requirements", {}).get("banned_openers", [
            "Many families",
            "As we age",
            "In today's world",
            "It's no secret that",
            "When it comes to",
            "There's no doubt that",
            "In recent years",
            "As we all know",
            "For many people",
            "It goes without saying"
        ])

    async def run(self, drafts: List[Dict]) -> tuple:
        """Run editorial review, quality improvements, then compliance checks.

        Args:
            drafts: List of article drafts from Writer Agent

        Returns:
            tuple: (final_articles, editor_report, uniqueness_report)
        """
        self.log_info("Starting Editor/QA process...")
        self.log_info("🎯 PRIMARY FOCUS: Editorial quality and reader engagement")

        editor_report = {
            "editorial_reviews": {},
            "editorial_improvements": {},
            "compliance_checks": {},
            "rewrite_attempts": 0,
            "final_status": "passed"
        }

        # PHASE 1: Editorial Review & Quality Improvements (PRIMARY FOCUS)
        # Target: Score >= 8/10, with rewrite loop until achieved or max attempts
        self.log_info("\n" + "="*60)
        self.log_info("PHASE 1: Editorial Quality Review (Target: 8+/10)")
        self.log_info("="*60)
        
        QUALITY_THRESHOLD = 8  # Articles must score >= 8 to pass
        MAX_EDITORIAL_REWRITES = 2  # Max rewrite attempts per article
        
        improved_drafts = []
        for draft in drafts:
            market = draft.get("market", "")
            market_name = draft.get("market_name", market)
            
            current_draft = draft
            rewrite_count = 0
            final_assessment = None
            
            while rewrite_count <= MAX_EDITORIAL_REWRITES:
                self.log_info(f"\n📝 Reviewing editorial quality for {market_name} (attempt {rewrite_count + 1})...")
                
                # Conduct editorial review
                editorial_assessment = await self._review_editorial_quality(current_draft)
                final_assessment = editorial_assessment
                current_score = editorial_assessment.get("overall_score", 0)
                
                self.log_info(f"  Score: {current_score}/10 (target: {QUALITY_THRESHOLD}+)")
                
                if current_score >= QUALITY_THRESHOLD:
                    self.log_info(f"✓ {market_name} meets quality threshold ({current_score}/10)")
                    break
                
                # Need improvement
                if rewrite_count < MAX_EDITORIAL_REWRITES:
                    self.log_info(f"  Key Issues: {', '.join(editorial_assessment.get('key_issues', [])[:2])}")
                    self.log_info(f"✨ Improving editorial quality for {market_name}...")
                    current_draft = await self._improve_editorial_quality(current_draft, editorial_assessment)
                    rewrite_count += 1
                else:
                    self.log_warning(f"⚠️ {market_name} did not reach threshold after {MAX_EDITORIAL_REWRITES} rewrites (score: {current_score})")
                    break
            
            improved_drafts.append(current_draft)
            editor_report["editorial_reviews"][market] = final_assessment
            editor_report["editorial_improvements"][market] = {
                "improved": rewrite_count > 0,
                "rewrite_attempts": rewrite_count,
                "final_score": final_assessment.get("overall_score", 0),
                "met_threshold": final_assessment.get("overall_score", 0) >= QUALITY_THRESHOLD,
                "issues_addressed": final_assessment.get("key_issues", []) if rewrite_count > 0 else []
            }

        # PHASE 2: Technical Compliance Checks (secondary)
        self.log_info("\n" + "="*60)
        self.log_info("PHASE 2: Technical Compliance Validation")
        self.log_info("="*60)
        
        for draft in improved_drafts:
            market = draft.get("market", "")
            editor_report["compliance_checks"][market] = self.html_validator.validate(
                draft.get("html_content", ""),
                market,
                week_theme=draft.get("week_theme")
            )

        # PHASE 2.5: Anti-Duplication Validation
        self.log_info("\n" + "="*60)
        self.log_info("PHASE 2.5: Anti-Duplication Validation")
        self.log_info("="*60)
        
        anti_dup_report = await self._validate_anti_duplication(improved_drafts)
        editor_report["anti_duplication"] = anti_dup_report
        
        if anti_dup_report["status"] != "passed":
            self.log_warning(f"Anti-duplication issues found: {anti_dup_report['issues_count']} issues")
            for issue in anti_dup_report.get("issues", [])[:5]:
                self.log_warning(f"  - {issue}")

        # PHASE 3: Link Validation (remove broken external links)
        self.log_info("\n" + "="*60)
        self.log_info("PHASE 3: External Link Validation")
        self.log_info("="*60)
        
        editor_report["link_validation"] = {}
        for i, draft in enumerate(improved_drafts):
            market = draft.get("market", "")
            market_name = draft.get("market_name", market)
            
            self.log_info(f"\n🔗 Validating links for {market_name}...")
            
            # Validate and fix broken links
            validated_html, link_report = await self._validate_and_fix_links(
                draft.get("html_content", ""),
                market_name
            )
            
            improved_drafts[i]["html_content"] = validated_html
            editor_report["link_validation"][market] = link_report
            
            if link_report["broken_links_removed"] > 0:
                self.log_info(f"  ⚠️ Removed {link_report['broken_links_removed']} broken link(s)")
            if link_report["valid_links"]:
                self.log_info(f"  ✓ {len(link_report['valid_links'])} valid link(s) verified")

        # PHASE 4: Cross-Run Archive Check (NEW - Phase 2)
        self.log_info("\n" + "="*60)
        self.log_info("PHASE 4A: Cross-Run Archive Duplicate Detection")
        self.log_info("="*60)
        
        archive_reports = {}
        archive_failing_markets = set()
        
        for draft in improved_drafts:
            market = draft.get("market", "")
            market_name = draft.get("market_name", market)
            
            self.log_info(f"Checking {market_name} against archive...")
            archive_report = await self.similarity_checker.check_against_archive(
                draft, market, days_back=180
            )
            archive_reports[market] = archive_report
            
            if archive_report["status"] != "passed":
                failing_matches = archive_report.get("failing_matches", [])
                if failing_matches:
                    archive_failing_markets.add(market)
                    self.log_warning(
                        f"  ⚠️ {market_name} has {len(failing_matches)} high-similarity match(es) "
                        f"with archived articles"
                    )
                    for match in failing_matches[:2]:  # Show first 2
                        self.log_warning(
                            f"    - {match.get('title', '')[:50]}... "
                            f"(TF-IDF: {match['tfidf']:.3f}, Embed: {match['embedding']:.3f})"
                        )
                else:
                    self.log_info(f"  ✓ {market_name} passed archive check")
            else:
                self.log_info(f"  ✓ {market_name} passed archive check ({archive_report['archived_count']} articles checked)")

        # PHASE 4B: Similarity Gate with Rewrite Loop (Within-Run)
        self.log_info("\n" + "="*60)
        self.log_info("PHASE 4B: Within-Run Uniqueness Verification")
        self.log_info("="*60)
        
        drafts_list = improved_drafts
        rewrite_attempt = 0

        while rewrite_attempt < self.max_rewrites:
            self.log_info(f"Similarity check attempt {rewrite_attempt + 1}/{self.max_rewrites}")

            # Re-run archive check after rewrites to see if issues are resolved
            if rewrite_attempt > 0:
                self.log_info("Re-running archive check after rewrites...")
                archive_failing_markets = set()
                for draft in drafts_list:
                    market = draft.get("market", "")
                    market_name = draft.get("market_name", market)
                    
                    archive_report = await self.similarity_checker.check_against_archive(
                        draft, market, days_back=180
                    )
                    archive_reports[market] = archive_report
                    
                    if archive_report["status"] != "passed":
                        failing_matches = archive_report.get("failing_matches", [])
                        if failing_matches:
                            archive_failing_markets.add(market)
                            self.log_warning(
                                f"  ⚠️ {market_name} still has {len(failing_matches)} archive match(es) after rewrite"
                            )

            # Compute similarity (within-run)
            similarity_report = await self.similarity_checker.check_pairwise(drafts_list)

            self.log_info(f"Similarity check completed. Status: {similarity_report['status']}")

            # Combine archive failures with within-run failures
            within_run_failing = set(similarity_report.get("failing_markets", []))
            all_failing_markets = archive_failing_markets | within_run_failing

            if similarity_report["status"] == "passed" and not archive_failing_markets:
                self.log_info("✓ All similarity checks passed (both archive and within-run)")
                break

            if not all_failing_markets:
                self.log_info("✓ All markets unique")
                editor_report["final_status"] = "passed"
                return drafts_list, editor_report, similarity_report

            # Rewrite failing markets (maintaining editorial quality)
            self.log_warning(f"Rewriting {len(all_failing_markets)} markets for uniqueness...")

            for i, draft in enumerate(drafts_list):
                if draft.get("market") in all_failing_markets:
                    # Pass archive context to rewrite function
                    archive_context = archive_reports.get(draft.get("market"), {})
                    rewritten_draft = await self._rewrite_for_uniqueness(
                        draft, rewrite_attempt, archive_context=archive_context
                    )
                    drafts_list[i] = rewritten_draft

            rewrite_attempt += 1

        # After max rewrites, proceed best-effort
        if rewrite_attempt >= self.max_rewrites:
            self.log_warning(f"⚠️ Max rewrites ({self.max_rewrites}) reached")
        
        # Final archive check after last rewrite (if any rewrites occurred)
        if rewrite_attempt > 0:
            self.log_info("Running final archive check after rewrite loop...")
            archive_failing_markets = set()
            for draft in drafts_list:
                market = draft.get("market", "")
                market_name = draft.get("market_name", market)
                
                archive_report = await self.similarity_checker.check_against_archive(
                    draft, market, days_back=180
                )
                archive_reports[market] = archive_report
                
                if archive_report["status"] != "passed":
                    failing_matches = archive_report.get("failing_matches", [])
                    if failing_matches:
                        archive_failing_markets.add(market)
                        self.log_warning(
                            f"  ⚠️ {market_name} still has {len(failing_matches)} archive match(es) after final rewrite"
                        )

        editor_report["rewrite_attempts"] = rewrite_attempt
        editor_report["archive_reports"] = archive_reports
        
        # Determine final status based on both archive and within-run checks
        archive_failed = len(archive_failing_markets) > 0
        within_run_failed = similarity_report["status"] != "passed"
        
        if archive_failed or within_run_failed:
            editor_report["final_status"] = "manual_review_required"
            editor_report["similarity_report"] = similarity_report
            if archive_failed:
                editor_report["archive_failing_markets"] = sorted(list(archive_failing_markets))
        else:
            editor_report["final_status"] = "passed"

        return drafts_list, editor_report, similarity_report

    async def _review_editorial_quality(self, draft: Dict) -> Dict:
        """Review article for editorial quality, newsworthiness, and evidence density.
        
        Args:
            draft: Article draft
            
        Returns:
            Dict: Editorial assessment with quality scores and issues
        """
        model_config = self.model_config["models"]["editing_default"]
        market_name = draft.get("market_name", draft.get("market", ""))
        primary_keyword = draft.get("primary_keyword", "")
        html_content = draft.get("html_content", "")
        
        # Extract multiple sections for comprehensive review (not just first 2000 chars)
        # Get intro (first 1500), middle section (next 2000), and conclusion (last 1500)
        intro_section = html_content[:1500]
        
        if len(html_content) > 4000:
            middle_start = len(html_content) // 3
            middle_section = html_content[middle_start:middle_start + 2000]
            conclusion_section = html_content[-1500:]
        else:
            middle_section = ""
            conclusion_section = html_content[-1000:] if len(html_content) > 1000 else ""
        
        # Count evidence markers
        import re
        citation_count = len(re.findall(r'(?:according to|source:|cited from|\(.*?20\d{2}\))', html_content.lower()))
        statistic_count = len(re.findall(r'\d+(?:\.\d+)?%|\$[\d,]+|\d+(?:,\d{3})+', html_content))
        program_mentions = len(re.findall(r'(?:program|initiative|credit|CLSC|RAMQ|OHIP|AHS|MSP)', html_content))
        
        prompt = f"""You are an expert editorial reviewer evaluating a senior care article for NEWS-WORTHINESS, EVIDENCE DENSITY, and READER ENGAGEMENT.

ARTICLE CONTEXT:
- Market: {market_name}
- Primary Keyword: {primary_keyword}
- Target Audience: Families seeking home care for seniors
- Target Score: 8+/10 (articles below 8 will be rewritten)

EVIDENCE MARKERS DETECTED:
- Citation references found: ~{citation_count}
- Statistics/numbers found: ~{statistic_count}
- Program/initiative mentions: ~{program_mentions}

=== ARTICLE OPENING (first 1500 chars) ===
{intro_section}

=== ARTICLE MIDDLE SECTION ===
{middle_section}

=== ARTICLE CONCLUSION (last 1500 chars) ===
{conclusion_section}

EVALUATE THESE 8 CRITERIA (each scored 1-10):

1. OPENING HOOK (Critical - must score 7+ to pass):
   - Does it immediately grab attention with a specific scenario, question, or surprising fact?
   - Does it AVOID generic openers like "Many families...", "In today's world...", "It's no secret..."?
   - Is it emotionally resonant and specific to {market_name}?
   - Score 1-10 (10 = compelling, 1 = generic)

2. NEWSWORTHINESS / TIMELINESS:
   - Does the article feel current and relevant (seasonal, policy-related, timely)?
   - Is there a "why now" element that makes this worth reading today?
   - Does it reference recent developments or initiatives?
   - Score 1-10

3. EVIDENCE DENSITY / CITATIONS:
   - Are there at least 2-3 specific facts with attributed sources?
   - Are statistics used appropriately with context?
   - Are local programs or initiatives mentioned by name?
   - Score 1-10 (7+ requires at least 2 cited facts)

4. SPECIFICITY / LOCAL RELEVANCE:
   - Does the article feel specifically written for {market_name}, not generic?
   - Are there concrete local examples, programs, or resources?
   - Does it reference the provincial healthcare system by name?
   - Score 1-10

5. ACTIONABLE VALUE:
   - Does the article provide at least 2-3 concrete actions readers can take?
   - Is there a "what to do this week" or practical next steps section?
   - Does it answer "what's in it for me?" clearly?
   - Score 1-10

6. NARRATIVE FLOW & ENGAGEMENT:
   - Is the article easy to follow and skimmable?
   - Do sections flow logically with smooth transitions?
   - Does it maintain interest throughout (not just the opening)?
   - Score 1-10

7. TONE & VOICE (TheKey Brand):
   - Is it warm, empathetic, and professional?
   - Does it avoid being too clinical, salesy, or condescending?
   - Is it appropriate for families making difficult care decisions?
   - Score 1-10

8. CTA EFFECTIVENESS:
   - Is the call-to-action helpful rather than pushy?
   - Does it connect to the article's value proposition?
   - Score 1-10

SCORING RULES:
- Overall score is the AVERAGE of all 8 criteria
- Score < 8 means the article NEEDS IMPROVEMENT
- Generic openings automatically cap the score at 6
- No citations automatically caps the score at 6
- No actionable takeaways automatically caps the score at 7

Return ONLY valid JSON:
{{
  "overall_score": 7,
  "needs_improvement": true,
  "key_issues": [
    "Opening is generic - starts with 'Many families...'",
    "No specific statistics with sources cited",
    "Missing actionable 'what to do this week' section"
  ],
  "scores": {{
    "opening_hook": 5,
    "newsworthiness": 6,
    "evidence_density": 4,
    "specificity": 7,
    "actionable_value": 5,
    "narrative_flow": 7,
    "tone_voice": 8,
    "cta_effectiveness": 6
  }},
  "strengths": [
    "Good local resource mentions",
    "Warm, empathetic tone throughout"
  ],
  "recommendations": [
    "Rewrite opening with a specific scenario or surprising stat",
    "Add 2-3 cited statistics from Canadian health sources",
    "Include a 'What You Can Do This Week' section with 3 actions"
  ],
  "evidence_gaps": [
    "No statistics with source attribution",
    "No specific program names beyond generic mentions"
  ]
}}"""

        try:
            response = await self.openai.generate(
                model=model_config["model"],
                prompt=prompt,
                max_tokens=800,
                temperature=0.3
            )
            
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                assessment = json.loads(json_match.group(0))
            else:
                # Fallback if JSON parsing fails
                assessment = {
                    "overall_score": 5,
                    "needs_improvement": True,
                    "key_issues": ["Unable to parse assessment"],
                    "scores": {},
                    "strengths": [],
                    "recommendations": []
                }
            
            # Log assessment
            self.log_info(f"  Overall Score: {assessment.get('overall_score', 'N/A')}/10")
            if assessment.get("key_issues"):
                self.log_info(f"  Key Issues: {', '.join(assessment['key_issues'][:2])}")

            # Enforce current-year check to avoid outdated dates in copy
            from datetime import datetime
            current_year = datetime.now().year
            html_content = draft.get("html_content", "")
            outdated_years = set()
            for y in range(2020, current_year):
                if str(y) in html_content:
                    outdated_years.add(y)
            if outdated_years:
                assessment.setdefault("key_issues", []).append(
                    f"Outdated year references detected: {sorted(outdated_years)}"
                )
                # Cap score to force rewrite
                assessment["overall_score"] = min(assessment.get("overall_score", 5), 6)
                assessment["needs_improvement"] = True

            # Basic checks for FAQ count and inline authority links
            requires_faq = self._requires_faq(draft)
            faq_count = html_content.lower().count("frequently asked questions")
            if faq_count == 0 and requires_faq:
                assessment.setdefault("key_issues", []).append("FAQ section missing")
                assessment["overall_score"] = min(assessment.get("overall_score", 5), 6)
                assessment["needs_improvement"] = True
            # Ensure at least two external links (rough heuristic)
            external_links = html_content.count('href="http')
            if external_links < 2:
                assessment.setdefault("key_issues", []).append("Too few external authority links inline")
                assessment["overall_score"] = min(assessment.get("overall_score", 5), 6)
                assessment["needs_improvement"] = True
            
            return assessment
            
        except Exception as e:
            self.log_error(f"Failed to review editorial quality: {e}")
            return {
                "overall_score": 5,
                "needs_improvement": True,
                "key_issues": ["Review failed"],
                "scores": {},
                "strengths": [],
                "recommendations": []
            }

    async def _improve_editorial_quality(self, draft: Dict, assessment: Dict) -> Dict:
        """Improve article's editorial quality based on assessment.
        
        Args:
            draft: Article draft
            assessment: Editorial quality assessment
            
        Returns:
            Dict: Improved article draft
        """
        # Use escalation model if score is very low
        model_config_key = "editing_escalation" if assessment.get("overall_score", 5) < 6 else "editing_default"
        model_config = self.model_config["models"][model_config_key]
        
        market_name = draft.get("market_name", draft.get("market", ""))
        primary_keyword = draft.get("primary_keyword", "")
        html_content = draft.get("html_content", "")
        key_issues = assessment.get("key_issues", [])
        recommendations = assessment.get("recommendations", [])
        scores = assessment.get("scores", {})
        evidence_gaps = assessment.get("evidence_gaps", [])
        
        # Identify which areas need the most improvement
        low_score_areas = [area for area, score in scores.items() if score < 7]
        
        prompt = f"""You are an expert editor improving a senior care article to achieve a score of 8+/10.

MARKET: {market_name}
PRIMARY KEYWORD: {primary_keyword}
CURRENT SCORE: {assessment.get('overall_score', 0)}/10 (TARGET: 8+)

=== CURRENT ARTICLE ===
{html_content}

=== ISSUES TO FIX (PRIORITY ORDER) ===
{chr(10).join(f"- {issue}" for issue in key_issues)}

=== LOW-SCORING AREAS ===
{chr(10).join(f"- {area}: {scores.get(area, 'N/A')}/10" for area in low_score_areas)}

=== EVIDENCE GAPS ===
{chr(10).join(f"- {gap}" for gap in evidence_gaps) if evidence_gaps else "- No specific gaps identified"}

=== RECOMMENDATIONS ===
{chr(10).join(f"- {rec}" for rec in recommendations)}

=== CRITICAL IMPROVEMENTS (target 8+/10) ===

1. OPENING HOOK (MUST FIX if scored < 7):
   - NEVER start with: "Many families...", "In today's world...", "It's no secret..."
   - START WITH: A specific scenario, surprising statistic, or compelling question
   - EXAMPLE: "Picture this: Your mother calls at 3 AM, confused about where she is..."
   - EXAMPLE: "Only 15% of Canadian seniors have a formal care plan—is your family prepared?"
   - Make it specific to {market_name} when possible

2. EVIDENCE DENSITY (MUST FIX if scored < 7):
   - ADD at least 2-3 specific statistics with source attribution
   - EXAMPLE: "According to the Canadian Institute for Health Information, 93% of seniors prefer aging at home."
   - INCLUDE named programs: {market_name} CLSC, provincial health authority, specific tax credits
   - CITE sources in text: "(Source: [Organization], 2025)" or "According to [Source]..."

3. ACTIONABLE VALUE (MUST FIX if scored < 7):
   - ADD a clear "What You Can Do This Week" section
   - INCLUDE 3-4 concrete, numbered steps families can take immediately
   - EXAMPLE: "1. Schedule a family meeting to discuss care preferences (use our free conversation guide)"
   - Make actions specific and achievable

4. NEWSWORTHINESS (MUST FIX if scored < 7):
   - ADD a "Why This Matters Now" element
   - Reference current season, recent policy changes, or timely initiatives
   - Connect to what's happening in {market_name} specifically

5. SPECIFICITY (MUST FIX if scored < 7):
   - REPLACE generic references with specific {market_name} examples
   - NAME the provincial healthcare system: {draft.get('healthcare_context', '')}
   - Reference specific local resources and programs

=== MAINTAIN (do not break these) ===
- All SEO requirements (primary keyword in H1, keyword density 1.5-2.5%)
- Canadian spelling: colour, centre, behaviour, organise, recognise
- Brightspot HTML structure (blog-content-module wrapper, inline styles)
- Word count: 900-1300 words
- Medical disclaimer at end
- TheKey brand voice: warm, empathetic, professional

=== OUTPUT ===
Return the COMPLETE improved HTML article. 
The article MUST address all key issues and score 8+/10 on:
- Opening hook (specific, compelling, not generic)
- Evidence density (2+ cited statistics)
- Actionable value (clear next steps)
- Newsworthiness (timely, relevant)
- Specificity (local, not generic)

Return ONLY the HTML, no commentary or explanation."""

        response = await self.openai.generate(
            model=model_config["model"],
            prompt=prompt,
            max_tokens=model_config["max_tokens"],
            temperature=model_config["temperature"]
        )

        # Clean up the response - remove markdown formatting and any trailing commentary
        draft["html_content"] = self._clean_html_response(response)
        return draft

    def _clean_html_response(self, response: str) -> str:
        """Clean up HTML response from LLM - remove markdown formatting and commentary.
        
        Args:
            response: Raw LLM response
            
        Returns:
            str: Clean HTML content
        """
        content = response.strip()
        
        # Remove markdown code block wrapper if present
        if content.startswith("```html"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]
        
        # Remove closing markdown code block
        if "```" in content:
            content = content.split("```")[0]
        
        # Remove any trailing summary/commentary (often starts with ### or "Summary")
        lines = content.split("\n")
        clean_lines = []
        for line in lines:
            # Stop if we hit commentary indicators
            if line.strip().startswith("###") or line.strip().lower().startswith("summary"):
                break
            clean_lines.append(line)
        
        content = "\n".join(clean_lines).strip()
        
        # Ensure it ends with the closing div
        if not content.rstrip().endswith("</div>"):
            # Try to find the last </div> and truncate there
            last_div = content.rfind("</div>")
            if last_div != -1:
                content = content[:last_div + 6]
        
        return content

    async def _validate_and_fix_links(
        self,
        html_content: str,
        market_name: str,
        *,
        skip_trusted_domains: bool = True
    ) -> Tuple[str, Dict]:
        """Validate all external links in HTML and remove broken ones.
        
        Args:
            html_content: HTML content with links
            market_name: Market name for logging
            
        Returns:
            Tuple[str, Dict]: (cleaned HTML, validation report)
        """
        cleaned_html = html_content

        # Replace known broken URLs with stable fallbacks
        fallback_applied = []
        for old_url, new_url in FALLBACK_LINKS.items():
            if old_url in cleaned_html:
                cleaned_html = cleaned_html.replace(old_url, new_url)
                fallback_applied.append({"from": old_url, "to": new_url})

        # Extract all links from HTML
        link_pattern = r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>([^<]*)</a>'
        matches = re.findall(link_pattern, cleaned_html, re.IGNORECASE)

        report = {
            "total_links": len(matches),
            "valid_links": [],
            "broken_links": [],
            "broken_links_removed": 0,
            "thekey_links_trusted": [],
            "fallback_links": fallback_applied,
        }
        
        if not matches:
            return cleaned_html, report
        
        # Check each link
        links_to_validate = []
        for url, link_text in matches:
            # Skip TheKey internal links (trusted)
            if url in THEKEY_VALID_URLS or url.startswith("https://thekey.ca/"):
                report["thekey_links_trusted"].append(url)
                report["valid_links"].append(url)
                continue
            
            # Skip anchor links and mailto
            if url.startswith("#") or url.startswith("mailto:"):
                report["valid_links"].append(url)
                continue
            
            # Skip trusted domains (government sites, Pexels, etc.)
            try:
                parsed = urlparse(url)
                domain = parsed.netloc.lower()
                if skip_trusted_domains and (
                    domain in TRUSTED_DOMAINS or any(domain.endswith(f".{td}") for td in TRUSTED_DOMAINS)
                ):
                    report["valid_links"].append(url)
                    continue
            except Exception:
                pass
                
            links_to_validate.append((url, link_text))
        
        # Validate external links concurrently
        if links_to_validate:
            validation_results = await self._check_links_batch(
                [url for url, _ in links_to_validate]
            )
            
            for (url, link_text), is_valid in zip(links_to_validate, validation_results):
                if is_valid:
                    report["valid_links"].append(url)
                else:
                    report["broken_links"].append({
                        "url": url,
                        "text": link_text
                    })
        
        # Remove broken links from HTML (convert to plain text)
        for broken in report["broken_links"]:
            url = broken["url"]
            text = broken["text"]
            
            # Find the full anchor tag and replace with just the text
            # Handle various attribute orders
            patterns = [
                rf'<a\s+[^>]*href=["\']' + re.escape(url) + r'["\'][^>]*>' + re.escape(text) + r'</a>',
                rf'<a\s+href=["\']' + re.escape(url) + r'["\'][^>]*>' + re.escape(text) + r'</a>',
            ]
            
            for pattern in patterns:
                cleaned_html = re.sub(pattern, text, cleaned_html, flags=re.IGNORECASE)
            
            report["broken_links_removed"] += 1
            self.log_warning(f"Removed broken link: {url}")
        
        return cleaned_html, report

    async def _check_links_batch(self, urls: List[str]) -> List[bool]:
        """Check multiple URLs concurrently for validity.
        
        Args:
            urls: List of URLs to check
            
        Returns:
            List[bool]: List of validity flags (True = valid, False = broken)
        """
        async def check_single_url(url: str) -> bool:
            try:
                # Parse URL to validate format
                parsed = urlparse(url)
                if not parsed.scheme or not parsed.netloc:
                    return False
                
                timeout = aiohttp.ClientTimeout(total=10)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.head(
                        url, 
                        allow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 TheKey Content Bot"}
                    ) as response:
                        # Accept 2xx and 3xx status codes
                        if response.status < 400:
                            return True
                        
                        # Some sites block HEAD, try GET
                        if response.status in [403, 405]:
                            async with session.get(
                                url,
                                allow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0 TheKey Content Bot"}
                            ) as get_response:
                                return get_response.status < 400
                        
                        return False
                        
            except asyncio.TimeoutError:
                self.log_warning(f"Timeout checking URL: {url}")
                return False
            except aiohttp.ClientError as e:
                self.log_warning(f"Error checking URL {url}: {e}")
                return False
            except Exception as e:
                self.log_warning(f"Unexpected error checking URL {url}: {e}")
                return False
        
        # Run all URL checks concurrently
        results = await asyncio.gather(
            *[check_single_url(url) for url in urls],
            return_exceptions=True
        )
        
        # Convert exceptions to False
        return [r if isinstance(r, bool) else False for r in results]

    async def _rewrite_for_uniqueness(
        self, 
        draft: Dict, 
        attempt: int,
        archive_context: Optional[Dict] = None
    ) -> Dict:
        """Rewrite article to reduce similarity while maintaining editorial quality.

        Args:
            draft: Article draft
            attempt: Current rewrite attempt
            archive_context: Optional archive check results with failing matches

        Returns:
            Dict: Rewritten article draft
        """
        self.log_info(f"Rewriting {draft['market']} for uniqueness (attempt {attempt})...")

        model_config_key = "editing_escalation" if attempt >= 2 else "editing_default"
        model_config = self.model_config["models"][model_config_key]

        seo_reqs = self.brand_config.get('content_guidelines', {}).get('seo_requirements', {})
        
        # Build archive context for prompt
        archive_context_text = ""
        if archive_context and archive_context.get("failing_matches"):
            failing_matches = archive_context["failing_matches"]
            archive_context_text = "\n\n=== RECENT ARTICLES FOR THIS MARKET (AVOID SIMILAR ANGLES) ===\n"
            archive_context_text += "Your article is too similar to these recently published articles. "
            archive_context_text += "You MUST use completely different angles, statistics, and structures:\n\n"
            
            for match in failing_matches[:3]:  # Show top 3 matches
                archive_context_text += f"- \"{match.get('title', 'Unknown')}\" "
                archive_context_text += f"(published {match.get('published_date', '')}) "
                archive_context_text += f"- Theme: {match.get('week_theme', 'N/A')}\n"
                archive_context_text += f"  Similarity: TF-IDF {match['tfidf']:.3f}, Embedding {match['embedding']:.3f}\n\n"
            
            archive_context_text += "CRITICAL: Do NOT repeat similar:\n"
            archive_context_text += "- Opening hooks or angles\n"
            archive_context_text += "- Statistics or data points\n"
            archive_context_text += "- H2 section structures\n"
            archive_context_text += "- Local examples or case studies\n"
            archive_context_text += "- Callout box content\n"
        
        prompt = f"""Rewrite the following article to make it significantly more unique while MAINTAINING excellent editorial quality and reader engagement.

MARKET: {draft['market_name']}
PRIMARY KEYWORD: {draft['primary_keyword']}
{archive_context_text}
CURRENT CONTENT:
{draft['html_content']}

CRITICAL UNIQUENESS REQUIREMENTS:
- Use COMPLETELY different sentence structures and word choices throughout
- Rephrase ALL concepts with fresh language and angles
- Add unique local examples, statistics, and references specific to {draft['market_name']}
- Change opening paragraph entirely - use a different hook/angle that's equally or more compelling
- Vary paragraph length and structure significantly
- Use different transitions and connecting phrases
- Ensure H2 outline is ≥60% unique from other markets
- Make FAQs more specific to {draft['market_name']} with unique concerns

EDITORIAL QUALITY (MAINTAIN/IMPROVE):
- Opening hook must be compelling and address real pain points
- Narrative flow must be smooth and engaging
- Maintain clarity and actionable value throughout
- Keep warm, empathetic, professional tone
- Ensure CTA feels helpful, not salesy
- Make it skimmable but compelling to read fully

SEO REQUIREMENTS (MAINTAIN):
- Primary keyword MUST remain in H1 naturally
- Primary keyword density: {seo_reqs.get('primary_keyword_density', '1.5-2.5%')}
- Secondary keywords should appear organically
- Meta description: {seo_reqs.get('meta_description_length', '150-160 characters')}
- Internal links: {seo_reqs.get('internal_links_min', 2)}-{seo_reqs.get('internal_links_max', 4)}
- Use semantic variations naturally

CONTENT REQUIREMENTS:
- Word count: 900-1300 words (aim for 1100-1200)
- MANDATORY Canadian spelling throughout
- Keep medical disclaimer
- Keep all internal links and CTA
- Maintain TheKey brand voice: warm, professional, empathetic
- Use TheKey color palette: Everest (#06262D), Gold (#D1B886), Light Gold (#F0EEDC)
- Reference provincial healthcare systems by full name
- Use Canadian terminology and Canadian dollar amounts

BRIGHTSPOT HTML REQUIREMENTS:
- Must have blog-content-module wrapper
- All styles must use px units (no rem/em)
- Use inline styles matching TheKey brand guidelines
- Hero image placeholder with proper styling
- Deck/subheadline with proper typography
- Callout box with light gold background (#F0EEDC)
- Dark CTA box (#06262D) with gold button (#D1B886)
- FAQs with proper formatting (if included)
- Medical disclaimer at bottom

OUTPUT:
Return the COMPLETE rewritten HTML article. The article must:
1. Be significantly more unique (target: <0.20 TF-IDF similarity, <0.80 embedding similarity)
2. Maintain or improve editorial quality and reader engagement
3. Pass all Brightspot HTML validation checks
4. Optimize for SEO while being natural and compelling
5. Feel like a completely different article while maintaining premium quality
"""

        response = await self.openai.generate(
            model=model_config["model"],
            prompt=prompt,
            max_tokens=model_config["max_tokens"],
            temperature=model_config["temperature"]
        )

        # Update the draft with cleaned rewritten content
        draft["html_content"] = self._clean_html_response(response)
        return draft

    async def _validate_anti_duplication(self, drafts: List[Dict]) -> Dict:
        """Validate anti-duplication requirements across all markets.
        
        Checks:
        - H2 uniqueness (50% unique per market vs others)
        - Stats validation (>=2 inline cited stats with URLs)
        - FAQ uniqueness (>=4 of 5 unique vs other markets)
        - Banned phrases (check entire body)
        - Meta validation (title <60 chars, description 150-160)
        - Keyword density (1.5-2.5%)
        
        Args:
            drafts: List of article drafts
            
        Returns:
            Dict: Validation report with status and issues
        """
        report = {
            "status": "passed",
            "issues_count": 0,
            "issues": [],
            "market_reports": {}
        }
        
        # Extract H2s and FAQs from all drafts for comparison
        all_h2s = {}
        all_faqs = {}
        
        for draft in drafts:
            market = draft.get("market", "")
            html = draft.get("html_content", "")
            
            # Extract H2s
            h2_pattern = r'<h2[^>]*>([^<]+)</h2>'
            h2s = re.findall(h2_pattern, html, re.IGNORECASE)
            all_h2s[market] = [h2.strip().lower() for h2 in h2s]
            
            # Extract FAQs (Q: text pattern)
            faq_pattern = r'<strong>Q:\s*([^<]+)</strong>'
            faqs = re.findall(faq_pattern, html, re.IGNORECASE)
            all_faqs[market] = [faq.strip().lower() for faq in faqs]
        
        # Validate each draft
        for draft in drafts:
            market = draft.get("market", "")
            market_name = draft.get("market_name", market)
            html = draft.get("html_content", "")
            
            market_report = {
                "h2_uniqueness": {"passed": True, "unique_pct": 0},
                "stats_validation": {"passed": True, "count": 0},
                "faq_uniqueness": {"passed": True, "unique_count": 0},
                "banned_phrases": {"passed": True, "found": []},
                "meta_validation": {"passed": True, "issues": []},
                "keyword_density": {"passed": True, "density": 0}
            }
            
            # 1. H2 Uniqueness Check (50% unique)
            h2_result = self._check_h2_uniqueness(market, all_h2s)
            market_report["h2_uniqueness"] = h2_result
            if not h2_result["passed"]:
                report["issues"].append(f"{market_name}: H2 uniqueness {h2_result['unique_pct']:.0f}% (need 50%+)")
            
            # 2. Stats Validation (>=2 inline cited with URLs)
            stats_result = self._check_inline_stats(html)
            market_report["stats_validation"] = stats_result
            if not stats_result["passed"]:
                report["issues"].append(f"{market_name}: Only {stats_result['count']} inline cited stats (need 2+)")
            
            # 3. FAQ Uniqueness Check (>=4 of 5 unique for standard articles)
            faq_required = self._requires_faq(draft)
            faq_result = self._check_faq_uniqueness(market, all_faqs, faq_required=faq_required)
            market_report["faq_uniqueness"] = faq_result
            if not faq_result["passed"]:
                if faq_result.get("reason"):
                    report["issues"].append(f"{market_name}: {faq_result['reason']}")
                else:
                    report["issues"].append(
                        f"{market_name}: Only {faq_result['unique_count']}/{faq_result.get('total_count', 0)} "
                        f"unique FAQs (need {faq_result.get('required_unique', 0)}+)"
                    )
            
            # 4. Banned Phrases Check (entire body)
            banned_result = self._check_banned_phrases(html)
            market_report["banned_phrases"] = banned_result
            if not banned_result["passed"]:
                report["issues"].append(f"{market_name}: Banned phrases found: {', '.join(banned_result['found'][:3])}")
            
            # 5. Meta Validation
            meta_result = self._check_meta_validation(draft)
            market_report["meta_validation"] = meta_result
            if not meta_result["passed"]:
                report["issues"].extend([f"{market_name}: {issue}" for issue in meta_result["issues"]])
            
            # 6. Keyword Density Check
            primary_keyword = draft.get("primary_keyword", "")
            density_result = self._check_keyword_density(html, primary_keyword)
            market_report["keyword_density"] = density_result
            if not density_result["passed"]:
                report["issues"].append(f"{market_name}: Keyword density {density_result['density']:.1f}% (need 1.5-2.5%)")
            
            report["market_reports"][market] = market_report
        
        # Count issues and set status
        report["issues_count"] = len(report["issues"])
        if report["issues_count"] > 0:
            report["status"] = "issues_found"
        
        return report

    def _check_h2_uniqueness(self, market: str, all_h2s: Dict[str, List[str]]) -> Dict:
        """Check if market's H2s are at least 50% unique vs other markets."""
        my_h2s = set(all_h2s.get(market, []))
        other_h2s = set()
        
        for other_market, h2s in all_h2s.items():
            if other_market != market:
                other_h2s.update(h2s)
        
        if not my_h2s:
            return {"passed": False, "unique_pct": 0, "reason": "No H2s found"}
        
        unique_h2s = my_h2s - other_h2s
        unique_pct = (len(unique_h2s) / len(my_h2s)) * 100
        
        return {
            "passed": unique_pct >= 50,
            "unique_pct": unique_pct,
            "unique_count": len(unique_h2s),
            "total_count": len(my_h2s)
        }

    def _check_inline_stats(self, html: str) -> Dict:
        """Check for at least 2 inline cited statistics with URLs."""
        # Look for patterns like: "According to [Source]..." or "(Source: ..." with URLs
        # Also look for stats with hyperlinks
        
        # Pattern 1: Stats with explicit citations
        citation_patterns = [
            r'(?:according to|source:|per|reports?)\s+[^.]+(?:https?://\S+|<a[^>]+href)',
            r'\d+(?:\.\d+)?%[^.]+(?:<a[^>]+href|https?://)',
            r'<a[^>]+href="[^"]+"[^>]*>[^<]*(?:\d+%|\$[\d,]+)',
        ]
        
        cited_stats_count = 0
        for pattern in citation_patterns:
            matches = re.findall(pattern, html, re.IGNORECASE)
            cited_stats_count += len(matches)
        
        # Pattern 2: Any stat followed by a link
        stat_link_pattern = r'\d+(?:\.\d+)?%[^<]{0,100}<a'
        stat_link_matches = re.findall(stat_link_pattern, html, re.IGNORECASE)
        cited_stats_count += len(stat_link_matches)
        
        return {
            "passed": cited_stats_count >= 2,
            "count": min(cited_stats_count, 5)  # Cap at 5 for display
        }

    def _requires_faq(self, draft: Dict) -> bool:
        """Determine whether FAQs are required based on structure type."""
        structure_type = draft.get("metadata", {}).get("structure_type") or "standard"
        return structure_type == "standard"

    def _check_faq_uniqueness(
        self,
        market: str,
        all_faqs: Dict[str, List[str]],
        faq_required: bool = True,
        min_questions: int = 5,
        min_unique: int = 4
    ) -> Dict:
        """Check FAQ uniqueness vs other markets."""
        my_faqs = all_faqs.get(market, [])
        other_faqs = set()
        
        for other_market, faqs in all_faqs.items():
            if other_market != market:
                other_faqs.update(faqs)
        
        if not my_faqs:
            if not faq_required:
                return {
                    "passed": True,
                    "unique_count": 0,
                    "total_count": 0,
                    "skipped": True
                }
            return {
                "passed": False,
                "unique_count": 0,
                "total_count": 0,
                "reason": "No FAQs found"
            }

        if faq_required and len(my_faqs) < min_questions:
            return {
                "passed": False,
                "unique_count": len(my_faqs),
                "total_count": len(my_faqs),
                "required_unique": min_unique,
                "reason": f"Only {len(my_faqs)} FAQs (need {min_questions})"
            }
        
        unique_faqs = [faq for faq in my_faqs if faq not in other_faqs]
        
        required_unique = min_unique if faq_required else min(min_unique, len(my_faqs))

        return {
            "passed": len(unique_faqs) >= required_unique,
            "unique_count": len(unique_faqs),
            "total_count": len(my_faqs),
            "required_unique": required_unique
        }

    def _check_banned_phrases(self, html: str) -> Dict:
        """Check for banned generic phrases in entire body."""
        # Strip HTML tags for text analysis
        text = re.sub(r'<[^>]+>', ' ', html).lower()
        
        found_phrases = []
        for phrase in self.banned_phrases:
            if phrase.lower() in text:
                found_phrases.append(phrase)
        
        return {
            "passed": len(found_phrases) == 0,
            "found": found_phrases
        }

    def _check_meta_validation(self, draft: Dict) -> Dict:
        """Check meta title and description requirements."""
        metadata = draft.get("metadata", {})
        issues = []
        
        # Check meta title (<60 chars, no ellipsis)
        meta_title = metadata.get("meta_title", metadata.get("title", ""))
        if len(meta_title) > 60:
            issues.append(f"Meta title too long ({len(meta_title)} chars, max 60)")
        if "..." in meta_title or "…" in meta_title:
            issues.append("Meta title contains ellipsis")
        
        # Check meta description (150-160 chars)
        meta_desc = metadata.get("meta_description", "")
        if len(meta_desc) < 150:
            issues.append(f"Meta description too short ({len(meta_desc)} chars, min 150)")
        elif len(meta_desc) > 160:
            issues.append(f"Meta description too long ({len(meta_desc)} chars, max 160)")
        
        return {
            "passed": len(issues) == 0,
            "issues": issues
        }

    def _check_keyword_density(self, html: str, primary_keyword: str) -> Dict:
        """Check primary keyword density is 1.5-2.5%."""
        if not primary_keyword:
            return {"passed": True, "density": 0, "reason": "No keyword specified"}
        
        # Strip HTML tags
        text = re.sub(r'<[^>]+>', ' ', html).lower()
        words = text.split()
        total_words = len(words)
        
        if total_words == 0:
            return {"passed": False, "density": 0, "reason": "No content"}
        
        # Count keyword occurrences (handle multi-word keywords)
        keyword_lower = primary_keyword.lower()
        keyword_count = text.count(keyword_lower)
        keyword_words = len(keyword_lower.split())
        
        # Calculate density
        density = (keyword_count * keyword_words / total_words) * 100
        
        return {
            "passed": 1.5 <= density <= 2.5,
            "density": density,
            "keyword_count": keyword_count,
            "total_words": total_words
        }
