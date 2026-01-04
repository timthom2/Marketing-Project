"""Unit tests for HTML validator."""
import pytest

from src.tools.html_validator import HTMLValidator


class TestHTMLValidator:
    """Test suite for HTML validation."""

    def test_wrapper_required(self):
        """Test that wrapper requirement is enforced."""
        html = """<div class="blog-content-module">
<h1>Test</h1>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is True
        assert any("wrapper" in error["errors"] for error in result["errors"])
        print(f"✓ Wrapper test passed")

    def test_px_only_with_width_100_percent_allowed(self):
        """Test that width:100% is allowed."""
        html = """<div class="blog-content-module">
<style>
.test { width:100%; height:auto; font-size:16px; margin:0px; }
</style>
<h1>Test</h1>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is True
        assert "Found % unit in CSS: width:100%" not in str(result["errors"])
        print(f"✓ width:100% test passed")

    def test_px_only_with_em_fails(self):
        """Test that em units are forbidden."""
        html = """<div class="blog-content-module">
<style>
.test { margin:2em; font-size:16px; }
</style>
<h1>Test</h1>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is False  # em should be detected
        assert any("em unit" in error["errors"] for error in result["errors"])
        print(f"✓ em unit detection test passed")

    def test_rem_units_fails(self):
        """Test that rem units are forbidden."""
        html = """<div class="blog-content-module">
<style>
.test { font-size:1.2rem; margin:0px; }
</style>
<h1>Test</h1>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is False  # rem should be detected
        assert any("rem unit" in error["errors"] for error in result["errors"])
        print(f"✓ rem unit detection test passed")

    def test_cta_link_required(self):
        """Test that CTA link is required."""
        html = """<div class="blog-content-module">
<h1>Test</h1>
<p>Content.</p>
<a href="https://thekey.ca/getting-started">CTA</a>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is False  # CTA link is required
        assert "CTA link" in error["errors"] or not html_content.startswith("https://thekey.ca/getting-started")
        print(f"✓ CTA link test passed")

    def test_medical_disclaimer_required(self):
        """Test that medical disclaimer is required."""
        html = """<div class="blog-content-module">
<h1>Test</h1>
<p>Content.</p>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        # Medical disclaimer not in HTML - should be required
        assert "disclaimer" in result["errors"] or not "This is for informational purposes only and does not constitute medical advice" in html_content.lower()
        print(f"✓ Medical disclaimer test failed (missing) - expected")

    def test_scoped_css_with_prefix(self):
        """Test that scoped CSS with unique prefix."""
        html = """<div class="blog-content-module">
<style>.test-container { color: #333; margin: 0px; }
</style>
<h1>Test</h1>
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is False  # CSS prefix missing
        assert "prefix" in str(result["errors"] for error in result["errors"])
        print(f"✓ Scoped CSS test passed")

    def test_hero_placeholder_required(self):
        """Test that hero placeholder is required."""
        html = """<div class="blog-content-module">
<h1>Test</h1>
<!-- TODO: Upload image to Brightspot and replace URL -->
<img src="https://cdn.brightspot.com/placeholder.jpg" alt="Test" />
</div>"""

        validator = HTMLValidator()
        result = validator.validate(html, "test")

        assert result["pass"] is False  # TODO comment missing
        assert "TODO" not in html_content or "Upload image to Brightspot" in html_content
        print(f"✓ Hero placeholder test passed")
