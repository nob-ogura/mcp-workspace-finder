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
4. 環境ファイルを作成: `cp .env.example .env`。少なくとも `OPENAI_API_KEY`, `SLACK_USER_TOKEN`, `GITHUB_TOKEN`, `DRIVE_TOKEN_PATH`（OAuth トークンファイルへのパス）を記入。秘密情報はローカルに保持し、`.env` と `token.json` は gitignore 済み。`.env` は **ファイルが存在** し、`--mock` 未指定、`ALLOW_REAL=1`、`servers.yaml` で `mode: real` が含まれる場合にだけ起動時に自動読み込みされます。
5. MCP サーバー（Slack/GitHub/Drive）の導入と `servers.yaml` の設定を行う（詳細は「MCP サーバーのインストール」を参照）。トークンや認証ファイルのパスがデフォルトと異なる場合も `servers.yaml` を調整する。

## MCP サーバーのインストール
CLI は各サービスごとに独立した MCP サーバーを子プロセスとして起動します。以下のいずれかの方法でサーバーを用意し、`servers.yaml` でパスを指し示してください。

### Slack MCP Server (Go 製)
1. Go 1.21 以上を用意。未導入なら macOS では `brew install go`、Ubuntu では `sudo apt-get install golang` などで入れる。`go version` で 1.21 以上を確認し、必要なら `export PATH="$(go env GOPATH)/bin:$PATH"` を `~/.zshrc` などに追記して `~/go/bin` を PATH に通す。
2. インストール: `go install github.com/korotovsky/slack-mcp-server/cmd/slack-mcp-server@latest`  
3. バイナリの場所を確認: `$(go env GOPATH)/bin/slack-mcp-server`（通常は `~/go/bin`）。GitHub Releases から取得した場合は配置したバイナリの絶対パスを控える。
4. `servers.yaml` の `slack.exec` に上記パスを設定する。

### GitHub MCP Server (TypeScript 製)
1. Node.js 18+ を用意。
2. 推奨: npx で最新を実行  
   `npx -y @modelcontextprotocol/server-github`  
   - `pip install github-mcp-server` のような PyPI パッケージは存在しないため、npm 経由で取得してください。
3. 定期利用で高速起動したい場合はグローバルインストール  
   `npm install -g @modelcontextprotocol/server-github` で `mcp-server-github` が入るので、`servers.yaml` の `github.exec` に `mcp-server-github`（または絶対パス）を指定する。
4. 認証: PAT を `GITHUB_TOKEN` として環境変数に渡す（この CLI も `GITHUB_TOKEN` を参照します）。`servers.yaml` で `env:` に設定する。
5. 例: `servers.yaml` で npx を使う場合  
   ```yaml
   github:
     exec: npx
     args:
       - -y
       - "@modelcontextprotocol/server-github"
     env:
       GITHUB_TOKEN: ${GITHUB_TOKEN}
   ```

### Google Drive MCP Server (Node 製)
1. Node.js 18+ を用意。
2. 実行方法は 2 通り:
   - グローバルインストール: `npm install -g @modelcontextprotocol/server-gdrive` の後、`servers.yaml` で `exec: server-gdrive` とする。
   - npx 実行（推奨・更新追従が容易）: `servers.yaml` を下記のように設定する。
     ```yaml
     exec: npx
     args:
       - -y
       - "@modelcontextprotocol/server-gdrive"
     ```
3. 初回起動前に OAuth フローで `token.json` を作成する必要があります。`GDRIVE_OAUTH_PATH=$GOOGLE_CREDENTIALS_PATH GDRIVE_CREDENTIALS_PATH=$DRIVE_TOKEN_PATH npx -y @modelcontextprotocol/server-gdrive auth` を実行し、ブラウザで許可後に保存された `secrets/token.json` を `DRIVE_TOKEN_PATH` として参照します。

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
- 実 API 呼び出しを許可する: 環境変数 `ALLOW_REAL=1` を設定（`--mock` 指定時はモックが優先）。この状態で `servers.yaml` に `mode: real` が含まれ、`.env` が存在すれば起動時に `.env` を読み込み、環境変数を補完します。それ以外のケースでは `.env` は読み込みません。

### フェーズ3: 実環境スモーク（受入基準）手順まとめ
起動時に Slack/GitHub/Drive が real になり、ログに 1 回だけ `real smoke enabled` が出ることを確認するための手順です。

1) 資材をそろえる（全サービス共通）
- `.env` に `OPENAI_API_KEY`, `SLACK_USER_TOKEN` (xoxp-), `GITHUB_TOKEN` (PAT), `GOOGLE_CREDENTIALS_PATH`, `DRIVE_TOKEN_PATH` を設定し、参照先のファイルが読み取り可能であること。
- `servers.yaml` で各サービスの `mode` を `real` のままにする（デフォルトのままで OK）。

2) Slack/GitHub の auth_files を満たす
- `servers.yaml` の `auth_files` で `secrets/slack_token.json`, `secrets/github_token.json` の存在が必須。現行サーバーは中身を使わないため、空ファイルで通過可能。
  ```sh
  mkdir -p secrets
  touch secrets/slack_token.json secrets/github_token.json
  chmod 600 secrets/slack_token.json secrets/github_token.json
  ```
- サーバー実装がキャッシュを書き出す場合は、上記空ファイルを置いた状態で `ALLOW_REAL=1 poetry run python -m app`（またはサーバーバイナリ単体）を一度実行すれば、`secrets/*.json` が実データで上書きされ、以降再認可なしで再利用できます。

3) Drive 実サーバーの準備
- `servers.yaml` に下記のように記述。
    ```yaml
    exec: npx
    args:
      - -y
      - "@modelcontextprotocol/server-gdrive"
    ```

4) 実行
- `ALLOW_REAL=1 poetry run python -m app`（`--mock` は付けない）。

5) 確認
- 起動ログに `real smoke enabled` が 1 回だけ出る。
- モード表示が `slack=real, github=real, drive=real` になっている。いずれか欠損すると自動で mock へフォールバックし、受入基準を満たさない。

6) トラブルシュート
- Slack/GitHub が mock になる: `secrets/slack_token.json`, `secrets/github_token.json` の存在・パーミッションを確認。空ファイルで可。
- Drive が timeout する: 事前インストールするか、`READINESS_TIMEOUT` を一時的に延長して初回ダウンロードを待つ。

### 実環境検索スモークの実行方法（タスク3）
実鍵で 3 サービスの検索 Tool を 1 回ずつ叩くスモークを CLI で実行できます。Slack では DM/Private を 1 件以上含むヒットが必須です。

1. 前提を満たす  
   - `ALLOW_REAL=1` を設定し、`--mock` は付けない。  
   - `.env` と `servers.yaml` が実鍵モードになるよう整備されていること。  
   - GitHub で検索対象となるリポジトリを `GITHUB_SMOKE_REPO=owner/repo` で指定する。  
   - 任意: 検索キーワードを変えたい場合は `SLACK_SMOKE_QUERY` / `GITHUB_SMOKE_QUERY` / `DRIVE_SMOKE_QUERY` を設定する。
2. 実行  
   - 直接実行: `poetry run python -m app smoke --report reports/smoke.json`  
   - またはラッパー: `ALLOW_REAL=1 scripts/smoke_real.sh --report reports/smoke.json`
3. 結果確認  
   - 成功時は `real smoke passed` が表示され、レポートに各サービスの `status: ok` と Slack の `dm_hit: true` が残ります。  
   - 失敗時は `real smoke failed` とともに理由が表示され、レポートに失敗理由が記録されます。結果ファイルは日付付きで JSON に保存するので、そのまま共有に利用できます。

## ノート
- トークン／認証情報はローカルに保持し、`.env`、`token.json`、`secrets/` 配下はコミットしないこと。
- Docker/コンテナは使用せず、Poetry が作成する仮想環境（`.venv` または `.poetry-env`）を利用してください。
- 実鍵なしで CI 風のチェックを走らせる場合は `poetry run pytest -m mock`。実モードのテストは `ALLOW_REAL=1` と有効なクレデンシャルが必要です。
