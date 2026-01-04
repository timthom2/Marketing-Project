"""Orchestrator coordinator."""
import asyncio
import os
from datetime import datetime
from pathlib import Path
from typing import Dict

from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from agents.editor_qa import EditorQAAgent
from agents.dispatcher import DispatcherAgent
from agents.image_selector import ImageSelectorAgent
# Note: ResearcherAgent also imported for reset_story_lead_rotation()
from utils.config_loader import load_config
from utils.file_manager import create_output_directory
from utils.logger import get_logger

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
        "test_market": test_market if test_market else None
    }

    logger.info(f"Initializing run {run_id}")
    logger.info(f"Output directory: {output_dir}")
    logger.info(f"Markets to process: {len(markets_to_process)} ({', '.join(markets_to_process.keys())})")

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

        # Step 5: Sequential image selection (after all articles finalized)
        # This ensures no duplicate images across markets by selecting one at a time
        logger.info("Step 5: Running sequential image selection for all markets...")
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

        # Step 6: Dispatcher email delivery
        logger.info("Step 6: Dispatching articles and sending email...")
        dispatcher = DispatcherAgent()
        run_summary = {
            **run_state,
            "end_time": datetime.now().isoformat(),
            "status": "completed",
            "tone_profile": tone_profile,
            "articles": final_articles,
            "editor_report": editor_report,
            "uniqueness_report": uniqueness_report
        }

        delivery_success = await dispatcher.dispatch(final_articles, run_summary)

        if not delivery_success:
            run_summary["status"] = "email_failed"
            logger.error("Email delivery failed - articles saved to output directory")
        else:
            logger.info("Email delivered successfully")

        # Step 7: Finalize
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
    import re
    html_content = article['html_content']
    
    # Pattern to match the hero image div
    hero_pattern = r'<div style="margin-bottom: [^"]+;">\s*<img src="[^"]*"[^>]*>\s*(?:<p style="font-size: 12px[^<]*</p>\s*)?</div>'
    
    if re.search(hero_pattern, html_content, re.DOTALL):
        html_content = re.sub(hero_pattern, hero_html, html_content, count=1, flags=re.DOTALL)
    else:
        # If no hero found, insert after blog-content-module div
        insert_pattern = r'(<div class="blog-content-module">)'
        html_content = re.sub(insert_pattern, f'\\1\n{hero_html}', html_content, count=1)
    
    article['html_content'] = html_content
    
    return article
