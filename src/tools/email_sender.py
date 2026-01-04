"""SMTP email sender (provider-agnostic)."""
import mimetypes
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path
from typing import List, Dict, Optional, Any

import aiosmtplib
from email_validator import validate_email, EmailNotValidError

from utils.logger import get_logger
from utils.config_loader import get_email_config

logger = get_logger(__name__)


class EmailSender:
    """SMTP email sender supporting any provider."""

    def __init__(self):
        self.config = get_email_config()

    async def send_email(
        self,
        subject: str,
        body: str,
        to_addresses: Optional[List[str]] = None,
        attachments: Optional[List[Path]] = None,
        html: bool = False
    ) -> bool:
        """Send email via SMTP.

        Args:
            subject: Email subject
            body: Email body (text or HTML)
            to_addresses: List of recipient addresses (default from config)
            attachments: List of file paths to attach
            html: Whether body is HTML

        Returns:
            bool: True if sent successfully
        """
        if to_addresses is None:
            to_addresses = [self.config["to"]]
        
        # Validate email addresses
        for addr in to_addresses + [self.config["from"]]:
            try:
                validate_email(addr)
            except EmailNotValidError as e:
                logger.error(f"Invalid email address {addr}: {e}")
                return False
        
        # Create message
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = formataddr(("TheKey Content Bot", self.config["from"]))
        message["To"] = ", ".join(to_addresses)
        message["Date"] = formatdate(localtime=True)
        
        if html:
            message.add_alternative(body, subtype="html")
        else:
            message.set_content(body)
        
        # Attach files
        if attachments:
            for filepath in attachments:
                if not filepath.exists():
                    logger.warning(f"Attachment not found: {filepath}")
                    continue
                
                mime_type, _ = mimetypes.guess_type(str(filepath))
                mime_type = mime_type or "application/octet-stream"
                
                with open(filepath, "rb") as f:
                    data = f.read()
                
                message.add_attachment(
                    data,
                    maintype=mime_type.split("/")[0],
                    subtype=mime_type.split("/")[1] if "/" in mime_type else "",
                    filename=filepath.name
                )
        
        # Send via SMTP
        try:
            async with aiosmtplib.SMTP(
                hostname=self.config["host"],
                port=self.config["port"],
                use_tls=True
            ) as smtp:
                await smtp.login(self.config["user"], self.config["pass"])
                await smtp.send_message(message)
            
            logger.info(f"Email sent successfully to {', '.join(to_addresses)}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email: {e}", exc_info=True)
            return False

    def build_success_email(
        self,
        run_date: str,
        articles: List[Dict],
        output_dir: Path
    ) -> tuple:
        """Build success email content.

        Args:
            run_date: Run date (YYYY-MM-DD)
            articles: List of article metadata
            output_dir: Output directory path

        Returns:
            tuple: (subject, body)
        """
        subject = f"✅ Weekly SEO Content Packet — {run_date}"
        
        # Build index table
        table_lines = [
            "| Market | Title | Primary Keyword | Image | Files |",
            "|--------|-------|-----------------|-------|-------|"
        ]
        
        for article in articles:
            table_lines.append(
                f"| {article['market']} | {article['title']} | "
                f"{article['primary_keyword']} | {article.get('image_filename', 'N/A')} | "
                f"{article['html_filename']}, {article['json_filename']} |"
            )
        
        body = f"""Weekly SEO Content Generation Complete ✅

Run Date: {run_date}
Articles Generated: {len(articles)}

## Content Summary

{chr(10).join(table_lines)}

## Output Directory

All files saved to: {output_dir}

## Next Steps

1. Review articles for accuracy
2. Upload images to Brightspot (replace placeholder URLs)
3. Publish articles to respective market pages

---

TheKey Canada SEO Content Bot
System-generated email - do not reply"""
        
        return subject, body

    def build_error_email(
        self,
        run_date: str,
        error_message: str,
        logs_path: Path
    ) -> tuple:
        """Build error email content.

        Args:
            run_date: Run date (YYYY-MM-DD)
            error_message: Error details
            logs_path: Path to log files

        Returns:
            tuple: (subject, body)
        """
        subject = f"❌ ERROR: TheKey Content Bot Failed — {run_date}"
        
        body = f"""Weekly SEO Content Generation Failed ❌

Run Date: {run_date}
Status: FAILED

## Error Details

{error_message}

## Logs

Check logs at: {logs_path}

## Recovery Steps

1. Review the error details above
2. Check the log file for full stack trace
3. Verify environment variables are set correctly
4. If the issue persists, contact the development team

---

TheKey Canada SEO Content Bot
System-generated email - do not reply"""
        
        return subject, body

    def build_similarity_alert_email(
        self,
        run_date: str,
        articles: List[Dict],
        similarity_report: Dict,
        output_dir: Path
    ) -> tuple:
        """Build similarity alert email (best-effort after max rewrites).

        Args:
            run_date: Run date (YYYY-MM-DD)
            articles: List of article metadata
            similarity_report: Similarity metrics
            output_dir: Output directory path

        Returns:
            tuple: (subject, body)
        """
        failing_markets = similarity_report.get("failing_markets", [])
        subject = f"⚠️ WEEKLY CONTENT PACKET (MANUAL REVIEW RECOMMENDED) — {run_date}"
        
        # Build index table
        table_lines = [
            "| Market | Title | Primary Keyword | Image | Files |",
            "|--------|-------|-----------------|-------|-------|"
        ]
        
        for article in articles:
            flag = " ⚠️" if article["market"] in failing_markets else ""
            table_lines.append(
                f"| {article['market']}{flag} | {article['title']} | "
                f"{article['primary_keyword']} | {article.get('image_filename', 'N/A')} | "
                f"{article['html_filename']}, {article['json_filename']} |"
            )
        
        # Build similarity score table
        sim_lines = [
            "## Similarity Score Summary",
            "",
            "| Market A | Market B | TF-IDF | Embedding | Status |",
            "|----------|----------|--------|------------|--------|"
        ]
        
        for pair in similarity_report.get("pairs", []):
            status = "✅ PASS" if pair["pass"] else "❌ FAIL"
            sim_lines.append(
                f"| {pair['market_a']} | {pair['market_b']} | "
                f"{pair['tfidf']:.3f} | {pair['embedding']:.3f} | {status} |"
            )
        
        body = f"""Weekly SEO Content Generation Complete (Manual Review Required) ⚠️

Run Date: {run_date}
Articles Generated: {len(articles)}

⚠️ MANUAL REVIEW RECOMMENDED FOR: {', '.join(failing_markets)}

The similarity gate failed after maximum rewrite attempts (3).
Articles have been generated with best-effort but may have duplicate content.

## Content Summary

{chr(10).join(table_lines)}

{chr(10).join(sim_lines)}

## Similarity Thresholds

- TF-IDF Cosine Similarity: > 0.25 = FAIL
- Embedding Similarity: > 0.82 = FAIL

## Output Directory

All files saved to: {output_dir}

## Next Steps

1. Review failing markets carefully for duplicate content
2. Manually rewrite sections as needed
3. Verify all articles are unique before publishing
4. Upload images to Brightspot (replace placeholder URLs)

---

TheKey Canada SEO Content Bot
System-generated email - do not reply"""
        
        return subject, body
