from app.evidence_links import format_evidence_links
from app.search_pipeline import FetchResult


def _make_result(service: str, title: str, uri: str) -> FetchResult:
    return FetchResult(
        service=service,
        kind="message",
        title=title,
        snippet=f"snippet for {title}",
        uri=uri,
        content="body",
    )


def test_evidence_links_are_sequential_and_unique():
    documents = [
        _make_result("slack", "Slack thread", "https://slack.test/1"),
        _make_result("gdrive", "Drive file", "https://drive.test/doc"),
        _make_result("github", "GitHub issue", "https://github.test/issue"),
        _make_result("slack", "Slack duplicate", "https://slack.test/1"),
        _make_result("gdrive", "Another Drive file", "https://drive.test/another"),
        _make_result("github", "PR details", "https://github.test/pr"),
    ]

    result = format_evidence_links(documents)

    assert [link.number for link in result.links] == [1, 2, 3, 4, 5]
    assert sum(1 for link in result.links if link.uri == "https://slack.test/1") == 1
    assert all(link.markdown.startswith(f"[{link.number}]") for link in result.links)
    assert result.links[0].markdown.startswith("[1] Slack thread (slack)")
    assert result.links[0].markdown.splitlines()[-1] == "https://slack.test/1"


def test_missing_uri_adds_warning_and_excludes():
    documents = [
        _make_result("slack", "No URI", ""),
        _make_result("github", "With URI", "https://github.test/ok"),
    ]

    result = format_evidence_links(documents)

    assert len(result.links) == 1
    assert result.links[0].number == 1
    assert result.links[0].uri == "https://github.test/ok"
    assert any("URI missing" in warning for warning in result.warnings)
