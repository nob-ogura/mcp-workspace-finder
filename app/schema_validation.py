from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
_SCHEMA_FILES: dict[str, str] = {
    "slack": "slack_search.json",
    "github": "github_search.json",
    "gdrive": "gdrive_search.json",
}


def _load_schema(service: str) -> dict[str, Any]:
    """Load schema JSON for the given service to ensure the file exists.

    We don't rely on a jsonschema dependency; the file presence acts as a
    contract and future extensibility point.
    """

    filename = _SCHEMA_FILES.get(service)
    if not filename:
        raise ValueError(f"Unsupported service: {service}")

    path = _SCHEMA_DIR / filename
    if not path.exists():
        raise ValueError(f"Schema file not found: {path}")

    # Parse once to catch malformed JSON; content is not otherwise used here.
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValueError(f"Schema file is invalid JSON: {path}") from exc


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _validate_common(payload: Mapping[str, Any], *, service: str) -> None:
    _require(isinstance(payload, Mapping), "payload must be a mapping")

    actual_service = payload.get("service")
    _require(actual_service == service, f"service must be '{service}'")

    query = payload.get("query")
    _require(isinstance(query, str) and query.strip(), "query is required")

    if "max_results" in payload:
        max_results = payload["max_results"]
        _require(isinstance(max_results, int), "max_results must be int")
        _require(1 <= max_results <= 3, "max_results must be between 1 and 3")


def _validate_slack(payload: Mapping[str, Any]) -> None:
    _validate_common(payload, service="slack")


def _validate_github(payload: Mapping[str, Any]) -> None:
    _validate_common(payload, service="github")


def _validate_gdrive(payload: Mapping[str, Any]) -> None:
    _validate_common(payload, service="gdrive")


_VALIDATORS: dict[str, Any] = {
    "slack": _validate_slack,
    "github": _validate_github,
    "gdrive": _validate_gdrive,
}


def validate_search_payload(payload: Mapping[str, Any]) -> None:
    """Validate search parameters payload for Slack/GitHub/Drive.

    Raises ValueError when validation fails. This performs lightweight checks
    that mirror the JSON Schemas located under `schemas/`.
    """

    _require(isinstance(payload, Mapping), "payload must be a mapping")

    service = payload.get("service")
    if not isinstance(service, str):
        raise ValueError("service is required")

    # Ensure the schema file exists and is valid JSON (for contract visibility).
    _load_schema(service)

    validator = _VALIDATORS.get(service)
    if not validator:
        raise ValueError(f"Unsupported service: {service}")

    validator(payload)
