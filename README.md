# event-watcher

RSS フィードを定期的に収集し、`config/targets.yaml` で指定した監視対象（作品・人物・ブランド・店舗など）のキーワードに一致した記事だけを SQLite で重複排除したうえで Discord Webhook に通知する MVP です。

## 1. プロジェクト概要

- 複数の RSS/Atom フィードを巡回し、記事（タイトル・要約）を取得します。
- YAML で定義したキーワードに部分一致した記事だけを「監視対象に一致した」とみなします。
- 一致した記事は SQLite に保存し、URL / 内容ハッシュで重複を排除します（同じ記事を二度通知しません）。
- 新規に保存できた記事だけを Discord Webhook に通知します。
- GitHub Actions 上で 6 時間ごとに自動実行できます。

## 2. アーキテクチャ

### ディレクトリツリー

```
event-watcher/
├── .github/
│   └── workflows/
│       └── watcher.yml     # GitHub Actions（定期実行 + 手動実行）
├── config/
│   └── targets.yaml        # 収集元フィード・監視対象・実行設定
├── data/
│   └── events.db           # SQLite ファイル（.gitignore 対象、実行時に自動生成）
├── src/
│   ├── main.py             # フローの組み立てとエントリポイント
│   ├── config.py           # targets.yaml の読み込み・検証
│   ├── models.py           # Article / Event などの共有ドメインモデル
│   ├── logging_config.py   # ログ設定
│   ├── collectors/
│   │   ├── base.py         # Collector の抽象基底クラス
│   │   └── rss.py          # RSS/Atom 収集の実装
│   ├── filters/
│   │   ├── base.py         # RelevanceFilter の抽象基底クラス
│   │   └── keyword.py      # キーワード部分一致による判定
│   ├── notifiers/
│   │   ├── base.py         # Notifier の抽象基底クラス
│   │   └── discord.py      # Discord Webhook 通知の実装
│   └── repositories/
│       └── sqlite.py       # SQLite への保存・取得
├── tests/                  # pytest テストコード
├── pytest.ini              # pytest 設定（testpaths / pythonpath）
├── requirements.txt
└── .gitignore
```

### 責務表

| モジュール | 責務 |
| --- | --- |
| Collector（`src/collectors/`） | 取得のみ。RSS を取得して `Article` のリストを返す。判定・保存・通知は行わない。取得失敗時は例外を投げず空リストを返す。 |
| Filter（`src/filters/`） | 判定のみ。`Article` が監視対象に一致するか判定し、一致した対象名のリストを返す。 |
| Repository（`src/repositories/`） | 保存取得のみ。SQLite への `Event` の保存・重複確認・取得を行う。判定や通知のロジックは持たない。 |
| Notifier（`src/notifiers/`） | 通知のみ。`Event` を Discord Webhook に送信する。保存や重複判定は行わない。 |
| `src/main.py` | フロー。上記の各コンポーネントを組み立て、収集→判定→重複排除→保存→通知の流れを制御する。個々の失敗でループ全体を止めない。 |

### 処理フロー

```
収集（RSSCollector） → キーワード判定（KeywordFilter） → 重複排除（SQLiteEventRepository.exists）
    → SQLite保存（SQLiteEventRepository.save） → Discord通知（DiscordNotifier）
```

各段階の失敗は個別に catch してログに残し、他のフィード・記事の処理は継続します（`src/main.py` の `_collect_safely` / `_save_safely` など）。

## 3. 必要環境

- Python 3.12
- 依存パッケージは `requirements.txt` に固定（`feedparser`, `requests`, `PyYAML`, `pytest`）

## 4. ローカルセットアップ

### Windows PowerShell

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### macOS / Linux

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## 5. targets.yaml の設定方法

設定ファイルは `config/targets.yaml`（`--config` で別のパスも指定可）。トップレベルに `feeds` / `targets` / `settings` の 3 キーを持ちます。

### feeds（収集元 RSS/Atom フィード）

| キー | 必須 | 説明 |
| --- | --- | --- |
| `name` | 任意 | フィードの識別名（`Event.source` に入る）。省略時は `url` がそのまま使われる。 |
| `url` | 必須 | RSS/Atom フィードの URL。 |

### targets（監視対象）

| キー | 必須 | 説明 |
| --- | --- | --- |
| `name` | 必須 | 監視対象の名前（通知の「一致した対象」欄に表示される）。 |
| `keywords` | 必須（1 件以上） | タイトル＋要約に対する部分一致（大文字小文字は無視）で判定するキーワードのリスト。 |

### settings（実行時の挙動、省略可）

| キー | 必須 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `request_timeout_seconds` | 任意 | `10` | RSS 取得・Discord 送信の HTTP タイムアウト（秒）。 |
| `max_notifications_per_run` | 任意 | `20` | 1 回の実行で送る通知件数の上限。超過分は保存のみ行い通知しない。 |
| `db_path` | 任意 | `data/events.db` | SQLite ファイルのパス（リポジトリルートからの相対パス）。 |

### 監視対象を追加する例

```yaml
targets:
  - name: Pokemon
    keywords:
      - ポケモン
      - Pokemon
      - ピカチュウ

  - name: My New Target
    keywords:
      - キーワードA
      - keywordB
```

## 6. Discord Webhook の設定方法

1. Discord サーバーの「サーバー設定」→「連携サービス」→「ウェブフック」を開く。
2. 「新しいウェブフック」を作成し、通知させたいチャンネルを選択する。
3. 「ウェブフック URL をコピー」で URL を取得する。
4. その URL を環境変数 `DISCORD_WEBHOOK_URL` に設定する（**URL は絶対にコードや設定ファイル、README にコミットしないこと**）。

### ローカルでの設定例

PowerShell:

```powershell
$env:DISCORD_WEBHOOK_URL = "取得したWebhook URLをここに貼り付け"
```

bash:

```bash
export DISCORD_WEBHOOK_URL="取得したWebhook URLをここに貼り付け"
```

## 7. ローカル実行方法

```bash
python -m src.main
```

### オプション

| オプション | 説明 |
| --- | --- |
| `--dry-run` | Discord へ通知せず、収集と SQLite への保存だけ行う。 |
| `--config` | 設定 YAML のパス（デフォルト: `config/targets.yaml`）。 |
| `--db` | SQLite ファイルのパス（指定すると `targets.yaml` の `db_path` より優先される）。 |
| `--log-level` | ログレベル（`DEBUG` / `INFO` / `WARNING` / `ERROR`、デフォルト: `INFO`）。 |

初回実行時は、意図しない大量通知を避けるため `--dry-run` での動作確認を推奨します。

```bash
python -m src.main --dry-run
```

### 終了コード

| コード | 意味 |
| --- | --- |
| `0` | 正常終了。 |
| `2` | 致命的エラー（設定ファイルの読み込み失敗、DB 初期化失敗、Discord Notifier の初期化失敗など）で処理を開始できなかった。 |

なお、個々のフィード取得・記事判定・通知の失敗は致命的エラーとして扱わず、ログに記録したうえで処理を継続し、終了コードは `0` のままです。

## 8. テスト方法

```bash
python -m pytest -q
```

## 9. GitHub Actions の設定方法

`.github/workflows/watcher.yml` に、6 時間ごとの定期実行（`schedule: cron: "0 */6 * * *"`）と、手動実行（`workflow_dispatch`）が設定済みです。

- 手動実行する場合は GitHub リポジトリの「Actions」タブ →「event-watcher」ワークフロー →「Run workflow」から実行します。
- 手動実行時は `dry_run`（真偽値、デフォルト `false`）を入力できます。`true` にすると Discord へ通知せず収集・保存のみ行います。

**重要**: この `.github/` ディレクトリを GitHub Actions に認識させるには、`event-watcher` ディレクトリ自体をリポジトリのルートにする必要があります（`event-watcher` が別リポジトリのサブディレクトリになっている状態では、GitHub Actions はワークフローを検出しません）。

## 10. GitHub Secrets の設定方法

1. GitHub リポジトリの「Settings」→「Secrets and variables」→「Actions」を開く。
2. 「New repository secret」をクリックする。
3. Name に `DISCORD_WEBHOOK_URL`、Value に取得した Webhook URL を入力して保存する。

## 11. 既知の制約 / 注意点

- **SQLite の永続化について**: GitHub Actions のランナーは実行のたびに破棄されるため、重複判定用の SQLite（`data/events.db`）は `actions/cache` を使って実行間で引き継いでいます。キャッシュが失効・削除された場合は重複判定の履歴が失われ、過去に通知済みの記事が再通知される可能性があります。恒久的な運用では、DB ファイルをリポジトリにコミットする方式や、外部データベース（例: マネージドな SQL/KVS サービス）への移行を検討してください。
- **通知順序について**: 現在の実装は「保存してから通知する」順序になっているため、保存が成功した後に Discord への通知が失敗した記事は、保存済み（重複判定の対象）として扱われ、以降のスキップ対象となり再通知されません。
- **キーワード判定の精度について**: `KeywordFilter` は部分一致による判定であり、形態素解析は行っていません。日本語のキーワードは部分一致で概ね機能しますが、英単語（例: `Pokemon`）は無関係な文脈でも一致してしまう（誤検知する）可能性があります。
- **初回実行時の通知量について**: 初回実行時は SQLite に何も保存されていないため、既存のフィード内の記事が大量に一致・通知され得ます。これを防ぐため `settings.max_notifications_per_run`（デフォルト 20 件）で 1 回あたりの通知件数を制限しています。上限を超えた一致記事は保存のみ行われ、通知はされません。

## 12. 今後の拡張候補

- LLM による関連性判定の追加: `src/filters/base.py` の `RelevanceFilter` を実装した `LLMRelevanceFilter`（仮）を追加し、`main.py` 側で `KeywordFilter` と差し替える。
- RSS 以外の Collector の追加: `src/collectors/base.py` の `Collector` を実装し、API 収集やスクレイピングなど別の情報源に対応する。
- Discord 以外の Notifier の追加: `src/notifiers/base.py` の `Notifier` を実装し、Slack やメールなど別の通知先に対応する。
- 通知済みフラグ列の追加: 現在は「保存済み＝通知不要」という前提だが、`events` テーブルに通知成否を持つ列を追加すれば、通知失敗時の再送が可能になる。
- 外部データベースへの移行: SQLite から外部 DB へ移行し、GitHub Actions のキャッシュ失効に依存しない永続化を実現する。
