"""OpenAI client wrapper for LLM operations.

This module provides a client interface compatible with the generate_search_parameters
and summarize_documents functions. The client wraps the OpenAI API and provides
the expected `create` method signature.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


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
        """Call OpenAI API and return response in expected format."""
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

        try:
            response = self._client.chat.completions.create(**call_kwargs)
        except Exception as exc:
            logger.error("OpenAI API call failed: %s", exc)
            raise

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

