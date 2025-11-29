from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.logging_utils import mask_sensitive_text
from app.search_pipeline import FetchResult

logger = logging.getLogger(__name__)

MODEL_NAME = "gpt-4o-mini"
TEMPERATURE = 0.3
REQUEST_TIMEOUT = 15
MAX_RETRIES = 1
_MAX_ATTEMPTS = 2  # initial + 1 retry on schema issues

SYSTEM_PROMPT = """
You are a summarization assistant. Produce concise Markdown grouped by service.

Formatting rules:
- Create a section per service that has documents. Use headings like "## Slack", "## GitHub", "## Drive".
- Under each heading, add bullet points that capture the key facts. Keep bullets short.
- Attach evidence numbers in square brackets matching the order of the provided documents: first document = [1], second = [2], etc. Do not skip or reorder numbers.
- If a service has no documents, omit that section. Do not invent content.

Example structure:
## Slack
- [1] Decision or finding
## GitHub
- [2] PR/issue summary
## Drive
- [3] Document highlights
""".strip()

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "write_markdown_summary",
            "description": "Summarize documents into Markdown with evidence numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {"type": "string"},
                    "evidence_count": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "Total number of evidence links used, should match provided documents.",
                    },
                },
                "required": ["markdown", "evidence_count"],
                "additionalProperties": False,
            },
        },
    }
]

_SENSITIVE_ENV_VARS = (
    "OPENAI_API_KEY",
    "SLACK_USER_TOKEN",
    "GITHUB_TOKEN",
    "DRIVE_TOKEN_PATH",
    "GOOGLE_API_KEY",
    "GOOGLE_CREDENTIALS_PATH",
)


@dataclass
class SummaryResult:
    markdown: str
    evidence_count: int


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


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except Exception:
        return str(content)


def _build_documents_payload(documents: Sequence[FetchResult]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for idx, doc in enumerate(documents, start=1):
        payload.append(
            {
                "id": idx,
                "service": doc.service,
                "kind": doc.kind,
                "title": doc.title,
                "snippet": doc.snippet,
                "uri": doc.uri,
                "content": _normalize_content(doc.content),
            }
        )
    return payload


def _validate_summary_payload(payload: Mapping[str, Any], expected_count: int) -> SummaryResult:
    if not isinstance(payload, Mapping):
        raise ValueError("summary payload must be a mapping")

    markdown = payload.get("markdown")
    if not isinstance(markdown, str) or not markdown.strip():
        raise ValueError("markdown must be a non-empty string")

    evidence_count = payload.get("evidence_count")
    if not isinstance(evidence_count, int) or evidence_count < 0:
        raise ValueError("evidence_count must be a non-negative integer")

    if evidence_count != expected_count:
        raise ValueError(f"evidence_count mismatch: expected {expected_count}, got {evidence_count}")

    return SummaryResult(markdown=markdown, evidence_count=evidence_count)


def summarize_documents(question: str, documents: Sequence[FetchResult], llm_client: Any) -> SummaryResult:
    """Summarize fetched documents into Markdown with ordered evidence numbers."""

    doc_payload = _build_documents_payload(documents)
    user_content = json.dumps(
        {
            "question": question,
            "documents": doc_payload,
            "instructions": "Assign evidence numbers in the same order as documents (1..N).",
        },
        ensure_ascii=False,
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    _safe_debug(
        "LLM request",
        {
            "messages": messages,
            "model": MODEL_NAME,
            "temperature": TEMPERATURE,
            "timeout": REQUEST_TIMEOUT,
            "max_retries": MAX_RETRIES,
        },
    )

    attempts = 0
    last_error: Exception | None = None
    while attempts < _MAX_ATTEMPTS:
        attempts += 1
        response = llm_client.create(
            messages=messages,
            model=MODEL_NAME,
            temperature=TEMPERATURE,
            timeout=REQUEST_TIMEOUT,
            max_retries=MAX_RETRIES,
            tools=TOOLS,
        )

        _safe_debug("LLM response", response)

        try:
            payload = _extract_function_arguments(response)
            return _validate_summary_payload(payload, expected_count=len(documents))
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempts < _MAX_ATTEMPTS:
                logger.warning("summary schema validation failed on attempt %s/%s; retrying: %s", attempts, _MAX_ATTEMPTS, exc)
                continue
            logger.warning("summary schema validation failed after %s attempts: %s", attempts, exc)
            raise ValueError(str(exc)) from exc

    raise ValueError(str(last_error) if last_error else "unknown error")
