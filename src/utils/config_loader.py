"""Configuration loader utility."""
import os
import yaml
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

load_dotenv()

def load_config(config_name: str) -> Dict[str, Any]:
    """Load a YAML configuration file.

    Args:
        config_name: Name of config file (without .yaml extension)

    Returns:
        Dict: Configuration data
    """
    config_path = Path(__file__).parent.parent.parent / "config" / f"{config_name}.yaml"
    
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    
    return config


def get_env_var(key: str, default: str = None, required: bool = False) -> str:
    """Get an environment variable.

    Args:
        key: Environment variable name
        default: Default value if not found
        required: If True, raise error if not found

    Returns:
        str: Environment variable value or default

    Raises:
        ValueError: If required and not found
    """
    value = os.getenv(key, default)
    
    if required and value is None:
        raise ValueError(f"Required environment variable not set: {key}")
    
    return value


def get_email_config() -> Dict[str, str]:
    """Get email configuration from environment variables.

    Returns:
        Dict: Email configuration with keys: host, port, user, pass, from, to
    """
    return {
        "host": get_env_var("SMTP_HOST", "smtp.postmarkapp.com"),
        "port": int(get_env_var("SMTP_PORT", "587")),
        "user": get_env_var(" SMTP_USER"),
        "pass": get_env_var("SMTP_PASS"),
        "from": get_env_var("EMAIL_FROM", "content-bot@thekey.com"),
        "to": get_env_var("EMAIL_TO", "tt@thekey.com")
    }


def get_timezone() -> str:
    """Get timezone from environment.

    Returns:
        str: Timezone string (default: America/Toronto)
    """
    return get_env_var("TZ", "America/Toronto")
