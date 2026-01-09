"""Dispatcher Agent: saves outputs and optionally sends email."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

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
                # Save HTML
                html_filename = article.get("html_filename") or f"{article['market']}.html"
                html_path = output_dir / html_filename
                html_path.write_text(article["html_content"], encoding="utf-8")
                article["html_filename"] = html_filename  # normalize

                # Save metadata JSON
                json_filename = article.get("json_filename") or f"{article['market']}.json"
                json_path = output_dir / json_filename
                save_json(article["metadata"], json_path)
                article["json_filename"] = json_filename  # normalize

            # Save run summary
            summary_path = output_dir / "run_summary.json"
            save_json(run_summary, summary_path)

        except Exception as e:
            self.log_error(f"Failed to save output files: {e}")
            return False

        # --- 3) Prepare attachments (zip if needed) ---
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

        # --- 4) Decide whether to send email ---
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

        # --- 5) Build and send the email ---
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
        Default attachments: each article's HTML + JSON, plus run_summary.json.
        """
        files: List[Path] = []

        for article in articles:
            html_fn = article.get("html_filename")
            json_fn = article.get("json_filename")
            if html_fn:
                files.append(output_dir / html_fn)
            if json_fn:
                files.append(output_dir / json_fn)

        # Always include run summary
        files.append(output_dir / "run_summary.json")

        # De-duplicate while preserving order
        seen = set()
        unique_files: List[Path] = []
        for f in files:
            if str(f) not in seen:
                unique_files.append(f)
                seen.add(str(f))

        return unique_files
