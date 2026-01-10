"""Review workflow state and feedback management."""
from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, time
from pathlib import Path
from typing import Dict, Optional, Tuple, List

from utils.config_loader import load_config, get_env_var
from utils.file_manager import save_json, load_json
from utils.logger import get_logger
from tools.email_sender import EmailSender

logger = get_logger(__name__)


def _outputs_root() -> Path:
    return Path(__file__).parent.parent.parent / "outputs"


def _review_index_path() -> Path:
    return _outputs_root() / "review_index.json"


def _review_dir(run_id: str) -> Path:
    return _outputs_root() / run_id / "review"


def _review_state_path(run_id: str) -> Path:
    return _review_dir(run_id) / "review_state.json"


def load_reviewers_config() -> Dict[str, Dict]:
    try:
        config = load_config("reviewers")
    except FileNotFoundError:
        logger.warning("config/reviewers.yaml not found; no GM review emails will be sent.")
        return {}

    return config.get("markets", {}) or {}


def _deadline_mode() -> str:
    return get_env_var("REVIEW_DEADLINE_MODE", "next_monday").strip().lower()


def _parse_state_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _next_monday_at(reference: datetime, hour: int) -> datetime:
    days_ahead = (7 - reference.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    target_date = (reference + timedelta(days=days_ahead)).date()
    return datetime.combine(target_date, time(hour=hour, minute=0, second=0))


def get_review_schedule(state: Dict) -> Tuple[datetime, datetime]:
    created_at = _parse_state_timestamp(state.get("created_at")) or datetime.now()
    reminder_at = _next_monday_at(created_at, 9)
    deadline_at = _parse_state_timestamp(state.get("deadline_at"))

    if _deadline_mode() != "hours" or not deadline_at:
        deadline_at = _next_monday_at(created_at, 17)

    return reminder_at, deadline_at


def normalize_review_deadline(state: Dict) -> bool:
    if not state or _deadline_mode() == "hours":
        return False
    _, deadline_at = get_review_schedule(state)
    deadline_str = deadline_at.isoformat()
    if state.get("deadline_at") == deadline_str:
        return False
    state["deadline_at"] = deadline_str
    run_id = state.get("run_id")
    if run_id:
        save_review_state(run_id, state)
    return True


def load_review_state(run_id: str) -> Optional[Dict]:
    return load_json(_review_state_path(run_id))


def save_review_state(run_id: str, state: Dict) -> None:
    review_dir = _review_dir(run_id)
    review_dir.mkdir(parents=True, exist_ok=True)
    save_json(state, _review_state_path(run_id))


def _load_review_index() -> Dict[str, Dict]:
    index_path = _review_index_path()
    index = load_json(index_path)
    return index if isinstance(index, dict) else {}


def _save_review_index(index: Dict[str, Dict]) -> None:
    index_path = _review_index_path()
    index_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(index, index_path)


def create_review_state(
    run_summary: Dict,
    base_url: Optional[str] = None,
    deadline_hours: Optional[int] = None
) -> Dict:
    run_id = run_summary.get("run_id", "")
    output_dir = Path(run_summary.get("output_dir", ""))

    if not run_id or not output_dir:
        raise ValueError("run_summary missing run_id or output_dir")

    reviewers = load_reviewers_config()
    base_url = (base_url or get_env_var("REVIEW_PORTAL_BASE_URL", "")).rstrip("/")
    if not base_url:
        raise ValueError("REVIEW_PORTAL_BASE_URL is required to generate review links")

    now = datetime.now()
    deadline_at = None
    if _deadline_mode() == "hours" and deadline_hours is not None:
        deadline_at = now + timedelta(hours=deadline_hours)
    else:
        deadline_at = _next_monday_at(now, 17)

    review_state = {
        "run_id": run_id,
        "created_at": now.isoformat(),
        "deadline_at": deadline_at.isoformat() if deadline_at else None,
        "status": "awaiting_reviews",
        "markets": {}
    }

    review_dir = _review_dir(run_id)
    feedback_dir = review_dir / "feedback"
    feedback_dir.mkdir(parents=True, exist_ok=True)

    review_index = _load_review_index()

    for article in run_summary.get("articles", []):
        market = article.get("market")
        if not market:
            continue
        market_name = article.get("market_name", market)
        reviewer = reviewers.get(market, {})
        gm_email = reviewer.get("gm_email", "")
        gm_name = reviewer.get("gm_name", "")

        token = secrets.token_urlsafe(18)
        review_url = f"{base_url}/review/{token}"

        feedback_path = str(feedback_dir / f"{market}.json")

        review_state["markets"][market] = {
            "market": market,
            "market_name": market_name,
            "gm_email": gm_email,
            "gm_name": gm_name,
            "token": token,
            "review_url": review_url,
            "status": "pending",
            "decision": None,
            "feedback_path": feedback_path,
            "submitted_at": None,
            "processed_at": None,
            "rewrite_attempts": 0,
            "last_error": None,
            "review_send_status": "pending",
            "review_sent_at": None,
            "review_send_failed_at": None,
            "review_send_error": None,
            "last_reminder_at": now.isoformat(),
            "reminder_count": 0
        }

        review_index[token] = {
            "run_id": run_id,
            "market": market
        }

    save_review_state(run_id, review_state)
    _save_review_index(review_index)

    return review_state


def find_review_by_token(token: str) -> Optional[Tuple[str, str, Dict]]:
    index = _load_review_index()
    entry = index.get(token)
    if not entry:
        return None
    run_id = entry.get("run_id")
    market = entry.get("market")
    if not run_id or not market:
        return None
    state = load_review_state(run_id)
    if not state:
        return None
    market_state = state.get("markets", {}).get(market)
    if not market_state or market_state.get("token") != token:
        return None
    return run_id, market, state


def record_feedback(
    run_id: str,
    market: str,
    decision: str,
    notes: str,
    reviewer_name: str = "",
    reviewer_email: str = ""
) -> Dict:
    state = load_review_state(run_id)
    if not state:
        raise FileNotFoundError(f"Review state not found for run_id={run_id}")

    market_state = state.get("markets", {}).get(market)
    if not market_state:
        raise KeyError(f"Market {market} not found in review state")

    now = datetime.now().isoformat()

    feedback = {
        "run_id": run_id,
        "market": market,
        "market_name": market_state.get("market_name", market),
        "decision": decision,
        "notes": notes,
        "reviewer_name": reviewer_name,
        "reviewer_email": reviewer_email,
        "submitted_at": now
    }

    feedback_path = Path(market_state.get("feedback_path"))
    feedback_path.parent.mkdir(parents=True, exist_ok=True)
    save_json(feedback, feedback_path)

    if decision == "approve":
        diff_path = market_state.get("diff_path")
        if diff_path:
            try:
                Path(diff_path).unlink(missing_ok=True)
            except Exception as e:
                logger.warning(f"Failed to remove diff for {market}: {e}")
        market_state["diff_path"] = None

    market_state["decision"] = decision
    market_state["submitted_at"] = now
    market_state["status"] = "submitted"
    state["markets"][market] = market_state

    save_review_state(run_id, state)

    return feedback


def update_market_state(run_id: str, market: str, updates: Dict) -> Dict:
    state = load_review_state(run_id)
    if not state:
        raise FileNotFoundError(f"Review state not found for run_id={run_id}")

    market_state = state.get("markets", {}).get(market)
    if not market_state:
        raise KeyError(f"Market {market} not found in review state")

    market_state.update(updates)
    state["markets"][market] = market_state
    save_review_state(run_id, state)

    return state


def list_review_runs() -> Dict[str, Path]:
    runs = {}
    root = _outputs_root()
    if not root.exists():
        return runs

    for run_dir in sorted(root.iterdir()):
        if not run_dir.is_dir():
            continue
        state_path = run_dir / "review" / "review_state.json"
        if state_path.exists():
            runs[run_dir.name] = state_path

    return runs


def review_deadline_passed(state: Dict) -> bool:
    _, deadline_at = get_review_schedule(state)
    return datetime.now() >= deadline_at


async def send_review_requests(state: Dict, run_summary: Dict) -> List[str]:
    email_sender = EmailSender()
    run_id = state.get("run_id", "")
    deadline_at = state.get("deadline_at")

    tasks = []
    task_markets: List[str] = []
    recipients: List[str] = []

    reply_to = get_env_var("REVIEW_REPLY_TO", "").strip() or None

    for article in run_summary.get("articles", []):
        market = article.get("market")
        if not market:
            continue
        market_state = state.get("markets", {}).get(market, {})
        gm_email = market_state.get("gm_email", "").strip()
        if not gm_email:
            logger.warning(f"No GM email configured for {market}; skipping review request.")
            continue

        subject, body = email_sender.build_review_request_email(
            run_id,
            market_state.get("market_name", market),
            article.get("title", ""),
            market_state.get("review_url", ""),
            deadline_at=deadline_at
        )

        recipients.append(gm_email)
        tasks.append(
            email_sender.send_email(
                subject=subject,
                body=body,
                to_addresses=[gm_email],
                reply_to=reply_to
            )
        )
        task_markets.append(market)

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        now = datetime.now().isoformat()
        for market, result in zip(task_markets, results):
            market_state = state.get("markets", {}).get(market, {})
            if isinstance(result, Exception) or result is False:
                market_state["review_send_status"] = "failed"
                market_state["review_send_failed_at"] = now
                market_state["review_send_error"] = str(result) if isinstance(result, Exception) else "send_failed"
            else:
                market_state["review_send_status"] = "sent"
                market_state["review_sent_at"] = now
                market_state["review_send_error"] = None
            state["markets"][market] = market_state
        save_review_state(run_id, state)

    return recipients
