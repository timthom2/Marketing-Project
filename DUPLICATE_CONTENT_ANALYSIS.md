# Duplicate Content Analysis & Remediation Plan

## Executive Summary

The system generated two Oakville articles (Jan 9 and Jan 13) with **40-50% content overlap**, creating SEO duplicate content risk. This analysis identifies the root structural causes and provides a comprehensive remediation plan.

---

## Root Cause Analysis

### Issue 1: No Cross-Run Duplicate Detection ⚠️ **CRITICAL**

**Problem:**
- `SimilarityChecker` only compares articles **within the same run** (pairwise)
- No mechanism to check against **previously published articles** from past runs
- Same market + same week theme = high probability of duplicate content

**Evidence:**
- `src/tools/similarity_checker.py` - `check_pairwise()` only accepts current run's articles
- `src/agents/editor_qa.py` - No reference to historical articles
- No content archive or tracking system

**Impact:** 
- Articles generated 4 days apart (Jan 9 vs Jan 13) both used `winter_safety` theme
- Resulted in 40-50% content overlap despite different angles

---

### Issue 2: Week Theme Rotation Causes Repetition ⚠️ **HIGH**

**Problem:**
- Content calendar uses **12-week rotation** based on week-of-year
- Same week theme can repeat every 12 weeks for the same market
- No tracking of which themes were used previously for each market
- **Even DIFFERENT themes can cause overlap** if they're semantically related (e.g., winter_safety vs winter_isolation)

**Evidence:**
```python
# src/agents/researcher.py:144-166
def _get_current_week_theme(self) -> Dict:
    week_of_year = today.isocalendar()[1]
    rotation_week = ((week_of_year - 1) % 12) + 1  # 12-week cycle
    week_key = f"week_{rotation_week}"
```

**Impact:**
- Oakville articles from Jan 9 (`week_2`: winter_safety) and Jan 13 (`week_3`: winter_isolation) had **40-50% overlap despite different themes**
- Related themes share similar research sources (winter-related, same health authorities)
- Same local resources (Halton Region, Ontario Health) referenced in both
- Demonstrates that theme variation alone doesn't prevent duplicate content

---

### Issue 3: Limited Keyword Variation ⚠️ **MEDIUM**

**Problem:**
- Keywords generated from week theme template, not historical tracking
- Primary keywords can repeat across runs for same market + theme
- No "keyword history" per market to ensure variation

**Evidence:**
```python
# src/agents/researcher.py:716-741
def _generate_keywords(self, market_config: Dict, week_theme: str) -> Dict:
    theme_keywords = self.content_calendar["rotation_schedule"][f"week_{week_num}"]["theme_keywords"]
    # No check against previous keywords for this market
```

**Impact:**
- Both Oakville articles used similar keyword variations
- Article 1: "Ontario home care Oakville"
- Article 2: "home care Oakville" (minor variation)

---

### Issue 4: Same Research Sources ⚠️ **MEDIUM**

**Problem:**
- Web discovery finds sources based on `market + province + week_theme`
- Same inputs = similar source discovery = similar evidence cards
- No mechanism to prioritize **new** sources over previously used ones

**Evidence:**
```python
# src/agents/researcher.py:168-186
async def _discover_sources(self, market: str, province: str, week_theme: str):
    sources = await self.web_discovery.discover_sources(
        market=market, province=province, week_theme=week_theme, year=datetime.now().year
    )
    # No filtering of previously used sources
```

**Impact:**
- Both articles likely pulled from same provincial health authority pages
- Same statistics and programs referenced
- Similar local hooks and evidence cards

---

### Issue 5: Fixed Article Structure Template ⚠️ **MEDIUM**

**Problem:**
- Writer prompt enforces rigid structure (H1 → Deck → Opening → H2s → Checklist → FAQ)
- Same structure across all articles = predictable outline
- Limited variation in section ordering or content types

**Evidence:**
```python
# src/agents/writer.py:291-303
REQUIRED SECTIONS:
1. Compelling H1 with primary keyword
2. Deck/subheadline that promises value
3. Opening paragraph using {assigned_story_lead_type}
4. "Why This Matters Now" or news peg section
5. 3-4 H2 sections (at least ONE from H2 SEEDS above)
6. Callout box with local hook and citation
7. "What You Can Do This Week" actionable checklist
8. Warm, helpful CTA
9. FAQ section (5 questions)
10. Medical disclaimer
```

**Impact:**
- Both Oakville articles follow identical structure
- Makes content feel templated and reduces uniqueness

---

### Issue 6: No Content Archive System ⚠️ **CRITICAL**

**Problem:**
- No database or file system tracking of published articles
- No metadata store (title, keywords, themes, dates) for historical lookup
- Cannot query: "What articles did we publish for Oakville in the last 6 months?"

**Evidence:**
- No `content_archive.py` or similar module
- `outputs/` directory exists but not indexed/queried
- No deduplication against historical content

**Impact:**
- System has no memory of what was published
- Cannot prevent duplicate topics, keywords, or angles

---

## Remediation Plan

### Phase 1: Content Archive System (Priority: CRITICAL)

**Goal:** Build a queryable archive of published articles

**Implementation:**
1. Create `src/archive/content_archive.py`:
   - Store metadata: `market`, `title`, `primary_keyword`, `week_theme`, `published_date`, `slug`
   - Use SQLite or JSON file for persistence
   - Index by market + date for fast lookups

2. Update `coordinator.py`:
   - After successful run, archive all articles
   - Store in `outputs/{run_id}/archive.json` or SQLite DB

3. Add archive query methods:
   ```python
   def get_articles_for_market(market: str, days_back: int = 180) -> List[Dict]
   def get_articles_by_theme(week_theme: str, days_back: int = 180) -> List[Dict]
   def get_recent_keywords(market: str, count: int = 10) -> List[str]
   ```

**Files to Create/Modify:**
- `src/archive/__init__.py` (new)
- `src/archive/content_archive.py` (new)
- `src/orchestrator/coordinator.py` (modify - add archiving step)

---

### Phase 2: Cross-Run Duplicate Detection (Priority: CRITICAL)

**Goal:** Check new articles against historical archive

**Implementation:**
1. Extend `SimilarityChecker`:
   - Add `check_against_archive(article: Dict, market: str, days_back: int)` method
   - Compare new article against last 3-6 months of articles for same market
   - Use same TF-IDF + embedding thresholds (0.25 / 0.82)

2. Update `EditorQAAgent`:
   - Before similarity gate, run archive check
   - If similarity > threshold, trigger rewrite with archive context
   - Pass recent article titles/angles to rewrite prompt

3. Add archive context to rewrite prompt:
   ```python
   RECENT ARTICLES FOR {market_name}:
   - "{title_1}" (published {date_1}) - Theme: {theme_1}
   - "{title_2}" (published {date_2}) - Theme: {theme_2}
   
   AVOID: Repeating similar angles, statistics, or structures from above.
   ```

**Files to Modify:**
- `src/tools/similarity_checker.py` (add archive methods)
- `src/agents/editor_qa.py` (add archive check before similarity gate)
- `src/agents/writer.py` (add archive context to rewrite prompt)

---

### Phase 3: Theme Variation Tracking (Priority: HIGH)

**Goal:** Prevent same theme from repeating too frequently for same market

**Implementation:**
1. Track theme usage per market:
   - Store `{market: [theme, date], ...}` in archive
   - Query: "What themes did we use for Oakville in last 90 days?"

2. Add theme variation logic:
   - If same theme used within 60 days, **force different angle**
   - If same theme used within 90 days, **warn and suggest alternative**
   - If theme used 3+ times in 6 months, **block and use fallback theme**

3. Update `researcher.py`:
   ```python
   def _get_current_week_theme(self) -> Dict:
       base_theme = # ... existing logic
       
       # Check archive for recent theme usage
       recent_themes = archive.get_recent_themes(market, days_back=90)
       if base_theme in recent_themes:
           # Use alternative angle or fallback theme
           return self._get_alternative_theme(base_theme, market)
       
       return base_theme
   ```

**Files to Modify:**
- `src/archive/content_archive.py` (add theme tracking)
- `src/agents/researcher.py` (add theme variation check)

---

### Phase 4: Keyword History & Variation (Priority: MEDIUM)

**Goal:** Ensure keyword diversity across runs

**Implementation:**
1. Track keyword usage:
   - Store `{market: [(keyword, date), ...]}` in archive
   - Query recent keywords to avoid repetition

2. Update keyword generation:
   ```python
   def _generate_keywords(self, market_config: Dict, week_theme: str) -> Dict:
       # Get recent keywords for this market
       recent_keywords = archive.get_recent_keywords(market, count=5)
       
       # Generate candidate keywords
       candidates = # ... existing logic
       
       # Filter out recently used variations
       filtered = [k for k in candidates if not _is_similar_to_recent(k, recent_keywords)]
       
       return {"primary": filtered[0], "secondary": filtered[1:6]}
   ```

3. Add similarity check for keywords:
   - "Ontario home care Oakville" vs "home care Oakville" = too similar
   - Require ≥3 word difference or different semantic angle

**Files to Modify:**
- `src/archive/content_archive.py` (add keyword tracking)
- `src/agents/researcher.py` (add keyword variation logic)

---

### Phase 5: Research Source Deduplication (Priority: MEDIUM)

**Goal:** Prioritize new sources over previously used ones

**Implementation:**
1. Track source URLs per market:
   - Store `{market: [source_url, date], ...}` in archive
   - Mark sources as "used" to deprioritize in future runs

2. Update web discovery:
   ```python
   async def _discover_sources(self, market: str, province: str, week_theme: str):
       # Get all sources
       all_sources = await self.web_discovery.discover_sources(...)
       
       # Get previously used sources for this market
       used_sources = archive.get_used_sources(market, days_back=180)
       
       # Prioritize new sources
       new_sources = [s for s in all_sources if s['url'] not in used_sources]
       fallback_sources = [s for s in all_sources if s['url'] in used_sources]
       
       # Use new sources first, fallback if needed
       return new_sources[:6] if new_sources else fallback_sources[:6]
   ```

**Files to Modify:**
- `src/archive/content_archive.py` (add source URL tracking)
- `src/agents/researcher.py` (add source deduplication)

---

### Phase 6: Article Structure Variation (Priority: LOW)

**Goal:** Allow more structural diversity

**Implementation:**
1. Add optional structure templates:
   - "News-driven" (lead with statistic, then analysis)
   - "How-to" (problem → solution → steps)
   - "Story-driven" (narrative → lessons → action)

2. Rotate structure type per market:
   - Track last structure used
   - Vary structure type across runs

3. Make some sections optional:
   - FAQ not always required (if word count high)
   - Checklist can be replaced with "Key Takeaways" box
   - Allow 2-3 callout boxes instead of always 1

**Files to Modify:**
- `src/agents/writer.py` (add structure variation)
- `src/archive/content_archive.py` (track structure type)

---

## Implementation Priority

### Immediate (Week 1)
1. ✅ **Content Archive System** - Foundation for all other fixes
2. ✅ **Cross-Run Duplicate Detection** - Prevents immediate duplicate content risk

### Short-term (Weeks 2-3)
3. ✅ **Theme Variation Tracking** - Prevents theme repetition
4. ✅ **Keyword History & Variation** - Ensures keyword diversity

### Medium-term (Weeks 4-6)
5. ✅ **Research Source Deduplication** - Improves content freshness
6. ✅ **Article Structure Variation** - Enhances reader experience

---

## Success Metrics

### Before (Current State)
- ❌ 40-50% content overlap between runs
- ❌ Same week theme can repeat every 12 weeks
- ❌ No duplicate detection across runs
- ❌ Keywords can repeat within 30 days

### After (Target State)
- ✅ <20% content overlap between runs (measured by TF-IDF)
- ✅ Same theme blocked if used within 60 days
- ✅ Cross-run duplicate detection with <0.25 TF-IDF threshold
- ✅ Keywords vary by ≥3 words or different semantic angle
- ✅ 80%+ of sources are "new" (not used in last 180 days)

---

## Testing Strategy

1. **Unit Tests:**
   - Test archive storage/retrieval
   - Test similarity checking against archive
   - Test theme variation logic

2. **Integration Tests:**
   - Generate 2 articles for same market 4 days apart
   - Verify similarity < 0.25 TF-IDF
   - Verify different keywords and angles

3. **Regression Tests:**
   - Ensure existing functionality (within-run uniqueness) still works
   - Verify no performance degradation from archive queries

---

## Risk Mitigation

1. **Archive Performance:**
   - Use SQLite with indexes for fast queries
   - Cache recent articles in memory
   - Limit archive queries to last 180 days

2. **False Positives:**
   - Similarity thresholds may flag legitimate similar topics
   - Add manual override flag for edge cases
   - Log all similarity checks for review

3. **Backward Compatibility:**
   - Archive system should work with existing `outputs/` structure
   - Can retroactively index past articles
   - No breaking changes to existing workflow

---

## Next Steps

1. **Review & Approve Plan** - Get stakeholder sign-off
2. **Create Content Archive Module** - Start with Phase 1
3. **Implement Cross-Run Detection** - Phase 2
4. **Test with Oakville Case** - Verify fixes prevent duplicate content
5. **Deploy & Monitor** - Track similarity metrics over next 4 weeks

---

## Appendix: Code Locations

### Current Similarity Check (Within Run Only)
- `src/tools/similarity_checker.py:26-60` - `check_pairwise()` method
- `src/agents/editor_qa.py:218-267` - Similarity gate implementation

### Week Theme Selection
- `src/agents/researcher.py:144-166` - `_get_current_week_theme()`

### Keyword Generation
- `src/agents/researcher.py:716-741` - `_generate_keywords()`

### Article Structure
- `src/agents/writer.py:229-329` - Writer prompt with fixed structure

### Content Calendar
- `config/content_calendar.yaml` - 12-week rotation schedule

