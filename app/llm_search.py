from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

from app.schema_validation import validate_search_payload

MODEL_NAME = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.25
REQUEST_TIMEOUT = 15
MAX_RETRIES = 1

SYSTEM_PROMPT = """
You are an assistant that generates search parameters for Slack, GitHub, and Google Drive.
- Return only via the function call `build_search_queries`.
- For each service, produce a JSON object with: service (slack|github|gdrive), query (string), and max_results (1-3, default 3).
- Slack syntax: logical operators (AND/OR), channel filter (in:#channel), DM/MPIM allowed, date filters (before:/after: YYYY-MM-DD), and phrases in quotes.
- GitHub syntax: repo:, org:, is:pr|issue, author:, label:, in:title|body, state:open|closed.
- Google Drive syntax: use keywords plus optional owner/email hints; prioritize filename and full-text search; include type hints like pdf/doc/sheet when helpful.
- Always generate parameters for all three services even if one seems less relevant.
- Also generate at least two alternative queries covering the same intent across services.
""".strip()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "build_search_queries",
            "description": "Generate search parameters for Slack, GitHub, and Google Drive plus alternative queries.",
            "parameters": {
                "type": "object",
                "properties": {
                    "searches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "service": {
                                    "type": "string",
                                    "enum": ["slack", "github", "gdrive"],
                                },
                                "query": {"type": "string"},
                                "max_results": {
                                    "type": "integer",
                                    "minimum": 1,
                                    "maximum": 3,
                                    "default": 3,
                                },
                                "channel": {"type": "string"},
                                "before": {"type": "string"},
                                "after": {"type": "string"},
                            },
                            "required": ["service", "query"],
                            "additionalProperties": True,
                        },
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                    },
                },
                "required": ["searches"],
                "additionalProperties": False,
            },
        },
    }
]


@dataclass
class SearchGenerationResult:
    searches: list[dict[str, Any]]
    alternatives: list[str]


def _extract_function_arguments(response: Mapping[str, Any]) -> dict[str, Any]:
    # Direct format used in tests
    if "function_call" in response:
        func = response["function_call"] or {}
    else:
        choices = response.get("choices", [])
        message = choices[0].get("message", {}) if choices else {}
        func = message.get("function_call")
        if not func and message.get("tool_calls"):
            func = (message["tool_calls"][0] or {}).get("function")
    if not func:
        raise ValueError("function_call missing")

    args = func.get("arguments")
    if args is None:
        raise ValueError("function_call.arguments missing")
    if isinstance(args, str):
        try:
            return json.loads(args)
        except json.JSONDecodeError as exc:
            raise ValueError("function_call.arguments is not valid JSON") from exc
    if isinstance(args, Mapping):
        return dict(args)
    raise ValueError("function_call.arguments has unexpected type")


def generate_search_parameters(question: str, llm_client: Any) -> SearchGenerationResult:
    """Generate search parameters for Slack/GitHub/Drive using LLM function calling.

    The client is expected to expose a ``create`` method compatible with the
    OpenAI client signature. Temperature/timeout/retry are fixed per spec.
    """

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]

    attempts = 0
    last_error: Exception | None = None
    while attempts < 2:
        attempts += 1
        response = llm_client.create(
            messages=messages,
            model=MODEL_NAME,
            temperature=DEFAULT_TEMPERATURE,
            timeout=REQUEST_TIMEOUT,
            max_retries=MAX_RETRIES,
            tools=TOOLS,
        )

        try:
            payload = _extract_function_arguments(response)
            searches = payload.get("searches", [])
            if not isinstance(searches, list):
                raise ValueError("searches must be a list")
            for item in searches:
                validate_search_payload(item)

            alternatives = payload.get("alternatives") or []
            if not isinstance(alternatives, list):
                raise ValueError("alternatives must be a list")

            return SearchGenerationResult(searches=searches, alternatives=list(alternatives))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempts >= 2:
                raise ValueError(str(exc))

    raise ValueError(str(last_error) if last_error else "unknown error")
