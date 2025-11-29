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
