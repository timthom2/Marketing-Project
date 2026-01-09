"""Review portal web app."""
from __future__ import annotations

import asyncio
import base64
import html as html_lib
import hashlib
import hmac
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from bs4 import BeautifulSoup

from agents.image_selector import ImageSelectorAgent
from review.review_manager import (
    find_review_by_token,
    list_review_runs,
    load_review_state,
    record_feedback,
    save_review_state,
    update_market_state,
)
from review.processor import process_run, _build_inline_diff
from utils.config_loader import get_env_var
from utils.file_manager import load_json, save_json
from tools.email_sender import EmailSender
from tools.pexels_client import PexelsClient

app = FastAPI()

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

ADMIN_COOKIE_NAME = "admin_session"


def _app_root() -> Path:
    return Path(__file__).parent.parent.parent


def _admin_secret() -> str:
    return get_env_var("ADMIN_SESSION_SECRET", "") or ""


def _admin_username() -> str:
    return get_env_var("ADMIN_USERNAME", "") or ""


def _admin_password() -> str:
    return get_env_var("ADMIN_PASSWORD", "") or ""


def _admin_password_hash() -> str:
    return get_env_var("ADMIN_PASSWORD_HASH", "") or ""


def _admin_session_ttl_seconds() -> int:
    try:
        hours = int(get_env_var("ADMIN_SESSION_TTL_HOURS", "12"))
    except ValueError:
        hours = 12
    return max(1, hours) * 3600


def _admin_cookie_secure() -> bool:
    base_url = get_env_var("REVIEW_PORTAL_BASE_URL", "").strip().lower()
    if base_url.startswith("https://"):
        return True
    return get_env_var("ADMIN_COOKIE_SECURE", "false").strip().lower() in ("true", "1", "yes")


def _admin_enabled() -> bool:
    return bool(_admin_username() and (_admin_password() or _admin_password_hash()) and _admin_secret())


def _pbkdf2_hash(password: str, salt: bytes, iterations: int = 260_000) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${iterations}${salt_b64}${digest_b64}"


def _verify_password(candidate: str) -> bool:
    password_hash = _admin_password_hash()
    if password_hash:
        try:
            algo, iterations_str, salt_b64, digest_b64 = password_hash.split("$", 3)
            if algo != "pbkdf2_sha256":
                return False
            iterations = int(iterations_str)
            salt_padding = "=" * (-len(salt_b64) % 4)
            digest_padding = "=" * (-len(digest_b64) % 4)
            salt = base64.urlsafe_b64decode(salt_b64 + salt_padding)
            expected = base64.urlsafe_b64decode(digest_b64 + digest_padding)
        except Exception:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", candidate.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(digest, expected)

    password = _admin_password()
    if not password:
        return False
    return hmac.compare_digest(password, candidate)


def _sign_session(username: str, issued_at: int) -> str:
    payload = f"{username}|{issued_at}"
    secret = _admin_secret().encode("utf-8")
    signature = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
    token = f"{payload}|{signature}".encode("utf-8")
    return base64.urlsafe_b64encode(token).decode("ascii").rstrip("=")


def _verify_session(token: str) -> Optional[str]:
    if not token:
        return None
    try:
        padding = "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(token + padding).decode("utf-8")
        username, issued_at_str, signature = decoded.split("|", 2)
        payload = f"{username}|{issued_at_str}"
        secret = _admin_secret().encode("utf-8")
        expected = hmac.new(secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        issued_at = int(issued_at_str)
    except Exception:
        return None

    if username != _admin_username():
        return None

    ttl = _admin_session_ttl_seconds()
    if (int(datetime.now().timestamp()) - issued_at) > ttl:
        return None

    return username


def _get_admin_user(request: Request) -> Optional[str]:
    token = request.cookies.get(ADMIN_COOKIE_NAME, "")
    if not token:
        return None
    if not _admin_enabled():
        return None
    return _verify_session(token)


def _require_admin(request: Request) -> Optional[RedirectResponse]:
    if _get_admin_user(request):
        return None
    next_path = quote(request.url.path)
    return RedirectResponse(url=f"/admin/login?next={next_path}", status_code=303)


ADMIN_EDITABLE_FILES = [
    {
        "id": "content_calendar",
        "label": "Content calendar",
        "path": "config/content_calendar.yaml",
        "category": "Calendar & Config",
        "description": "Weekly themes and rotation rules."
    },
    {
        "id": "markets",
        "label": "Markets configuration",
        "path": "config/markets.yaml",
        "category": "Calendar & Config",
        "description": "Market-specific keywords, healthcare context, and local sources."
    },
    {
        "id": "brand",
        "label": "Brand rules",
        "path": "config/brand.yaml",
        "category": "Calendar & Config",
        "description": "Tone, CTA, and brand requirements."
    },
    {
        "id": "model_routing",
        "label": "Model routing",
        "path": "config/model_routing.yaml",
        "category": "Calendar & Config",
        "description": "Model assignments and token caps."
    },
    {
        "id": "reviewers",
        "label": "Reviewers",
        "path": "config/reviewers.yaml",
        "category": "Calendar & Config",
        "description": "GM email mappings per market."
    },
    {
        "id": "brightspot_guide",
        "label": "Brightspot guide",
        "path": "config/brightspot_guide.yaml",
        "category": "Calendar & Config",
        "description": "HTML requirements and placeholders."
    },
    {
        "id": "research_sources",
        "label": "Research sources",
        "path": "config/research_sources.yaml",
        "category": "Calendar & Config",
        "description": "Priority sources and research constraints."
    },
    {
        "id": "rules",
        "label": "Editorial rules",
        "path": "config/rules.yaml",
        "category": "Calendar & Config",
        "description": "Validation rules and quality checks."
    },
    {
        "id": "tone_seed_urls",
        "label": "Tone seed URLs",
        "path": "config/tone_seed_urls.yaml",
        "category": "Calendar & Config",
        "description": "Tone reference sources."
    },
    {
        "id": "agent_researcher",
        "label": "Researcher agent",
        "path": "src/agents/researcher.py",
        "category": "Agents",
        "description": "Research synthesis and local context."
    },
    {
        "id": "agent_writer",
        "label": "Writer agent",
        "path": "src/agents/writer.py",
        "category": "Agents",
        "description": "Draft generation and Brightspot HTML."
    },
    {
        "id": "agent_editor",
        "label": "Editor QA agent",
        "path": "src/agents/editor_qa.py",
        "category": "Agents",
        "description": "QA checks and rewrite logic."
    },
    {
        "id": "agent_image_selector",
        "label": "Image selector agent",
        "path": "src/agents/image_selector.py",
        "category": "Agents",
        "description": "Vision scoring and Pexels selection."
    },
    {
        "id": "agent_dispatcher",
        "label": "Dispatcher agent",
        "path": "src/agents/dispatcher.py",
        "category": "Agents",
        "description": "Email dispatch and final output packaging."
    },
    {
        "id": "agent_review_rewriter",
        "label": "Review rewriter",
        "path": "src/agents/review_rewriter.py",
        "category": "Agents",
        "description": "Rewrite pass for GM revision notes."
    },
    {
        "id": "orchestrator",
        "label": "Orchestrator coordinator",
        "path": "src/orchestrator/coordinator.py",
        "category": "Orchestration",
        "description": "Workflow ordering and run summary output."
    },
    {
        "id": "review_processor",
        "label": "Review processor",
        "path": "src/review/processor.py",
        "category": "Orchestration",
        "description": "Review processing, reminders, and finalization."
    },
    {
        "id": "review_manager",
        "label": "Review manager",
        "path": "src/review/review_manager.py",
        "category": "Orchestration",
        "description": "Review state and GM mapping."
    },
    {
        "id": "tool_email_sender",
        "label": "Email sender",
        "path": "src/tools/email_sender.py",
        "category": "Tools",
        "description": "SMTP email delivery."
    },
    {
        "id": "tool_web_discovery",
        "label": "Web discovery",
        "path": "src/tools/web_discovery.py",
        "category": "Tools",
        "description": "Source discovery and filtering."
    },
    {
        "id": "tool_web_fetch",
        "label": "Web fetch + extract",
        "path": "src/tools/web_fetch_extract.py",
        "category": "Tools",
        "description": "Fetch + extraction for research sources."
    },
    {
        "id": "tool_similarity",
        "label": "Similarity checker",
        "path": "src/tools/similarity_checker.py",
        "category": "Tools",
        "description": "TF-IDF and embedding similarity checks."
    },
    {
        "id": "tool_html_validator",
        "label": "HTML validator",
        "path": "src/tools/html_validator.py",
        "category": "Tools",
        "description": "Brightspot HTML validation."
    },
    {
        "id": "tool_pexels",
        "label": "Pexels client",
        "path": "src/tools/pexels_client.py",
        "category": "Tools",
        "description": "Image search and credits."
    },
    {
        "id": "web_app",
        "label": "Review portal app",
        "path": "src/web/app.py",
        "category": "Web",
        "description": "Web routes and admin views."
    },
    {
        "id": "web_base",
        "label": "Portal base template",
        "path": "src/web/templates/base.html",
        "category": "Web",
        "description": "Shared layout and styles."
    },
    {
        "id": "web_review",
        "label": "Review page template",
        "path": "src/web/templates/review.html",
        "category": "Web",
        "description": "GM review interface."
    },
    {
        "id": "web_status",
        "label": "Review status template",
        "path": "src/web/templates/status.html",
        "category": "Web",
        "description": "GM status page."
    },
    {
        "id": "web_index",
        "label": "Portal index template",
        "path": "src/web/templates/index.html",
        "category": "Web",
        "description": "Landing page content."
    },
    {
        "id": "util_openai_client",
        "label": "OpenAI client",
        "path": "src/utils/openai_client.py",
        "category": "Core Utils",
        "description": "API calls and usage logging."
    },
    {
        "id": "util_config_loader",
        "label": "Config loader",
        "path": "src/utils/config_loader.py",
        "category": "Core Utils",
        "description": "Environment + YAML loader."
    },
]


def _admin_file_catalog() -> List[dict]:
    catalog = []
    for entry in ADMIN_EDITABLE_FILES:
        path = _app_root() / entry["path"]
        exists = path.exists()
        size = path.stat().st_size if exists else None
        catalog.append({
            **entry,
            "path_display": str(entry["path"]),
            "exists": exists,
            "size": size,
        })
    return catalog


def _admin_file_entry(file_id: str) -> Optional[dict]:
    for entry in ADMIN_EDITABLE_FILES:
        if entry["id"] == file_id:
            return entry
    return None


def _backup_file(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup_path = path.with_name(f"{path.name}.bak-{timestamp}")
    backup_path.write_bytes(path.read_bytes())
    return backup_path


def _load_run_summary(run_id: str) -> dict:
    summary_path = _run_output_dir(run_id) / "run_summary.json"
    return load_json(summary_path) or {}


def _count_market_statuses(markets: dict) -> dict:
    counts = {
        "pending": 0,
        "submitted": 0,
        "approved": 0,
        "rewritten": 0,
        "auto_approved_due_to_deadline": 0,
        "unknown": 0,
    }
    for market_state in markets.values():
        status = market_state.get("status") or "unknown"
        if status not in counts:
            counts["unknown"] += 1
        else:
            counts[status] += 1
    counts["total"] = len(markets)
    counts["approved_total"] = (
        counts["approved"]
        + counts["rewritten"]
        + counts["auto_approved_due_to_deadline"]
    )
    return counts


def _outputs_root() -> Path:
    return _app_root() / "outputs"


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


@app.get("/admin/login", response_class=HTMLResponse)
async def admin_login(request: Request, next: Optional[str] = None) -> HTMLResponse:
    context = {
        "request": request,
        "title": "Admin Login",
        "next": next or "/admin",
        "enabled": _admin_enabled(),
        "error": None,
    }
    return templates.TemplateResponse("login.html", context)


@app.post("/admin/login")
async def admin_login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form("/admin"),
) -> HTMLResponse:
    if not _admin_enabled():
        context = {
            "request": request,
            "title": "Admin Login",
            "next": next,
            "enabled": False,
            "error": "Admin login is not configured. Set ADMIN_USERNAME, ADMIN_PASSWORD, and ADMIN_SESSION_SECRET."
        }
        return templates.TemplateResponse("login.html", context)

    if not (username and password):
        context = {
            "request": request,
            "title": "Admin Login",
            "next": next,
            "enabled": True,
            "error": "Username and password are required.",
        }
        return templates.TemplateResponse("login.html", context)

    if username != _admin_username() or not _verify_password(password):
        context = {
            "request": request,
            "title": "Admin Login",
            "next": next,
            "enabled": True,
            "error": "Invalid username or password.",
        }
        return templates.TemplateResponse("login.html", context)

    issued_at = int(datetime.now().timestamp())
    token = _sign_session(username, issued_at)
    response = RedirectResponse(url=next or "/admin", status_code=303)
    response.set_cookie(
        ADMIN_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=_admin_cookie_secure(),
        max_age=_admin_session_ttl_seconds(),
    )
    return response


@app.get("/admin/logout")
async def admin_logout() -> RedirectResponse:
    response = RedirectResponse(url="/admin/login", status_code=303)
    response.delete_cookie(ADMIN_COOKIE_NAME)
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request) -> HTMLResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    runs = []
    for run_id, _path in list_review_runs().items():
        state = load_review_state(run_id) or {}
        summary = _load_run_summary(run_id)
        counts = _count_market_statuses(state.get("markets", {}))
        runs.append({
            "run_id": run_id,
            "created_at": state.get("created_at") or summary.get("start_time"),
            "deadline_at": state.get("deadline_at"),
            "status": state.get("status", "unknown"),
            "markets_total": counts.get("total", 0),
            "approved_total": counts.get("approved_total", 0),
            "pending": counts.get("pending", 0),
            "submitted": counts.get("submitted", 0),
        })

    runs.sort(key=lambda x: x.get("run_id", ""), reverse=True)

    stats = {
        "runs": len(runs),
        "markets_total": sum(run["markets_total"] for run in runs),
        "approved_total": sum(run["approved_total"] for run in runs),
        "pending_total": sum(run["pending"] for run in runs),
        "submitted_total": sum(run["submitted"] for run in runs),
    }

    context = {
        "request": request,
        "title": "Admin Dashboard",
        "runs": runs,
        "stats": stats,
    }
    return templates.TemplateResponse("admin/index.html", context)


@app.get("/admin/run/{run_id}", response_class=HTMLResponse)
async def admin_run_detail(request: Request, run_id: str) -> HTMLResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    state = load_review_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    summary = _load_run_summary(run_id)
    counts = _count_market_statuses(state.get("markets", {}))

    markets = []
    for market_key, market_state in state.get("markets", {}).items():
        metadata = _load_article_metadata(run_id, market_key)
        markets.append({
            "market": market_key,
            "market_name": market_state.get("market_name", market_key),
            "gm_name": market_state.get("gm_name", ""),
            "gm_email": market_state.get("gm_email", ""),
            "status": market_state.get("status", "pending"),
            "decision": market_state.get("decision"),
            "review_url": market_state.get("review_url"),
            "review_sent_at": market_state.get("review_sent_at") or state.get("created_at"),
            "review_send_status": market_state.get("review_send_status", "pending"),
            "last_reminder_at": market_state.get("last_reminder_at"),
            "reminder_count": market_state.get("reminder_count", 0),
            "submitted_at": market_state.get("submitted_at"),
            "processed_at": market_state.get("processed_at"),
            "last_error": market_state.get("last_error"),
            "title": metadata.get("title", ""),
        })

    markets.sort(key=lambda x: x.get("market_name", ""))

    context = {
        "request": request,
        "title": f"Run {run_id}",
        "run_id": run_id,
        "state": state,
        "summary": summary,
        "counts": counts,
        "markets": markets,
    }
    return templates.TemplateResponse("admin/run.html", context)


@app.post("/admin/run/{run_id}/deadline")
async def admin_update_deadline(
    request: Request,
    run_id: str,
    deadline_at: str = Form(""),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    state = load_review_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    state["deadline_at"] = deadline_at or None
    save_review_state(run_id, state)
    return RedirectResponse(url=f"/admin/run/{run_id}", status_code=303)


@app.post("/admin/run/{run_id}/market/{market}/status")
async def admin_update_market_status(
    request: Request,
    run_id: str,
    market: str,
    status: str = Form(...),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    allowed = {
        "pending",
        "submitted",
        "approved",
        "rewritten",
        "auto_approved_due_to_deadline",
    }
    status = status.strip()
    if status not in allowed:
        raise HTTPException(status_code=400, detail="Invalid status")

    updates = {"status": status}
    if status in ("approved", "rewritten", "auto_approved_due_to_deadline"):
        updates["processed_at"] = datetime.now().isoformat()
    update_market_state(run_id, market, updates)
    return RedirectResponse(url=f"/admin/run/{run_id}", status_code=303)


@app.post("/admin/run/{run_id}/market/{market}/email")
async def admin_resend_email(
    request: Request,
    run_id: str,
    market: str,
    kind: str = Form("request"),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    state = load_review_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    market_state = state.get("markets", {}).get(market, {})
    gm_email = market_state.get("gm_email", "").strip()
    if not gm_email:
        raise HTTPException(status_code=400, detail="GM email not configured")

    metadata = _load_article_metadata(run_id, market)
    subject = ""
    body = ""
    reply_to = get_env_var("REVIEW_REPLY_TO", "").strip() or None

    email_sender = EmailSender()
    if kind == "reminder":
        subject, body = email_sender.build_review_reminder_email(
            run_id,
            market_state.get("market_name", market),
            metadata.get("title", ""),
            market_state.get("review_url", ""),
            deadline_at=state.get("deadline_at"),
            reminder_count=market_state.get("reminder_count", 0) + 1,
        )
    else:
        subject, body = email_sender.build_review_request_email(
            run_id,
            market_state.get("market_name", market),
            metadata.get("title", ""),
            market_state.get("review_url", ""),
            deadline_at=state.get("deadline_at"),
        )

    result = await email_sender.send_email(
        subject=subject,
        body=body,
        to_addresses=[gm_email],
        reply_to=reply_to,
    )

    now = datetime.now().isoformat()
    updates = {}
    if kind == "reminder":
        updates["last_reminder_at"] = now
        updates["reminder_count"] = market_state.get("reminder_count", 0) + 1
    else:
        updates["review_sent_at"] = now
        updates["review_send_status"] = "sent"

    if not result:
        updates["review_send_status"] = "failed"
        updates["review_send_failed_at"] = now
        updates["review_send_error"] = "send_failed"
    else:
        updates["review_send_error"] = None
    update_market_state(run_id, market, updates)

    return RedirectResponse(url=f"/admin/run/{run_id}", status_code=303)


@app.post("/admin/run/{run_id}/send-reminders")
async def admin_send_reminders(request: Request, run_id: str) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    state = load_review_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    email_sender = EmailSender()
    reply_to = get_env_var("REVIEW_REPLY_TO", "").strip() or None
    now = datetime.now().isoformat()

    for market, market_state in state.get("markets", {}).items():
        if market_state.get("status") != "pending":
            continue
        gm_email = market_state.get("gm_email", "").strip()
        if not gm_email:
            continue

        metadata = _load_article_metadata(run_id, market)
        subject, body = email_sender.build_review_reminder_email(
            run_id,
            market_state.get("market_name", market),
            metadata.get("title", ""),
            market_state.get("review_url", ""),
            deadline_at=state.get("deadline_at"),
            reminder_count=market_state.get("reminder_count", 0) + 1,
        )

        result = await email_sender.send_email(
            subject=subject,
            body=body,
            to_addresses=[gm_email],
            reply_to=reply_to,
        )

        if result:
            market_state["last_reminder_at"] = now
            market_state["reminder_count"] = market_state.get("reminder_count", 0) + 1
            market_state["review_send_error"] = None
        else:
            market_state["review_send_status"] = "failed"
            market_state["review_send_failed_at"] = now
            market_state["review_send_error"] = "send_failed"

        state["markets"][market] = market_state

    save_review_state(run_id, state)
    return RedirectResponse(url=f"/admin/run/{run_id}", status_code=303)


@app.get("/admin/system", response_class=HTMLResponse)
async def admin_system(request: Request) -> HTMLResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    catalog = _admin_file_catalog()
    categories = {}
    for entry in catalog:
        categories.setdefault(entry["category"], []).append(entry)
    for group in categories.values():
        group.sort(key=lambda x: x["label"])

    context = {
        "request": request,
        "title": "System Editor",
        "categories": categories,
    }
    return templates.TemplateResponse("admin/system.html", context)


@app.get("/admin/system/{file_id}", response_class=HTMLResponse)
async def admin_edit_file(request: Request, file_id: str, saved: Optional[str] = None) -> HTMLResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    entry = _admin_file_entry(file_id)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")

    path = _app_root() / entry["path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    content = path.read_text(encoding="utf-8")
    context = {
        "request": request,
        "title": f"Edit {entry['label']}",
        "entry": entry,
        "content": content,
        "path_display": entry["path"],
        "last_modified": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
        "saved": bool(saved),
    }
    return templates.TemplateResponse("admin/edit.html", context)


@app.post("/admin/system/{file_id}")
async def admin_save_file(
    request: Request,
    file_id: str,
    content: str = Form(""),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    entry = _admin_file_entry(file_id)
    if not entry:
        raise HTTPException(status_code=404, detail="File not found")

    path = _app_root() / entry["path"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing on disk")

    _backup_file(path)
    path.write_text(content or "", encoding="utf-8")
    return RedirectResponse(url=f"/admin/system/{file_id}?saved=1", status_code=303)


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
