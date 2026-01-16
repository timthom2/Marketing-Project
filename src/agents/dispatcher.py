"""Dispatcher Agent: saves outputs and optionally sends email."""
from __future__ import annotations

import asyncio
import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

import aiohttp
from bs4 import BeautifulSoup

from agents.base_agent import BaseAgent
from tools.email_sender import EmailSender
from utils.file_manager import save_json
from utils.logger import get_logger

logger = get_logger(__name__)


class DispatcherAgent(BaseAgent):
    """Agent responsible for file organization and email delivery."""

    MAX_EMAIL_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25MB

    def __init__(self):
        super().__init__()
        self.email_sender = EmailSender()

    async def run(self, articles: List[Dict], run_summary: Dict) -> bool:
        """
        Required BaseAgent interface.
        Delegates to dispatch() so this class is not abstract.
        """
        return await self.dispatch(articles=articles, run_summary=run_summary)

    async def dispatch(self, articles: List[Dict], run_summary: Dict, send_email: bool = True) -> bool:
        """
        Save all output artifacts to disk and send email (if configured).
        Returns True if files saved successfully AND email sent (when enabled).
        Returns True if files saved successfully AND email is intentionally skipped.
        Returns False only if saving fails or email was enabled but sending failed.
        """
        self.log_info("Starting dispatcher...")

        # --- 1) Create output directory ---
        output_dir = Path(run_summary["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)

        # --- 2) Write article files ---
        try:
            for article in articles:
                # Save HTML (body starts below subheadline)
                html_filename = article.get("html_filename") or f"{article['market']}.html"
                html_path = output_dir / html_filename
                body_html = self._strip_to_body_html(article.get("html_content", ""))
                html_path.write_text(body_html, encoding="utf-8")
                article["html_filename"] = html_filename  # normalize

            # Save run summary
            summary_path = output_dir / "run_summary.json"
            save_json(run_summary, summary_path)

        except Exception as e:
            self.log_error(f"Failed to save output files: {e}")
            return False

        # --- 3) Download Pexels images for attachments ---
        await self._download_article_images(output_dir, articles)

        # --- 4) Prepare attachments (zip if needed) ---
        zip_path: Optional[Path] = None
        attachments = self._get_attachment_files(output_dir, articles)

        total_size = 0
        for f in attachments:
            if f.exists():
                total_size += f.stat().st_size

        if total_size > self.MAX_EMAIL_ATTACHMENT_BYTES:
            zip_path = output_dir / f"thekey-content-packet-{run_summary.get('run_id', 'run')}.zip"
            self.log_info(f"Attachments exceed 25MB; creating ZIP: {zip_path}")

            try:
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for f in attachments:
                        if f.exists():
                            zf.write(f, arcname=f.name)
                attachments = [zip_path]
            except Exception as e:
                self.log_error(f"Failed to create ZIP archive: {e}")
                # Still proceed without email, but files are already saved
                # Return True because the primary deliverable (files) exists.
                return True

        # --- 5) Decide whether to send email ---
        send_mode = os.getenv("EMAIL_SEND_MODE", "send").strip().lower()
        smtp_host = os.getenv("SMTP_HOST", "").strip()
        email_to = os.getenv("EMAIL_TO", "").strip()

        if not send_email:
            self.log_info("Review mode active → Skipping email send (files saved).")
            return True

        if send_mode in ("off", "disabled", "no", "false"):
            self.log_info("EMAIL_SEND_MODE=off → Skipping email send (files saved).")
            return True

        if not smtp_host or not email_to:
            self.log_warning("SMTP_HOST or EMAIL_TO not set → Skipping email send (files saved).")
            return True

        # --- 6) Build and send the email ---
        try:
            # If your EmailSender supports different templates for manual review, use that.
            # Otherwise, build_success_email should still work.
            subject, body = self.email_sender.build_success_email(
                run_summary.get("run_id", ""),
                articles,
                output_dir
            )

            email_sent = await self.email_sender.send_email(
                subject=subject,
                body=body,
                attachments=attachments
            )

            if email_sent:
                self.log_info("Email delivered successfully.")
                return True

            self.log_error("Email send failed (files saved).")
            return False

        except Exception as e:
            self.log_error(f"Unexpected email send error: {e} (files saved).")
            return False

    def _get_attachment_files(self, output_dir: Path, articles: List[Dict]) -> List[Path]:
        """
        Default attachments: each article's HTML + image.
        """
        files: List[Path] = []

        for article in articles:
            html_fn = article.get("html_filename")
            image_fn = (
                article.get("image_filename")
                or (article.get("metadata", {}).get("images") or [{}])[0].get("recommended_filename")
            )
            if html_fn:
                files.append(output_dir / html_fn)
            if image_fn:
                image_path = output_dir / image_fn
                if image_path.exists():
                    files.append(image_path)

        # De-duplicate while preserving order
        seen = set()
        unique_files: List[Path] = []
        for f in files:
            if str(f) not in seen:
                unique_files.append(f)
                seen.add(str(f))

        return unique_files

    def _strip_to_body_html(self, html_content: str) -> str:
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

    async def _download_article_images(self, output_dir: Path, articles: List[Dict]) -> None:
        """Download Pexels hero images to output directory for email attachments."""
        image_tasks = []

        async def download_one(session: aiohttp.ClientSession, url: str, path: Path) -> None:
            if path.exists():
                return
            try:
                async with session.get(url) as response:
                    if response.status >= 400:
                        logger.warning(f"Image download failed ({response.status}): {url}")
                        return
                    data = await response.read()
                path.write_bytes(data)
            except Exception as exc:
                logger.warning(f"Image download error for {url}: {exc}")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            for article in articles:
                image_info = (article.get("metadata", {}).get("images") or [{}])[0]
                image_url = image_info.get("url") or image_info.get("url_large") or ""
                image_fn = article.get("image_filename") or image_info.get("recommended_filename")
                if not image_url or not image_fn:
                    continue
                if "pexels.com" not in image_url:
                    continue
                image_path = output_dir / image_fn
                image_tasks.append(download_one(session, image_url, image_path))

            if image_tasks:
                await asyncio.gather(*image_tasks)
