# フェーズ1: 環境とプロジェクト基盤 — タスク一覧

## タスク1: Poetry 環境と依存管理の整備
- Python 3.10+ を前提に `pyproject.toml` と `poetry.lock` を整備し、CLI 実行に必要な最小依存（Typer, Rich, asyncio 周辺, mcp クライアント, pytest など）を定義する。開発コマンドは `poetry run` 前提で統一する。

### 受入基準 (Gherkin)
```
シナリオ: クリーン環境で依存を再現できる
  前提: リポジトリをクローンし Python 3.10 系がインストールされている
  かつ: `poetry.lock` がコミットされている
  もし: `poetry install` を実行する
  ならば: 依存解決がエラーなく完了し exit code が 0 になる
  かつ: `poetry run python -m app --help` が 0 で終了する
```

## タスク2: CLI エントリポイント骨格と REPL 最小ルート
- Typer ベースの `python -m app` エントリポイントを用意し、TTY では REPL プロンプトが表示され、非対話入力ではヘルプ/ワンショット実行に遷移する最小実装を用意する。Rich を使った簡易進行表示プレースホルダも配置する。

### 受入基準 (Gherkin)
```
シナリオ: 鍵がなくても REPL が起動できる
  前提: `.env` に必須鍵が未設定の状態
  もし: `poetry run python -m app` を TTY で実行する
  ならば: 起動時に鍵不足の警告が表示される
  かつ: REPL プロンプトが表示されたまま終了しない
```
```
シナリオ: 非対話モードでヘルプが確認できる
  前提: 端末が非 TTY もしくは `--help` を指定する
  もし: `poetry run python -m app --help` を実行する
  ならば: コマンドオプション一覧が表示され exit code が 0 になる
```

## タスク3: 実鍵/モックモード切替と警告フローの実装
- 実行モード優先度を `--mock` > `ALLOW_REAL=1` > それ以外はモックとし、鍵欠如時は自動でモックへフォールバックする警告メッセージを実装する。現在のモードを起動ログに明示する。

### 受入基準 (Gherkin)
```
シナリオ: 鍵欠如時に自動モックへフォールバックする
  前提: `.env` が存在しないか必須鍵が未設定である
  もし: `poetry run python -m app` を実行する
  ならば: 「鍵不足によりモックへフォールバック」旨の警告が一度表示される
  かつ: プロセスは異常終了せず起動モードが mock であることを表示する
```
```
シナリオ: --mock 指定が最優先される
  前提: `ALLOW_REAL=1` が設定されている
  もし: `poetry run python -m app --mock` を実行する
  ならば: mock モードで起動する旨が表示される
  かつ: 実鍵を検査しなくてもプロセスが起動する
```

## タスク4: 設定テンプレートの配置 (.env.example と servers.yaml)
- `./.env.example` に OpenAI / Slack xoxp / GitHub PAT / Drive token パスなど必要キーを列挙し説明コメントを付ける。`./servers.yaml`（またはテンプレートファイル）に Slack / GitHub / Drive 向けの起動コマンド・モード・認証ファイルパスのプレースホルダを記載する。

### 受入基準 (Gherkin)
```
シナリオ: テンプレートが存在し必須項目が列挙されている
  前提: リポジトリ直下に `.env.example` と `servers.yaml` テンプレートが配置されている
  かつ: `.env.example` に OPENAI_API_KEY, SLACK_USER_TOKEN, GITHUB_TOKEN, DRIVE_TOKEN_PATH が含まれる
  かつ: `servers.yaml` に Slack/GitHub/Drive 3 サービス分の項目がある
  ならば: 新規開発者がテンプレートをコピーするだけで必要項目を把握できる
```

## タスク5: README と .gitignore の基盤更新
- `docs/Requirements.md` の前提（Python 3.10+, poetry, コンテナ禁止など）を README に反映し、初回セットアップ手順を記述する。`.gitignore` に `.env`, `token.json`, `secrets/`, `.venv` など秘密情報・生成物を追加する。

### 受入基準 (Gherkin)
```
シナリオ: セットアップ手順が README から追従できる
  前提: 新規開発者が README だけを参照する
  もし: README の手順通りに Python 3.10+ と poetry を準備しコマンドを実行する
  ならば: 依存インストールから `python -m app` 起動まで完了し警告以外のエラーが出ない
```
```
シナリオ: 秘密情報が誤コミットされない
  前提: `.gitignore` が最新化されている
  もし: `git status` を確認する
  ならば: `.env`, `token.json`, `secrets/` 配下、`.venv` が追跡対象に含まれない
```
