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
    # URLs should be injected directly under each item (not in separate section)
    assert "https://slack.test/1" in out
    assert "https://github.test/2" in out
    assert "次の検索候補" in out
    assert "追加の検索" in out
    # URL should appear right after the corresponding item, before alternatives
    assert out.index("https://slack.test/1") < out.index("次の検索候補")


def test_non_tty_outputs_plain_markdown_without_ansi():
    buffer = io.StringIO()
    console = Console(record=True, file=buffer, force_terminal=False, color_system=None, width=120)
    summary_md = "## Slack\n- Update [1]"

    render_summary_with_links(console, summary_md, _sample_links(), alternatives=["Alt 1"])

    out = console.export_text()
    assert "## Slack" in out
    # URL should be injected directly after the item
    assert "https://slack.test/1" in out
    assert "次の検索候補" in out
    assert "\x1b[" not in out


def test_url_injection_replaces_reference_number():
    """Test that [N] references are replaced with actual URLs."""
    console = Console(record=True, force_terminal=False, color_system=None, width=120)
    summary_md = "## Slack\n- メッセージ共有 [1]\n## GitHub\n- Issue修正 [2]"

    render_summary_with_links(console, summary_md, _sample_links())

    out = console.export_text()
    # Reference numbers should be replaced with URLs
    assert "[1]" not in out
    assert "[2]" not in out
    # URLs should be present
    assert "https://slack.test/1" in out
    assert "https://github.test/2" in out
    # Content should be preserved
    assert "メッセージ共有" in out
    assert "Issue修正" in out


def test_url_injected_on_new_line_with_indent():
    """Test that URLs appear on a new line with proper indentation."""
    console = Console(record=True, force_terminal=False, color_system=None, width=120)
    summary_md = "## Slack\n- First item [1]"

    render_summary_with_links(console, summary_md, _sample_links())

    out = console.export_text()
    # URL should follow the item on a new indented line
    assert "First item" in out
    assert "https://slack.test/1" in out
    # The URL line should be indented (starts with spaces)
    lines = out.split("\n")
    url_lines = [line for line in lines if "https://slack.test/1" in line]
    assert len(url_lines) == 1
    assert url_lines[0].startswith("  ")  # indented
