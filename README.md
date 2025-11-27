# MCP Workspace Finder (PoC)

Google Drive、Slack、GitHub を Model Context Protocol (MCP) 経由で横断検索する CLI の PoC。コンテナは使わずローカルだけで動作し、Typer/Rich ベースの REPL と非対話コマンドの両方を備えています。

## 前提条件
- ホストに Python 3.10 以上がインストールされていること（例: `pyenv` の 3.10 系）。
- Poetry が導入されていること（`pipx install poetry` など）。
- ローカル環境のみを想定。Docker/コンテナはサポートしません。

## 初期セットアップ
1. このリポジトリを clone し、ディレクトリに移動する。
2. Poetry で Python 3.10+ を使うよう設定する（例: `poetry env use python3.10`）。
3. 依存関係をインストール: `poetry install`。
4. 環境ファイルを作成: `cp .env.example .env`。少なくとも `OPENAI_API_KEY`, `SLACK_USER_TOKEN`, `GITHUB_TOKEN`, `DRIVE_TOKEN_PATH`（OAuth トークンファイルへのパス）を記入。秘密情報はローカルに保持し、`.env` と `token.json` は gitignore 済み。
5. トークンや認証ファイルのパスがデフォルトと異なる場合、またはモックモードを使う場合は `servers.yaml` で MCP サーバーのエンドポイントを調整する。

## 実環境用の通信情報の取得手順
`ALLOW_REAL=1` かつ `--mock` 未指定で最小スモーク（検索 API が成功するかの手動確認）を行うために、各サービスと通信できる実トークン/認証情報を準備します。取得後は `.env` と `servers.yaml` に反映し、ファイルはローカルのみで管理してください。

- **Slack (User Token / xoxp-)**
  1. https://api.slack.com/apps で新規アプリを作成（From scratch で可）。
  2. *OAuth & Permissions* → *User Token Scopes* に `search:read`, `channels:history`, `groups:history`, `im:history`, `users:read` を追加。
  3. *Install to Workspace* して表示される User OAuth Token（`xoxp-` で始まる）をコピーし、`.env` に `SLACK_USER_TOKEN=<xoxp-...>` として保存。
  4. 任意: `curl -H "Authorization: Bearer $SLACK_USER_TOKEN" "https://slack.com/api/search.messages?query=smoke"` で 200/`ok:true` を確認すると通信準備完了。

- **GitHub (PAT / repo 読取のみ)**
  1. GitHub → *Settings* → *Developer settings* → *Personal access tokens* でトークンを作成。
     - Fine-grained の場合: 対象 org/repo を限定し、Repository permissions を *Contents: Read-only*, *Metadata: Read-only*, *Issues/Pull requests: Read-only* 程度に設定。
     - Classic の場合: `repo` スコープを付与（その他は不要）。
  2. 発行されたトークン（`ghp_` または `github_pat_` で始まる）を `.env` の `GITHUB_TOKEN` に保存。

- **Google Drive (OAuth token.json)**
  1. Google Cloud Console でプロジェクトを作成し、OAuth 同意画面を設定（Internal/External いずれか）。
  2. 「API とサービス」→「ライブラリ」で *Google Drive API* を検索して有効化する。
  3. *認証情報* から OAuth クライアント ID（アプリケーションの種類: デスクトップ）を発行し、ダウンロードしたクライアントシークレットを `secrets/credentials.json` に保存。
  4. `.env` に `GOOGLE_CREDENTIALS_PATH=secrets/credentials.json`、`DRIVE_TOKEN_PATH=secrets/token.json` を設定し、`servers.yaml` で同じパスを参照させる。
  5. 初回のみブラウザでの同意フローを実施して `token.json` を生成する。`modelcontextprotocol` の PyPI 版には gdrive サーバーは含まれないため、公式 Node 版 `@modelcontextprotocol/server-gdrive` を使う。例: `GDRIVE_OAUTH_PATH=$GOOGLE_CREDENTIALS_PATH GDRIVE_CREDENTIALS_PATH=$DRIVE_TOKEN_PATH npx -y @modelcontextprotocol/server-gdrive auth` を実行し、表示された URL で認可すると `secrets/token.json` が保存される。
  6. スモーク用に検索可能な検証フォルダ（アクセス権付与済み）を 1 つ決めておく。

## CLI の使い方
- REPL を起動（TTY）: `poetry run python -m app`  
  - 実キーがない場合は警告のうえ自動でモックにフォールバックします。
- ヘルプ / 非対話モード: `poetry run python -m app --help`。
- モックを強制する: `poetry run python -m app --mock`。
- 実 API 呼び出しを許可する: 環境変数 `ALLOW_REAL=1` を設定（`--mock` 指定時はモックが優先）。

## ノート
- トークン／認証情報はローカルに保持し、`.env`、`token.json`、`secrets/` 配下はコミットしないこと。
- Docker/コンテナは使用せず、Poetry が作成する仮想環境（`.venv` または `.poetry-env`）を利用してください。
- 実鍵なしで CI 風のチェックを走らせる場合は `poetry run pytest -m mock`。実モードのテストは `ALLOW_REAL=1` と有効なクレデンシャルが必要です。
