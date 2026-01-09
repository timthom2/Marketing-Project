"""Rewrite agent for GM feedback."""
from __future__ import annotations

import re
from typing import Dict, List

from agents.base_agent import BaseAgent
from utils.config_loader import load_config


class ReviewRewriteAgent(BaseAgent):
    """Apply GM feedback to an existing article."""

    def __init__(self):
        super().__init__()
        self.model_config = load_config("model_routing")["models"]["editing_default"]
        self.markets_config = load_config("markets")

    async def run(self, *args, **kwargs) -> Dict:
        """BaseAgent interface wrapper for review rewrites."""
        html = await self.rewrite(*args, **kwargs)
        return {"html": html}

    async def rewrite(
        self,
        market: str,
        html_content: str,
        feedback: str,
        primary_keyword: str
    ) -> str:
        market_info = self.markets_config.get("markets", {}).get(market, {})
        market_name = market_info.get("name", market)
        healthcare_context = market_info.get("healthcare_context", "")
        must_include = market_info.get("must_include_entities", [])

        must_include_text = ", ".join(must_include[:6]) if must_include else "None specified"

        prompt = f"""You are an expert editor applying GM feedback to a Brightspot-ready HTML article.

MARKET: {market_name}
PRIMARY KEYWORD: {primary_keyword}
HEALTHCARE CONTEXT: {healthcare_context}
MUST INCLUDE ENTITIES: {must_include_text}

GM FEEDBACK (apply all items):
{feedback}

CURRENT ARTICLE (HTML):
{html_content}

REQUIREMENTS:
- Apply the GM feedback precisely and completely.
- Preserve the overall structure and layout (wrapper div, inline styles, CTA, disclaimer).
- Keep all internal links and CTA button links intact.
- Preserve all existing URLs exactly as they appear in the current article.
- Do not invent new links unless the URL already exists in the current article.
- Maintain Canadian spelling and local references.
- Do not remove required sections (Local Resources, CTA, disclaimer).
- Keep primary keyword in H1 and keep keyword density reasonable.
- Return ONLY the full HTML article, no commentary.
"""

        response = await self.openai.generate(
            model=self.model_config["model"],
            prompt=prompt,
            max_tokens=self.model_config["max_tokens"],
            temperature=self.model_config["temperature"]
        )

        return self._clean_html_response(response)

    def _clean_html_response(self, response: str) -> str:
        content = response.strip()

        if content.startswith("```html"):
            content = content[7:]
        elif content.startswith("```"):
            content = content[3:]

        if "```" in content:
            content = content.split("```")[0]

        lines = content.split("\n")
        clean_lines: List[str] = []
        for line in lines:
            if line.strip().startswith("###") or line.strip().lower().startswith("summary"):
                break
            clean_lines.append(line)

        content = "\n".join(clean_lines).strip()

        if not content.rstrip().endswith("</div>"):
            last_div = content.rfind("</div>")
            if last_div != -1:
                content = content[: last_div + 6]

        content = re.sub(r"<promise>.*?</promise>", "", content, flags=re.IGNORECASE | re.DOTALL)
        return content.strip()
