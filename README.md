# event-watcher

RSS フィードを定期的に収集し、`config/targets.yaml` で指定した監視対象（作品・人物・ブランド・店舗など）のキーワードに一致した記事だけを SQLite で重複排除したうえで Discord Webhook に通知する MVP です。

## 1. プロジェクト概要

- 複数の RSS/Atom フィードを巡回し、記事（タイトル・要約）を取得します。
- YAML で定義したキーワードに部分一致した記事だけを「監視対象に一致した」とみなします。
- 一致した記事は SQLite に保存し、URL / 内容ハッシュで重複を排除します（同じ記事を二度通知しません）。
- 新規に保存できた記事だけを、公開日時の古い順に Discord Webhook へ通知します。通知には記事タイトル・要約・公開日時を載せます（要約はフィードが持っている場合のみ）。
- 通知は 10 件ずつ 1 メッセージにまとめて送り、レート制限（429）は `Retry-After` に従って待ってから再送します。
- 通知の成否は `notifications` テーブルに記録され、**送れなかった記事は次回実行で自動的に再送されます**（上限 `settings.max_retry_count` 回）。
- GitHub Actions 上で 6 時間ごとに自動実行できます。

現在の `config/targets.yaml` は「ポケモンのコラボ情報」を追う設定になっています（[5. targets.yaml の設定方法](#5-targetsyaml-の設定方法) 参照）。

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
│   │   ├── base.py             # RelevanceFilter の抽象基底クラス
│   │   ├── keyword.py          # キーワード部分一致による判定
│   │   └── feed_requirement.py # フィードごとの必須キーワード（AND）で前段絞り込み
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
| Filter（`src/filters/`） | 判定のみ。`Article` が監視対象に一致するか判定し、一致した対象名のリストを返す。`FeedRequirementFilter` は `KeywordFilter` を包み、フィードごとの必須キーワードを前段で適用する。 |
| Repository（`src/repositories/`） | 保存取得のみ。`EventRepository` が「何が起きたか」（`events`）、`NotificationRepository` が「通知できたか」（`notifications`）を担当する。判定や通知のロジックは持たない。 |
| Notifier（`src/notifiers/`） | 通知のみ。`Event` を Discord Webhook に送信する（`notify_batch` で 10 件ずつまとめ送信）。保存や重複判定は行わない。 |
| `src/main.py` | フロー。上記の各コンポーネントを組み立て、収集→判定→重複排除→保存→通知の流れを制御する。個々の失敗でループ全体を止めない。 |

### 処理フロー

```
収集（RSSCollector） → キーワード判定（KeywordFilter） → 重複排除（SQLiteEventRepository.exists）
    → SQLite保存（events） → 未通知として記録（notifications: pending）
    → まとめてDiscord通知（DiscordNotifier.notify_batch） → 結果を記録（notifications: sent / failed）
```

通知は記事 1 件ごとには送らず、全フィードの収集・保存が終わってから最後にまとめて送ります
（1 件 1 リクエストだと過去分の一括通知でレート制限に掛かるため）。

送信対象は「今回保存したもの」だけではありません。実行のたびに `notifications` テーブルから
**未通知（pending）と、再送上限に達していない失敗（failed）** を拾い直して送ります。そのため

- 通知に失敗した記事は、次回実行で自動的に再送される
- `max_notifications_per_run` を超えて積み残した記事も、次回以降に順次送られる（取りこぼさない）

という挙動になります。`events`（何が起きたか）と `notifications`（通知できたか）は
テーブルも Repository も分かれており、`content_hash` で紐づいています。

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
| `require_any` | 任意 | このフィードから拾う記事に必須のキーワード（1 つでも含めば通す）。`targets` の判定（OR）に対して **AND 条件**として働く。一般ニュースのフィードから特定ジャンルだけを拾うために使う。省略時は絞り込まない。 |

### targets（監視対象）

| キー | 必須 | 説明 |
| --- | --- | --- |
| `name` | 必須 | 監視対象の名前（通知の「一致した対象」欄に表示される）。 |
| `keywords` | 必須（1 件以上） | タイトル＋要約に対する部分一致（大文字小文字は無視）で判定するキーワードのリスト。 |

### settings（実行時の挙動、省略可）

| キー | 必須 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `request_timeout_seconds` | 任意 | `10` | RSS 取得・Discord 送信の HTTP タイムアウト（秒）。 |
| `max_notifications_per_run` | 任意 | `20` | 1 回の実行で送る通知件数の上限。超過分は未通知のまま残り、次回以降の実行で送られる。 |
| `max_retry_count` | 任意 | `3` | 1 つの記事に対する**送信試行回数**の上限（初回 + 再送）。既定の `3` なら、初回失敗後に 2 回まで再送して諦める。 |
| `db_path` | 任意 | `data/events.db` | SQLite ファイルのパス（リポジトリルートからの相対パス）。 |

### 監視対象を追加する例

```yaml
targets:
  - name: My New Target
    keywords:
      - キーワードA
      - keywordB
```

### 現在の設定（ポケモンのコラボ情報）

`feeds` には **Google ニュースの検索 RSS** を使っています。これには 2 つの狙いがあります。

1. **検索クエリ自体が絞り込みになる。** 「ポケモン コラボ」で検索するため、記事本文まで含めて
   コラボ関連と判定されたものだけが返ります。`KeywordFilter` は部分一致しかできず
   「ポケモン **かつ** コラボ」のような AND 判定ができないので、その役割をフィード側に持たせています。
2. **過去に遡れる。** クエリ末尾の `when:30d` で過去 30 日分が返るため、
   「今まさに開催中のコラボ」も拾えます。PR TIMES などの通常の新着 RSS は最新の数十件しか
   持たないので、この用途には使えません。

そのため `targets` 側のキーワードは「本当にポケモンの話か」を確かめる安全網に留めています。
ここに `コラボ` を必須キーワードとして足したくなりますが、**やらないでください**。
Google ニュースの RSS はタイトルしか返さず、実際のコラボ記事でもタイトルに「コラボ」が
入らないもの（例:「サーモス『ポケモン』シリーズ登場」）が約 4 割あり、取りこぼします。

検索クエリを変える場合の注意点として、`ポケモン キャンペーン OR フェア` のように
**AND と OR を混ぜると 0 件になります**。OR だけ・AND だけで 1 本のフィードにしてください。

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
- **再送上限を超えた記事は諦める**: `max_retry_count` 回の送信試行がすべて失敗した記事は、それ以降は再送されません（無限に再送し続けないため）。該当があれば実行ログに警告が出ます。
- **まとめ送信の粒度**: 通知は 10 件で 1 リクエストのため、成否も 10 件単位でしか分かりません。1 リクエストが失敗すると、その 10 件すべてが「通知失敗」として記録され、次回まとめて再送されます。
- **既存 DB との互換性**: `notifications` テーブルは初回実行時に自動作成されます。この機構より前に保存済みだったイベントには通知状態の行が無いため、再送対象にはなりません（＝過去に通知済みのものが再送される心配はありません）。
- **同じコラボの重複**: 重複排除は URL / タイトルのハッシュ単位です。同一のコラボを複数のメディアが報じた記事はそれぞれ別物として通知されます（初回の一括通知ではこれが目立ちます）。まとめるには記事単位ではなく「出来事」単位の同定が必要で、LLM フィルタ側の課題です。
- **Google ニュース RSS への依存**: 現在の `feeds` の主力は Google ニュースの検索 RSS です。1 クエリあたり最大 100 件で、仕様変更やクエリ構文の解釈に影響を受けます（`when:30d` の遡り幅も同様）。
- **要約が付くのは一部の記事だけ**: 通知本文の要約は、フィードが要約を持っている場合にのみ表示されます。Google ニュースの検索 RSS が `summary` として返すのは**タイトルと媒体名を繰り返す `<a>` タグだけ**で、要約としての中身がありません（記事ページも JS リダイレクトのため取得できません）。要約を持つのは直接ソースのフィード（GAME Watch / ITmedia / PR TIMES / マイナビニュース / ケータイ Watch）だけですが、そこにポケモンのコラボ記事が流れてくる頻度は低く、**実測では通知全体の 1〜2% 程度**にとどまります。中身の無い要約は `clean_summary()` が空文字に落とすため、「タイトルの焼き直し」が本文に出ることはありません。すべての通知に要約を付けるには、記事本文を取得したうえで LLM に要約させる仕組みが必要です。
- **重複排除の範囲**: 重複判定は URL・`content_hash`（タイトル + URL）に加えて、`title_key`（タイトルを正規化した名寄せキー）で行います。`title_key` はタイトル末尾の配信元表記（「 - 朝日新聞」など）を落として記号と空白を無視したもので、これにより「Google ニュース経由と直接ソース経由で同じ記事」「Google ニュースが同じ記事を別 URL で返す」ケースをまとめられます。ただし**見出しが異なれば別記事として扱われる**ため、同じコラボを各媒体が独自の見出しで報じた場合は、それぞれ通知されます。正規化後が短いタイトル（既定 10 文字未満）は誤って別記事どうしを一致させないよう名寄せの対象外です。
- **フィードの並び順に意味がある**: 同じ記事を複数のフィードが拾った場合、`config/targets.yaml` で先に書かれたフィードのものが保存され、後のものは名寄せで弾かれます。要約を持つ直接ソースを検索フィードより先に並べているのはこのためです。
- **キーワード判定の精度について**: `KeywordFilter` は部分一致による判定であり、形態素解析は行っていません。日本語のキーワードは部分一致で概ね機能しますが、英単語（例: `Pokemon`）は無関係な文脈でも一致してしまう（誤検知する）可能性があります。
- **初回実行時の通知量について**: 初回実行時は SQLite に何も保存されていないため、既存のフィード内の記事が大量に一致・通知され得ます。これを防ぐため `settings.max_notifications_per_run`（デフォルト 20 件）で 1 回あたりの通知件数を制限しています。上限を超えた一致記事は保存のみ行われ、通知はされません。

## 12. 今後の拡張候補

- LLM による関連性判定の追加: `src/filters/base.py` の `RelevanceFilter` を実装した `LLMRelevanceFilter`（仮）を追加し、`main.py` 側で `KeywordFilter` と差し替える。
- RSS 以外の Collector の追加: `src/collectors/base.py` の `Collector` を実装し、API 収集やスクレイピングなど別の情報源に対応する。
- Discord 以外の Notifier の追加: `src/notifiers/base.py` の `Notifier` を実装し、Slack やメールなど別の通知先に対応する。
- 同一コラボの名寄せ: 現在は記事単位で重複排除しているため、同じコラボを複数メディアが報じた記事はそれぞれ通知される。「出来事」単位でまとめるには LLM による同定が必要。
- 外部データベースへの移行: SQLite から外部 DB へ移行し、GitHub Actions のキャッシュ失効に依存しない永続化を実現する。
