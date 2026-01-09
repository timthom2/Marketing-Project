# TheKey Canada SEO Content Bot

Production-grade multi-agent system for generating weekly SEO content for TheKey Canada's 8 markets.

## Overview

Automatically generates 8 Brightspot-ready HTML articles weekly (one per market) and emails them for review:
- Montreal, Toronto, Oakville, Winnipeg, Calgary, Vancouver, Surrey, Victoria

**Schedule:** Tuesdays at 8:00 AM America/Toronto
**Runtime:** Linode server (Ubuntu) via systemd timer

**Content Calendar:** 16-week rotation with holiday/trend awareness (Valentine's Day, Tax Season, Winter Safety, etc.)

**Writing Style:** Matches TheKey learning center (thekey.com) with Canadian English for thekey.ca

## Features

- ✅ **Multi-Agent Architecture**: Researcher, Writer, Editor/QA, Image Selector, Dispatcher agents
- ✅ **Intelligent Image Selection**: Vision-powered image matching via Pexels API
- ✅ **Brightspot Compliance**: Validated HTML with wrapper, px-only CSS, scoped styles
- ✅ **Anti-Duplicate Content**: TF-IDF + embedding similarity gates with rewrite loops
- ✅ **Canadian-First**: Mandated Canadian spelling, healthcare context, local sources
- ✅ **Email Notifications**: Success, error, and similarity alerts (provider-agnostic SMTP)
- ✅ **GM Review Portal**: Market-specific review links with auto-rewrite processing
- ✅ **Cost-Conscious**: Token tracking, caching, model routing optimization
- ✅ **Automated Retention**: 12-month output archive with auto-zipping

## Quick Start

### Local Development Setup

1. **Clone repository**
```bash
git clone <repo-url> Marketing-Project
cd Marketing-Project
```

2. **Create virtual environment**
```bash
python3.10 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

3. **Configure environment variables**
```bash
cp .env.example .env
nano .env
```

Edit `.env` with your credentials:
```bash
# OpenAI API (required)
OPENAI_API_KEY=sk-proj-...

# Pexels API (optional - for image search, falls back to curated images if not set)
PEXELS_API_KEY=your-pexels-api-key

# Email Configuration (provider-agnostic)
SMTP_HOST=smtp.postmarkapp.com
SMTP_PORT=587
SMTP_USER=your-postmark-token
SMTP_PASS=your-postmark-token
EMAIL_FROM=content-bot@thekey.com
EMAIL_TO=tt@thekey.com

# Timezone
TZ=America/Toronto
```

4. **Run manual test**

**Single Market Testing (Recommended for initial testing):**
```bash
# Test with Montreal only
TEST_MARKET=montreal python -m src.run_weekly
```

**Full Run (All 8 markets):**
```bash
python -m src.run_weekly
```

The `TEST_MARKET` environment variable allows you to test with a single market before running the full production workflow. Output will be saved to `outputs/YYYY-MM-DD/{market}.html` and `{market}.json`.

### Production Deployment (Linode)

See [deploy/README.md](deploy/README.md) for detailed deployment instructions.

**Quick install:**
```bash
# Upload repository to Linode
scp -r Marketing-Project/ user@your-linode:/home/thekey/

# SSH into Linode
ssh user@your-linode

# Run install script
cd /home/thekey/thekey-content-bot
sudo bash deploy/install.sh
```

## Configuration

### Markets Configuration

Edit `config/markets.yaml` to:
- Add/remove markets
- Update keyword pools
- Modify location URLs
- Add local domain filters

### Reviewers Configuration

Edit `config/reviewers.yaml` to:
- Map each market to its GM name/email
- Enable GM-specific review links and automated rewrites

### Brand Configuration

Edit `config/brand.yaml` to:
- Update boilerplate text
- Modify disclaimer
- Change CTA templates
- Adjust tone requirements
- Configure Canadian English spelling guide
- Set provincial healthcare system references

**Canadian English Requirements:**
- Mandatory Canadian spelling (colour, centre, behaviour, theatre, defence, etc.)
- Use "home care" not "home health care"
- Reference provincial health authorities by full name
- Use Canadian dollar amounts and programs
- Comprehensive spelling guide included in config

### Model Routing

Edit `config/model_routing.yaml` to:
- Change model assignments
- Adjust token caps
- Modify cost controls

### Content Calendar

Edit `config/content_calendar.yaml` to:
- Set rotation schedule (16 weeks with holiday/trend awareness)
- Define weekly themes (dementia, hospital-to-home, seasonal themes, holidays)
- Configure override triggers
- Adjust seasonal themes and holiday mappings

**Current Calendar Structure:**
- **16 weeks** of rotating themes
- **Holiday awareness**: Valentine's Day, Mother's Day, Father's Day, Tax Season
- **Seasonal themes**: Winter Safety, Spring Safety, Summer Wellness
- **Service themes**: Dementia, Parkinson's, Stroke, Heart Health, Cancer, End-of-Life
- Each week includes suggested keywords and local hook suggestions

## Usage

### Manual Test Run

Run immediately (without waiting for scheduled time):

```bash
# Local
python -m src.run_weekly

# Production (via sudo as thekey user)
sudo -u thekey bash -c 'cd /home/thekey/thekey-content-bot && source venv/bin/activate && python -m src.run_weekly'
```

### Scheduled Runs

Systemd timer automatically runs at 8 AM every Tuesday (America/Toronto).

**Check timer status:**
```bash
sudo systemctl list-timers thekey-content-bot
```

**View logs:**
```bash
# Systemd journal
sudo journalctl -u thekey-content-bot -f

# Application logs
tail -f /home/thekey/thekey-content-bot/logs/weekly.log
```

**Next scheduled run:**
```bash
sudo systemctl show thekey-content-bot.timer --property=NextElapseUSecMonotonic
```

## Output Structure

```
outputs/
├── YYYY-MM-DD/
│   ├── run_summary.json           # Run status and metrics
│   ├── tone_profile.json         # Extracted tone profile
│   ├── editor_report.json        # QA checks and fixes
│   ├── uniqueness_report.json     # Similarity metrics
│   ├── montreal.html            # Brightspot-ready HTML
│   ├── montreal.json            # Article metadata
│   ├── toronto.html
│   ├── toronto.json
│   ... (8 markets total)
└── outputs_2025-01-01.tar.gz  # Archived runs (>12 months)
```

## Brightspot HTML Requirements

Generated articles include:
- ✅ `<!-- Brightspot Content Block: {TITLE} -->` comment
- ✅ `<div class="blog-content-module">` wrapper
- ✅ Scoped CSS with unique class prefix (e.g., `bs-montreal-*`)
- ✅ px units only (no rem, em, %)
- ✅ H1 with primary keyword
- ✅ Deck/subheadline
- ✅ Skimmable H2 sections with bullet lists
- ✅ Light gold callout box
- ✅ Local Resources section (250-400 words, 3-6 links)
- ✅ FAQ section (5 questions)
- ✅ Dark CTA box with button
- ✅ Medical disclaimer (≤40 words)
- ✅ Hero image from Pexels with vision-verified relevance and attribution

## Anti-Duplicate Content System

### Similarity Gates

- **TF-IDF Cosine Similarity**: > 0.25 = FAIL
- **Embedding Similarity**: > 0.82 = FAIL

### Rewrite Loop

- Max 3 rewrite attempts per market
- Escalate from gpt-4o-mini to gpt-4.1 after 2 failed attempts
- After max rewrites: proceed best-effort + flag for manual review

### Requirements Per Market

1. Distinct primary keyword with unique intent
2. H2 outline ≥60% unique vs other markets
3. Unique local hook with citation
4. Local Resources section (250-400 words, 3-6 links)
5. 5 FAQs (≥4 unique across markets)
6. Internal links: location page + 1-2 service pages

## Email Notifications

### Success Email
Subject: `✅ Weekly SEO Content Packet — YYYY-MM-DD`
- Summary table (market, title, keyword, files)
- Output directory location
- Next steps

### Error Email
Subject: `❌ ERROR: TheKey Content Bot Failed — YYYY-MM-DD`
- Error details
- Log file location
- Recovery steps

### Similarity Alert Email
Subject: `⚠️ WEEKLY CONTENT PACKET (MANUAL REVIEW RECOMMENDED) — YYYY-MM-DD`
- Flag at top: failing markets
- Summary table
- Similarity score summary (full metrics)
- Manual review recommendations

## Testing

### Run Unit Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_html_validator.py

# Run with coverage
pytest --cov=src tests/
```

### Run Integration Smoke Test

```bash
pytest tests/test_integration.py
```

Tests use mocked web fetch and stubbed SMTP to avoid external dependencies.

## Troubleshooting

### Email Not Sending
1. Verify `.env` SMTP credentials
2. Check firewall: `sudo ufw status`
3. Test SMTP manually
4. Check logs: `sudo journalctl -u thekey-content-bot -n 100`

### Timer Not Triggering
1. Check enabled: `sudo systemctl is-enabled thekey-content-bot.timer`
2. Check status: `sudo systemctl list-timers`
3. Reload: `sudo systemctl daemon-reload`
4. Restart: `sudo systemctl restart thekey-content-bot.timer`

### OpenAI API Errors
1. Verify `OPENAI_API_KEY`
2. Check quota: https://platform.openai.com/usage
3. Check network connectivity

### Permission Errors
```bash
sudo chown -R thekey:thekey /home/thekey/thekey-content-bot
sudo chmod 600 /home/thekey/thekey-content-bot/.env
```

## Development

### Project Structure

```
src/
├── agents/          # Researcher, Writer, Editor/QA, Image Selector, Dispatcher
├── orchestrator/     # Workflow coordination
├── tools/           # Email, HTML validator, similarity checker, Pexels client
├── cache/           # Prompt and research caching
└── utils/           # Config loader, logger, file manager, OpenAI client (with vision)
```

### Adding a New Market

1. Add to `config/markets.yaml`:
```yaml
my_market:
  name: My Market
  province: My Province
  location_url: https://thekey.ca/locations/canada/my-market
  primary_keyword_pool: [...]
  secondary_keyword_pool: [...]
  local_domains: [...]
  healthcare_context: "..."
```

2. No code changes required!

### Adding a New Service Page

Edit `config/markets.yaml`:
```yaml
service_pages:
  my_service:
    url: https://thekey.ca/our-services/my-service
    keywords: ["keyword1", "keyword2"]
```

## Cost Monitoring

Tokens are tracked and logged weekly. Monitor in application logs:

```bash
grep "total_tokens" /home/thekey/thekey-content-bot/logs/weekly.log
```

Alert threshold: 500,000 tokens/week (configurable in `config/model_routing.yaml`).

## Support

For issues or questions:
1. Check logs: `sudo journalctl -u thekey-content-bot -n 100`
2. Check application logs: `tail -f logs/weekly.log`
3. Review troubleshooting section
4. Contact development team

## License

Proprietary - TheKey Canada
