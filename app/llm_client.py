"""OpenAI client wrapper for LLM operations.

This module provides a client interface compatible with the generate_search_parameters
and summarize_documents functions. The client wraps the OpenAI API and provides
the expected `create` method signature.
"""
from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

logger = logging.getLogger(__name__)

# Rate limit retry configuration
RATE_LIMIT_MAX_RETRIES = 3
RATE_LIMIT_DEFAULT_WAIT = 10.0  # seconds
RATE_LIMIT_MAX_WAIT = 60.0  # seconds

# Fallback model when primary model hits daily limit
FALLBACK_MODEL = "gpt-3.5-turbo"


def _extract_retry_after(exc: Exception) -> float | None:
    """Extract retry-after time from OpenAI rate limit error.
    
    Parses messages like:
    - "Please try again in 8.64s."
    - "Please try again in 1m30s."
    """
    message = str(exc)
    
    # Match patterns like "8.64s", "10s", "1m30s"
    match = re.search(r"try again in\s+([\d.]+)s", message, re.IGNORECASE)
    if match:
        return float(match.group(1))
    
    # Match "Xm" or "XmYs" patterns
    match = re.search(r"try again in\s+(\d+)m(?:(\d+)s)?", message, re.IGNORECASE)
    if match:
        minutes = int(match.group(1))
        seconds = int(match.group(2)) if match.group(2) else 0
        return float(minutes * 60 + seconds)
    
    return None


def _is_daily_limit_error(exc: Exception) -> bool:
    """Check if this is a daily request limit (RPD) error.
    
    Daily limits cannot be resolved by waiting a few seconds.
    """
    message = str(exc).lower()
    # Check for "requests per day" or "RPD" patterns
    return ("requests per day" in message or 
            " rpd:" in message.lower() or
            "per day (rpd)" in message)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Check if exception is an OpenAI rate limit error (429)."""
    # Check for openai.RateLimitError
    if exc.__class__.__name__ == "RateLimitError":
        return True
    
    # Check status code
    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        if response is not None:
            status = getattr(response, "status_code", None)
    
    if status == 429:
        return True
    
    # Check message content
    message = str(exc).lower()
    return "429" in str(exc) and "rate" in message


def _get_fallback_model() -> str | None:
    """Get fallback model from environment or use default.
    
    Environment variable: OPENAI_FALLBACK_MODEL
    Default: gpt-3.5-turbo
    Set to empty string to disable fallback.
    """
    env_value = os.getenv("OPENAI_FALLBACK_MODEL")
    if env_value is not None:
        return env_value if env_value.strip() else None
    return FALLBACK_MODEL


class OpenAIClientWrapper:
    """Wrapper around OpenAI client that provides the expected interface.

    The `create` method signature matches what llm_search and llm_summary expect:
    - messages: list of message dicts
    - model: model name
    - temperature: float
    - timeout: int
    - max_retries: int
    - tools: list of tool definitions (optional)
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise ImportError(
                "openai package is required. Install with: poetry add openai"
            ) from exc

        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise ValueError(
                "OPENAI_API_KEY is required. Set it in .env or environment."
            )

        self._base_url = base_url or os.getenv("OPENAI_BASE_URL")
        self._client = OpenAI(
            api_key=self._api_key,
            base_url=self._base_url,
        )

    def create(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float,
        timeout: int,
        max_retries: int,
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Call OpenAI API and return response in expected format.
        
        Includes automatic retry with exponential backoff for rate limit errors.
        """
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "timeout": timeout,
        }

        if tools:
            call_kwargs["tools"] = tools
            # Force tool use when tools are provided
            call_kwargs["tool_choice"] = "auto"

        last_exc: Exception | None = None
        current_model = call_kwargs["model"]
        used_fallback = False
        
        for attempt in range(1, RATE_LIMIT_MAX_RETRIES + 1):
            try:
                response = self._client.chat.completions.create(**call_kwargs)
                if attempt > 1:
                    if used_fallback:
                        logger.info(
                            "OpenAI API call succeeded with fallback model '%s'",
                            call_kwargs["model"]
                        )
                    else:
                        logger.info("OpenAI API call succeeded after %d attempts", attempt)
                break
            except Exception as exc:
                last_exc = exc
                
                if not _is_rate_limit_error(exc):
                    logger.error("OpenAI API call failed: %s", exc)
                    raise
                
                # Check if this is a daily limit - switch to fallback model immediately
                if _is_daily_limit_error(exc):
                    fallback = _get_fallback_model()
                    if fallback and call_kwargs["model"] != fallback:
                        logger.warning(
                            "Daily limit (RPD) reached for '%s', switching to fallback model '%s'",
                            call_kwargs["model"], fallback
                        )
                        call_kwargs["model"] = fallback
                        used_fallback = True
                        continue  # Retry immediately with fallback model
                    else:
                        logger.error(
                            "Daily limit (RPD) reached and no fallback model available: %s",
                            exc
                        )
                        raise
                
                if attempt >= RATE_LIMIT_MAX_RETRIES:
                    logger.error(
                        "OpenAI API rate limit exceeded after %d attempts: %s",
                        attempt, exc
                    )
                    raise
                
                # Extract wait time from error message or use default
                wait_time = _extract_retry_after(exc)
                if wait_time is None:
                    wait_time = RATE_LIMIT_DEFAULT_WAIT * attempt  # exponential-ish backoff
                
                # Cap the wait time
                wait_time = min(wait_time, RATE_LIMIT_MAX_WAIT)
                
                logger.warning(
                    "OpenAI API rate limit hit (attempt %d/%d), waiting %.1fs before retry: %s",
                    attempt, RATE_LIMIT_MAX_RETRIES, wait_time, exc
                )
                time.sleep(wait_time)
        else:
            # This shouldn't happen but handle it just in case
            if last_exc:
                raise last_exc
            raise RuntimeError("Unexpected retry loop exit")

        # Convert response to dict format expected by llm_search/llm_summary
        message = response.choices[0].message

        result: dict[str, Any] = {
            "choices": [
                {
                    "message": {
                        "role": message.role,
                        "content": message.content,
                    }
                }
            ]
        }

        # Handle tool calls (function calling)
        if message.tool_calls:
            result["choices"][0]["message"]["tool_calls"] = [
                {
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                }
                for tc in message.tool_calls
            ]

        # Handle legacy function_call format
        if hasattr(message, "function_call") and message.function_call:
            result["choices"][0]["message"]["function_call"] = {
                "name": message.function_call.name,
                "arguments": message.function_call.arguments,
            }

        return result


def create_llm_client() -> OpenAIClientWrapper | None:
    """Factory function to create an LLM client.

    Returns None if OPENAI_API_KEY is not set, allowing graceful fallback.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set; LLM features will be disabled")
        return None

    try:
        return OpenAIClientWrapper(api_key=api_key)
    except Exception as exc:
        logger.warning("Failed to create LLM client: %s", exc)
        return None


