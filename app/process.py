from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
from asyncio.subprocess import Process as AsyncProcess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config import RunMode, ServerDefinition, ResolvedService

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())

_PLACEHOLDER_PATTERN = re.compile(r"\$\{([^}]+)\}")


class StartConfigurationError(RuntimeError):
    """Raised when a server cannot be started due to config or environment issues."""


@dataclass
class CommandSpec:
    argv: list[str]
    env: dict[str, str]
    workdir: Path


@dataclass
class StartResult:
    name: str
    mode: RunMode
    command: list[str] | None
    process: subprocess.Popen[str] | None
    error: str | None = None

    @property
    def started(self) -> bool:
        return self.process is not None and self.error is None


@dataclass
class RuntimeStatus:
    name: str
    mode: RunMode
    command: list[str] | None
    process: AsyncProcess | None
    ready: bool
    warning: str | None = None
    error: str | None = None

    @property
    def started(self) -> bool:
        return self.process is not None and self.error is None


def _render_template(value: str, env: Mapping[str, str], *, allow_missing: bool = False) -> str:
    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in env:
            if allow_missing:
                return ""
            raise StartConfigurationError(f"環境変数不足: {key}")
        return env[key]

    return _PLACEHOLDER_PATTERN.sub(_replace, value)


def _merge_env(command_env: Mapping[str, str], base_env: Mapping[str, str], *, allow_missing: bool) -> dict[str, str]:
    merged = dict(base_env)
    for key, template in command_env.items():
        merged[key] = _render_template(template, base_env, allow_missing=allow_missing)
    return merged


def _resolve_executable(raw_exec: str, workdir: Path, env: Mapping[str, str]) -> str:
    expanded = os.path.expanduser(raw_exec)
    has_sep = os.sep in expanded or (os.altsep and os.altsep in expanded)

    if os.path.isabs(expanded) or has_sep:
        candidate = Path(expanded)
        if not candidate.is_absolute():
            candidate = (workdir / candidate).resolve()

        if not candidate.exists():
            raise StartConfigurationError(f"実行パス不備: {candidate} が存在しません")
        if not os.access(candidate, os.X_OK):
            raise StartConfigurationError(f"実行パス不備: {candidate} に実行権限がありません")
        return str(candidate)

    resolved = shutil.which(expanded, path=env.get("PATH"))
    if not resolved:
        raise StartConfigurationError(f"実行パス不備: {expanded} が PATH 上に見つかりません")
    return resolved


def _build_command_spec(
    definition: ServerDefinition,
    selected_mode: RunMode,
    base_env: Mapping[str, str],
) -> CommandSpec:
    command = definition.real if selected_mode is RunMode.REAL else definition.mock

    env_templates: dict[str, str] = {}
    env_templates.update(definition.env)
    env_templates.update(command.env)

    env = _merge_env(env_templates, base_env, allow_missing=selected_mode is RunMode.MOCK)

    workdir = Path(_render_template(command.workdir, env, allow_missing=selected_mode is RunMode.MOCK)).expanduser()
    if not workdir.exists():
        raise StartConfigurationError(f"作業ディレクトリ不備: {workdir} が存在しません")

    exec_value = _render_template(command.exec, env, allow_missing=selected_mode is RunMode.MOCK)
    args = [_render_template(arg, env, allow_missing=selected_mode is RunMode.MOCK) for arg in command.args]

    executable = _resolve_executable(exec_value, workdir, env)
    argv = [executable, *args]

    return CommandSpec(argv=argv, env=env, workdir=workdir)


def start_services(
    definitions: Mapping[str, ServerDefinition],
    resolved: Mapping[str, ResolvedService],
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, StartResult]:
    """Start services based on resolved modes and return their processes or errors."""
    default_env = dict(os.environ if base_env is None else base_env)
    results: dict[str, StartResult] = {}

    for name, decision in resolved.items():
        definition = definitions.get(name)
        if not definition:
            continue

        try:
            spec = _build_command_spec(definition, decision.selected_mode, default_env)
        except StartConfigurationError as exc:
            logger.error("%s: %s", name, exc)
            results[name] = StartResult(
                name=name,
                mode=decision.selected_mode,
                command=None,
                process=None,
                error=str(exc),
            )
            continue

        process = subprocess.Popen(
            spec.argv,
            cwd=spec.workdir,
            env=spec.env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        results[name] = StartResult(
            name=name,
            mode=decision.selected_mode,
            command=spec.argv,
            process=process,
        )

    return results


async def _wait_for_readiness(name: str, process: AsyncProcess, timeout: float) -> tuple[bool, str | None]:
    try:
        line = await asyncio.wait_for(process.stdout.readline(), timeout)
    except asyncio.TimeoutError:
        warning = f"readiness timeout after {timeout:.1f}s"
        logger.warning("%s: %s", name, warning)
        return False, warning

    if not line:
        returncode = process.returncode
        warning = f"起動失敗: exit code {returncode}" if returncode is not None else "起動失敗"
        logger.warning("%s: %s", name, warning)
        return False, warning

    return True, None


async def launch_services_async(
    definitions: Mapping[str, ServerDefinition],
    resolved: Mapping[str, ResolvedService],
    *,
    base_env: Mapping[str, str] | None = None,
    readiness_timeout: float = 1.0,
) -> dict[str, RuntimeStatus]:
    """Start services concurrently and wait for initial readiness signals.

    A service is considered ready when the first stdout line arrives. When a
    timeout or early exit occurs, a warning is recorded but other services
    continue to launch.
    """
    default_env = dict(os.environ if base_env is None else base_env)
    default_env.setdefault("PYTHONUNBUFFERED", "1")

    async def _launch(name: str, decision: ResolvedService) -> RuntimeStatus:
        definition = definitions.get(name)
        if not definition:
            return RuntimeStatus(name=name, mode=decision.selected_mode, command=None, process=None, ready=False, warning="definition missing", error="definition missing")

        try:
            spec = _build_command_spec(definition, decision.selected_mode, default_env)
        except StartConfigurationError as exc:
            logger.error("%s: %s", name, exc)
            return RuntimeStatus(
                name=name,
                mode=decision.selected_mode,
                command=None,
                process=None,
                ready=False,
                warning=None,
                error=str(exc),
            )

        try:
            process = await asyncio.create_subprocess_exec(
                *spec.argv,
                cwd=spec.workdir,
                env=spec.env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:  # noqa: BLE001
            warning = f"起動失敗: {exc}"
            logger.warning("%s: %s", name, warning)
            return RuntimeStatus(
                name=name,
                mode=decision.selected_mode,
                command=spec.argv,
                process=None,
                ready=False,
                warning=warning,
                error=str(exc),
            )

        ready, warning = await _wait_for_readiness(name, process, readiness_timeout)

        return RuntimeStatus(
            name=name,
            mode=decision.selected_mode,
            command=spec.argv,
            process=process,
            ready=ready,
            warning=warning,
            error=None if ready else warning,
        )

    tasks = {name: asyncio.create_task(_launch(name, decision)) for name, decision in resolved.items()}
    results: dict[str, RuntimeStatus] = {}
    for name, task in tasks.items():
        results[name] = await task

    return results
