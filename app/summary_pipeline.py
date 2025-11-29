from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.evidence_links import EvidenceLink, format_evidence_links
from app.llm_summary import summarize_documents
from app.search_mapping import MAX_RESULTS_PER_SERVICE
from app.search_pipeline import FetchResult, FetchRunner, SearchRunner, run_search_and_fetch_pipeline

logger = logging.getLogger(__name__)

SUMMARY_LOG_FILENAME = "llm-summary.jsonl"


@dataclass
class SummaryPipelineResult:
    summary_markdown: str
    links: list[EvidenceLink]
    warnings: list[str]
    used_fallback: bool = False


@dataclass
class SearchFetchSummaryResult:
    documents: list[FetchResult]
    summary_markdown: str
    links: list[EvidenceLink]
    warnings: list[str]
    used_fallback: bool
    alternatives: list[str]


def _write_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False, default=str))
        fp.write("\n")


def _build_fallback_markdown(documents: Sequence[FetchResult]) -> str:
    lines = ["## 取得本文 (フォールバック)"]
    if not documents:
        lines.append("- 本文が取得できませんでした")
        return "\n".join(lines)

    for doc in documents:
        uri = (doc.uri or "").strip() or "URI missing"
        lines.append(f"- {doc.title} ({doc.service}): {uri}")
    return "\n".join(lines)


def _build_io_logger(log_path: Path | None):
    def _log(payload: dict[str, Any]) -> None:
        if log_path is None:
            return
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "stage": payload.get("stage", "summary"),
            "direction": payload.get("direction"),
            **{k: v for k, v in payload.items() if k not in {"stage", "direction"}},
        }
        try:
            _write_jsonl(log_path, record)
        except Exception:  # noqa: BLE001
            logger.debug("failed to write LLM summary log", exc_info=True)

    return _log


def run_summary_pipeline(
    question: str,
    documents: Sequence[FetchResult],
    llm_client: Any,
    *,
    initial_warnings: list[str] | None = None,
    debug_enabled: bool = False,
    log_dir: Path | None = None,
) -> SummaryPipelineResult:
    """Run LLM summarization with fail-soft fallback and optional IO logging."""

    warnings = list(initial_warnings or [])
    links_result = format_evidence_links(documents, initial_warnings=warnings)
    warnings = links_result.warnings

    log_path = (log_dir or Path.cwd() / "logs") / SUMMARY_LOG_FILENAME if debug_enabled else None
    io_logger = _build_io_logger(log_path)

    try:
        summary = summarize_documents(
            question,
            documents,
            llm_client,
            io_logger=io_logger,
        )
        return SummaryPipelineResult(
            summary_markdown=summary.markdown,
            links=links_result.links,
            warnings=warnings,
            used_fallback=False,
        )
    except (asyncio.TimeoutError, TimeoutError) as exc:
        message = f"summary timeout: {exc}"
    except Exception as exc:  # noqa: BLE001
        message = f"summary failed: {exc}"
    else:
        message = ""

    if message:
        warnings.append(message)
        logger.warning(message)

    fallback_markdown = _build_fallback_markdown(documents)
    return SummaryPipelineResult(
        summary_markdown=fallback_markdown,
        links=links_result.links,
        warnings=warnings,
        used_fallback=True,
    )


async def run_search_fetch_and_summarize_pipeline(
    question: str,
    searches: Iterable[Mapping[str, Any]],
    *,
    search_runners: Mapping[str, SearchRunner],
    fetch_runners: Mapping[str, FetchRunner],
    llm_client: Any,
    max_results_per_service: int = MAX_RESULTS_PER_SERVICE,
    initial_warnings: list[str] | None = None,
    debug_enabled: bool = False,
    log_dir: Path | None = None,
    alternatives: list[str] | None = None,
) -> SearchFetchSummaryResult:
    """Execute search+fetch asynchronously then summarize with evidence links."""

    search_output = await run_search_and_fetch_pipeline(
        searches,
        search_runners=search_runners,
        fetch_runners=fetch_runners,
        max_results_per_service=max_results_per_service,
        initial_warnings=initial_warnings,
    )

    summary_result = run_summary_pipeline(
        question,
        search_output.documents,
        llm_client,
        initial_warnings=search_output.warnings,
        debug_enabled=debug_enabled,
        log_dir=log_dir,
    )

    return SearchFetchSummaryResult(
        documents=search_output.documents,
        summary_markdown=summary_result.summary_markdown,
        links=summary_result.links,
        warnings=summary_result.warnings,
        used_fallback=summary_result.used_fallback,
        alternatives=alternatives or [],
    )
