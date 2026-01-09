#!/usr/bin/env python3
"""
TheKey Canada SEO Content Bot - Weekly Runner

This is the main entry point for the automated weekly content generation.
Run via: python -m src.run_weekly

Scheduled via systemd timer: Tuesdays 8:00 AM America/Toronto
"""

import asyncio
import sys
from datetime import datetime
from pathlib import Path

from orchestrator.coordinator import run_weekly
from utils.logger import get_logger
from utils.config_loader import get_timezone, get_env_var

import pytz

# Set timezone
tz = pytz.timezone(get_timezone())

def _env_flag(name: str, default: str = "false") -> bool:
    return get_env_var(name, default).strip().lower() in ("1", "true", "yes", "on")


def _output_dir_for(run_id: str) -> Path:
    return Path(__file__).parent.parent / "outputs" / run_id


def _should_skip_run(start_time: datetime, logger) -> bool:
    allow_non_tuesday = _env_flag("ALLOW_NON_TUESDAY_RUN")
    allow_rerun = _env_flag("ALLOW_SAME_DAY_RERUN")
    weekday = start_time.weekday()  # Monday=0
    run_id = start_time.strftime('%Y-%m-%d')

    if not allow_non_tuesday and weekday != 1:
        logger.info(
            f"Guard: Skipping run for {run_id} (today is {start_time.strftime('%A')}; only Tuesdays allowed)."
        )
        return True

    output_dir = _output_dir_for(run_id)
    if output_dir.exists() and not allow_rerun:
        logger.info(
            f"Guard: Skipping run for {run_id} (output directory already exists)."
        )
        return True

    return False


async def main():
    """Main entry point for weekly content generation."""
    start_time = datetime.now(tz)
    logger = get_logger(__name__)

    if _should_skip_run(start_time, logger):
        return 0
    
    logger.info("=" * 70)
    logger.info(f"TheKey Canada SEO Content Bot - Starting weekly run")
    logger.info(f"Run ID: {start_time.strftime('%Y-%m-%d')}")
    logger.info(f"Start time: {start_time.isoformat()}")
    logger.info(f"Timezone: {str(tz)}")
    logger.info("=" * 70)
    
    try:
        # Run weekly content generation
        result = await run_weekly()
        
        end_time = datetime.now(tz)
        duration = (end_time - start_time).total_seconds()
        
        logger.info("=" * 70)
        logger.info(f"Weekly run completed successfully")
        logger.info(f"End time: {end_time.isoformat()}")
        logger.info(f"Duration: {duration:.2f} seconds")
        logger.info("=" * 70)
        
        return 0
        
    except Exception as e:
        logger = get_logger(__name__)
        logger.error(f"Fatal error in weekly run: {e}", exc_info=True)
        logger.info("=" * 70)
        logger.info("Weekly run failed - check logs for details")
        logger.info("=" * 70)
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
