from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Mapping

from app.schema_validation import validate_search_payload
from app.logging_utils import mask_sensitive_text

logger = logging.getLogger(__name__)

_SENSITIVE_ENV_VARS = (
    "OPENAI_API_KEY",
    "SLACK_USER_TOKEN",
    "GITHUB_TOKEN",
    "DRIVE_TOKEN_PATH",
    "GOOGLE_API_KEY",
    "GOOGLE_CREDENTIALS_PATH",
)

# Environment variable for GitHub search scope (owner/repo or org)
_GITHUB_SEARCH_SCOPE_VAR = "GITHUB_SEARCH_SCOPE"
_GITHUB_SMOKE_REPO_VAR = "GITHUB_SMOKE_REPO"

DEFAULT_MODEL_NAME = "gpt-4o-mini"
DEFAULT_TEMPERATURE = 0.25
REQUEST_TIMEOUT = 15
MAX_RETRIES = 1


def _get_model_name() -> str:
    """Get model name from environment variable or use default.
    
    Environment variable: OPENAI_MODEL
    Default: gpt-4o-mini
    """
    return os.getenv("OPENAI_MODEL") or DEFAULT_MODEL_NAME

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
                        "description": "One search query per service. You MUST include exactly 3 items: one for slack, one for github, and one for gdrive.",
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
                        "minItems": 3,
                        "maxItems": 3,
                    },
                    "alternatives": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 2,
                    },
                },
                "required": ["searches", "alternatives"],
                "additionalProperties": False,
            },
        },
    }
]


@dataclass
class SearchGenerationResult:
    searches: list[dict[str, Any]]
    alternatives: list[str]


def _redact_env_values(text: str) -> str:
    redacted = text
    for name in _SENSITIVE_ENV_VARS:
        value = os.getenv(name)
        if value:
            redacted = redacted.replace(value, "[redacted]")
    return redacted


def _safe_debug(prefix: str, payload: Any) -> None:
    if not logger.isEnabledFor(logging.DEBUG):
        return
    try:
        serialized = json.dumps(payload, ensure_ascii=False)
    except Exception:
        serialized = str(payload)
    cleaned = mask_sensitive_text(_redact_env_values(serialized))
    logger.debug("%s %s", prefix, cleaned)


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

    model_name = _get_model_name()
    _safe_debug(
        "LLM request",
        {
            "messages": messages,
            "model": model_name,
            "temperature": DEFAULT_TEMPERATURE,
            "timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
        },
    )

    attempts = 0
    last_error: Exception | None = None
    while attempts < 2:
        attempts += 1
        response = llm_client.create(
            messages=messages,
            model=model_name,
            temperature=DEFAULT_TEMPERATURE,
            timeout=REQUEST_TIMEOUT,
            max_retries=MAX_RETRIES,
            tools=TOOLS,
        )

        _safe_debug("LLM response", response)

        try:
            payload = _extract_function_arguments(response)
            searches = payload.get("searches", [])
            if not isinstance(searches, list):
                raise ValueError("searches must be a list")
            for item in searches:
                validate_search_payload(item)

            # Validate that all three services are present
            services_found = {s.get("service") for s in searches}
            required_services = {"slack", "github", "gdrive"}
            missing = required_services - services_found
            if missing:
                raise ValueError(f"searches missing required services: {', '.join(sorted(missing))}")

            # Apply GitHub search scope if configured
            searches = _apply_github_search_scope(searches)

            alternatives = _clean_alternatives(payload.get("alternatives"))
            enriched_alternatives = _ensure_intent_keywords(question, alternatives)

            return SearchGenerationResult(searches=searches, alternatives=enriched_alternatives)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempts >= 2:
                raise ValueError(str(exc))

    raise ValueError(str(last_error) if last_error else "unknown error")


def _clean_alternatives(raw: Any) -> list[str]:
    if raw is None:
        raise ValueError("alternatives missing")
    if not isinstance(raw, list):
        raise ValueError("alternatives must be a list")

    cleaned: list[str] = []
    seen: set[str] = set()
    for alt in raw:
        if not isinstance(alt, str):
            raise ValueError("alternatives must contain strings")
        stripped = alt.strip()
        if not stripped:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        cleaned.append(stripped)

    if len(cleaned) < 2:
        raise ValueError("alternatives must contain at least two non-empty strings")

    return cleaned


_INTENT_KEYWORDS = ("設計", "デザイン", "design")


def _get_github_search_scope() -> str | None:
    """Get GitHub search scope from environment variables.

    Checks GITHUB_SEARCH_SCOPE first, then falls back to GITHUB_SMOKE_REPO.
    Returns the value if set, or None if not configured.
    """
    scope = os.getenv(_GITHUB_SEARCH_SCOPE_VAR) or os.getenv(_GITHUB_SMOKE_REPO_VAR)
    return scope.strip() if scope else None


def _apply_github_search_scope(searches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply GitHub search scope (repo: or org:) to GitHub search queries.

    If GITHUB_SEARCH_SCOPE or GITHUB_SMOKE_REPO is set, ensures the GitHub
    search queries include the appropriate repo: or org: filter.
    """
    scope = _get_github_search_scope()
    if not scope:
        return searches

    # Determine if scope is org-only or owner/repo
    if "/" in scope:
        # owner/repo format -> use repo: filter
        scope_filter = f"repo:{scope}"
    else:
        # org-only format -> use org: filter
        scope_filter = f"org:{scope}"

    updated: list[dict[str, Any]] = []
    for search in searches:
        if search.get("service") != "github":
            updated.append(search)
            continue

        query = search.get("query", "")

        # Skip if query already has repo: or org: filter
        if re.search(r"\b(repo:|org:)", query, re.IGNORECASE):
            updated.append(search)
            continue

        # Append scope filter to query
        new_query = f"{query} {scope_filter}".strip()
        updated.append({**search, "query": new_query})
        logger.debug("Applied GitHub scope: %s -> %s", query, new_query)

    return updated


def _ensure_intent_keywords(question: str, alternatives: list[str]) -> list[str]:
    """Ensure at least one alternative retains design-related intent words."""

    lower_question = question.lower()
    intent_present = any(kw.lower() in lower_question for kw in _INTENT_KEYWORDS)

    if not intent_present:
        return alternatives

    has_keyword = any(
        kw.lower() in alt.lower()
        for alt in alternatives
        for kw in _INTENT_KEYWORDS
    )
    if has_keyword:
        return alternatives

    # Prepend the original question to preserve intent while keeping order stable.
    fallback = question.strip() or _INTENT_KEYWORDS[0]
    enriched = [fallback, *alternatives]

    deduped: list[str] = []
    seen: set[str] = set()
    for alt in enriched:
        if alt in seen:
            continue
        seen.add(alt)
        deduped.append(alt)

    if len(deduped) < 2:
        raise ValueError("alternatives must contain at least two non-empty strings")

    return deduped
