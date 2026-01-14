"""Orchestrator coordinator."""
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from agents.editor_qa import EditorQAAgent
from agents.dispatcher import DispatcherAgent
from agents.image_selector import ImageSelectorAgent
# Note: ResearcherAgent also imported for reset_story_lead_rotation()
import re
from tools.serp_gap_checker import SerpGapChecker
from utils.config_loader import load_config
from utils.file_manager import create_output_directory, save_json
from utils.logger import get_logger
from review.review_manager import create_review_state, send_review_requests
from archive.content_archive import ContentArchive

logger = get_logger(__name__)


async def run_weekly() -> Dict:
    """Run complete weekly content generation workflow.

    Returns:
        Dict: Run summary with status, metrics, and results
    """
    # Initialize
    markets_config = load_config("markets")
    run_id = datetime.now().strftime("%Y-%m-%d")
    output_dir = create_output_directory(run_id)
    run_mode = os.getenv("RUN_MODE", "publish").strip().lower() or "publish"
    if run_mode not in ("publish", "explore"):
        logger.warning(f"Invalid RUN_MODE '{run_mode}' provided. Defaulting to 'publish'.")
        run_mode = "publish"

    # Check for single-market testing mode
    test_market = os.getenv("TEST_MARKET", "").strip().lower()
    if test_market:
        logger.info(f"🧪 TEST MODE: Processing only '{test_market}' market")
        if test_market not in markets_config["markets"]:
            logger.error(f"TEST_MARKET '{test_market}' not found in markets config. Available: {list(markets_config['markets'].keys())}")
            return {
                "run_id": run_id,
                "start_time": datetime.now().isoformat(),
                "output_dir": str(output_dir),
                "status": "failed",
                "error": f"Invalid TEST_MARKET: {test_market}"
            }
        # Filter to only the test market
        markets_to_process = {test_market: markets_config["markets"][test_market]}
    else:
        markets_to_process = markets_config["markets"]

    run_state = {
        "run_id": run_id,
        "start_time": datetime.now().isoformat(),
        "output_dir": str(output_dir),
        "markets": {},
        "status": "running",
        "test_mode": bool(test_market),
        "test_market": test_market if test_market else None,
        "run_mode": run_mode
    }

    logger.info(f"Initializing run {run_id}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Markets to process: {len(markets_to_process)} ({', '.join(markets_to_process.keys())})")
    logger.info(f"Run mode: {run_mode}")

    try:
        # Reset image deduplication tracker for this run
        ImageSelectorAgent.reset_used_images()
        
        # Reset story lead rotation for this run
        ResearcherAgent.reset_story_lead_rotation()
        
        # Step 1: Load tone profile (simplified for vertical slice)
        logger.info("Step 1: Loading tone profile...")
        tone_profile = _get_default_tone_profile()
        logger.info("Tone profile loaded")

        # Step 2: Run researchers in parallel
        logger.info(f"Step 2: Running researcher agents for {len(markets_to_process)} market(s)...")
        researcher = ResearcherAgent()

        research_tasks = [
            researcher.run(market, market_config)
            for market, market_config in markets_to_process.items()
        ]

        research_packs = await asyncio.gather(*research_tasks)
        research_packs_dict = {
            market_config["name"]: research_pack
            for market_config, research_pack in zip(markets_to_process.values(), research_packs)
        }

        logger.info(f"Research packs generated for {len(research_packs_dict)} markets")

        # Debug: Log research pack details
        for market_name, research_pack in research_packs_dict.items():
            logger.info(f"📊 RESEARCHER OUTPUT for {market_name}:")
            logger.info(f"   • Week Theme: {research_pack.get('week_theme', 'N/A')}")
            logger.info(f"   • Primary Keyword: {research_pack['keywords']['primary']}")
            logger.info(f"   • Secondary Keywords: {', '.join(research_pack['keywords']['secondary'][:3])}...")
            logger.info(f"   • Local Hook: {research_pack['local_hook']['title']}")
            logger.info(f"   • Medical Sources: {len(research_pack['medical_sources'])}")
            logger.info(f"   • Local Resources: {len(research_pack['local_resources'])}")
            logger.info(f"   • FAQs Generated: {len(research_pack['faqs'])}")
            # Log content suggestions if available
            content_suggestions = research_pack.get('content_suggestions', {})
            if content_suggestions.get('must_include'):
                logger.info(f"   • Must Include: {content_suggestions['must_include'][:2]}...")
            if research_pack.get('story_leads'):
                logger.info(f"   • Story Leads: {len(research_pack['story_leads'])} options")
            if research_pack.get('evidence_cards'):
                logger.info(f"   • Evidence Cards: {len(research_pack['evidence_cards'])} facts")

        # Step 3: Run all 8 writers in parallel
        logger.info("Step 3: Running writer agents for all markets...")
        writer = WriterAgent()

        writing_tasks = [
            writer.run(tone_profile, research_pack, markets_config)
            for research_pack in research_packs_dict.values()
        ]

        drafts = await asyncio.gather(*writing_tasks)
        logger.info(f"Drafts generated for {len(drafts)} markets")

        # Debug: Log writer output details
        for draft in drafts:
            logger.info(f"✍️ WRITER OUTPUT for {draft['market_name']}:")
            logger.info(f"   • Title: {draft['title']}")
            logger.info(f"   • Word Count: {draft['metadata']['word_count']}")
            logger.info(f"   • Sections Generated: {len([s for s in draft['html_content'].split('<h2>') if s.strip()])-1}")
            logger.info(f"   • Primary Keyword: {draft['primary_keyword']}")
            logger.info(f"   • Internal Links: {len(draft['metadata']['internal_links'])}")
            logger.info(f"   • Images Suggested: {len(draft['metadata']['images'])}")

        # Step 4: Editor QA with similarity gate + rewrite loop
        logger.info("Step 4: Running editor QA and similarity checks...")
        editor_qa = EditorQAAgent()
        final_articles, editor_report, uniqueness_report = await editor_qa.run(drafts)

        logger.info(f"Editor QA completed. Similarity gate: {uniqueness_report['status']}")
        logger.info(f"🔍 EDITOR/QA OUTPUT:")
        logger.info(f"   • Compliance Checks: {len(editor_report['compliance_checks'])} markets validated")
        logger.info(f"   • Rewrite Attempts: {editor_report['rewrite_attempts']}")
        logger.info(f"   • Final Status: {editor_report['final_status']}")
        logger.info(f"   • Similarity Status: {uniqueness_report['status']}")
        if uniqueness_report["status"] == "manual_review_required":
            logger.warning(f"   • Manual review required for: {', '.join(uniqueness_report['failing_markets'])}")

        # Debug: Log final article details
        for article in final_articles:
            logger.info(f"📄 FINAL ARTICLE for {article['market_name']}:")
            logger.info(f"   • Final Title: {article['title']}")
            logger.info(f"   • Final Word Count: {article['metadata']['word_count']}")
            logger.info(f"   • HTML File: {article['html_filename']}")
            logger.info(f"   • JSON File: {article['json_filename']}")

        # Post-process articles to enforce banned-phrase removal, meta length, and keyword density
        final_articles = [
            _post_process_article(article, research_packs_dict.get(article["market_name"], {}))
            for article in final_articles
        ]

        # Step 5: SERP gap check (optional)
        serp_gap_checker = SerpGapChecker()
        serp_gap_summary = {"enabled": serp_gap_checker.enabled}
        if serp_gap_checker.enabled:
            logger.info("Step 5: Running SERP gap checks...")
            checked_at = datetime.now().isoformat()
            try:
                serp_gap_payload = await _run_serp_gap_checks(final_articles, serp_gap_checker, output_dir, checked_at)
                serp_gap_summary.update({
                    "file": "serp_gap.json",
                    "checked_at": checked_at,
                    "result_count": len(serp_gap_payload.get("results", [])),
                })
            except Exception as exc:
                serp_gap_summary.update({
                    "status": "failed",
                    "error": str(exc),
                    "checked_at": checked_at,
                })
                logger.warning(f"SERP gap check failed: {exc}")
        else:
            logger.info("Step 5: SERP gap checks disabled.")

        # Step 6: Sequential image selection (after all articles finalized)
        # Skip real selection in explore mode to keep runs fast and avoid burning uniqueness pool
        if run_mode == "publish":
            logger.info("Step 6: Running sequential image selection for all markets...")
            image_selector = ImageSelectorAgent()
            
            for i, article in enumerate(final_articles):
                market_name = article['market_name']
                market_key = article['market']
                
                # Find the corresponding research pack
                research_pack = research_packs_dict.get(market_name)
                if not research_pack:
                    logger.warning(f"No research pack found for {market_name}, skipping image selection")
                    continue
                
                logger.info(f"Selecting image for {market_name} ({i+1}/{len(final_articles)})...")
                selected_image = await image_selector.run(article, research_pack)
                
                # Update article with selected image
                article = _update_article_with_image(article, selected_image, market_key, market_name)
                final_articles[i] = article
                
                logger.info(f"✓ Image selected for {market_name} (ID: {selected_image.get('id', 'N/A')})")
            
            logger.info(f"Image selection completed for {len(final_articles)} markets")
        else:
            logger.info("Step 6: RUN_MODE=explore → skipping real image selection; keeping placeholders.")
            for i, article in enumerate(final_articles):
                metadata = article.get("metadata", {})
                images = metadata.get("images", [])
                if images:
                    # Mark status for downstream reviewers
                    metadata["image_selection_status"] = "skipped_explore_mode"
                    # Ensure placeholder URL is set
                    if not images[0].get("url"):
                        images[0]["url"] = load_config("brightspot_guide").get("placeholder_image", "")
                        images[0]["url_large"] = images[0]["url"]
                final_articles[i]["metadata"] = metadata

        # Step 7: Dispatcher email delivery (or review handoff)
        review_mode = os.getenv("REVIEW_MODE", "off").strip().lower() in ("on", "true", "1", "yes")
        logger.info("Step 7: Dispatching articles and sending email...")
        dispatcher = DispatcherAgent()
        run_summary = {
            **run_state,
            "end_time": datetime.now().isoformat(),
            "status": "completed",
            "tone_profile": tone_profile,
            "articles": final_articles,
            "editor_report": editor_report,
            "uniqueness_report": uniqueness_report,
            "serp_gap": serp_gap_summary,
        }

        delivery_success = await dispatcher.dispatch(final_articles, run_summary, send_email=not review_mode)

        if not delivery_success:
            run_summary["status"] = "email_failed"
            logger.error("Email delivery failed - articles saved to output directory")
        else:
            logger.info("Email delivered successfully")

        if review_mode:
            logger.info("Review mode enabled - creating GM review requests...")
            try:
                deadline_hours = int(os.getenv("REVIEW_DEADLINE_HOURS", "48"))
            except ValueError:
                deadline_hours = 48

            review_state = create_review_state(
                run_summary,
                base_url=os.getenv("REVIEW_PORTAL_BASE_URL", ""),
                deadline_hours=deadline_hours
            )
            recipients = await send_review_requests(review_state, run_summary)
            run_summary["review_mode"] = True
            run_summary["review_recipients"] = recipients
            run_summary["status"] = "awaiting_review"
            logger.info(f"Review requests sent to {len(recipients)} GM(s)")
            try:
                save_json(run_summary, Path(run_summary["output_dir"]) / "run_summary.json")
            except Exception as e:
                logger.warning(f"Failed to update run_summary.json with review metadata: {e}")

        # Step 8: Archive articles for duplicate content prevention
        if run_summary.get("status") in ("completed", "awaiting_review"):
            logger.info("Step 8: Archiving articles to content archive...")
            try:
                archive = ContentArchive()
                for article in final_articles:
                    metadata = article.get("metadata", {})
                    research_pack = research_packs_dict.get(article.get("market_name", ""))
                    week_theme = research_pack.get("week_theme") if research_pack else None
                    
                    archive.archive_article(
                        run_id=run_id,
                        market=article.get("market", ""),
                        market_name=article.get("market_name", ""),
                        title=article.get("title", ""),
                        primary_keyword=metadata.get("primary_keyword", ""),
                        secondary_keywords=metadata.get("secondary_keywords", []),
                        week_theme=week_theme,
                        slug=metadata.get("suggested_slug", ""),
                        html_content=article.get("html_content", ""),  # P1 Fix: Store full HTML for duplicate detection
                        published_date=run_id
                    )
                    
                    # Archive research sources if available
                    if research_pack:
                        source_urls = []
                        # Phase 5: Archive web discovery URLs (P2 Fix)
                        for url in research_pack.get("discovered_sources", []):
                            if url:
                                source_urls.append(url)
                        # Also archive local_resources and medical_sources
                        for source in research_pack.get("local_resources", []):
                            if source.get("url"):
                                source_urls.append(source["url"])
                        for source in research_pack.get("medical_sources", []):
                            if source.get("url"):
                                source_urls.append(source["url"])
                        if source_urls:
                            archive.archive_sources(run_id, article.get("market", ""), source_urls)
                
                logger.info(f"✓ Archived {len(final_articles)} articles")
            except Exception as e:
                logger.warning(f"Failed to archive articles: {e}")
                # Don't fail the run if archiving fails

        # Step 9: Finalize
        duration = (datetime.now() - datetime.fromisoformat(run_state["start_time"])).total_seconds()
        run_summary["duration_seconds"] = duration

        logger.info(f"Run completed in {duration:.2f} seconds")
        logger.info("=" * 70)

        return run_summary

    except Exception as e:
        logger.error(f"Fatal error in weekly run: {e}", exc_info=True)
        run_state["status"] = "failed"
        run_state["error"] = str(e)
        run_state["end_time"] = datetime.now().isoformat()
        return run_state


def _get_default_tone_profile() -> Dict:
    """Get default tone profile (simplified for vertical slice)."""
    return {
        "phrasing_norms": [
            "your loved one",
            "aging with dignity",
            "independence and safety",
            "white-glove care"
        ],
        "cadence": "warm, professional, empathetic",
        "structure_patterns": {
            "h2_per_article": 6,
            "bullet_usage": 0.7
        },
        "cta_vibe": "helpful_invitation",
        "extracted_at": datetime.now().isoformat(),
        "cache_until": (datetime.now()).isoformat()
    }


def _update_article_with_image(article: Dict, selected_image: Dict, market: str, market_name: str) -> Dict:
    """Update article with selected image data and update HTML.
    
    Args:
        article: Article dict to update
        selected_image: Selected image from ImageSelectorAgent
        market: Market key (e.g., 'toronto')
        market_name: Market display name (e.g., 'Toronto')
        
    Returns:
        Dict: Updated article with image integrated
    """
    from utils.config_loader import load_config
    
    brand_config = load_config("brand")
    colors = brand_config.get('color_palette', {})
    styling = brand_config.get('styling_patterns', {})
    hero_style = styling.get('hero_image', {})
    
    # Format image for metadata
    image_data = {
        "url": selected_image.get("url_large", selected_image.get("url", "")),
        "credit": selected_image.get("credit", ""),
        "photographer": selected_image.get("photographer", "Unknown"),
        "recommended_filename": f"{market}-hero-pexels.jpg",
        "alt_text": selected_image.get("alt_text", selected_image.get("alt", f"Home care in {market_name}")),
        "is_recommended": True,
        "relevance_score": selected_image.get("relevance_score", 5),
        "match_description": selected_image.get("match_description", ""),
        "id": selected_image.get("id")
    }
    
    # Update metadata
    article['metadata']['images'] = [image_data]
    article['image_filename'] = image_data['recommended_filename']
    
    # Update HTML with selected image
    image_url = image_data['url']
    image_alt = image_data['alt_text']
    credit_html = image_data['credit']
    
    # Build new hero image HTML
    hero_html = (
        f'<div style="margin-bottom: {hero_style.get("margin_bottom", "32px")};">\n'
        f'<img src="{image_url}" '
        f'alt="{image_alt}" '
        f'style="width: {hero_style.get("width", "100%")}; height: {hero_style.get("height", "auto")}; '
        f'border-radius: {hero_style.get("border_radius", "8px")}; '
        f'box-shadow: {hero_style.get("box_shadow", "0 4px 6px -1px rgba(0, 0, 0, 0.1)")};">\n'
    )
    if credit_html:
        hero_html += (
            f'<p style="font-size: 12px; color: #999; margin-top: 8px; text-align: right;">'
            f'{credit_html}</p>\n'
        )
    hero_html += '</div>'
    
    # Replace existing hero image in HTML
    html_content = article['html_content']
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, "html.parser")
        wrapper = soup.find("div", class_="blog-content-module")
        if not wrapper:
            raise ValueError("Missing wrapper")

        # Remove any hero blocks before the first H1 to avoid duplicates
        for child in list(wrapper.children):
            if getattr(child, "name", None) is None and not str(child).strip():
                continue
            if getattr(child, "name", None) == "h1":
                break
            if getattr(child, "name", None) == "div" and child.find("img"):
                child.decompose()

        hero_fragment = BeautifulSoup(hero_html, "html.parser")
        h1 = wrapper.find("h1")
        if h1:
            for element in reversed(hero_fragment.contents):
                h1.insert_before(element)
        else:
            wrapper.insert(0, hero_fragment)

        html_content = str(soup)
    except Exception:
        # Fallback: insert after wrapper start if parsing fails
        import re
        insert_pattern = r'(<div class="blog-content-module">)'
        html_content = re.sub(insert_pattern, f'\\1\n{hero_html}', html_content, count=1)
    
    article['html_content'] = html_content
    
    return article


def _post_process_article(article: Dict, research_pack: Dict) -> Dict:
    """Clean final article after editor rewrites to enforce banned-phrase removal,
    meta description length, and cap keyword density without extra LLM calls."""
    banned = load_config("brand").get("anti_generic_requirements", {}).get("banned_openers", [])
    if not banned:
        banned = [
            "Many families", "As we age", "In today's world", "It's no secret that",
            "When it comes to", "There's no doubt that", "In recent years",
            "As we all know", "For many people", "It goes without saying", "In this article"
        ]

    def remove_banned_phrases(text: str) -> str:
        for phrase in banned:
            text = re.sub(rf"\b{re.escape(phrase)}\b", "Families", text, flags=re.IGNORECASE)
        return text

    def unwrap_lists(text: str) -> str:
        text = re.sub(r"<p>([^<]*?)(<ol[^>]*>.*?</ol>)</p>", r"<p>\1</p>\n\2", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"<p>([^<]*?)(<ul[^>]*>.*?</ul>)</p>", r"<p>\1</p>\n\2", text, flags=re.IGNORECASE | re.DOTALL)
        return text

    def sanitize_sources(text: str) -> str:
        text = re.sub(r"\(Source:[^)]+\)", "", text, flags=re.IGNORECASE)
        text = re.sub(r"<span[^>]*>[^<]*source:[^<]*</span>", "", text, flags=re.IGNORECASE)
        text = re.sub(r"Source:\s*<a[^>]+>.*?</a>", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"\(.*?source:.*?\)", "", text, flags=re.IGNORECASE | re.DOTALL)
        text = re.sub(r"Source:\s*[^.<]+(?:\.\s*|$)", "", text, flags=re.IGNORECASE)
        return text

    def clamp_keyword_density(text: str, keyword: str, market_name: str, max_pct: float = 2.5) -> str:
        if not keyword:
            return text
        plain = re.sub(r"<[^>]+>", " ", text)
        words = [w for w in plain.split() if w.strip()]
        if not words:
            return text
        max_occ = int((max_pct / 100.0) * len(words))
        if max_occ < 1:
            max_occ = 1
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        matches = list(pattern.finditer(text))
        if len(matches) <= max_occ:
            return text
        keep = matches[:max_occ]
        drop = matches[max_occ:]
        repl = f"{market_name} care"
        # Replace from end to preserve earlier placements
        for m in reversed(drop):
            text = text[:m.start()] + repl + text[m.end():]
        return text

    def enforce_meta_description(metadata: Dict, market_name: str, primary_keyword: str) -> None:
        meta_desc = metadata.get("meta_description", "") or ""
        if 150 <= len(meta_desc) <= 160:
            return
        template = (
            f"{market_name} {primary_keyword.lower()} guide: use local services, annual care reviews, "
            f"and practical steps to keep aging in place safe this year."
        )
        if len(template) < 150:
            template += " Learn how to schedule assessments, use tax credits, and improve winter safety."
        if len(template) > 160:
            template = template[:157].rsplit(" ", 1)[0].rstrip(".,;:") + "."
        metadata["meta_description"] = template

    html = article.get("html_content", "")
    primary_keyword = article.get("primary_keyword") or article.get("metadata", {}).get("primary_keyword", "")
    market_name = article.get("market_name", "")

    html = sanitize_sources(html)
    html = unwrap_lists(html)
    html = remove_banned_phrases(html)
    html = clamp_keyword_density(html, primary_keyword, market_name, max_pct=2.5)
    html = remove_banned_phrases(html)  # second pass in case replacements reintroduced patterns

    article["html_content"] = html

    # Meta description enforcement
    metadata = article.get("metadata", {})
    enforce_meta_description(metadata, market_name, primary_keyword or market_name)
    article["metadata"] = metadata

    return article


def _build_serp_query(article: Dict) -> str:
    primary_keyword = article.get("primary_keyword") or article.get("metadata", {}).get("primary_keyword", "")
    market_name = article.get("market_name", "") or article.get("market", "")
    if not primary_keyword:
        return market_name
    if market_name and market_name.lower() not in primary_keyword.lower():
        return f"{market_name} {primary_keyword}"
    return primary_keyword


async def _run_serp_gap_checks(
    articles: List[Dict],
    checker: SerpGapChecker,
    output_dir: Path,
    checked_at: str,
) -> Dict:
    try:
        concurrency = int(os.getenv("SERP_GAP_CONCURRENCY", "2"))
    except ValueError:
        concurrency = 2
    concurrency = max(1, concurrency)
    semaphore = asyncio.Semaphore(concurrency)

    async def _check(article: Dict):
        query = _build_serp_query(article)
        async with semaphore:
            try:
                report = await checker.check(
                    query,
                    article.get("html_content", ""),
                    article.get("market_name", ""),
                )
                return article, query, report, None
            except Exception as exc:
                return article, query, None, str(exc)

    tasks = [_check(article) for article in articles]
    results = await asyncio.gather(*tasks)

    serp_results = []
    for article, query, report, error in results:
        if report:
            serp_results.append(report)
            metadata = article.get("metadata", {})
            metadata["serp_gap"] = {
                "query": report["query"],
                "checked_at": report["checked_at"],
                "coverage_ratio": report["coverage_ratio"],
                "missing_topics": report["missing_topics"],
                "top_results": report["top_results"],
            }
            article["metadata"] = metadata
        else:
            serp_results.append({
                "market": article.get("market_name", ""),
                "query": query,
                "checked_at": checked_at,
                "status": "failed" if error else "skipped",
                "error": error,
            })

    payload = {
        "checked_at": checked_at,
        "results": serp_results,
    }
    save_json(payload, output_dir / "serp_gap.json")
    return payload
