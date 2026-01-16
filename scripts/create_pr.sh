#!/bin/bash
# Create PR for Phase 1 Content Archive System

REPO="timthom2/Marketing-Project"
BRANCH="pr-review-workflow-updates"
BASE="review-workflow-updates"

TITLE="Phase 1: Content Archive System for Duplicate Content Prevention"

BODY=$(cat <<'EOF'
## Phase 1: Content Archive System

This PR implements Phase 1 of the duplicate content remediation plan outlined in `DUPLICATE_CONTENT_ANALYSIS.md`.

### Changes
- ✅ Created `ContentArchive` class with SQLite storage
- ✅ Tracks articles, keywords, themes, and sources per market
- ✅ Archives articles automatically after successful runs
- ✅ Query methods for historical lookup
- ✅ Similarity detection for keywords and themes
- ✅ Comprehensive test suite

### Testing
- Code compiles without errors
- Test suite created (requires pytest to run)
- Archive system is backend-only; will be tested via web interface when Phase 2 (cross-run detection) is implemented

### Next Steps
Phase 2 will add cross-run duplicate detection using this archive to prevent duplicate content like the Oakville articles (Jan 9 vs Jan 13) that had 40-50% overlap despite different themes.

### Related
Addresses duplicate content issues identified in Oakville articles from Jan 9 and Jan 13 runs.
EOF
)

# Try GitHub CLI first
if command -v gh &> /dev/null; then
    echo "Using GitHub CLI to create PR..."
    gh pr create \
        --title "$TITLE" \
        --body "$BODY" \
        --head "$BRANCH" \
        --base "$BASE"
    exit $?
fi

# Fallback: Provide manual instructions
echo "GitHub CLI not found. Please create PR manually:"
echo ""
echo "URL: https://github.com/$REPO/compare/$BASE...$BRANCH"
echo ""
echo "Title: $TITLE"
echo ""
echo "Body:"
echo "$BODY"

