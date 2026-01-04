# Changelog

## [Unreleased] - 2026-01-04

### Fixed
- **JSON Serialization Error**: Added `SafeJSONEncoder` to handle datetime, Path, sets, bytes, and other edge cases in metadata export
- **Package Rename Warning**: Updated `duckduckgo_search` to `ddgs` package and fixed imports
- **Anti-Duplication Enforcement**: Added post-generation validation in writer for citations, meta descriptions, and banned phrases with retry logic
- **Image Pool Diversity**: Expanded image search queries from 5 to 8 per market with market-hash rotation, theme-specific queries, and increased candidate pool from 10 to 15
- **BC Market Differentiation**: Added distinct `h2_seeds` and `must_include_entities` for Vancouver (VCH/downtown focus), Surrey (Fraser Health/Newton/Fleetwood focus), and Victoria (Island Health/Oak Bay/Saanich focus)

### Changed
- Image selector now uses market-hash rotation to ensure diverse image pools across markets
- Writer agent now validates critical requirements and retries if issues are found
- All markets now use `h2_seeds` instead of `h2_prompts` for consistency

### Technical
- Updated `requirements.txt` to use `ddgs>=8.0.0` instead of `duckduckgo-search`
- Enhanced `file_manager.py` with custom JSON encoder for edge cases
- Improved image search diversity with 14 core query variations and theme-specific fallbacks
