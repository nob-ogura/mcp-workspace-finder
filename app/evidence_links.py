from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.search_pipeline import FetchResult


@dataclass
class EvidenceLink:
    number: int
    title: str
    service: str
    uri: str

    @property
    def markdown(self) -> str:
        return f"[{self.number}] {self.title} ({self.service})\n{self.uri}"


@dataclass
class EvidenceFormattingResult:
    links: list[EvidenceLink]
    warnings: list[str]


def format_evidence_links(
    documents: Sequence[FetchResult],
    *,
    initial_warnings: list[str] | None = None,
) -> EvidenceFormattingResult:
    """Create numbered, deduplicated evidence links in Markdown-friendly form.

    - Assigns numbers starting at 1 across all services.
    - Skips duplicate URIs while preserving first-seen order.
    - Emits a warning and omits entries when the URI is missing/blank.
    """

    warnings = list(initial_warnings or [])
    links: list[EvidenceLink] = []
    seen_uris: set[str] = set()

    for doc in documents:
        uri = (doc.uri or "").strip()
        if not uri:
            warnings.append(f"URI missing for {doc.title} ({doc.service})")
            continue

        if uri in seen_uris:
            continue

        seen_uris.add(uri)
        number = len(links) + 1
        links.append(
            EvidenceLink(
                number=number,
                title=doc.title,
                service=doc.service,
                uri=uri,
            )
        )

    return EvidenceFormattingResult(links=links, warnings=warnings)
