from __future__ import annotations

import re
from typing import Sequence

from rich.console import Console
from rich.markdown import Markdown

from app.evidence_links import EvidenceLink


def _inject_urls_into_summary(summary: str, links: Sequence[EvidenceLink]) -> str:
    """Replace [N] references with the corresponding URL on the next line."""
    if not links:
        return summary

    # Build mapping from evidence number to URL
    url_map = {link.number: link.uri for link in links}

    def replace_ref(match: re.Match[str]) -> str:
        num = int(match.group(1))
        url = url_map.get(num)
        if url:
            # Replace [N] with URL on a new indented line
            return f"\n  {url}"
        return match.group(0)

    return re.sub(r'\s*\[(\d+)\]', replace_ref, summary)


def _build_alternatives_block(alternatives: Sequence[str] | None) -> str:
    if not alternatives:
        return ""

    cleaned = [alt.strip() for alt in alternatives if isinstance(alt, str) and alt.strip()]
    if not cleaned:
        return ""

    lines = ["## 次の検索候補"]
    lines.extend(f"- {alt}" for alt in cleaned)
    return "\n".join(lines)


def _compose_markdown(summary_markdown: str, links: Sequence[EvidenceLink], alternatives: Sequence[str] | None) -> str:
    blocks: list[str] = []

    summary = (summary_markdown or "").strip()
    if summary:
        # Inject URLs directly into the summary
        summary_with_urls = _inject_urls_into_summary(summary, links)
        blocks.append(summary_with_urls)

    alt_block = _build_alternatives_block(alternatives)
    if alt_block:
        blocks.append(alt_block)

    return "\n\n".join(blocks)


def render_summary_with_links(
    console: Console,
    summary_markdown: str,
    links: Sequence[EvidenceLink],
    *,
    alternatives: Sequence[str] | None = None,
) -> None:
    """Render Markdown summary + numbered evidence links to the console.

    - TTY: use Rich Markdown renderer for headings/bullets.
    - non-TTY: print raw Markdown to avoid ANSI codes.
    - Appends "次の検索候補" when alternative queries are provided.
    """

    payload = _compose_markdown(summary_markdown, links, alternatives)
    if not payload:
        return

    if console.is_terminal:
        console.print(Markdown(payload))
    else:
        console.print(payload, markup=False)
