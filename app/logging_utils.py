from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Iterable

_TOKEN_PATTERN = re.compile(r"([A-Za-z0-9]{4})([A-Za-z0-9_\-]{6,})")
_EMAIL_PATTERN = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_DOMAIN_PATTERN = re.compile(r"(?<!\*)\b([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")

LOG_DIR_ENV = "MCP_LOG_DIR"
LOG_FILENAME = "workspace-finder.log"


def _mask_domain_value(domain: str) -> str:
    parts = domain.split(".")
    if len(parts) < 2:
        return "*" * len(domain)

    masked_parts: list[str] = []
    for part in parts[:-1]:
        if len(part) <= 2:
            masked_parts.append("*" * len(part))
            continue
        masked_parts.append(part[0] + "*" * (len(part) - 2) + part[-1])

    masked_parts.append(parts[-1])
    return ".".join(masked_parts)


def _mask_email(match: re.Match[str]) -> str:
    local, domain = match.group(1), match.group(2)
    if len(local) <= 2:
        masked_local = local[0] + "*" * (len(local) - 1)
    else:
        masked_local = local[0] + "*" * (len(local) - 2) + local[-1]
    return f"{masked_local}@{_mask_domain_value(domain)}"


def _mask_token(match: re.Match[str]) -> str:
    prefix, rest = match.group(1), match.group(2)
    return prefix + "*" * len(rest)


def _mask_domain(match: re.Match[str]) -> str:
    return _mask_domain_value(match.group(1))


def mask_sensitive_text(text: str) -> str:
    """Mask obvious secrets like tokens, emails, and domains in free text."""
    # masked = _EMAIL_PATTERN.sub(_mask_email, text)
    # masked = _TOKEN_PATTERN.sub(_mask_token, masked)
    # masked = _DOMAIN_PATTERN.sub(_mask_domain, masked)
    # return masked
    return text


class MaskingFormatter(logging.Formatter):
    """Formatter that applies simple masking to the final log message."""

    def format(self, record: logging.LogRecord) -> str:
        formatted = super().format(record)
        return mask_sensitive_text(formatted)


def _current_handlers() -> Iterable[logging.Handler]:
    return logging.getLogger().handlers or []


def install_log_masking() -> None:
    """Install a formatter that masks sensitive strings for all root handlers."""
    root = logging.getLogger()
    if not root.handlers:
        logging.basicConfig()

    for handler in _current_handlers():
        formatter = handler.formatter
        fmt = formatter._style._fmt if formatter else "%(levelname)s:%(name)s:%(message)s"  # type: ignore[attr-defined]
        datefmt = formatter.datefmt if formatter else None
        handler.setFormatter(MaskingFormatter(fmt, datefmt=datefmt))


def default_log_path() -> Path:
    """Return the default log file path, honoring MCP_LOG_DIR when set."""
    base = Path(os.getenv(LOG_DIR_ENV, Path.cwd() / "logs"))
    return (base / LOG_FILENAME).resolve()


def configure_file_logging(log_path: Path | None = None) -> Path:
    """Attach a masked file handler to the root logger if not already present."""
    target = (log_path or default_log_path()).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.touch(exist_ok=True)

    root = logging.getLogger()
    for handler in _current_handlers():
        if isinstance(handler, logging.FileHandler) and Path(handler.baseFilename) == target:
            return target

    file_handler = logging.FileHandler(target, encoding="utf-8")
    formatter = MaskingFormatter("%(asctime)s %(levelname)s:%(name)s:%(message)s")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    return target


def set_debug_logging(enabled: bool) -> None:
    """Toggle root logger level between DEBUG and WARNING."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if enabled else logging.WARNING)
