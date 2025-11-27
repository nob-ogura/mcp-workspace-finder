from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from collections.abc import Iterable
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[1] / "servers.yaml"

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


class RunMode(str, Enum):
    MOCK = "mock"
    REAL = "real"


@dataclass
class ServerCommand:
    exec: str
    args: list[str]
    workdir: str
    env: dict[str, str]


@dataclass
class ServerDefinition:
    name: str
    declared_mode: RunMode
    kind: str
    env: dict[str, str]
    real: ServerCommand
    mock: ServerCommand
    auth_files: list[str] = field(default_factory=list)

    def required_env_keys(self) -> set[str]:
        required: set[str] = set(self.env.keys())
        required.update(_extract_placeholders(self.env.values()))
        required.update(_extract_placeholders(self.auth_files))
        return required


@dataclass
class ResolvedService:
    name: str
    declared_mode: RunMode
    selected_mode: RunMode
    missing_keys: list[str]
    warning: str | None
    missing_files: list[str] = field(default_factory=list)

    @property
    def is_override(self) -> bool:
        return self.declared_mode is RunMode.REAL and self.selected_mode is RunMode.MOCK


def _extract_placeholders(values: Any) -> set[str]:
    placeholders: set[str] = set()
    if isinstance(values, dict):
        iterable = values.values()
    elif isinstance(values, Iterable) and not isinstance(values, (str, bytes)):
        iterable = values
    else:
        iterable = [values]

    for value in iterable:
        if isinstance(value, str):
            placeholders.update(_PLACEHOLDER_PATTERN.findall(value))
    return placeholders


def _render_template(value: str, env: Mapping[str, str]) -> str:
    def _replace(match: re.Match[str]) -> str:
        return env.get(match.group(1), "")

    return _PLACEHOLDER_PATTERN.sub(_replace, value)


def _collect_missing_auth_files(definition: ServerDefinition, env: Mapping[str, str]) -> list[str]:
    missing: list[str] = []
    for raw_path in definition.auth_files:
        resolved = _render_template(raw_path, env).strip()
        if not resolved:
            missing.append(raw_path)
            continue
        candidate = Path(resolved).expanduser()
        if not candidate.exists() or not candidate.is_file() or not os.access(candidate, os.R_OK):
            missing.append(resolved)
    return missing


def _parse_mode(raw_mode: str | None) -> RunMode:
    try:
        return RunMode(raw_mode) if raw_mode else RunMode.REAL
    except ValueError:
        return RunMode.MOCK


def load_server_definitions(path: str | Path | None = None) -> dict[str, ServerDefinition]:
    target = Path(path) if path else DEFAULT_CONFIG_PATH
    raw_config = yaml.safe_load(target.read_text()) or {}
    services: Mapping[str, Any] = raw_config.get("services", {}) or {}

    definitions: dict[str, ServerDefinition] = {}
    for name, raw in services.items():
        declared_mode = _parse_mode(raw.get("mode"))
        env = raw.get("env") or {}
        auth_files = [entry.get("path", "") for entry in raw.get("auth_files", []) or [] if entry.get("path")]

        real_command = ServerCommand(
            exec=raw.get("exec", ""),
            args=list(raw.get("args") or []),
            workdir=raw.get("workdir", "."),
            env=env,
        )

        mock_raw = raw.get("mock") or {}
        mock_command = ServerCommand(
            exec=mock_raw.get("exec", real_command.exec),
            args=list(mock_raw.get("args") or []),
            workdir=mock_raw.get("workdir", real_command.workdir),
            env=mock_raw.get("env") or {},
        )

        definitions[name] = ServerDefinition(
            name=name,
            declared_mode=declared_mode,
            kind=raw.get("kind", ""),
            env=env,
            real=real_command,
            mock=mock_command,
            auth_files=auth_files,
        )

    return definitions


def resolve_service_modes(
    definitions: Mapping[str, ServerDefinition],
    *,
    force_mock: bool,
    allow_real: bool | None = None,
) -> dict[str, ResolvedService]:
    allow_real = allow_real if allow_real is not None else os.getenv("ALLOW_REAL") == "1"
    results: dict[str, ResolvedService] = {}

    for name, definition in definitions.items():
        warning = None
        missing: list[str] = []
        missing_files: list[str] = []
        selected = RunMode.MOCK

        if force_mock:
            if definition.declared_mode is RunMode.REAL:
                warning = f"CLI override: forced mock for {name}"
            selected = RunMode.MOCK
        else:
            if allow_real and definition.declared_mode is RunMode.REAL:
                missing = sorted(k for k in definition.required_env_keys() if not os.getenv(k))
                if missing:
                    warning = (
                        "鍵不足によりモックへフォールバック: "
                        f"{name} ({', '.join(missing)})"
                    )
                    selected = RunMode.MOCK
                else:
                    missing_files = _collect_missing_auth_files(definition, os.environ)
                    if missing_files:
                        warning = (
                            "認証ファイル不足によりモックへフォールバック: "
                            f"{name} ({', '.join(missing_files)})"
                        )
                        selected = RunMode.MOCK
                    else:
                        selected = RunMode.REAL
            else:
                selected = RunMode.MOCK

        results[name] = ResolvedService(
            name=name,
            declared_mode=definition.declared_mode,
            selected_mode=selected,
            missing_keys=missing,
            missing_files=missing_files,
            warning=warning,
        )

    return results


def mode_summary(resolved: Mapping[str, ResolvedService]) -> str:
    if not resolved:
        return ""
    return ", ".join(
        f"{name}={service.selected_mode.value}" for name, service in resolved.items()
    )
