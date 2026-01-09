"""Review portal web app."""
from __future__ import annotations

import asyncio
import html as html_lib
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from bs4 import BeautifulSoup

from agents.image_selector import ImageSelectorAgent
from review.review_manager import find_review_by_token, record_feedback, update_market_state
from review.processor import process_run, _build_inline_diff
from utils.config_loader import get_env_var
from utils.file_manager import load_json, save_json
from tools.pexels_client import PexelsClient

app = FastAPI()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _outputs_root() -> Path:
    return Path(__file__).parent.parent.parent / "outputs"


def _run_output_dir(run_id: str) -> Path:
    return _outputs_root() / run_id


def _review_dir(run_id: str) -> Path:
    return _run_output_dir(run_id) / "review"


def _load_article_html(run_id: str, market: str) -> str:
    html_path = _run_output_dir(run_id) / f"{market}.html"
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    return html_path.read_text(encoding="utf-8")


def _load_article_metadata(run_id: str, market: str) -> dict:
    json_path = _run_output_dir(run_id) / f"{market}.json"
    return load_json(json_path) or {}


def _load_feedback(feedback_path: Optional[str]) -> dict:
    if not feedback_path:
        return {}
    path = Path(feedback_path)
    if not path.exists():
        return {}
    return load_json(path) or {}


def _load_diff_html(run_id: str, market: str, market_state: dict) -> Optional[str]:
    diff_path = market_state.get("diff_path")
    path = Path(diff_path) if diff_path else (_review_dir(run_id) / "diff" / f"{market}.html")
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def _refresh_diff(run_id: str, market: str, after_html: str) -> Optional[str]:
    diff_dir = _review_dir(run_id) / "diff"
    snapshot_path = diff_dir / f"{market}_before.html"
    if not snapshot_path.exists():
        return None
    before_html = snapshot_path.read_text(encoding="utf-8")
    diff_html = _build_inline_diff(before_html, after_html)
    diff_dir.mkdir(parents=True, exist_ok=True)
    diff_path = diff_dir / f"{market}.html"
    diff_path.write_text(diff_html, encoding="utf-8")
    return str(diff_path)


def _register_image(image: dict, used_ids: set, used_urls: set) -> None:
    if not image:
        return
    image_id = image.get("id")
    if image_id:
        used_ids.add(str(image_id))
    for key in ("url", "url_large", "url_medium"):
        url = image.get(key)
        if url:
            used_urls.add(url)


def _collect_used_images(run_id: str, state: dict, exclude_market: Optional[str] = None) -> tuple[set, set]:
    used_ids: set = set()
    used_urls: set = set()

    for market, market_state in state.get("markets", {}).items():
        if exclude_market and market == exclude_market:
            continue

        status = market_state.get("status")
        decision = market_state.get("decision")
        if status not in ("approved", "rewritten", "auto_approved_due_to_deadline") and decision != "approve":
            continue

        selection = market_state.get("image_selection") or {}
        _register_image(selection, used_ids, used_urls)

        metadata = _load_article_metadata(run_id, market)
        for image in metadata.get("images") or []:
            _register_image(image, used_ids, used_urls)

    return used_ids, used_urls


def _default_image_query(market_name: str, metadata: dict) -> str:
    primary_keyword = (metadata.get("primary_keyword") or "").strip()
    if primary_keyword:
        return f"{primary_keyword} senior care"
    return f"{market_name} senior care"


def _extract_article_context(html_content: str, metadata: dict, market_name: str) -> dict:
    title = metadata.get("title", "")
    primary_keyword = metadata.get("primary_keyword", "")
    deck = ""
    main_topics: List[str] = []

    try:
        soup = BeautifulSoup(html_content, "html.parser")
        h1 = soup.find("h1")
        if not title and h1:
            title = h1.get_text(" ", strip=True)
        if h1:
            next_p = h1.find_next("p")
            if next_p:
                deck = next_p.get_text(" ", strip=True)
        main_topics = [h.get_text(" ", strip=True) for h in soup.find_all("h2")]
    except Exception:
        pass

    return {
        "title": title,
        "deck": deck,
        "primary_keyword": primary_keyword,
        "main_topics": main_topics,
        "market_name": market_name,
    }


def _image_key(image: dict) -> str:
    return str(
        image.get("id")
        or image.get("url")
        or image.get("url_large")
        or image.get("url_medium")
        or ""
    )


async def _rank_images_with_vision(
    images: List[dict],
    context: dict,
    max_results: int = 6,
    max_to_score: int = 8,
) -> Tuple[List[dict], bool]:
    if not images:
        return [], False

    agent = ImageSelectorAgent()
    vision_config = agent.model_config.get("models", {}).get("image_analysis", {})
    model = vision_config.get("model", "gpt-4o")
    max_tokens = vision_config.get("max_tokens", 300)
    temperature = vision_config.get("temperature", 0.3)

    candidates = images[:max_to_score]
    tasks = [
        agent._analyze_single_image(
            image,
            context.get("title", ""),
            context.get("deck", ""),
            context.get("primary_keyword", ""),
            context.get("main_topics", []),
            context.get("market_name", ""),
            model,
            max_tokens,
            temperature,
        )
        for image in candidates
    ]

    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    except Exception:
        return images[:max_results], False

    scored = [
        result
        for result in results
        if isinstance(result, dict) and "relevance_score" in result
    ]
    if not scored:
        return images[:max_results], False

    scored.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
    filtered = [
        image for image in scored
        if image.get("suitable", True)
        and image.get("relevance_score", 0) >= agent.MIN_RELEVANCE_SCORE
    ]
    if not filtered:
        filtered = scored

    selected = filtered[:max_results]
    selected_keys = {_image_key(image) for image in selected if _image_key(image)}
    if len(selected) < max_results:
        for image in images:
            if len(selected) >= max_results:
                break
            key = _image_key(image)
            if not key or key in selected_keys:
                continue
            selected.append(image)
            selected_keys.add(key)

    return selected, True


def _replace_attr(tag: str, attr: str, value: str) -> str:
    pattern = rf'{attr}="[^"]*"'
    replacement = f'{attr}="{value}"'
    if re.search(pattern, tag):
        return re.sub(pattern, replacement, tag, count=1)
    if tag.endswith(">"):
        return tag[:-1] + f' {replacement}>'
    return tag


def _update_hero_image(html_content: str, image_url: str, image_alt: str, credit_html: str) -> str:
    match = re.search(r"<img\s+[^>]*>", html_content, flags=re.IGNORECASE)
    if not match:
        return html_content

    img_tag = match.group(0)
    img_tag = _replace_attr(img_tag, "src", html_lib.escape(image_url, quote=True))
    img_tag = _replace_attr(img_tag, "alt", html_lib.escape(image_alt, quote=True))

    updated_html = html_content[:match.start()] + img_tag + html_content[match.end():]
    img_end = match.start() + len(img_tag)

    credit_block = (
        '<p style="font-size: 12px; color: #999; margin-top: 8px; text-align: right;">'
        f"{credit_html}</p>"
    )

    tail = updated_html[img_end:]
    credit_match = re.search(r"<p[^>]*>.*?Pexels.*?</p>", tail, flags=re.IGNORECASE | re.DOTALL)
    if credit_match:
        start = img_end + credit_match.start()
        end = img_end + credit_match.end()
        updated_html = updated_html[:start] + credit_block + updated_html[end:]
    else:
        updated_html = updated_html[:img_end] + credit_block + updated_html[img_end:]

    try:
        soup = BeautifulSoup(updated_html, "html.parser")
        wrapper = soup.find("div", class_="blog-content-module")
        if not wrapper:
            return updated_html
        hero_blocks = []
        for child in list(wrapper.children):
            if getattr(child, "name", None) is None and not str(child).strip():
                continue
            if getattr(child, "name", None) == "h1":
                break
            if getattr(child, "name", None) == "div" and child.find("img"):
                hero_blocks.append(child)
        for extra in hero_blocks[1:]:
            extra.decompose()
        return str(soup)
    except Exception:
        return updated_html


def _apply_image_selection(run_id: str, market: str, selection: dict) -> None:
    output_dir = _run_output_dir(run_id)
    html_path = output_dir / f"{market}.html"
    json_path = output_dir / f"{market}.json"

    image_url = selection.get("url_large") or selection.get("url") or ""
    if not image_url:
        return

    image_alt = selection.get("alt", "") or selection.get("alt_text", "") or f"{market} home care"
    credit_html = PexelsClient().get_credit_html(selection)

    diff_path = None
    html_content = None
    if html_path.exists():
        html_content = html_path.read_text(encoding="utf-8")
        html_content = _update_hero_image(html_content, image_url, image_alt, credit_html)
        html_path.write_text(html_content, encoding="utf-8")
        diff_path = _refresh_diff(run_id, market, html_content)

    metadata = load_json(json_path) or {}
    images = metadata.get("images") or []
    image_entry = {
        "id": selection.get("id"),
        "url": selection.get("url", image_url),
        "photographer": selection.get("photographer", ""),
        "photographer_url": selection.get("photographer_url", ""),
        "alt_text": image_alt,
        "credit": credit_html,
        "is_recommended": True,
    }
    if images:
        images[0].update(image_entry)
    else:
        images = [image_entry]
    metadata["images"] = images
    metadata["last_review_update"] = datetime.now().isoformat()
    if diff_path:
        metadata["diff_path"] = diff_path
    save_json(metadata, json_path)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    context = {
        "request": request,
        "title": "TheKey Review Portal",
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/review/{token}", response_class=HTMLResponse)
async def review_page(request: Request, token: str, image_query: Optional[str] = None) -> HTMLResponse:
    match = find_review_by_token(token)
    if not match:
        raise HTTPException(status_code=404, detail="Review token not found")

    run_id, market, state = match
    market_state = state["markets"][market]
    market_name = market_state.get("market_name", market)

    html_content = _load_article_html(run_id, market)
    metadata = _load_article_metadata(run_id, market)
    feedback = _load_feedback(market_state.get("feedback_path"))
    html_content_diff = _load_diff_html(run_id, market, market_state)

    raw_query = (image_query or "").strip()
    search_query = raw_query or _default_image_query(market_name, metadata)
    image_source_label = (
        f"Search results for “{raw_query}” (ranked for relevance)"
        if raw_query
        else "Suggested options (ranked for relevance)"
    )

    image_results = []
    image_filtered_count = 0
    pexels = PexelsClient()
    image_results = await pexels.search_images(
        search_query,
        per_page=18,
        orientation="landscape"
    )
    used_ids, used_urls = _collect_used_images(run_id, state, exclude_market=market)
    filtered_results = []
    for image in image_results:
        image_id = image.get("id")
        if image_id and str(image_id) in used_ids:
            continue
        urls = [image.get("url"), image.get("url_large"), image.get("url_medium")]
        if any(url in used_urls for url in urls if url):
            continue
        filtered_results.append(image)
    image_filtered_count = max(0, len(image_results) - len(filtered_results))

    context_data = _extract_article_context(html_content, metadata, market_name)
    image_results, validation_applied = await _rank_images_with_vision(
        filtered_results,
        context_data,
        max_results=6,
        max_to_score=8,
    )
    image_validation_note = "AI relevance checks applied to prioritize the best matches." if validation_applied else ""

    context = {
        "request": request,
        "token": token,
        "run_id": run_id,
        "market": market,
        "market_name": market_name,
        "article_title": metadata.get("title", ""),
        "primary_keyword": metadata.get("primary_keyword", ""),
        "status": market_state.get("status"),
        "decision": market_state.get("decision"),
        "submitted_at": market_state.get("submitted_at"),
        "feedback": feedback,
        "html_content": html_content,
        "html_content_diff": html_content_diff,
        "image_query": raw_query,
        "image_source_label": image_source_label,
        "image_validation_note": image_validation_note,
        "image_results": image_results,
        "image_filtered_count": image_filtered_count,
    }

    return templates.TemplateResponse("review.html", context)


@app.post("/review/{token}")
async def submit_review(
    request: Request,
    token: str,
    decision: str = Form(...),
    notes: str = Form(""),
    reviewer_name: str = Form(""),
    reviewer_email: str = Form(""),
    image_url: str = Form(""),
    image_url_large: str = Form(""),
    image_url_medium: str = Form(""),
    image_alt: str = Form(""),
    image_photographer: str = Form(""),
    image_photographer_url: str = Form(""),
    image_id: str = Form(""),
) -> RedirectResponse:
    match = find_review_by_token(token)
    if not match:
        raise HTTPException(status_code=404, detail="Review token not found")

    run_id, market, _state = match
    decision = decision.strip().lower()

    if decision not in ("approve", "revise"):
        raise HTTPException(status_code=400, detail="Invalid decision")

    if decision == "revise" and not notes.strip():
        raise HTTPException(status_code=400, detail="Feedback required for revisions")

    record_feedback(
        run_id=run_id,
        market=market,
        decision=decision,
        notes=notes.strip(),
        reviewer_name=reviewer_name.strip(),
        reviewer_email=reviewer_email.strip(),
    )

    selection = {}
    if image_url or image_url_large:
        selection = {
            "id": image_id or None,
            "url": image_url.strip(),
            "url_large": image_url_large.strip() or image_url.strip(),
            "url_medium": image_url_medium.strip(),
            "photographer": image_photographer.strip(),
            "photographer_url": image_photographer_url.strip(),
            "alt": image_alt.strip(),
            "alt_text": image_alt.strip(),
        }
        _apply_image_selection(run_id, market, selection)
        update_market_state(run_id, market, {
            "image_selection": selection,
            "image_updated_at": datetime.now().isoformat(),
        })

    if get_env_var("REVIEW_PROCESS_ON_SUBMIT", "true").lower() in ("true", "1", "yes"):
        asyncio.create_task(process_run(run_id))

    return RedirectResponse(url=f"/review/{token}/status", status_code=303)


@app.get("/review/{token}/status", response_class=HTMLResponse)
async def review_status(request: Request, token: str) -> HTMLResponse:
    match = find_review_by_token(token)
    if not match:
        raise HTTPException(status_code=404, detail="Review token not found")

    run_id, market, state = match
    market_state = state["markets"][market]
    metadata = _load_article_metadata(run_id, market)

    context = {
        "request": request,
        "token": token,
        "run_id": run_id,
        "market": market,
        "market_name": market_state.get("market_name", market),
        "article_title": metadata.get("title", ""),
        "status": market_state.get("status"),
        "decision": market_state.get("decision"),
        "submitted_at": market_state.get("submitted_at"),
        "processed_at": market_state.get("processed_at"),
        "last_error": market_state.get("last_error"),
    }

    return templates.TemplateResponse("status.html", context)


@app.get("/review/{token}/download")
async def download_article(token: str) -> FileResponse:
    match = find_review_by_token(token)
    if not match:
        raise HTTPException(status_code=404, detail="Review token not found")

    run_id, market, _state = match
    html_path = _run_output_dir(run_id) / f"{market}.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="HTML file not found")

    return FileResponse(path=str(html_path), filename=f"{market}.html")
