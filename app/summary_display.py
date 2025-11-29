from __future__ import annotations

from typing import Sequence

from rich.console import Console
from rich.markdown import Markdown

from app.evidence_links import EvidenceLink


def _build_links_block(links: Sequence[EvidenceLink]) -> str:
    if not links:
        return ""

    lines = ["## 根拠リンク"]
    for link in links:
        wrapped = link.markdown.replace("\n", "\n  ")
        lines.append(f"- {wrapped}")
    return "\n".join(lines)


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
        blocks.append(summary)

    links_block = _build_links_block(links)
    if links_block:
        blocks.append(links_block)

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
