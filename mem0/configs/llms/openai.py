import copy
import math
from typing import Any, Callable, Dict, List, Optional

from mem0.configs.llms.base import BaseLlmConfig


class OpenAIConfig(BaseLlmConfig):
    """
    Configuration class for OpenAI and OpenRouter-specific parameters.
    Inherits from BaseLlmConfig and adds OpenAI-specific settings.
    """

    def __init__(
        self,
        # Base parameters
        model: Optional[str] = None,
        temperature: float = 0.1,
        api_key: Optional[str] = None,
        max_tokens: int = 2000,
        top_p: float = 0.1,
        top_k: int = 1,
        enable_vision: bool = False,
        vision_details: Optional[str] = "auto",
        reasoning_effort: Optional[str] = None,
        http_client_proxies: Optional[dict] = None,
        is_reasoning_model: Optional[bool] = None,
        # OpenAI-specific parameters
        openai_base_url: Optional[str] = None,
        models: Optional[List[str]] = None,
        route: Optional[str] = "fallback",
        openrouter_base_url: Optional[str] = None,
        site_url: Optional[str] = None,
        app_name: Optional[str] = None,
        store: Optional[bool] = None,
        # Response monitoring callback
        response_callback: Optional[Callable[[Any, dict, dict], None]] = None,
        extra_body: Optional[Dict[str, Any]] = None,
        request_timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        """
        Initialize OpenAI configuration.

        Args:
            model: OpenAI model to use, defaults to None
            temperature: Controls randomness, defaults to 0.1
            api_key: OpenAI API key, defaults to None
            max_tokens: Maximum tokens to generate, defaults to 2000
            top_p: Nucleus sampling parameter, defaults to 0.1
            top_k: Top-k sampling parameter, defaults to 1
            enable_vision: Enable vision capabilities, defaults to False
            vision_details: Vision detail level, defaults to "auto"
            reasoning_effort: Effort level for reasoning models ("low", "medium", "high"), defaults to None
            http_client_proxies: HTTP client proxy settings, defaults to None
            is_reasoning_model: Explicit override for reasoning-model detection.
                None (default) uses the name-based heuristic. Set True to drop
                max_tokens/temperature, or False to force standard params.
            openai_base_url: OpenAI API base URL, defaults to None
            models: List of models for OpenRouter, defaults to None
            route: OpenRouter route strategy, defaults to "fallback"
            openrouter_base_url: OpenRouter base URL, defaults to None
            site_url: Site URL for OpenRouter, defaults to None
            app_name: Application name for OpenRouter, defaults to None
            store: Whether to store the conversation on OpenAI's server. Opt-in;
                defaults to None (not sent). Set to True or False only if you
                want the value forwarded to the OpenAI API. Leaving it None
                avoids leaking the field into OpenAI-compatible backends that
                reject unknown fields (Gemini, Groq, vLLM, etc.).
            response_callback: Optional callback for monitoring LLM responses.
            extra_body: Backend-specific JSON fields, omitted unless configured.
                Per-call extra_body replaces these defaults rather than merging.
            request_timeout: Positive finite SDK timeout in seconds; None retains SDK defaults.
            max_retries: Nonnegative SDK retry count; zero disables retries, None retains SDK defaults.
        """
        # Initialize base parameters
        super().__init__(
            model=model,
            temperature=temperature,
            api_key=api_key,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            enable_vision=enable_vision,
            vision_details=vision_details,
            reasoning_effort=reasoning_effort,
            http_client_proxies=http_client_proxies,
            is_reasoning_model=is_reasoning_model,
        )

        # OpenAI-specific parameters
        self.openai_base_url = openai_base_url
        self.models = models
        self.route = route
        self.openrouter_base_url = openrouter_base_url
        self.site_url = site_url
        self.app_name = app_name
        self.store = store

        # Response monitoring
        self.response_callback = response_callback

        if extra_body is not None and not isinstance(extra_body, dict):
            raise ValueError("extra_body must be a dictionary")
        if request_timeout is not None and (
            isinstance(request_timeout, bool)
            or not isinstance(request_timeout, (int, float))
            or not math.isfinite(request_timeout)
            or request_timeout <= 0
        ):
            raise ValueError("request_timeout must be positive finite seconds")
        if max_retries is not None and (
            isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0
        ):
            raise ValueError("max_retries must be a nonnegative integer")
        self.extra_body = copy.deepcopy(extra_body)
        self.request_timeout = request_timeout
        self.max_retries = max_retries
