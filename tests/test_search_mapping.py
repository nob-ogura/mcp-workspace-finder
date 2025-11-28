from app.search_mapping import map_search_results, SearchResult


def test_mapping_adds_fetch_params_per_service():
    raw_results = [
        {
            "service": "slack",
            "title": "Design thread",
            "snippet": "initial comment",
            "uri": "https://slack.com/archives/C1/p1",
            "thread_ts": "171.0",
        },
        {
            "service": "github",
            "kind": "issue",
            "title": "Bug #1",
            "snippet": "null pointer",
            "uri": "https://api.github.com/repos/org/repo/issues/1",
        },
        {
            "service": "gdrive",
            "title": "Design Doc",
            "snippet": "architecture notes",
            "uri": "https://drive.google.com/file/d/123",
        },
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 3

    slack = mapped[0]
    assert isinstance(slack, SearchResult)
    assert slack.fetch_tool == "slack.get_thread"
    assert slack.fetch_params["permalink"] == raw_results[0]["uri"]
    assert slack.fetch_params["thread_ts"] == raw_results[0]["thread_ts"]

    github = mapped[1]
    assert github.fetch_tool == "github.get_issue"
    assert github.fetch_params["uri"] == raw_results[1]["uri"]

    gdrive = mapped[2]
    assert gdrive.fetch_tool == "gdrive.read_resource"
    assert gdrive.fetch_params["uri"] == raw_results[2]["uri"]


def test_mapping_enforces_max_results_per_service():
    raw_results = [
        {"service": "slack", "title": f"msg{i}", "snippet": "", "uri": f"u{i}"}
        for i in range(5)
    ]

    mapped = map_search_results(raw_results)

    assert len(mapped) == 3
    assert all(r.service == "slack" for r in mapped)
    assert [r.title for r in mapped] == ["msg0", "msg1", "msg2"]
