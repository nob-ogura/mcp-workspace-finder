# 実装漏れ対応タスク一覧

## 概要
要件定義および初期フェーズ（Phase 2, 6, 7）で計画されていたものの、実装漏れとなっていた以下の項目に対応します。

1.  **`read_resource` 機能の追加**: StdioMcpClient へのリソース読み込み機能の実装
2.  **Drive/GitHub ファイルの本文取得**: `search_mapping.py` での `read_resource` 利用設定
3.  **代替クエリ表示の修正**: 検索結果0件時以外でも代替クエリが表示されるように修正（または要件に合わせて表示ロジックを整備）
4.  **検索結果表示の仕様適合確認**: URLを含む最終的な出力形式の整合性確認

---

## タスク1: `read_resource` 機能の実装 (Phase 2 補完)
- **目的**: MCP プロトコルの `resources/read` リクエストを送信し、リソース（ファイル等）の中身を取得できるようにする。
- **作業内容**:
    - `app/mcp_runners.py` の `StdioMcpClient` クラスに `read_resource(uri: str) -> str` メソッドを追加する。
    - `resources/read` メソッドを JSON-RPC で送信し、レスポンスの `contents[0].text` または `blob` (base64デコード) を返す。
    - `create_fetch_runner` で `tool_name` が指定されない（`None`の）場合、かつ `read_resource` 系の処理が必要な場合にこのメソッドを呼ぶアダプタを実装する。

### 受入基準
- `StdioMcpClient.read_resource("gdrive://id")` を呼ぶと、MCP サーバー経由でコンテンツが返る。
- `resources/read` が失敗した場合に `McpClientError` が送出される。

## タスク2: コンテンツ取得マッピングの修正 (Phase 6 補完)
- **目的**: Google Drive と GitHub のファイル検索結果について、`skip` ではなく実際に `read_resource` を用いて本文を取得する。
- **作業内容**:
    - `app/search_mapping.py` の `_build_fetch_info` を修正する。
    - `gdrive` の場合: `gdrive.skip` ではなく、fetch ツール名として特殊トークン（例: `__read_resource__` または `None`）を返し、URI をターゲットとする。
    - `github` の場合: `kind=code` またはファイルパスの場合、`github.skip` ではなく `read_resource` を使用するように変更する。
    - `app/mcp_runners.py` の `create_fetch_runner` を修正し、リソース読み込み用の Runner を生成できるようにする（タスク1と連携）。

### 受入基準
- `python -m app --query "..."` 実行時、Google Drive の検索結果に対して「Found files...」だけでなく、中身の要約が生成される。
- GitHub のコード検索結果に対しても同様に中身が取得される。

## タスク3: 代替クエリ表示の修正 (Phase 7 補完)
- **目的**: LLM が生成した「代替クエリ（Next steps）」がユーザーに表示されていない問題を解消する。
- **作業内容**:
    - `app/llm_search.py` の `SearchGenerationResult` に含まれる `alternatives` が、パイプライン全体を通して `app/__main__.py` の表示層まで渡るようにする。
    - `app/mcp_runners.py` の `run_oneshot_with_mcp` の戻り値（`SearchFetchSummaryResult`）に `alternatives` フィールドを追加する（または既存の構造に乗せる）。
    - `app/summary_pipeline.py` の `run_search_fetch_and_summarize_pipeline` も同様に `alternatives` を受け渡すように修正する。
    - `app/__main__.py` の `run_oneshot_with_mcp_sync` 内で、`_render_summary_output` に `alternatives` を正しく渡す。

### 受入基準
- `python -m app --query "..."` 実行後、結果の末尾に「## 次の検索候補」セクションが表示され、LLM が生成した代替クエリが列挙される。

## タスク4: 検索結果表示の仕様適合確認
- **目的**: 実装された各要素（本文要約、既存の根拠リンク、代替クエリ）が、ユーザーにとって適切な順序とフォーマットで統合表示されているかを確認する。
- **作業内容**:
    - 統合テスト（または手動確認）を行い、最終的な出力レイアウトを検証する。

### 受入基準
- `python -m app --query "..."` 実行時、以下の順序で情報が表示されること。
    1. **要約**: GitHub/Drive のファイル内容も含んだ回答テキスト（タスク2の成果）。
    2. **根拠リンク**: `## 根拠リンク` セクションとして、参照元ドキュメントのタイトルと **URL** が表示されること（既存機能の維持確認）。
    3. **代替クエリ**: `## 次の検索候補` セクションとして、次のアクションが表示されること（タスク3の成果）。

---

## 実行計画
1. タスク1 (`read_resource`) の実装と単体テスト（モック利用）。
2. タスク2 (マッピング修正) の実装と結合テスト。
3. タスク3 (代替クエリ) の修正と E2E 確認。
4. タスク4 (表示仕様確認) の実施。
