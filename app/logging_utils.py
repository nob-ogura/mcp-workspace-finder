from __future__ import annotations

import logging
import re
from typing import Iterable

_TOKEN_PATTERN = re.compile(r"([A-Za-z0-9]{4})([A-Za-z0-9_\-]{6,})")
_EMAIL_PATTERN = re.compile(r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
_DOMAIN_PATTERN = re.compile(r"(?<!\*)\b([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)\b")


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
    masked = _EMAIL_PATTERN.sub(_mask_email, text)
    masked = _TOKEN_PATTERN.sub(_mask_token, masked)
    masked = _DOMAIN_PATTERN.sub(_mask_domain, masked)
    return masked


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
