from __future__ import annotations

import io

from rich.console import Console

from app.evidence_links import EvidenceLink
from app.summary_display import render_summary_with_links


def _sample_links() -> list[EvidenceLink]:
    return [
        EvidenceLink(number=1, title="Slack thread", service="slack", uri="https://slack.test/1"),
        EvidenceLink(number=2, title="GitHub issue", service="github", uri="https://github.test/2"),
    ]


def test_summary_and_links_render_in_order():
    console = Console(record=True, force_terminal=True, color_system=None, width=120)
    summary_md = "## Slack\n- Update [1]\n## GitHub\n- Fix [2]"

    render_summary_with_links(console, summary_md, _sample_links(), alternatives=["追加の検索", "別案"])

    out = console.export_text()
    assert "Slack" in out
    assert "GitHub" in out
    assert "根拠リンク" in out
    assert "[1] Slack thread (slack)" in out
    assert "https://slack.test/1" in out
    assert "次の検索候補" in out
    assert "追加の検索" in out
    assert out.index("Slack") < out.index("根拠リンク")
    assert out.index("根拠リンク") < out.index("次の検索候補")


def test_non_tty_outputs_plain_markdown_without_ansi():
    buffer = io.StringIO()
    console = Console(record=True, file=buffer, force_terminal=False, color_system=None, width=120)
    summary_md = "## Slack\n- Update [1]"

    render_summary_with_links(console, summary_md, _sample_links(), alternatives=["Alt 1"])

    out = console.export_text()
    assert "## Slack" in out
    assert "[1] Slack thread (slack)" in out
    assert "次の検索候補" in out
    assert "\x1b[" not in out
