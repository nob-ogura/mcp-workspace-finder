# 社内情報横断検索システム PoC 設計

## 1. 目的と範囲
- 目的: Google Drive / Slack / GitHub の社内情報を MCP 経由でリアルタイム検索し、LLM による要約と根拠リンク提示を CLI で実現する PoC を構築する。
- 範囲: 要件定義で示された 3 サービスのみを対象とし、ベクトル検索やバッチ収集は行わない。CLI は Typer + Rich ベースで、TTY では REPL、非対話入力時はワンショット実行で即終了する自動モードを提供する。実行モードはデフォルトで実鍵を利用し、`--mock` を明示した場合のみモックに切り替える（鍵欠如時は警告して失敗）。

## 2. 全体アーキテクチャ
- ホスト CLI (Python 3.10+, Typer/Rich, asyncio) が単一プロセスで以下を制御する:
  - MCP サーバー 3 種の起動・監視 (Stdio 接続)。
  - LLM へのクエリ生成・要約指示。
  - 各サービス検索 Tool の並列実行と本文取得 Tool の並列実行。
  - 進行状況表示と結果レンダリング。
- LLM: OpenAI `gpt-4o mini` を function calling/JSON mode で利用し、サービス別検索クエリ生成と統合要約を行う。
- MCP サーバー:
  - Drive: `modelcontextprotocol/servers/gdrive` (Python)。
  - GitHub: `github/github-mcp-server` (Python)。
  - Slack: `korotovsky/slack-mcp-server` (Go)。
- 通信: すべて Stdio、HTTP/SSE ラッパーは使用しない。

## 3. コンポーネント設計
- **CLI/Host (Python)**
  - Typer ベースの単一エントリポイント。TTY かつ `--query` 無指定時は REPL に入り、`--query` 指定または stdin パイプ入力がある場合は単発実行して結果を表示後に終了。
  - Rich によるプログレス表示と Markdown レンダリング。
  - REPL 時のみ内部コマンド（`quit` / `reload` / `help` / `debug on|off` / `logpath` 等）を受け付ける。
  - サービス別の検索クエリ、代替クエリ、根拠リンクセクションを描画。
  - サーバープロセス管理 (起動・死活監視・再起動)。
  - 並列実行: `asyncio.gather` で検索/取得を非同期化。上限 `max_results=3` / サービス。
- **LLM 層**
  - プロンプト: システムに検索構文ガイド + リソーステンプレートを注入。
  - 出力: Slack/GitHub/Drive 用の検索パラメータを JSON で一括生成。0 件時の代替クエリ候補も生成。
  - 要約: 取得済み本文とスニペットを統合し、Markdown で要約 + 根拠付き番号参照を返す。
- **サーバー接続層**
  - サーバーごとに「検索 Tool → 本文取得 Tool」対応マップを保持。
  - GitHub: 検索結果が Issue/PR の場合は `get_issue` 等を使用。ファイル URI は `read_resource`。
  - Slack: `search.messages` で得た permalink を `get_message` / `get_thread` に渡す。
  - Drive: `search` のファイル URI を `read_resource` で取得。
- **設定**
  - `servers.yaml` (想定): サーバー実行パス、起動コマンド、認証ファイルパス、並列上限などを記述。`mode: real|mock` を持ち、通常は `real` を既定とする。
  - `.env` (ローカルのみ): OpenAI API Key、Slack User Token など。Git 無視設定を前提。鍵が無い場合は起動時に警告を出して失敗とし、モックへの自動フォールバックは行わない。

## 4. フロー設計
### 4.1 起動フロー
1) CLI 起動時に `servers.yaml` を読み込み。  
2) 各サーバーをサブプロセスで起動し、Stdio 接続を確立。死活監視タスクを開始。  
3) 認証ファイルの存在確認。未存在の場合は警告を出して終了する（PoC 方針として自動フォールバックはしない）。  
4) TTY で `--query` が無い場合は REPL 受付を開始。`--query` 指定または stdin パイプ入力がある場合は単発処理を実行し、結果表示後にプロセスを終了。

### 4.2 質問処理フロー
1) ユーザー質問を受領し、進行表示「Preparing query...」。  
2) LLM に質問 + サービス検索構文ガイドを渡し、Slack/GitHub/Drive 用クエリ JSON を生成。  
3) 非同期で各サービスの検索 Tool を実行（上位 3 件まで）。進行表示「Searching Slack...」などを並列更新。  
4) 検索結果を解析し、種別ごとに本文取得 Tool を決定。非同期で取得。取得不可はスニペットのみ。  
5) 取得結果を LLM に渡し統合要約を生成。  
6) Rich で本文を Markdown 描画し、直下に根拠リンク（サービス別 1〜3 件、合計 ≤9）を番号付きで表示。  
7) 0 件の場合は即時終了し、代替クエリ候補を表示。再検索は行わない。

### 4.3 エラーハンドリング
- サービス単位でフェイルソフト。エラー時はそのサービスをスキップし警告表示、他サービス結果を返す。  
- 429 検知で該当サービスをスキップし、警告を根拠欄付近に表示。  
- LLM/サーバー例外は REPL を落とさず、再入力を促す。  
- サーバープロセス死亡時は自動再起動を試行し、失敗時にメッセージを表示。

## 5. データモデル/インタフェース
- **検索クエリ構造 (例)**
  - Slack: `{type: "slack_search", query: "... OR ...", channel?: "...", before?: "...", after?: "..."}`  
  - GitHub: `{type: "github_search", query: "repo:org/repo ...", scopes: {issues: true, code: true}}`  
  - Drive: `{type: "gdrive_search", query: "\"keyword\" OR synonym", mime_filters?: [...]}`  
- **検索結果共通形式**
  - `{service, kind (file|issue|pr|message), title, snippet, uri, fetch_tool, fetch_params}`  
- **取得結果**
  - `{service, kind, uri, content (text), meta (timestamp, authorship)}`  
- LLM 入出力は JSON Schema で固定し、function calling で厳密化する。

## 6. セキュリティ・権限
- 認証情報はローカル保存のみ。`gitignore` で除外し、パスは設定ファイルに明示。  
- Slack は User Token (xoxp-) 前提。Private/DM も検索対象になることを UI に明示。
- GitHub は個人の PAT（classic or fine-grained）を PoC でも実際に使用し、最小読取スコープで org/repo へのアクセスを行う。
- Google Drive は実ユーザーの OAuth トークン (`token.json`) を PoC でも使用し、実ファイルへの検索/取得で検証する。
- Drive/GitHub も利用者本人権限のみ。越権操作は行わない。
- DM/Private 送信時の自動マスキングは未実装であることを CLI ヘルプと README に明記。

## 7. 非機能設計
- **性能/並列度**: 検索・取得ともにサービスごと最大 3 件。`asyncio` で I/O 並列。  
- **可用性**: TTY 時は REPL 常駐、非対話入力時はワンショットで終了。サーバー死活監視と再起動ロジックを実装。  
- **運用**: コンテナ禁止。ローカル venv + Go バイナリのみでセットアップ。  
- **拡張性**: MCP サーバー追加は `servers.yaml` 追記で可。構文ガイドをリソーステンプレート経由で LLM に注入。

## 8. ロギングと可観測性
- 進行状況を Rich でリアルタイム表示。  
- デバッグログは `--debug` オプションで JSON Lines 出力 (クエリ、レスポンス、エラー)。本文・クエリは平文のまま残し、トークン等の明白なシークレットのみ伏字化する。  
- 検索クエリと取得 URI をログし、回答の根拠追跡を容易にする。

## 9. 受入基準対応
- AC-01: 並列検索/取得と要約で 3×3 件以内の結果と根拠を表示。  
- AC-02: LLM が代替クエリを生成し、0 件時に表示する実装をフロー 4.2 に組み込み。  
- AC-03: 認証ファイルをローカル保存し、起動時に再利用。存在チェックとウィザードで担保。  
- AC-04: Slack User Token を利用し Private/DM 検索をサポート。  
- AC-05: 進行状況・検索クエリ・根拠リンクを Rich で表示。

## 10. リスクと対策
- **トークン漏洩**: `.env` / 認証ファイルを gitignore、CLI で注意喚起。  
- **レートリミット**: 固定 `max_results=3`、429 時スキップ＋警告。  
- **ノイズ混入**: Rerank 不実施を明示し、LLM 要約で圧縮する。  
- **機微情報表示**: DM/Private のマスキング未実装をヘルプに明記、PoC 運用で制御。
