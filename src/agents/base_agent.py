"""Base agent class."""
from abc import ABC, abstractmethod
from typing import Dict

from utils.logger import get_logger
from utils.config_loader import load_config
from utils.openai_client import OpenAIClient


class BaseAgent(ABC):
    """Base class for all agents."""

    def __init__(self):
        self.logger = get_logger(self.__class__.__name__)
        self.openai = OpenAIClient()

    @abstractmethod
    async def run(self, *args, **kwargs) -> Dict:
        """Main agent execution method.

        Returns:
            Dict: Agent result
        """
        pass

    def log_info(self, message: str):
        """Log info message."""
        self.logger.info(message)

    def log_error(self, message: str, exc_info: bool = False):
        """Log error message."""
        self.logger.error(message, exc_info=exc_info)

    def log_warning(self, message: str):
        """Log warning message."""
        self.logger.warning(message)
