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
from urllib.parse import quote, urlparse

import aiohttp
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
from review.processor import (
    process_run,
    _build_inline_diff,
    _build_attachments,
    _load_articles_for_email,
    _final_recipients,
)
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
        "id": "tool_serp_gap",
        "label": "SERP gap checker",
        "path": "src/tools/serp_gap_checker.py",
        "category": "Tools",
        "description": "DuckDuckGo SERP headings vs article coverage."
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

def _update_run_summary_metadata(run_id: str, market: str, metadata: dict) -> None:
    summary_path = _run_output_dir(run_id) / "run_summary.json"
    summary = load_json(summary_path) or {}
    articles = summary.get("articles", [])
    updated = False
    for article in articles:
        if article.get("market") == market:
            article["metadata"] = metadata
            if metadata.get("title"):
                article["title"] = metadata["title"]
            if metadata.get("primary_keyword"):
                article["primary_keyword"] = metadata["primary_keyword"]
            image_filename = (metadata.get("images") or [{}])[0].get("recommended_filename")
            if image_filename:
                article["image_filename"] = image_filename
            updated = True
            break
    if updated:
        save_json(summary, summary_path)


def _update_run_summary_html(run_id: str, market: str, html_content: str) -> None:
    summary_path = _run_output_dir(run_id) / "run_summary.json"
    summary = load_json(summary_path) or {}
    articles = summary.get("articles", [])
    updated = False
    for article in articles:
        if article.get("market") == market:
            article["html_content"] = html_content
            updated = True
            break
    if updated:
        save_json(summary, summary_path)

def _backfill_review_send_status(state: dict, summary: dict) -> bool:
    if not state or not summary:
        return False

    recipients = summary.get("review_recipients") or []
    if not recipients:
        return False

    recipients_set = {addr.strip().lower() for addr in recipients if addr}
    if not recipients_set:
        return False

    sent_at = summary.get("end_time") or state.get("created_at") or datetime.now().isoformat()
    changed = False

    for market_state in state.get("markets", {}).values():
        if market_state.get("review_send_status"):
            continue
        gm_email = (market_state.get("gm_email") or "").strip().lower()
        if not gm_email:
            continue
        if gm_email in recipients_set:
            market_state["review_send_status"] = "sent"
            market_state["review_sent_at"] = sent_at
            market_state["review_send_error"] = None
            changed = True
        else:
            market_state["review_send_status"] = "pending"
            changed = True

    if changed and state.get("run_id"):
        save_review_state(state["run_id"], state)

    return changed


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


def _load_full_article_html(run_id: str, market: str) -> str:
    summary = _load_run_summary(run_id)
    for article in summary.get("articles", []):
        if article.get("market") == market:
            html_content = article.get("html_content") or ""
            if html_content:
                return html_content
            break
    html_path = _run_output_dir(run_id) / f"{market}.html"
    if not html_path.exists():
        raise FileNotFoundError(str(html_path))
    return html_path.read_text(encoding="utf-8")


def _load_article_html(run_id: str, market: str) -> str:
    return _load_full_article_html(run_id, market)


def _strip_to_body_html(html_content: str) -> str:
    """Return HTML that begins after the subheadline (deck) section."""
    if not html_content:
        return ""

    try:
        soup = BeautifulSoup(html_content, "html.parser")
    except Exception:
        return html_content

    wrapper = soup.find("div", class_="blog-content-module")
    target = wrapper or soup

    h1_tag = target.find("h1")
    if not h1_tag:
        return str(wrapper) if wrapper else html_content

    deck_tag = h1_tag.find_next("p")

    for child in list(target.children):
        if child == h1_tag:
            break
        child.extract()

    h1_tag.extract()

    if deck_tag:
        deck_tag.extract()

    return str(wrapper) if wrapper else str(soup)


def _load_article_metadata(run_id: str, market: str) -> dict:
    json_path = _run_output_dir(run_id) / f"{market}.json"
    if json_path.exists():
        metadata = load_json(json_path) or {}
        if metadata:
            return metadata
    summary = _load_run_summary(run_id)
    for article in summary.get("articles", []):
        if article.get("market") == market:
            return article.get("metadata") or {}
    return {}


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


def _image_proxy_hosts() -> set[str]:
    raw = get_env_var("IMAGE_PROXY_HOSTS", "images.pexels.com")
    hosts = {host.strip().lower() for host in raw.split(",") if host.strip()}
    return hosts or {"images.pexels.com"}


def _image_cache_dir() -> Path:
    cache_dir = _app_root() / "data" / "image_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _image_cache_ttl_seconds() -> Optional[int]:
    try:
        hours = int(get_env_var("IMAGE_CACHE_TTL_HOURS", "168"))
    except ValueError:
        hours = 168
    if hours <= 0:
        return None
    return hours * 3600


def _image_cache_max_bytes() -> int:
    try:
        max_bytes = int(get_env_var("IMAGE_CACHE_MAX_BYTES", str(10 * 1024 * 1024)))
    except ValueError:
        max_bytes = 10 * 1024 * 1024
    return max(512 * 1024, max_bytes)


def _image_proxy_timeout_seconds() -> int:
    try:
        timeout = int(get_env_var("IMAGE_PROXY_TIMEOUT_SECONDS", "12"))
    except ValueError:
        timeout = 12
    return max(3, timeout)


def _image_cache_max_age_seconds() -> int:
    ttl = _image_cache_ttl_seconds()
    if ttl is None:
        return 86400
    return max(300, ttl)


def _image_cache_paths(url: str) -> Tuple[Path, Path]:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()
    cache_dir = _image_cache_dir()
    return cache_dir / f"{key}.bin", cache_dir / f"{key}.json"


def _is_cache_fresh(meta: dict) -> bool:
    ttl = _image_cache_ttl_seconds()
    if ttl is None:
        return True
    fetched_at = meta.get("fetched_at")
    if not fetched_at:
        return False
    try:
        fetched_at_dt = datetime.fromisoformat(fetched_at)
    except ValueError:
        return False
    age = (datetime.now() - fetched_at_dt).total_seconds()
    return age <= ttl


def _should_proxy_image_url(url: str) -> bool:
    if not url:
        return False
    if url.startswith("/media/image?"):
        return False
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    return host in _image_proxy_hosts()


def _proxy_image_url(url: str) -> str:
    return f"/media/image?url={quote(url, safe='')}"


def _proxy_html_images(html_content: str) -> str:
    try:
        soup = BeautifulSoup(html_content, "html.parser")
    except Exception:
        return html_content

    updated = False
    for img in soup.find_all("img"):
        src = (img.get("src") or "").strip()
        if not src:
            continue
        if _should_proxy_image_url(src):
            img["src"] = _proxy_image_url(src)
            updated = True

    return str(soup) if updated else html_content


def _strip_images_from_html(html_content: str) -> str:
    try:
        soup = BeautifulSoup(html_content, "html.parser")
    except Exception:
        return html_content

    removed = False
    for tag in soup.find_all(["img", "picture", "source"]):
        tag.decompose()
        removed = True

    return str(soup) if removed else html_content


def _attach_proxy_urls(image: dict) -> None:
    for key, proxy_key in (
        ("url", "proxy_url"),
        ("url_large", "proxy_url_large"),
        ("url_medium", "proxy_url_medium"),
    ):
        url = (image.get(key) or "").strip()
        if url and _should_proxy_image_url(url):
            image[proxy_key] = _proxy_image_url(url)


async def _fetch_and_cache_image(url: str, image_path: Path, meta_path: Path) -> str:
    timeout = aiohttp.ClientTimeout(total=_image_proxy_timeout_seconds())
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    max_bytes = _image_cache_max_bytes()
    tmp_path = image_path.with_suffix(".tmp")

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, headers=headers, allow_redirects=True) as response:
            if response.status != 200:
                raise HTTPException(status_code=502, detail="Image fetch failed")
            content_type = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                raise HTTPException(status_code=400, detail="Invalid image content type")

            size = 0
            try:
                with tmp_path.open("wb") as handle:
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise HTTPException(status_code=413, detail="Image too large")
                        handle.write(chunk)
                tmp_path.replace(image_path)
            except HTTPException:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise
            except Exception as exc:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except Exception:
                        pass
                raise HTTPException(status_code=502, detail="Image fetch failed") from exc

            meta = {
                "url": url,
                "content_type": content_type,
                "fetched_at": datetime.now().isoformat(),
            }
            save_json(meta, meta_path)
            return content_type


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
    try:
        html_content = _load_full_article_html(run_id, market)
    except FileNotFoundError:
        html_content = None

    if html_content:
        html_content = _update_hero_image(html_content, image_url, image_alt, credit_html)
        body_html = _strip_to_body_html(html_content)
        html_path.write_text(body_html, encoding="utf-8")
        diff_path = _refresh_diff(run_id, market, html_content)
        _update_run_summary_html(run_id, market, html_content)

    if json_path.exists():
        metadata = load_json(json_path) or {}
    else:
        metadata = _load_article_metadata(run_id, market)
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
    if json_path.exists():
        save_json(metadata, json_path)
    _update_run_summary_metadata(run_id, market, metadata)


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
        _backfill_review_send_status(state, summary)
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
    _backfill_review_send_status(state, summary)
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
            "rewrite_attempts": market_state.get("rewrite_attempts", 0),
            "awaiting_rereview": (
                market_state.get("status") == "rewritten"
                and market_state.get("decision") == "revise"
            ),
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
        "summary_status": summary.get("status"),
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


@app.post("/admin/run/{run_id}/final-email")
async def admin_send_final_email(
    request: Request,
    run_id: str,
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    state = load_review_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    output_dir = _run_output_dir(run_id)
    markets = list(state.get("markets", {}).keys())
    if not markets:
        raise HTTPException(status_code=400, detail="No markets found")

    articles = _load_articles_for_email(output_dir, markets)
    email_sender = EmailSender()
    subject, body = email_sender.build_success_email(run_id, articles, output_dir)
    attachments = _build_attachments(output_dir, markets)
    recipients = _final_recipients()

    now = datetime.now().isoformat()
    state["final_email_recipients"] = recipients
    state["final_email_resend_count"] = state.get("final_email_resend_count", 0) + 1
    if not recipients:
        state["final_email_status"] = "skipped_no_recipients"
        state["final_email_error"] = "no_recipients"
        state["final_email_sent_at"] = None
        save_review_state(run_id, state)
        return RedirectResponse(url=f"/admin/run/{run_id}", status_code=303)

    sent = await email_sender.send_email(
        subject=subject,
        body=body,
        to_addresses=recipients,
        attachments=attachments,
    )
    state["final_email_sent_at"] = now
    state["final_email_status"] = "sent" if sent else "failed"
    state["final_email_error"] = None if sent else "send_failed"
    save_review_state(run_id, state)
    return RedirectResponse(url=f"/admin/run/{run_id}", status_code=303)


@app.post("/admin/run/{run_id}/override")
async def admin_toggle_override(
    request: Request,
    run_id: str,
    mode: str = Form("enable"),
) -> RedirectResponse:
    redirect = _require_admin(request)
    if redirect:
        return redirect

    state = load_review_state(run_id)
    if not state:
        raise HTTPException(status_code=404, detail="Run not found")

    mode = (mode or "enable").strip().lower()
    now = datetime.now().isoformat()
    if mode == "disable":
        state["admin_override"] = False
        state["admin_override_cleared_at"] = now
    else:
        state["admin_override"] = True
        state["admin_override_set_at"] = now
        if state.get("status") == "finalized":
            state["status"] = "awaiting_reviews"

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


@app.get("/media/image")
async def proxy_image(url: str) -> FileResponse:
    if not url:
        raise HTTPException(status_code=400, detail="Missing url")
    if not _should_proxy_image_url(url):
        raise HTTPException(status_code=403, detail="URL not allowed")

    image_path, meta_path = _image_cache_paths(url)
    meta = load_json(meta_path) or {}
    if image_path.exists() and meta and _is_cache_fresh(meta):
        content_type = meta.get("content_type") or "image/jpeg"
        headers = {"Cache-Control": f"public, max-age={_image_cache_max_age_seconds()}"}
        return FileResponse(image_path, media_type=content_type, headers=headers)

    if image_path.exists() or meta_path.exists():
        if not _is_cache_fresh(meta):
            try:
                image_path.unlink()
            except Exception:
                pass
            try:
                meta_path.unlink()
            except Exception:
                pass

    content_type = await _fetch_and_cache_image(url, image_path, meta_path)
    headers = {"Cache-Control": f"public, max-age={_image_cache_max_age_seconds()}"}
    return FileResponse(image_path, media_type=content_type, headers=headers)


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
    html_content = _proxy_html_images(html_content)
    if html_content_diff:
        html_content_diff = _strip_images_from_html(html_content_diff)

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
    for image in image_results:
        _attach_proxy_urls(image)
    image_validation_note = "AI relevance checks applied to prioritize the best matches." if validation_applied else ""

    feedback_notes = feedback.get("notes", "") if feedback else ""
    if (market_state.get("status") or "").lower() == "rewritten":
        feedback_notes = ""

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
        "feedback_notes": feedback_notes,
        "html_content": html_content,
        "html_content_diff": html_content_diff,
        "image_query": raw_query,
        "image_source_label": image_source_label,
        "image_validation_note": image_validation_note,
        "image_results": image_results,
        "image_filtered_count": image_filtered_count,
        "section_titles": context_data.get("main_topics", []),
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
    html_content = _load_full_article_html(run_id, market)
    download_dir = _review_dir(run_id)
    download_dir.mkdir(parents=True, exist_ok=True)
    download_path = download_dir / f"{market}_review.html"
    download_path.write_text(html_content, encoding="utf-8")
    return FileResponse(path=str(download_path), filename=f"{market}.html")
