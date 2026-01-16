"""HTML validator for Brightspot compliance."""
import re
from pathlib import Path
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from utils.logger import get_logger
from utils.config_loader import load_config

logger = get_logger(__name__)


class HTMLValidator:
    """Validates Brightspot HTML requirements."""

    def __init__(self):
        self.config = load_config("brightspot_guide")
        self.brand_config = load_config("brand")
        self.rules_config = load_config("rules")

    def validate(self, html_content: str, market: str, week_theme: Optional[str] = None) -> Dict:
        """Validate HTML content against Brightspot requirements.

        Args:
            html_content: HTML content to validate
            market: Market name for CSS prefix check
            week_theme: Week theme for theme coverage checks

        Returns:
            Dict: Validation result with pass/fail status and errors
        """
        errors = []
        warnings = []

        # Check wrapper
        if not self._check_wrapper(html_content):
            errors.append("Missing or incorrect wrapper: <div class='blog-content-module'>")

        # Check px-only units
        px_errors = self._check_px_only(html_content)
        if px_errors:
            errors.extend(px_errors)

        # Check scoped CSS with unique prefix
        css_errors = self._check_scoped_css(html_content, market)
        if css_errors:
            errors.extend(css_errors)

        # Check CTA link
        if not self._check_cta_link(html_content, week_theme):
            errors.append(f"Missing or incorrect CTA link: {self.config['cta_link']}")

        # Check medical disclaimer
        if not self._check_disclaimer(html_content):
            errors.append("Missing medical disclaimer")

        # Check FAQ count
        faq_errors = self._check_faq_count(html_content)
        if faq_errors:
            errors.extend(faq_errors)

        # Check H1 with keyword
        if not self._check_h1_with_keyword(html_content):
            warnings.append("H1 may not include primary keyword naturally")

        # Check hero placeholder + TODO
        if not self._check_hero_placeholder(html_content):
            errors.append("Missing hero image placeholder or TODO comment")

        # Check theme coverage
        theme_errors = self._check_theme_coverage(html_content, week_theme)
        if theme_errors:
            errors.extend(theme_errors)

        # Check word count
        word_count_warnings = self._check_word_count(html_content)
        if word_count_warnings:
            warnings.extend(word_count_warnings)

        result = {
            "pass": len(errors) == 0,
            "errors": errors,
            "warnings": warnings,
            "market": market
        }

        return result

    def _check_wrapper(self, html_content: str) -> bool:
        """Check for correct Brightspot wrapper."""
        pattern = r'<div\s+class="blog-content-module'
        return bool(re.search(pattern, html_content))

    def _check_px_only(self, html_content: str) -> List[str]:
        """Check for non-px units while allowing % for width/height."""
        errors = []

        # Find CSS rules
        style_pattern = r'<style[^>]*>(.*?)</style>'
        style_matches = re.findall(style_pattern, html_content, re.DOTALL)

        for style_content in style_matches:
            css_lines = style_content.split(';')

            for line in css_lines:
                line = line.strip()
                if not line or line.startswith('/*') or line.startswith('*'):
                    continue

                # Skip selector lines (starting with . or #)
                if line.startswith('.') or line.startswith('#'):
                    continue

                # Skip color definitions with percentages
                if re.search(r'rgba?\(|hsla?\(', line):
                    continue

                # Allow % for width, max-width, min-width, height, max-height, min-height
                # Extract property and value
                if ':' in line:
                    prop = line.split(':', 1)[0].strip()
                    is_allowed_prop = prop in ['width', 'max-width', 'min-width', 'height', 'max-height', 'min-height']

                    # Extract value part
                    value_part = line.split(':', 1)[1]

                    if is_allowed_prop:
                        # For width/height properties, % is allowed, check only rem/em/vw/vh

                        if re.search(r'rem(?!\w)', value_part):
                            errors.append(f"Found rem unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'em(?!\w)', value_part):
                            errors.append(f"Found em unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'vw(?!\w)', value_part):
                            errors.append(f"Found vw unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'vh(?!\w)', value_part):
                            errors.append(f"Found vh unit in CSS: {line[:50]}...")
                            continue
                    else:
                        # For other properties, all units except px are forbidden (including %)
                        if re.search(r'rem(?!\w)', value_part):
                            errors.append(f"Found rem unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'em(?!\w)', value_part):
                            errors.append(f"Found em unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'vw(?!\w)', value_part):
                            errors.append(f"Found vw unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'vh(?!\w)', value_part):
                            errors.append(f"Found vh unit in CSS: {line[:50]}...")
                            continue

                        if re.search(r'%', value_part):
                            errors.append(f"Found % unit in CSS: {line[:50]}...")

        return errors

    def _check_scoped_css(self, html_content: str, market: str) -> List[str]:
        """Check for scoped CSS with unique prefix."""
        errors = []

        # Check for <style> tag
        if '<style>' not in html_content:
            return ["Missing scoped <style> tag"]

        style_pattern = r'<style[^>]*>(.*?)</style>'
        style_matches = re.findall(style_pattern, html_content, re.DOTALL)

        for style_content in style_matches:
            # Check for unique class prefix
            expected_prefix = f"bs-{market.lower()}-"

            # Find all CSS selectors
            selector_pattern = r'^([.#]?[\w-]+)'
            for line in style_content.split('\n'):
                line = line.strip()
                if not line or line.startswith('@') or line.startswith('/*') or line.startswith('*'):
                    continue

                match = re.match(selector_pattern, line)
                if match:
                    selector = match.group(1)
                    if not selector.startswith(expected_prefix):
                        errors.append(
                            f"CSS selector '{selector}' missing prefix '{expected_prefix}'"
                        )

        return errors

    def _check_cta_link(self, html_content: str, week_theme: Optional[str]) -> bool:
        """Check for required CTA link."""
        if self.config["cta_link"] in html_content:
            return True

        if not week_theme:
            return False

        theme_mapping = self.brand_config.get("theme_service_mapping", {})
        theme_data = theme_mapping.get(week_theme, {})
        theme_url = theme_data.get("url", "")
        if theme_url and theme_url in html_content:
            return True

        return False

    def _check_theme_coverage(self, html_content: str, week_theme: Optional[str]) -> List[str]:
        """Check that content meaningfully covers the assigned theme."""
        errors = []
        if not week_theme:
            return errors

        theme_rules = self.rules_config.get("theme_coverage", {})
        themes = theme_rules.get("themes", {})
        theme_config = themes.get(week_theme, {})
        terms = [t.lower() for t in theme_config.get("terms", []) if isinstance(t, str)]
        if not terms:
            return errors

        min_count = theme_config.get(
            "min_term_count",
            theme_rules.get("default_min_term_count", 2)
        )

        soup = BeautifulSoup(html_content, 'html.parser')
        for element in soup(['style', 'script', 'code']):
            element.decompose()
        text = soup.get_text(separator=' ').lower()

        term_count = sum(text.count(term) for term in terms)
        if term_count < min_count:
            errors.append(
                f"Theme coverage: expected at least {min_count} mentions of theme terms, found {term_count}"
            )

        return errors

    def _check_disclaimer(self, html_content: str) -> bool:
        """Check for medical disclaimer."""
        disclaimer_text = self.brand_config["medical_disclaimer"]["text"].strip().lower()
        return disclaimer_text in html_content.lower()

    def _check_faq_count(self, html_content: str) -> List[str]:
        """Check for exactly 5 FAQ questions."""
        errors = []

        soup = BeautifulSoup(html_content, 'html.parser')

        required_count = self.config["validation_rules"]["faq_count"]

        # Look for FAQ section
        faq_sections = soup.find_all(
            lambda tag: tag.name in ['h2', 'h3'] and
            'faq' in tag.get_text().lower()
        )

        if not faq_sections and required_count == 0:
            return errors

        if not faq_sections:
            errors.append("Could not find FAQ section")
            return errors

        # Count questions in each FAQ section
        total_questions = 0
        for section in faq_sections:
            # Find questions after the FAQ header - look for Q: or question marks
            questions = section.find_all_next(
                lambda tag: tag.name in ['strong', 'b', 'p', 'h3', 'h4'] and
                ('Q:' in tag.get_text() or tag.get_text().strip().endswith('?'))
            )
            # Stop at the next major section
            next_h2 = section.find_next('h2')
            if next_h2:
                questions = [q for q in questions if q.sourceline and section.sourceline and 
                           next_h2.sourceline and q.sourceline < next_h2.sourceline]

            total_questions += len([q for q in questions if '?' in q.get_text() or 'Q:' in q.get_text()])

        if required_count == 0:
            if total_questions == 0:
                errors.append("FAQ section present but no questions found")
            elif total_questions > 5:
                errors.append(
                    f"FAQ count: {total_questions} questions "
                    f"(max: 5)"
                )
            return errors

        if total_questions != required_count:
            errors.append(
                f"FAQ count: {total_questions} questions "
                f"(required: {required_count})"
            )

        return errors

    def _check_h1_with_keyword(self, html_content: str) -> bool:
        """Check if H1 includes a keyword naturally."""
        soup = BeautifulSoup(html_content, 'html.parser')
        h1 = soup.find('h1')

        if not h1:
            return False

        # Check for common keyword patterns
        h1_text = h1.get_text().lower()
        keyword_indicators = ['care', 'support', 'services', 'home', 'elderly', 'senior']

        return any(kw in h1_text for kw in keyword_indicators)

    def _check_hero_placeholder(self, html_content: str) -> bool:
        """Check for hero image placeholder and TODO comment."""
        has_placeholder = self.config["placeholder_image"] in html_content
        has_todo = self.config["image_todo"] in html_content

        return has_placeholder or has_todo

    def _check_word_count(self, html_content: str) -> List[str]:
        """Check word count is within range."""
        warnings = []
        soup = BeautifulSoup(html_content, 'html.parser')

        # Extract text content (excluding style, script, comments)
        for element in soup(['style', 'script', 'code']):
            element.decompose()

        text = soup.get_text(separator=' ')
        words = [w for w in text.split() if w.strip()]

        min_words = self.brand_config["content_guidelines"]["word_count"]["min"]
        max_words = self.brand_config["content_guidelines"]["word_count"]["max"]

        if len(words) < min_words:
            warnings.append(
                f"Word count: {len(words)} words (minimum: {min_words})"
            )

        if len(words) > max_words:
            warnings.append(
                f"Word count: {len(words)} words (maximum: {max_words})"
            )

        return warnings

    def validate_batch(self, articles: List[Dict]) -> Dict[str, Dict]:
        """Validate multiple articles.

        Args:
            articles: List of {market, html_content} dicts

        Returns:
            Dict: Mapping of market -> validation result
        """
        results = {}

        for article in articles:
            market = article.get("market")
            html_content = article.get("html_content")

            if not market or not html_content:
                results[market] = {
                    "pass": False,
                    "errors": ["Missing market or html_content"]
                }
                continue

            results[market] = self.validate(
                html_content,
                market,
                week_theme=article.get("week_theme")
            )

        return results
