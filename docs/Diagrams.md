# MCP Workspace Finder - システムアーキテクチャ図

このドキュメントでは、検索処理の流れを複数の観点からMermaid図表で説明します。

## 1. 全体処理フロー（High-Level Flow）

```mermaid
flowchart TB
    subgraph Entry["エントリーポイント (__main__.py)"]
        CLI[CLI起動] --> Mode{入力モード判定}
        Mode -->|--query または stdin| Oneshot[Oneshotモード]
        Mode -->|TTY 対話| REPL[REPLモード]
    end

    subgraph Config["設定読み込み (config.py)"]
        LoadDef[サーバー定義読み込み<br/>servers.yaml]
        ResolveMode[モード解決<br/>mock/real判定]
        LoadDef --> ResolveMode
    end

    subgraph LLMSearch["検索パラメータ生成 (llm_search.py)"]
        GenParams[LLMで検索パラメータ生成]
        Validate[スキーマ検証]
        GenParams --> Validate
    end

    subgraph MCPServers["MCPサーバー管理 (process.py)"]
        Launch[サーバー起動<br/>asyncio.create_subprocess_exec]
        Readiness[Readinessチェック]
        Monitor[プロセス監視]
        Launch --> Readiness --> Monitor
    end

    subgraph MCPClient["MCPクライアント (mcp_runners.py)"]
        StdioClient[StdioMcpClient]
        SearchRunner[検索Runner作成]
        FetchRunner[Fetch Runner作成]
        StdioClient --> SearchRunner
        StdioClient --> FetchRunner
    end

    subgraph SearchPipeline["検索パイプライン (search_pipeline.py)"]
        RunSearch[並列検索実行<br/>asyncio.gather]
        MapResults[結果マッピング<br/>search_mapping.py]
        RunFetch[並列Fetch実行<br/>asyncio.gather]
        RunSearch --> MapResults --> RunFetch
    end

    subgraph Summary["要約パイプライン (summary_pipeline.py)"]
        EvidenceLinks[エビデンスリンク生成<br/>evidence_links.py]
        LLMSummary[LLM要約生成<br/>llm_summary.py]
        EvidenceLinks --> LLMSummary
    end

    subgraph Output["出力表示"]
        RenderSummary[サマリ表示<br/>summary_display.py]
        RenderLinks[リンク表示]
        RenderSummary --> RenderLinks
    end

    Oneshot --> Config
    REPL --> Config
    Config --> LLMSearch
    LLMSearch --> MCPServers
    MCPServers --> MCPClient
    MCPClient --> SearchPipeline
    SearchPipeline --> Summary
    Summary --> Output

    style Entry fill:#e1f5fe
    style Config fill:#fff3e0
    style LLMSearch fill:#f3e5f5
    style MCPServers fill:#e8f5e9
    style MCPClient fill:#fce4ec
    style SearchPipeline fill:#e0f7fa
    style Summary fill:#fff8e1
    style Output fill:#f1f8e9
```

## 2. 検索実行シーケンス図

```mermaid
sequenceDiagram
    autonumber
    participant User as ユーザー
    participant CLI as __main__.py
    participant Config as config.py
    participant LLMSearch as llm_search.py
    participant Process as process.py
    participant MCPClient as mcp_runners.py
    participant Pipeline as search_pipeline.py
    participant MCP as MCPサーバー群
    participant Summary as summary_pipeline.py
    participant Display as summary_display.py

    User->>CLI: クエリ入力
    CLI->>Config: load_server_definitions()
    Config-->>CLI: ServerDefinition[]
    
    CLI->>Config: resolve_service_modes()
    Config-->>CLI: ResolvedService[]
    
    CLI->>LLMSearch: generate_search_parameters(query)
    Note over LLMSearch: OpenAI API呼び出し<br/>Function Calling使用
    LLMSearch-->>CLI: SearchGenerationResult<br/>(searches[], alternatives[])
    
    CLI->>Process: launch_services_async()
    Process->>MCP: プロセス起動 (stdin/stdout/stderr)
    MCP-->>Process: 起動完了
    Process-->>CLI: RuntimeStatus[]
    
    CLI->>MCPClient: create_mcp_runners_from_processes()
    MCPClient-->>CLI: search_runners, fetch_runners
    
    CLI->>Pipeline: run_search_and_fetch_pipeline()
    
    par 並列検索
        Pipeline->>MCP: Slack検索 (conversations_search_messages)
        Pipeline->>MCP: GitHub検索 (search_code / search_issues)
        Pipeline->>MCP: GDrive検索 (search)
    end
    
    MCP-->>Pipeline: 検索結果 (JSON/CSV/Text)
    
    Pipeline->>Pipeline: map_search_results()
    Note over Pipeline: サービスごとに<br/>最大3件に制限
    
    par 並列Fetch
        Pipeline->>MCP: Slack Fetch (conversations_replies)
        Pipeline->>MCP: GitHub Fetch (get_issue / get_file_contents)
        Pipeline->>MCP: GDrive Fetch (resources/read)
    end
    
    MCP-->>Pipeline: 詳細コンテンツ
    Pipeline-->>CLI: PipelineOutput (documents[], warnings[])
    
    CLI->>Summary: run_summary_pipeline()
    Summary->>Summary: format_evidence_links()
    Summary->>Summary: summarize_documents()
    Note over Summary: OpenAI API呼び出し<br/>Markdown生成
    Summary-->>CLI: SummaryPipelineResult
    
    CLI->>Display: render_summary_with_links()
    Display-->>User: 結果表示
```

## 3. MCPサーバー通信詳細

```mermaid
flowchart LR
    subgraph Client["MCP クライアント (Python)"]
        StdioClient[StdioMcpClient]
        SendReq[JSON-RPC Request送信]
        RecvRes[JSON-RPC Response受信]
    end

    subgraph Protocol["MCP Protocol (JSON-RPC 2.0)"]
        Init["initialize"]
        ToolsCall["tools/call"]
        ResourcesRead["resources/read"]
    end

    subgraph Servers["MCP サーバー"]
        Slack["Slack MCP<br/>(korotovsky/slack-mcp-server)"]
        GitHub["GitHub MCP<br/>(@modelcontextprotocol/server-github)"]
        GDrive["GDrive MCP<br/>(@modelcontextprotocol/server-gdrive)"]
    end

    StdioClient --> SendReq
    SendReq --> Init
    SendReq --> ToolsCall
    SendReq --> ResourcesRead
    
    Init --> Slack & GitHub & GDrive
    ToolsCall --> Slack & GitHub & GDrive
    ResourcesRead --> GDrive
    
    Slack & GitHub & GDrive --> RecvRes
    RecvRes --> StdioClient

    style Client fill:#e3f2fd
    style Protocol fill:#fff3e0
    style Servers fill:#e8f5e9
```

## 4. 検索ツール対応表

```mermaid
flowchart TB
    subgraph Slack["Slack"]
        SlackSearch[["検索: conversations_search_messages"]]
        SlackFetch[["Fetch: conversations_replies"]]
        SlackFormat["CSV形式<br/>MsgID,UserID,UserName,..."]
    end

    subgraph GitHub["GitHub"]
        GitHubCode[["検索: search_code"]]
        GitHubIssues[["検索: search_issues"]]
        GitHubFetchIssue[["Fetch: get_issue"]]
        GitHubFetchFile[["Fetch: get_file_contents"]]
        GitHubFormat["JSON形式<br/>{items: [...]}"]
    end

    subgraph GDrive["Google Drive"]
        GDriveSearch[["検索: search"]]
        GDriveFetch[["Fetch: resources/read"]]
        GDriveFormat["Text形式<br/>Found N files:..."]
    end

    SlackSearch --> SlackFormat --> SlackFetch
    GitHubCode --> GitHubFormat --> GitHubFetchFile
    GitHubIssues --> GitHubFormat --> GitHubFetchIssue
    GDriveSearch --> GDriveFormat --> GDriveFetch

    style Slack fill:#e1bee7
    style GitHub fill:#c8e6c9
    style GDrive fill:#bbdefb
```

## 5. データフロー詳細

```mermaid
flowchart TB
    subgraph Input["入力"]
        Query["ユーザークエリ<br/>(自然言語)"]
    end

    subgraph LLMGeneration["LLM検索パラメータ生成"]
        SystemPrompt["システムプロンプト<br/>(検索構文ルール)"]
        FunctionCall["Function Call<br/>build_search_queries"]
        SearchParams["検索パラメータ<br/>[{service, query, max_results}]"]
        Alternatives["代替クエリ<br/>[string]"]
    end

    subgraph SearchExecution["検索実行"]
        SlackQ["Slack Query<br/>論理演算子, in:#channel"]
        GitHubQ["GitHub Query<br/>repo:, is:issue, author:"]
        GDriveQ["GDrive Query<br/>fulltext検索"]
    end

    subgraph Results["検索結果"]
        SearchResult["SearchResult<br/>{service, kind, title, snippet, uri, fetch_tool, fetch_params}"]
    end

    subgraph Fetch["Fetch結果"]
        FetchResult["FetchResult<br/>{service, kind, title, snippet, uri, content}"]
    end

    subgraph Summarization["要約生成"]
        DocPayload["ドキュメントペイロード<br/>[{id, service, title, content}]"]
        SummaryPrompt["要約プロンプト<br/>(Markdown形式指定)"]
        FuncCallSummary["Function Call<br/>write_markdown_summary"]
        MarkdownOutput["Markdownサマリ<br/>+ evidence_count"]
    end

    subgraph Evidence["エビデンス"]
        EvidenceLinks["EvidenceLink<br/>{number, title, service, uri}"]
    end

    subgraph FinalOutput["最終出力"]
        Summary["## Slack<br/>- 要点 [1]<br/>## GitHub<br/>- 要点 [2]"]
        Links["[1] タイトル (Slack)<br/>URL"]
    end

    Query --> SystemPrompt
    SystemPrompt --> FunctionCall
    FunctionCall --> SearchParams
    FunctionCall --> Alternatives

    SearchParams --> SlackQ & GitHubQ & GDriveQ
    SlackQ & GitHubQ & GDriveQ --> SearchResult
    SearchResult --> FetchResult

    FetchResult --> DocPayload
    DocPayload --> SummaryPrompt
    SummaryPrompt --> FuncCallSummary
    FuncCallSummary --> MarkdownOutput

    FetchResult --> EvidenceLinks
    MarkdownOutput --> Summary
    EvidenceLinks --> Links

    style Input fill:#fff9c4
    style LLMGeneration fill:#f3e5f5
    style SearchExecution fill:#e0f7fa
    style Results fill:#e8f5e9
    style Fetch fill:#fff3e0
    style Summarization fill:#fce4ec
    style Evidence fill:#e1f5fe
    style FinalOutput fill:#c8e6c9
```

## 6. エラーハンドリング・リトライフロー

```mermaid
flowchart TB
    subgraph RetryPolicy["リトライポリシー (retry_policy.py)"]
        Attempt["実行試行"]
        RateLimit{"レートリミット<br/>エラー?"}
        Retry["指数バックオフ<br/>リトライ"]
        MaxRetry{"最大リトライ<br/>超過?"}
        Success["成功"]
        Fail["失敗 (警告記録)"]
    end

    subgraph Fallback["フォールバック"]
        MockFallback["モックサーバーへ<br/>フォールバック"]
        SummaryFallback["サマリ失敗時<br/>本文一覧表示"]
        SkipFetch["Fetch失敗時<br/>snippetを使用"]
    end

    Attempt --> RateLimit
    RateLimit -->|Yes| Retry
    RateLimit -->|No| Success
    Retry --> MaxRetry
    MaxRetry -->|No| Attempt
    MaxRetry -->|Yes| Fail

    Fail --> MockFallback
    Fail --> SummaryFallback
    Fail --> SkipFetch

    style RetryPolicy fill:#ffccbc
    style Fallback fill:#fff9c4
```

## 7. モジュール依存関係

```mermaid
graph TB
    subgraph EntryPoint["エントリーポイント"]
        Main["__main__.py"]
    end

    subgraph Core["コア機能"]
        Config["config.py"]
        Process["process.py"]
        MCPRunners["mcp_runners.py"]
    end

    subgraph Search["検索機能"]
        LLMSearch["llm_search.py"]
        SearchPipeline["search_pipeline.py"]
        SearchMapping["search_mapping.py"]
        SchemaValidation["schema_validation.py"]
    end

    subgraph Summary["要約機能"]
        SummaryPipeline["summary_pipeline.py"]
        LLMSummary["llm_summary.py"]
        EvidenceLinks["evidence_links.py"]
    end

    subgraph Display["表示機能"]
        SummaryDisplay["summary_display.py"]
        StatusDisplay["status_display.py"]
        ProgressDisplay["progress_display.py"]
    end

    subgraph Utilities["ユーティリティ"]
        LLMClient["llm_client.py"]
        RetryPolicy["retry_policy.py"]
        LoggingUtils["logging_utils.py"]
    end

    Main --> Config
    Main --> Process
    Main --> MCPRunners
    Main --> LLMSearch
    Main --> SummaryDisplay

    MCPRunners --> Process
    MCPRunners --> SearchPipeline
    MCPRunners --> SummaryPipeline

    LLMSearch --> SchemaValidation
    LLMSearch --> LoggingUtils
    LLMSearch --> LLMClient

    SearchPipeline --> SearchMapping
    SearchPipeline --> RetryPolicy

    SummaryPipeline --> LLMSummary
    SummaryPipeline --> EvidenceLinks
    SummaryPipeline --> SearchPipeline

    LLMSummary --> LoggingUtils

    style EntryPoint fill:#e1f5fe
    style Core fill:#e8f5e9
    style Search fill:#fff3e0
    style Summary fill:#fce4ec
    style Display fill:#f3e5f5
    style Utilities fill:#e0f7fa
```

## 8. 状態遷移図（MCPサーバープロセス）

```mermaid
stateDiagram-v2
    [*] --> Launching: launch_services_async()
    
    Launching --> WaitingReadiness: プロセス生成
    WaitingReadiness --> Ready: stdout/stderr出力検出
    WaitingReadiness --> Failed: タイムアウト
    WaitingReadiness --> Failed: 即時終了
    
    Ready --> Running: モニタリング開始
    Running --> Crashed: 異常終了
    Running --> Stopped: 正常終了
    Running --> [*]: プロセスkill
    
    Crashed --> Restarting: リトライ可
    Crashed --> PermanentFailure: 認証エラー
    Crashed --> PermanentFailure: リトライ上限
    
    Restarting --> WaitingReadiness: 再起動
    
    Failed --> [*]
    Stopped --> [*]
    PermanentFailure --> [*]

    note right of Ready
        MCPプロトコル初期化
        (initialize request)
    end note

    note right of Running
        tools/call, resources/read
        リクエスト処理中
    end note
```

## 9. Oneshotモード処理の詳細フロー

```mermaid
flowchart TB
    Start([クエリ受信]) --> LoadConfig[設定読み込み]
    LoadConfig --> LoadEnv[.env読み込み判定]
    LoadEnv --> CreateLLM[LLMクライアント作成]
    
    CreateLLM --> GenSearch[検索パラメータ生成]
    GenSearch --> LaunchMCP[MCPサーバー起動]
    
    LaunchMCP --> CreateRunners[Runner作成]
    CreateRunners --> ExecSearch[検索実行]
    
    subgraph SearchPhase["検索フェーズ"]
        ExecSearch --> ParallelSearch{並列実行}
        ParallelSearch --> SlackSearch[Slack検索]
        ParallelSearch --> GitHubSearch[GitHub検索]
        ParallelSearch --> GDriveSearch[GDrive検索]
        SlackSearch & GitHubSearch & GDriveSearch --> CollectResults[結果収集]
    end
    
    CollectResults --> MapResults[結果マッピング]
    MapResults --> CapResults[サービス毎3件制限]
    
    subgraph FetchPhase["Fetchフェーズ"]
        CapResults --> ParallelFetch{並列実行}
        ParallelFetch --> SlackFetch[Slack Fetch]
        ParallelFetch --> GitHubFetch[GitHub Fetch]
        ParallelFetch --> GDriveFetch[GDrive Fetch]
        SlackFetch & GitHubFetch & GDriveFetch --> CollectDocs[ドキュメント収集]
    end
    
    CollectDocs --> FormatLinks[エビデンスリンク生成]
    FormatLinks --> SummarizeDocs[LLM要約生成]
    
    SummarizeDocs --> HasSummary{要約成功?}
    HasSummary -->|Yes| RenderSummary[サマリ表示]
    HasSummary -->|No| RenderFallback[フォールバック表示]
    
    RenderSummary --> RenderLinks[リンク表示]
    RenderFallback --> RenderLinks
    RenderLinks --> Cleanup[MCPサーバー終了]
    Cleanup --> End([完了])

    style SearchPhase fill:#e0f7fa
    style FetchPhase fill:#fff3e0
```

---

## 凡例

| 色           | 意味                     |
| ------------ | ------------------------ |
| 🔵 青系       | エントリーポイント・表示 |
| 🟢 緑系       | サーバー管理・プロセス   |
| 🟡 黄系       | 検索処理                 |
| 🟣 紫系       | LLM連携                  |
| 🟠 オレンジ系 | データ変換・マッピング   |
| 🔴 赤系       | エラー処理・リトライ     |

