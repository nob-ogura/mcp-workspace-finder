from app.search_mapping import map_search_results, SearchResult


def test_mapping_adds_fetch_params_per_service():
    raw_results = [
        {
            "service": "slack",
            "title": "Design thread",
            "snippet": "initial comment",
            "uri": "https://slack.com/archives/C123ABC/p1234567890123456",
            "channel_id": "C123ABC",
            "thread_ts": "1234567890.123456",
        },
        {
            "service": "github",
            "kind": "issue",
            "title": "Bug #1",
            "snippet": "null pointer",
            "uri": "https://api.github.com/repos/org/repo/issues/1",
            "owner": "org",
            "repo": "repo",
            "issue_number": 1,
        },
        {
            "service": "gdrive",
            "title": "Design Doc",
            "snippet": "architecture notes",
            "uri": "gdrive:///file123",
        },
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 3

    slack = mapped[0]
    assert isinstance(slack, SearchResult)
    # With proper channel_id format, should use conversations_replies
    assert slack.fetch_tool == "slack.conversations_replies"
    assert slack.fetch_params["channel_id"] == "C123ABC"
    assert slack.fetch_params["thread_ts"] == "1234567890.123456"

    github = mapped[1]
    assert github.fetch_tool == "github.get_issue"
    assert github.fetch_params["owner"] == "org"
    assert github.fetch_params["repo"] == "repo"
    assert github.fetch_params["issue_number"] == 1

    gdrive = mapped[2]
    # GDrive uses resources via read_resource
    assert gdrive.fetch_tool == "gdrive.__read_resource__"
    assert gdrive.fetch_params == {"uri": "gdrive:///file123"}


def test_mapping_enforces_max_results_per_service():
    raw_results = [
        {"service": "slack", "title": f"msg{i}", "snippet": "", "uri": f"u{i}"}
        for i in range(5)
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 3
    assert all(r.service == "slack" for r in mapped)
    assert [r.title for r in mapped] == ["msg0", "msg1", "msg2"]


def test_gdrive_prefers_webviewlink_for_display_uri():
    """GDrive results should use webViewLink for display, not internal gdrive:/// URI."""
    raw_results = [
        {
            "service": "gdrive",
            "title": "Design Doc",
            "snippet": "architecture notes",
            "uri": "gdrive:///file123",  # Internal URI for fetching
            "webViewLink": "https://docs.google.com/document/d/file123/edit",  # User-facing URL
        },
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 1
    gdrive = mapped[0]
    # Display URI should be webViewLink (user-facing URL), not internal gdrive:/// URI
    assert gdrive.uri == "https://docs.google.com/document/d/file123/edit"
    # Fetch params should still use the internal URI
    assert gdrive.fetch_params == {"uri": "gdrive:///file123"}


def test_gdrive_falls_back_to_uri_when_no_webviewlink():
    """GDrive results without webViewLink should fall back to uri field."""
    raw_results = [
        {
            "service": "gdrive",
            "title": "Old Doc",
            "snippet": "legacy format",
            "uri": "gdrive:///legacy123",  # Only internal URI available
        },
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 1
    gdrive = mapped[0]
    # Falls back to the internal URI when no webViewLink
    assert gdrive.uri == "gdrive:///legacy123"


def test_gdrive_skips_fetch_when_search_url():
    """GDrive results with search URL (not gdrive:///) should skip fetching.
    
    The real GDrive MCP server returns text format without file IDs.
    We construct a search URL for display, but cannot fetch actual content.
    """
    raw_results = [
        {
            "service": "gdrive",
            "title": "Design Doc",
            "snippet": "Type: application/pdf",
            "uri": "https://drive.google.com/drive/search?q=Design%20Doc",  # Search URL
            "webViewLink": "https://drive.google.com/drive/search?q=Design%20Doc",
        },
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 1
    gdrive = mapped[0]
    # Should skip fetching since URI is not gdrive:/// format
    assert gdrive.fetch_tool == "gdrive.skip"
    assert gdrive.fetch_params == {}
    # Display URI should still be the search URL
    assert gdrive.uri == "https://drive.google.com/drive/search?q=Design%20Doc"
