"""Integration smoke test with mocked web fetch and stubbed SMTP."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.researcher import ResearcherAgent
from agents.writer import WriterAgent
from agents.editor_qa import EditorQAAgent
from agents.dispatcher import DispatcherAgent
from orchestrator.coordinator import run_weekly


@pytest.mark.asyncio
class TestIntegrationSmokeTest:
    """Integration smoke test for full workflow."""

    async def test_full_workflow_smoke(self, tmp_path):
        """Test end-to-end workflow with mocked dependencies."""
        # Mock OpenAI API
        mock_openai_instance = AsyncMock()
        mock_openai_instance.generate = AsyncMock(return_value=json.dumps({
            "title": "Test Article",
            "meta_description": "Test description",
            "internal_links": ["https://thekey.ca/locations/canada/test"],
            "sections": [],
            "faqs": []
        }))
        mock_openai_instance.generate_embedding = AsyncMock(return_value=[0.1] * 1536)

        with patch('agents.base_agent.OpenAIClient', return_value=mock_openai_instance):
            # Mock SMTP
            with patch('tools.email_sender.EmailSender') as mock_smtp:
                mock_smtp_instance = AsyncMock()
                mock_smtp_instance.__aenter__ = AsyncMock(return_value=mock_smtp_instance)
                mock_smtp_instance.__aexit__ = AsyncMock(return_value=None)
                mock_smtp_instance.login = AsyncMock(return_value=None)
                mock_smtp_instance.send_email = AsyncMock(return_value=None)
                mock_smtp.return_value = mock_smtp_instance

                # Mock file manager to use temp path
                with patch('utils.file_manager.create_output_directory', return_value=tmp_path):
                    # Run full workflow
                    result = await run_weekly()

                    # Assertions
                    assert result is not None
                    assert "run_id" in result
                    assert "status" in result
                    assert "start_time" in result
                    assert "end_time" in result

                    # Check that output files were created
                    output_files = list(tmp_path.glob("*"))
                    assert len(output_files) > 0

                    # Check for HTML files
                    html_files = list(tmp_path.glob("*.html"))
                    assert len(html_files) >= 1

                    # Check for JSON files
                    json_files = list(tmp_path.glob("*.json"))
                    assert len(json_files) >= 1

                    # Check that SMTP was called
                    mock_smtp_instance.login.assert_called_once()
                    mock_smtp_instance.send_email.assert_called_once()

    async def test_researcher_agent_creates_valid_pack(self):
        """Test that ResearcherAgent creates valid research pack."""
        agent = ResearcherAgent()
        market_config = {
            "name": "Test City",
            "province": "Test Province",
            "location_url": "https://test.ca",
            "primary_keyword_pool": ["test home care"],
            "secondary_keyword_pool": ["test dementia", "test companion"],
            "local_domains": ["test.ca"],
            "healthcare_context": "Test healthcare system"
        }

        result = await agent.run("test", market_config)

        assert result["market"] == "test"
        assert result["market_name"] == "Test City"
        assert result["province"] == "Test Province"
        assert result["local_hook"] in result
        assert result["medical_sources"] in result
        assert result["local_resources"] in result
        assert result["keywords"] in result
        assert result["faqs"] in result

    async def test_writer_agent_generates_html(self, tmp_path):
        """Test that WriterAgent generates valid HTML."""
        agent = WriterAgent()

        tone_profile = {
            "phrasing_norms": ["your loved one"],
            "cadence": "warm",
            "cta_vibe": "helpful"
        }

        research_pack = {
            "market": "test",
            "market_name": "Test City",
            "province": "Test Province",
            "location_url": "https://test.ca",
            "local_hook": {
                "title": "Test Hook",
                "summary": "Test summary"
            },
            "medical_sources": [],
            "local_resources": [],
            "keywords": {
                "primary": "test home care",
                "secondary": ["test dementia"]
            },
            "faqs": []
        }

        markets_config = {
            "markets": {
                "test": {
                    "name": "Test City",
                    "province": "Test Province",
                    "location_url": "https://test.ca",
                    "healthcare_context": "Test healthcare"
                }
            },
            "service_pages": {
                "dementia": {"url": "https://test.ca/dementia"},
                "hospital_to_home": {"url": "https://test.ca/hospital"}
            },
            "cta_base_url": "https://thekey.ca/getting-started",
            "lifeguard_url": "https://thekey.ca/lifeguard"
        }

        # Mock OpenAI
        with patch('agents.base_agent.OpenAIClient') as mock_openai:
            mock_response = {
                "title": "Test Article: Test Home Care",
                "meta_description": "Test meta description",
                "internal_links": ["https://test.ca"],
                "sections": [],
                "faqs": []
            }

            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = json.dumps(mock_response)

            mock_completion_response = MagicMock()
            mock_completion_response.choices = [mock_completion]
            mock_completion_response.usage = MagicMock(total_tokens=300)

            mock_client = AsyncMock()
            mock_client.chat.completions.create = AsyncMock(return_value=mock_completion_response)
            mock_openai.return_value = mock_client

            result = await agent.run(tone_profile, research_pack, markets_config)

            assert result["market"] == "test"
            assert "html_content" in result
            assert "metadata" in result
            assert "<div class=\"blog-content-module\"" in result["html_content"]
            assert "Test Article" in result["html_content"]

    async def test_editor_qa_agent_validates_articles(self, tmp_path):
        """Test that EditorQAAgent validates articles."""
        agent = EditorQAAgent()

        drafts = [
            {
                "market": "test1",
                "market_name": "Test City 1",
                "title": "Test 1",
                "primary_keyword": "test 1",
                "html_content": """<div class="blog-content-module">
<style>.test1-container { font-size: 16px; }</style>
<h1>Test Article 1</h1>
<p>Unique content for test 1.</p>
<p class="test1-disclaimer">This is for informational purposes only.</p>
</div>""",
                "metadata": {}
            },
            {
                "market": "test2",
                "market_name": "Test City 2",
                "title": "Test 2",
                "primary_keyword": "test 2",
                "html_content": """<div class="blog-content-module">
<style>.test2-container { font-size: 16px; }</style>
<h1>Test Article 2</h1>
<p>Unique content for test 2.</p>
<p class="test2-disclaimer">This is for informational purposes only.</p>
</div>""",
                "metadata": {}
            }
        ]

        # Mock embedding
            with patch('agents.base_agent.OpenAIClient') as mock_openai:
            mock_client = AsyncMock()
            mock_embedding = MagicMock()
            mock_embedding.embedding = [0.1] * 1536
            mock_embedding_response = MagicMock()
            mock_embedding_response.data = [mock_embedding]
            mock_embedding_response.usage = MagicMock(total_tokens=100)
            mock_client.embeddings.create = AsyncMock(return_value=mock_embedding_response)
            mock_openai.return_value = mock_client

            result = await agent.run(drafts)

            assert result is not None
            assert len(result) == 2
            final_articles, editor_report, uniqueness_report = result

            assert editor_report is not None
            assert uniqueness_report is not None
            assert "status" in uniqueness_report

    async def test_dispatcher_agent_saves_files(self, tmp_path):
        """Test that DispatcherAgent saves files correctly."""
        agent = DispatcherAgent()

        articles = [
            {
                "market": "test",
                "market_name": "Test City",
                "title": "Test Article",
                "primary_keyword": "test",
                "html_content": """<div class="blog-content-module">
<style>.test { font-size: 16px; }</style>
<h1>Test</h1>
</div>""",
                "metadata": {"word_count": 100},
                "html_filename": "test.html",
                "json_filename": "test.json"
            }
        ]

        run_summary = {
            "run_id": "2026-01-06",
            "status": "completed",
            "output_dir": str(tmp_path),
            "articles": articles,
            "editor_report": {},
            "uniqueness_report": {"status": "passed"}
        }

        # Mock SMTP
        with patch('tools.email_sender.EmailSender') as mock_smtp:
            mock_smtp_instance = AsyncMock()
            mock_smtp_instance.__aenter__ = AsyncMock(return_value=mock_smtp_instance)
            mock_smtp_instance.__aexit__ = AsyncMock(return_value=None)
            mock_smtp_instance.login = AsyncMock(return_value=None)
            mock_smtp_instance.send_email = AsyncMock(return_value=None)
            mock_smtp.return_value = mock_smtp_instance

            result = await agent.dispatch(articles, run_summary)

            assert result is True

            # Check files were created
            assert (tmp_path / "test.html").exists()
            assert (tmp_path / "test.json").exists()
            assert (tmp_path / "run_summary.json").exists()

            # Check SMTP was called
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.send_email.assert_called_once()


    async def test_researcher_agent_creates_valid_pack(self, tmp_path):
        """Test that ResearcherAgent creates valid research pack."""
        agent = ResearcherAgent()
        market_config = {
            "name": "Test City",
            "province": "Test Province",
            "location_url": "https://test.ca",
            "primary_keyword_pool": ["test home care"],
            "secondary_keyword_pool": ["test dementia", "test companion"],
            "local_domains": ["test.ca"],
            "healthcare_context": "Test healthcare system"
        }

        result = await agent.run("test", market_config)

        assert result["market"] == "test"
        assert result["market_name"] == "Test City"
        assert result["province"] == "Test Province"
        assert "local_hook" in result
        assert "medical_sources" in result
        assert "local_resources" in result
        assert "keywords" in result
        assert "faqs" in result

    async def test_writer_agent_generates_html(self, tmp_path):
        """Test that WriterAgent generates valid HTML."""
        agent = WriterAgent()

        tone_profile = {
            "phrasing_norms": ["your loved one"],
            "cadence": "warm",
            "cta_vibe": "helpful"
        }

        research_pack = {
            "market": "test",
            "market_name": "Test City",
            "province": "Test Province",
            "location_url": "https://test.ca",
            "local_hook": {
                "title": "Test Hook",
                "summary": "Test summary"
            },
            "medical_sources": [],
            "local_resources": [],
            "keywords": {
                "primary": "test home care",
                "secondary": ["test dementia"]
            },
            "faqs": []
        }

        markets_config = {
            "markets": {
                "test": {
                    "name": "Test City",
                    "province": "Test Province",
                    "location_url": "https://test.ca",
                    "healthcare_context": "Test healthcare"
                }
            },
            "service_pages": {
                "dementia": {"url": "https://test.ca/dementia"},
                "hospital_to_home": {"url": "https://test.ca/hospital"}
            },
            "cta_base_url": "https://thekey.ca/getting-started",
            "lifeguard_url": "https://thekey.ca/lifeguard"
        }

        # Mock OpenAI
            with patch('agents.base_agent.OpenAIClient') as mock_openai:
            mock_client = AsyncMock()
            mock_response = {
                "title": "Test Article: Test Home Care",
                "meta_description": "Test meta description",
                "internal_links": ["https://test.ca"],
                "sections": [],
                "faqs": []
            }

            mock_completion = MagicMock()
            mock_completion.choices = [MagicMock()]
            mock_completion.choices[0].message.content = json.dumps(mock_response)

            mock_completion_response = MagicMock()
            mock_completion_response.choices = [mock_completion]
            mock_completion_response.usage = MagicMock(total_tokens=300)

            mock_client.chat.completions.create = AsyncMock(return_value=mock_completion_response)
            mock_openai.return_value = mock_client

            result = await agent.run(tone_profile, research_pack, markets_config)

            assert result["market"] == "test"
            assert "html_content" in result
            assert "metadata" in result
            assert "<div class=\"blog-content-module\"" in result["html_content"]
            assert "Test Article" in result["html_content"]

    async def test_editor_qa_agent_validates(self, tmp_path):
        """Test that EditorQAAgent validates articles."""
        agent = EditorQAAgent()

        drafts = [
            {
                "market": "test1",
                "market_name": "Test City 1",
                "title": "Test 1",
                "primary_keyword": "test 1",
                "html_content": """<div class="blog-content-module">
<style>.test1-container { font-size:16px; }</style>
<h1>Test Article 1</h1>
</div>""",
                "metadata": {}
            }
        ]

        # Mock embedding
            with patch('agents.base_agent.OpenAIClient') as mock_openai:
            mock_client = AsyncMock()
            mock_embedding = MagicMock()
            mock_embedding.embedding = [0.1] * 1536
            mock_embedding_response = MagicMock()
            mock_embedding_response.data = [mock_embedding]
            mock_embedding_response.usage = MagicMock(total_tokens=100)

            mock_client.embeddings.create = AsyncMock(return_value=mock_embedding_response)
            mock_openai.return_value = mock_client

            result = await agent.run(drafts)

            assert result is not None
            assert len(result) == 2
            final_articles, editor_report, uniqueness_report = result

            assert editor_report is not None
            assert uniqueness_report is not None
            assert "status" in uniqueness_report

    async def test_dispatcher_agent_saves_files(self, tmp_path):
        """Test that DispatcherAgent saves files correctly."""
        agent = DispatcherAgent()

        articles = [
            {
                "market": "test",
                "market_name": "Test City",
                "title": "Test Article",
                "primary_keyword": "test",
                "html_content": """<div class="blog-content-module">
<style>.test { font-size:16px; }</style>
<h1>Test</h1>
</div>""",
                "metadata": {"word_count": 100},
                "html_filename": "test.html",
                "json_filename": "test.json"
            }
        ]

        run_summary = {
            "run_id": "2026-01-06",
            "status": "completed",
            "output_dir": str(tmp_path),
            "articles": articles,
            "editor_report": {},
            "uniqueness_report": {"status": "passed"}
        }

        # Mock SMTP
        with patch('aiosmtplib.SMTP') as mock_smtp:
            mock_smtp_instance = AsyncMock()
            mock_smtp_instance.__aenter__ = AsyncMock(return_value=mock_smtp_instance)
            mock_smtp_instance.__aexit__ = AsyncMock(return_value=None)
            mock_smtp_instance.login = AsyncMock(return_value=None)
            mock_smtp_instance.send_message = AsyncMock(return_value=None)
            mock_smtp.return_value = mock_smtp_instance

            result = await agent.dispatch(articles, run_summary)

            assert result is True

            # Check files were created
            assert (tmp_path / "test.html").exists()
            assert (tmp_path / "test.json").exists()
            assert (tmp_path / "run_summary.json").exists()

            # Check SMTP was called
            mock_smtp_instance.login.assert_called_once()
            mock_smtp_instance.send_message.assert_called_once()
