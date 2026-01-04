"""OpenAI API client with token tracking."""
from typing import Dict, List, Optional
import json as json_lib

try:
    from openai import AsyncOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    AsyncOpenAI = None

from .logger import get_logger
from .config_loader import get_env_var

logger = get_logger(__name__)


class OpenAIClient:
    """OpenAI API wrapper with token tracking and graceful error handling."""

    _instance: Optional['OpenAIClient'] = None
    _total_tokens = 0

    def __new__(cls):
        """Singleton pattern for OpenAI client."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._client = None
            cls._instance._initialize_client()
        return cls._instance

    def _initialize_client(self):
        """Initialize the OpenAI client."""
        api_key = get_env_var("OPENAI_API_KEY")

        if not OPENAI_AVAILABLE:
            logger.warning("OpenAI library not installed. API calls will be mocked.")
            self._client = None
        elif not api_key:
            logger.warning("OPENAI_API_KEY not set. API calls will fail.")
            try:
                self._client = AsyncOpenAI(api_key="test_key_placeholder")
            except Exception as e:
                logger.warning(f"Could not initialize client: {e}")
                self._client = None
        else:
            try:
                self._client = AsyncOpenAI(api_key=api_key)
                logger.debug("OpenAI client initialized")
            except Exception as e:
                logger.error(f"Failed to initialize OpenAI client: {e}")
                self._client = None

    @property
    def client(self):
        """Get OpenAI client instance."""
        if self._client is None:
            self._initialize_client()
        return self._client

    async def generate(self, **kwargs) -> str:
        """Generate text using OpenAI API.

        Args:
            model: Model name (e.g., gpt-4o-mini)
            prompt/user_prompt/user: User prompt (multiple accepted names for compatibility)
            system_prompt/system: Optional system prompt
            max_tokens/max_output_tokens: Maximum tokens to generate
            temperature: Sampling temperature
            response_format: Optional response format (e.g., {"type":"json_object"})

        Returns:
            str: Generated text

        Raises:
            Exception: If API call fails
        """
        if self._client is None:
            raise RuntimeError("OpenAI client not initialized. Set OPENAI_API_KEY environment variable.")

        # Extract parameters with flexible naming
        model = kwargs.get("model")
        system = kwargs.get("system_prompt") or kwargs.get("system") or ""
        user = kwargs.get("user_prompt") or kwargs.get("prompt") or kwargs.get("user") or ""
        temperature = kwargs.get("temperature", 0.7)
        max_tokens = kwargs.get("max_tokens", kwargs.get("max_output_tokens", 1200))
        response_format = kwargs.get("response_format")

        # Handle json_schema by degrading to json_object
        if isinstance(response_format, dict) and response_format.get("type") in ("json_schema", "json"):
            response_format = {"type": "json_object"}

        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": user})

            # Build request params - some newer models use max_completion_tokens
            request_params = {
                "model": model,
                "messages": messages,
                "temperature": temperature,
            }
            
            # Use max_completion_tokens for o1/o3/gpt-5.x/gpt-4.1 models
            # These newer models require max_completion_tokens instead of max_tokens
            if model and (model.startswith("o1") or model.startswith("o3") or 
                          model.startswith("gpt-5") or model.startswith("gpt-4.1")):
                request_params["max_completion_tokens"] = max_tokens
            else:
                request_params["max_tokens"] = max_tokens
            
            # Add response format if specified
            if response_format:
                request_params["response_format"] = response_format

            response = await self._client.chat.completions.create(**request_params)

            content = response.choices[0].message.content
            usage = response.usage

            self._total_tokens += usage.total_tokens

            logger.debug(f"Generated {model}: {usage.prompt_tokens} prompt + "
                         f"{usage.completion_tokens} completion = {usage.total_tokens} total")

            return content

        except Exception as e:
            logger.error(f"OpenAI API error: {e}")
            raise

    async def generate_embedding(
        self,
        text: str,
        model: str = "text-embedding-3-small"
    ) -> List[float]:
        """Generate embedding for text.

        Args:
            text: Text to embed
            model: Embedding model name

        Returns:
            List[float]: Embedding vector

        Raises:
            Exception: If API call fails
        """
        if self._client is None:
            raise RuntimeError("OpenAI client not initialized. Set OPENAI_API_KEY environment variable.")

        try:
            response = await self._client.embeddings.create(
                model=model,
                input=text
            )

            embedding = response.data[0].embedding
            usage = response.usage

            self._total_tokens += usage.total_tokens

            logger.debug(f"Generated embedding: {usage.total_tokens} tokens")

            return embedding

        except Exception as e:
            logger.error(f"OpenAI embedding error: {e}")
            raise

    async def analyze_image(
        self,
        image_url: str,
        prompt: str,
        model: str = "gpt-4o",
        max_tokens: int = 300,
        temperature: float = 0.3
    ) -> Dict:
        """Analyze an image using vision model.

        Args:
            image_url: URL of the image to analyze
            prompt: Text prompt describing what to analyze
            model: Vision-capable model name (gpt-4o, gpt-4-turbo)
            max_tokens: Maximum tokens to generate
            temperature: Sampling temperature

        Returns:
            Dict: Analysis result with relevance_score, match_description, suitable, concerns

        Raises:
            Exception: If API call fails
        """
        if self._client is None:
            raise RuntimeError("OpenAI client not initialized. Set OPENAI_API_KEY environment variable.")

        try:
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_url,
                                "detail": "low"  # Use low detail for faster/cheaper analysis
                            }
                        }
                    ]
                }
            ]

            response = await self._client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )

            content = response.choices[0].message.content
            usage = response.usage

            self._total_tokens += usage.total_tokens

            logger.debug(f"Vision analysis {model}: {usage.prompt_tokens} prompt + "
                         f"{usage.completion_tokens} completion = {usage.total_tokens} total")

            # Parse JSON response
            try:
                result = json_lib.loads(content)
            except json_lib.JSONDecodeError:
                logger.warning(f"Failed to parse vision response as JSON: {content[:200]}")
                result = {
                    "relevance_score": 5,
                    "match_description": "Unable to parse response",
                    "suitable": True,
                    "concerns": ["Response parsing failed"]
                }

            return result

        except Exception as e:
            logger.error(f"OpenAI vision API error: {e}")
            raise

    def get_total_tokens(self) -> int:
        """Get total tokens used this session."""
        return self._total_tokens
