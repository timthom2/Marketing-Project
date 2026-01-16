"""Background processor for GM review submissions."""
from __future__ import annotations

import asyncio
import difflib
import html as html_lib
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from agents.editor_qa import EditorQAAgent
from agents.review_rewriter import ReviewRewriteAgent
from tools.email_sender import EmailSender
from tools.html_validator import HTMLValidator
from tools.pexels_client import PexelsClient
from utils.file_manager import load_json, save_json
from utils.logger import get_logger
from utils.config_loader import get_env_var
from review.review_manager import (
    list_review_runs,
    load_review_state,
    get_review_schedule,
    normalize_review_deadline,
    review_deadline_passed,
    update_market_state,
    save_review_state,
)

logger = get_logger(__name__)

MAX_EMAIL_ATTACHMENT_BYTES = 25 * 1024 * 1024


def _outputs_root() -> Path:
    return Path(__file__).parent.parent.parent / "outputs"


def _run_output_dir(run_id: str) -> Path:
    return _outputs_root() / run_id


def _review_dir(run_id: str) -> Path:
    return _run_output_dir(run_id) / "review"


def _diff_dir(run_id: str) -> Path:
    return _review_dir(run_id) / "diff"


def _diff_path(run_id: str, market: str) -> Path:
    return _diff_dir(run_id) / f"{market}.html"


def _snapshot_path(run_id: str, market: str) -> Path:
    return _diff_dir(run_id) / f"{market}_before.html"


def _load_run_summary(output_dir: Path) -> Dict:
    summary_path = output_dir / "run_summary.json"
    return load_json(summary_path) or {}


def _find_summary_article(summary: Dict, market: str) -> Optional[Dict]:
    for article in summary.get("articles", []):
        if article.get("market") == market:
            return article
    return None


def _load_html(output_dir: Path, market: str) -> str:
    summary = _load_run_summary(output_dir)
    article = _find_summary_article(summary, market) if summary else None
    html_content = article.get("html_content") if article else ""
    if html_content:
        return html_content
    return (output_dir / f"{market}.html").read_text(encoding="utf-8")


def _save_html(output_dir: Path, market: str, html: str) -> None:
    summary = _load_run_summary(output_dir)
    article = _find_summary_article(summary, market) if summary else None
    if article is not None:
        article["html_content"] = html
        save_json(summary, output_dir / "run_summary.json")

    body_html = _strip_to_body_html(html)
    (output_dir / f"{market}.html").write_text(body_html, encoding="utf-8")


def _strip_to_body_html(html_content: str) -> str:
    """Return HTML that begins after the subheadline (deck) section."""
    if not html_content:
        return ""

    try:
        from bs4 import BeautifulSoup
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


def _load_metadata(output_dir: Path, market: str) -> Dict:
    metadata_path = output_dir / f"{market}.json"
    if metadata_path.exists():
        return load_json(metadata_path) or {}
    summary = _load_run_summary(output_dir)
    article = _find_summary_article(summary, market) or {}
    return article.get("metadata", {}) or {}


def _save_metadata(output_dir: Path, market: str, metadata: Dict) -> None:
    metadata_path = output_dir / f"{market}.json"
    if metadata_path.exists():
        save_json(metadata, metadata_path)

    summary_path = output_dir / "run_summary.json"
    summary = load_json(summary_path) or {}
    article = _find_summary_article(summary, market)
    if not article:
        return
    article["metadata"] = metadata
    if metadata.get("title"):
        article["title"] = metadata["title"]
    if metadata.get("primary_keyword"):
        article["primary_keyword"] = metadata["primary_keyword"]
    image_filename = (metadata.get("images") or [{}])[0].get("recommended_filename")
    if image_filename:
        article["image_filename"] = image_filename
    save_json(summary, summary_path)


def _count_words(html_content: str) -> int:
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_content, "html.parser")
    for element in soup(["style", "script", "code"]):
        element.decompose()
    text = soup.get_text(separator=" ")
    words = [w for w in text.split() if w.strip()]
    return len(words)


def _is_diffable_text(node) -> bool:
    if not node or not node.strip():
        return False
    parent = getattr(node, "parent", None)
    if not parent:
        return True
    return parent.name not in ("style", "script")


def _tokenize(text: str) -> List[str]:
    return re.split(r"(\s+)", text)


def _build_inline_diff(before_html: str, after_html: str) -> str:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return after_html

    soup_before = BeautifulSoup(before_html, "html.parser")
    soup_after = BeautifulSoup(after_html, "html.parser")

    before_nodes = [n for n in soup_before.find_all(string=True) if _is_diffable_text(n)]
    after_nodes = [n for n in soup_after.find_all(string=True) if _is_diffable_text(n)]

    for before_node, after_node in zip(before_nodes, after_nodes):
        before_text = str(before_node)
        after_text = str(after_node)
        if before_text == after_text:
            continue

        before_tokens = _tokenize(before_text)
        after_tokens = _tokenize(after_text)
        matcher = difflib.SequenceMatcher(a=before_tokens, b=after_tokens)
        chunks: List[str] = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                chunks.append(html_lib.escape("".join(after_tokens[j1:j2])))
            elif tag == "insert":
                added = html_lib.escape("".join(after_tokens[j1:j2]))
                chunks.append(f'<mark class="diff-add">{added}</mark>')
            elif tag == "delete":
                removed = html_lib.escape("".join(before_tokens[i1:i2]))
                chunks.append(f'<del class="diff-del">{removed}</del>')
            elif tag == "replace":
                removed = html_lib.escape("".join(before_tokens[i1:i2]))
                added = html_lib.escape("".join(after_tokens[j1:j2]))
                chunks.append(f'<del class="diff-del">{removed}</del>')
                chunks.append(f'<mark class="diff-add">{added}</mark>')

        diff_html = "".join(chunks)
        fragment = BeautifulSoup(diff_html, "html.parser")
        if not fragment.contents:
            after_node.replace_with("")
            continue
        first = fragment.contents[0]
        after_node.replace_with(first)
        current = first
        for extra in fragment.contents[1:]:
            current.insert_after(extra)
            current = extra

    highlight_style = (
        "<style>"
        ".diff-add{background:#fde68a;padding:0 2px;border-radius:3px;}"
        ".diff-del{background:#fecaca;padding:0 2px;border-radius:3px;text-decoration:line-through;}"
        "</style>"
    )
    return f"{highlight_style}{str(soup_after)}"


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
        from bs4 import BeautifulSoup
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


def _apply_image_selection_html(html: str, selection: Dict) -> str:
    if not selection:
        return html
    image_url = selection.get("url_large") or selection.get("url") or ""
    if not image_url:
        return html
    image_alt = selection.get("alt") or selection.get("alt_text") or ""
    credit_html = PexelsClient().get_credit_html(selection)
    return _update_hero_image(html, image_url, image_alt, credit_html)


def _load_articles_for_email(output_dir: Path, markets: List[str]) -> List[Dict]:
    articles: List[Dict] = []
    summary = _load_run_summary(output_dir)
    summary_map = {
        article.get("market"): article
        for article in summary.get("articles", [])
        if article.get("market")
    }
    for market in markets:
        summary_article = summary_map.get(market, {})
        metadata = summary_article.get("metadata") or _load_metadata(output_dir, market)
        html_path = output_dir / f"{market}.html"
        html_content = summary_article.get("html_content") or ""
        if not html_content and html_path.exists():
            html_content = html_path.read_text(encoding="utf-8")
        title = summary_article.get("title") or metadata.get("title", "")
        primary_keyword = summary_article.get("primary_keyword") or metadata.get("primary_keyword", "")
        image_filename = (
            summary_article.get("image_filename")
            or metadata.get("images", [{}])[0].get("recommended_filename", "")
        )
        market_name = (
            summary_article.get("market_name")
            or metadata.get("market_name", "")
        )
        articles.append({
            "market": market,
            "market_name": market_name,
            "title": title,
            "primary_keyword": primary_keyword,
            "image_filename": image_filename,
            "html_filename": f"{market}.html",
            "html_content": html_content,
            "metadata": metadata,
        })
    return articles


def _build_attachments(output_dir: Path, markets: List[str]) -> List[Path]:
    attachments: List[Path] = []
    for market in markets:
        attachments.append(output_dir / f"{market}.html")
        metadata = _load_metadata(output_dir, market)
        image_filename = metadata.get("images", [{}])[0].get("recommended_filename", "")
        if image_filename:
            image_path = output_dir / image_filename
            if image_path.exists():
                attachments.append(image_path)

    total_size = sum(f.stat().st_size for f in attachments if f.exists())
    if total_size <= MAX_EMAIL_ATTACHMENT_BYTES:
        return attachments

    zip_path = output_dir / f"thekey-content-packet-{output_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in attachments:
            if f.exists():
                zf.write(f, arcname=f.name)
    return [zip_path]


def _final_recipients() -> List[str]:
    override = get_env_var("REVIEW_FINAL_EMAIL_TO", "").strip()
    if override:
        return [a.strip() for a in override.split(",") if a.strip()]
    fallback = get_env_var("EMAIL_TO", "").strip()
    return [fallback] if fallback else []


def _all_markets_processed(state: Dict) -> bool:
    for market_state in state.get("markets", {}).values():
        if market_state.get("status") not in ("approved", "rewritten", "auto_approved_due_to_deadline"):
            return False
    return True


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _hours_since(value: Optional[str]) -> Optional[float]:
    timestamp = _parse_timestamp(value)
    if not timestamp:
        return None
    return (datetime.now() - timestamp).total_seconds() / 3600.0


def _load_feedback(feedback_path: str) -> Optional[Dict]:
    try:
        return load_json(Path(feedback_path))
    except Exception:
        return None


async def _process_market(run_id: str, market: str, state: Dict) -> None:
    market_state = state.get("markets", {}).get(market, {})
    status = market_state.get("status")
    decision = market_state.get("decision")

    if status != "submitted":
        return

    output_dir = _run_output_dir(run_id)
    now = datetime.now().isoformat()

    if decision == "approve":
        update_market_state(run_id, market, {
            "status": "approved",
            "processed_at": now,
            "last_error": None,
        })
        return

    if decision != "revise":
        update_market_state(run_id, market, {
            "status": "approved",
            "processed_at": now,
            "last_error": "Unknown decision; auto-approved"
        })
        return

    feedback = _load_feedback(market_state.get("feedback_path", ""))
    if not feedback:
        update_market_state(run_id, market, {
            "status": "approved",
            "processed_at": now,
            "last_error": "Feedback missing; auto-approved"
        })
        return

    notes = feedback.get("notes", "")
    if not notes:
        update_market_state(run_id, market, {
            "status": "approved",
            "processed_at": now,
            "last_error": "Empty feedback; auto-approved"
        })
        return

    try:
        html = _load_html(output_dir, market)
        before_html = html
        metadata = _load_metadata(output_dir, market)
        primary_keyword = metadata.get("primary_keyword", "")

        agent = ReviewRewriteAgent()
        rewritten = await agent.rewrite(market, html, notes, primary_keyword)

        link_report = None
        try:
            qa_agent = EditorQAAgent()
            rewritten, link_report = await qa_agent._validate_and_fix_links(
                rewritten,
                market_state.get("market_name", market)
            )
        except Exception as e:
            logger.warning(f"Link validation failed for {market}: {e}")

        selection = market_state.get("image_selection") or {}
        if selection:
            rewritten = _apply_image_selection_html(rewritten, selection)

        validator = HTMLValidator()
        validation = validator.validate(
            rewritten,
            market,
            week_theme=metadata.get("week_theme")
        )
        if not validation.get("pass"):
            logger.warning(
                f"Review rewrite failed HTML validation for {market}: {validation.get('errors')}"
            )

        diff_path = None
        try:
            _diff_dir(run_id).mkdir(parents=True, exist_ok=True)
            _snapshot_path(run_id, market).write_text(before_html, encoding="utf-8")
            diff_html = _build_inline_diff(before_html, rewritten)
            diff_path = _diff_path(run_id, market)
            diff_path.write_text(diff_html, encoding="utf-8")
        except Exception as e:
            logger.warning(f"Failed to generate diff for {market}: {e}")

        _save_html(output_dir, market, rewritten)
        metadata["word_count"] = _count_words(rewritten)
        metadata["last_review_update"] = now
        if link_report is not None:
            metadata["link_validation"] = link_report
        if diff_path is not None:
            metadata["diff_path"] = str(diff_path)
        _save_metadata(output_dir, market, metadata)

        new_attempts = market_state.get("rewrite_attempts", 0) + 1
        update_market_state(run_id, market, {
            "status": "rewritten",
            "processed_at": now,
            "last_error": None,
            "rewrite_attempts": new_attempts,
            "validation": validation,
            "link_validation": link_report,
            "diff_path": str(diff_path) if diff_path else None,
        })

        gm_email = (market_state.get("gm_email") or "").strip()
        review_url = market_state.get("review_url", "")
        notice_attempt = market_state.get("rewrite_notice_attempt", 0)
        if gm_email and review_url and notice_attempt < new_attempts:
            email_sender = EmailSender()
            state = load_review_state(run_id) or {}
            subject, body = email_sender.build_review_rewrite_ready_email(
                run_id,
                market_state.get("market_name", market),
                metadata.get("title", ""),
                review_url,
                deadline_at=state.get("deadline_at"),
                rewrite_round=new_attempts,
            )
            reply_to = get_env_var("REVIEW_REPLY_TO", "").strip() or None
            sent = await email_sender.send_email(
                subject=subject,
                body=body,
                to_addresses=[gm_email],
                reply_to=reply_to,
            )
            update_market_state(run_id, market, {
                "rewrite_notice_attempt": new_attempts,
                "rewrite_notice_sent_at": now if sent else None,
                "rewrite_notice_status": "sent" if sent else "failed",
                "rewrite_notice_error": None if sent else "send_failed",
            })
    except Exception as e:
        update_market_state(run_id, market, {
            "last_error": str(e),
        })
        logger.error(f"Failed to rewrite {market} for run {run_id}: {e}")


async def _finalize_run(run_id: str, state: Dict) -> None:
    output_dir = _run_output_dir(run_id)
    markets = list(state.get("markets", {}).keys())
    if not markets:
        return

    if not _all_markets_processed(state):
        now = datetime.now().isoformat()
        for market, market_state in state.get("markets", {}).items():
            if market_state.get("status") in ("approved", "rewritten", "auto_approved_due_to_deadline"):
                continue
            market_state["status"] = "auto_approved_due_to_deadline"
            market_state["processed_at"] = now
            market_state["last_error"] = "Auto-approved due to review deadline"
        save_review_state(run_id, state)

    email_sender = EmailSender()
    articles = _load_articles_for_email(output_dir, markets)
    subject, body = email_sender.build_success_email(run_id, articles, output_dir)

    attachments = _build_attachments(output_dir, markets)

    recipients = _final_recipients()
    state["final_email_recipients"] = recipients
    if not recipients:
        state["final_email_status"] = "skipped_no_recipients"
        logger.warning("No final recipients configured; skipping final email send.")
    else:
        sent_at = datetime.now().isoformat()
        result = await email_sender.send_email(
            subject=subject,
            body=body,
            to_addresses=recipients,
            attachments=attachments,
        )
        state["final_email_sent_at"] = sent_at
        state["final_email_status"] = "sent" if result else "failed"
        state["final_email_error"] = None if result else "send_failed"

    state["status"] = "finalized"
    state["finalized_at"] = datetime.now().isoformat()
    save_review_state(run_id, state)


async def _send_reminders(run_id: str, state: Dict) -> None:
    output_dir = _run_output_dir(run_id)
    email_sender = EmailSender()
    reply_to = get_env_var("REVIEW_REPLY_TO", "").strip() or None

    now = datetime.now()
    if now.weekday() >= 5:
        return

    reminder_at, deadline_at = get_review_schedule(state)
    if now < reminder_at:
        return
    if now >= deadline_at:
        return

    for market, market_state in state.get("markets", {}).items():
        if market_state.get("status") != "pending":
            continue
        gm_email = market_state.get("gm_email", "").strip()
        if not gm_email:
            continue

        last_reminder = market_state.get("last_reminder_at")
        last_reminder_dt = _parse_timestamp(last_reminder) if last_reminder else None
        if last_reminder_dt and last_reminder_dt >= reminder_at:
            continue

        metadata = _load_metadata(output_dir, market)
        subject, body = email_sender.build_review_reminder_email(
            run_id,
            market_state.get("market_name", market),
            metadata.get("title", ""),
            market_state.get("review_url", ""),
            deadline_at=deadline_at.isoformat(),
            reminder_count=market_state.get("reminder_count", 0) + 1
        )

        await email_sender.send_email(
            subject=subject,
            body=body,
            to_addresses=[gm_email],
            reply_to=reply_to
        )

        update_market_state(run_id, market, {
            "last_reminder_at": datetime.now().isoformat(),
            "reminder_count": market_state.get("reminder_count", 0) + 1
        })


async def process_run(run_id: str) -> None:
    state = load_review_state(run_id)
    if not state:
        return

    normalize_review_deadline(state)

    if state.get("status") == "finalized" and not state.get("admin_override"):
        return

    for market in list(state.get("markets", {}).keys()):
        await _process_market(run_id, market, state)

    state = load_review_state(run_id)
    if not state:
        return

    await _send_reminders(run_id, state)

    state = load_review_state(run_id)
    if not state:
        return

    if state.get("admin_override"):
        return

    if _all_markets_processed(state) or review_deadline_passed(state):
        await _finalize_run(run_id, state)


async def process_pending(run_id: Optional[str] = None) -> None:
    if run_id:
        await process_run(run_id)
        return

    runs = list_review_runs()
    for run_id in runs.keys():
        await process_run(run_id)


def main() -> int:
    run_id = get_env_var("REVIEW_RUN_ID", "").strip() or None
    asyncio.run(process_pending(run_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
