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
