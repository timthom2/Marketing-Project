"""File and directory management utility."""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class SafeJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder that handles edge cases gracefully."""
    
    def default(self, obj):
        # Handle datetime objects
        if isinstance(obj, datetime):
            return obj.isoformat()
        # Handle Path objects
        if isinstance(obj, Path):
            return str(obj)
        # Handle sets
        if isinstance(obj, set):
            return list(obj)
        # Handle bytes
        if isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        # For any other non-serializable object, convert to string
        try:
            return super().default(obj)
        except TypeError:
            return str(obj)


def create_output_directory(run_id: Optional[str] = None) -> Path:
    """Create output directory for weekly run.

    Args:
        run_id: Optional run ID (defaults to current date)

    Returns:
        Path: Output directory path
    """
    if run_id is None:
        run_id = datetime.now().strftime("%Y-%m-%d")
    
    output_dir = Path(__file__).parent.parent.parent / "outputs" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)
    
    return output_dir


def save_json(data: Dict[str, Any], filepath: Path) -> None:
    """Save data to JSON file.

    Args:
        data: Data to save
        filepath: File path to save to
    """
    filepath.parent.mkdir(parents=True, exist_ok=True)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, cls=SafeJSONEncoder)


def load_json(filepath: Path) -> Optional[Dict[str, Any]]:
    """Load data from JSON file.

    Args:
        filepath: File path to load from

    Returns:
        Optional[Dict]: Loaded data or None if file not found
    """
    if not filepath.exists():
        return None
    
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)
