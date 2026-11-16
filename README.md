# event-watcher

RSS フィードを定期的に収集し、`config/targets.yaml` で指定した監視対象（作品・人物・ブランド・店舗など）のキーワードに一致した記事だけを SQLite で重複排除したうえで Discord Webhook に通知する MVP です。

## 1. プロジェクト概要

- 複数の RSS/Atom フィードを巡回し、記事（タイトル・要約）を取得します。
- YAML で定義したキーワードに部分一致した記事だけを「監視対象に一致した」とみなします。
- 一致した記事は SQLite に保存し、URL / 内容ハッシュで重複を排除します（同じ記事を二度通知しません）。
- 新規に保存できた記事だけを、公開日時の古い順に Discord Webhook へ通知します。通知には記事タイトル・要約・公開日時を載せます（要約はフィードが持っている場合のみ）。
- 通知は 10 件ずつ 1 メッセージにまとめて送り、レート制限（429）は `Retry-After` に従って待ってから再送します。
- 通知の成否は `notifications` テーブルに記録され、**送れなかった記事は次回実行で自動的に再送されます**（上限 `settings.max_retry_count` 回）。
- 公開から `settings.max_article_age_days` 日（既定 3 日）を過ぎた記事と、元記事の写真ページ（「画像1 / 6」など。古い記事が新しい日付で出し直されるため）は、保存だけして通知しません。
- DB が空の初回実行（新しい話題の追加時・キャッシュ消失後）は、記録だけして通知しません（`settings.seed_on_first_run`）。
- 同じコラボを複数の媒体が報じた記事は、最初の 1 件だけ通知します（通知から `event_grouping.window_days` 日の間、同じ出来事の記事は保存のみ）。
- 1 日 2 回（日本時間 9:00 / 21:00）、cron-job.org から GitHub Actions を起動して自動実行します（GitHub 自体の定期実行は数時間遅れることがあるため。[9. GitHub Actions の設定方法](#9-github-actions-の設定方法) 参照）。

現在は 2 つの話題を監視しており、それぞれ別の Discord チャンネルに、同じタイミングで通知します。

| 話題 | 設定ファイル | 通知先（環境変数 / Secret） | DB |
| --- | --- | --- | --- |
| ポケモンのコラボ情報 | `config/targets.yaml` | `DISCORD_WEBHOOK_URL` | `data/events.db` |
| ハイキュー!! のコラボ情報 | `config/haikyu.yaml` | `DISCORD_WEBHOOK_URL_HAIKYU` | `data/haikyu.db` |

設定ファイルの書き方は [5. targets.yaml の設定方法](#5-targetsyaml-の設定方法) を参照してください（`haikyu.yaml` も同じ形式です）。

## 2. アーキテクチャ

### ディレクトリツリー

```
event-watcher/
├── .github/
│   └── workflows/
│       └── watcher.yml     # GitHub Actions（cron-job.org からの起動 + 予備の定期実行 + 手動実行）
├── config/
│   ├── targets.yaml        # ポケモン: 収集元フィード・監視対象・実行設定
│   └── haikyu.yaml         # ハイキュー!!: 同上（通知先チャンネルと DB は別）
├── data/
│   ├── events.db           # ポケモン用 SQLite（.gitignore 対象、実行時に自動生成）
│   └── haikyu.db           # ハイキュー用 SQLite（同上）
├── src/
│   ├── main.py             # フローの組み立てとエントリポイント
│   ├── config.py           # 設定 YAML の読み込み・検証
│   ├── models.py           # Article / Event などの共有ドメインモデル、名寄せキー
│   ├── titles.py           # 見出しの配信元表記（「 - 媒体名」など）の扱い
│   ├── source_priority.py  # 同じ記事のどの媒体の版を残すか
│   ├── event_grouping.py   # 同じ出来事（同じコラボ）の記事の判定
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
│       ├── base.py         # Repository の抽象基底クラス
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
| `titles` / `source_priority` / `event_grouping`（`src/`） | 見出しを使った判定のみ。配信元表記の除去、媒体の優先順位、同じ出来事かどうかの判定を行う。保存や通知は行わない。 |
| `src/main.py` | フロー。上記の各コンポーネントを組み立て、収集→判定→重複排除→保存→通知の流れを制御する。個々の失敗でループ全体を止めない。 |

### 処理フロー

```
1. 収集（RSSCollector）→ キーワード判定（FeedRequirementFilter / KeywordFilter）
2. 同じ記事の版を「良い順」に並べる（source_priority → 要約の有無 → feeds の並び順）
3. 1 件ずつ:
     保存済み（URL / content_hash / title_key が一致）     → 何もしない
     新しい記事 → events に保存したうえで、通知するかを決める
        似たタイトルの記事が保存済み（転載）               → 通知しない
        元記事の写真ページ（「画像1 / 6」など）             → 通知しない
        公開から max_article_age_days 日を過ぎている        → 通知しない
        window_days 日以内に通知した記事と同じ出来事        → 通知しない
        DB が空の初回実行（seed_on_first_run）              → 通知しない
        それ以外                                             → 未通知として記録（notifications: pending）
4. まとめて Discord 通知（DiscordNotifier.notify_batch）→ 結果を記録（notifications: sent / failed）
```

**新しい記事は、通知しないものも含めてすべて保存します。** 保存しておけば、次回以降に同じ記事が
フィードに残っていても「保存済み」として即座に弾けるので、判定をやり直したり、
古い記事を後から通知してしまったりすることがありません。

通知は記事 1 件ごとには送らず、全フィードの収集・保存が終わってから最後にまとめて送ります
（1 件 1 リクエストだと過去分の一括通知でレート制限に掛かるため）。

送信対象は「今回保存したもの」だけではありません。実行のたびに `notifications` テーブルから
**未通知（pending）と、再送上限に達していない失敗（failed）** を拾い直して送ります。そのため

- 通知に失敗した記事は、次回実行で自動的に再送される
- `max_notifications_per_run` を超えて積み残した記事も、次回以降に順次送られる（取りこぼさない）

という挙動になります。`events`（何が起きたか）と `notifications`（通知できたか）は
テーブルも Repository も分かれており、`content_hash` で紐づいています。

各段階の失敗は個別に catch してログに残し、他のフィード・記事の処理は継続します（`src/main.py` の `_collect_safely` / `_save_safely` など）。

通知するかどうかの判定（古い記事・同じ出来事）は `src/main.py` の `_NotificationGate`、転載の判定は `_TitleIndex` にまとまっています。

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

設定ファイルは `config/targets.yaml`（`--config` で別のパスも指定可。ハイキューは `config/haikyu.yaml`）。トップレベルに必須の `feeds` / `targets` と、任意の `settings` / `source_priority` / `event_grouping` を持ちます。

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
| `max_article_age_days` | 任意 | `3` | 公開からこの日数を過ぎた記事は保存のみ行い、通知しない（保存はするので、以後同じ記事が流れてきても重複として弾かれる）。公開日時が取れない記事は通知する。`0` 以下で無制限。 |
| `seed_on_first_run` | 任意 | `true` | DB に記事が 1 件も無いとき（新しい話題の初回・キャッシュ消失後）は、記録だけして通知しない。過去に見た記事が分からない状態では、Google ニュースが新しい日付で出し直した古い記事も「新着」に見えてしまうため。 |
| `max_retry_count` | 任意 | `3` | 1 つの記事に対する**送信試行回数**の上限（初回 + 再送）。既定の `3` なら、初回失敗後に 2 回まで再送して諦める。 |
| `db_path` | 任意 | `data/events.db` | SQLite ファイルのパス（リポジトリルートからの相対パス）。話題ごとに別のファイルにすること。 |
| `webhook_env` | 任意 | `DISCORD_WEBHOOK_URL` | 通知先 Discord Webhook URL を読む環境変数名。話題ごとにチャンネルを分けるときに変える。 |

### 話題を追加する（別チャンネルに通知する）

1. `config/haikyu.yaml` をコピーして `config/<話題>.yaml` を作り、`feeds` / `targets` / `source_priority` を書き換える。
2. `settings.db_path` と `settings.webhook_env` を、他の話題と重ならない値にする（例: `data/<話題>.db`、`DISCORD_WEBHOOK_URL_<話題>`）。
3. 通知先チャンネルで Webhook を作り、その URL を GitHub Secrets に `webhook_env` と同じ名前で登録する。
4. `.github/workflows/watcher.yml` に、ハイキューと同じ形で「DB の restore / save」と「Run ... watcher」のステップを追加する。

### source_priority（同じ記事のどの版を残すか、省略可）

同じ記事が複数の媒体から届いたとき、どの媒体の版を残すかを指定します。

| キー | 必須 | 説明 |
| --- | --- | --- |
| `preferred` | 任意 | 上から順に優先して残す媒体名のリスト。 |
| `deprioritized` | 任意 | 他に版があればそちらを残したい媒体名のリスト（Yahoo!ニュースなどの転載ポータル）。 |

どちらにも無い媒体はその中間として扱います。媒体名は Discord に届く見出し末尾の「 - 媒体名」（直接ソースのフィードはフィード名）と一致させてください（全角・半角と大文字小文字は区別しません）。

### event_grouping（同じコラボを報じた別媒体の記事をまとめる、省略可）

同じコラボでも媒体ごとに見出しはまったく違いますが、コラボ相手や商品の固有名詞（「モスバーガー」「福袋」など）はほぼ必ず共通しています。そこで見出しから**特徴語**（カタカナ・漢字・英字の連なり）を取り出し、どの記事にも出てくる一般語（コラボ・グッズ・発売…）と話題そのもの（ポケモン・ハイキュー…）を除いたうえで、次のどちらかなら同じ出来事とみなします（判定は [src/event_grouping.py](src/event_grouping.py)）。

- **コラボ相手が一致する**: 見出しの「×」の隣にある語（「ハイキュー!!×**モスバーガー**」）が、もう片方の見出しにも含まれる
- **特徴語を 2 つ以上共有する**（「モス」と「モスバーガー」のように先頭が一致する語は同じとみなす）

通知した記事から `window_days` 日の間、同じ出来事の記事は保存だけして通知しません。期間を過ぎれば同じコラボの続報（店頭販売開始など）も再び通知されます。止めるのは「通知した記事」を起点にした期間だけなので、記事が出続けても期間が延び続けることはありません。

| キー | 必須 | デフォルト | 説明 |
| --- | --- | --- | --- |
| `window_days` | 任意 | `7` | 通知した記事から、同じ出来事の記事を通知しない日数。`0` 以下で無効。 |
| `stopwords` | 任意 | なし | 特徴語から除く、その話題のどの記事にも出てくる語（キャラクター名・学校名など）。`targets` のキーワードは自動で除かれる。 |

別のコラボどうしがまとめられてしまう（＝見たい通知が来ない）場合は、両方の見出しに共通している語を `stopwords` に足してください。

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

`.github/workflows/watcher.yml` は、次の 3 通りで起動します。

| 起動方法 | 時刻 | 役割 |
| --- | --- | --- |
| **cron-job.org からの起動**（`workflow_dispatch`） | 日本時間 9:00 / 21:00 | **本番の起動**。ほぼ時刻どおりに動く。 |
| GitHub の定期実行（`schedule: cron: "0 0,12 * * *"`） | UTC 0:00 / 12:00（日本時間 9:00 / 21:00） | **予備**。混雑時は数時間遅れる（実測で 3〜9 時間遅れ）。**本番が 13 時間以内に成功していれば何もせず終わる**（guard ジョブ）。本番が止まっているときだけ、遅れてでも通知して警告を出す。 |
| 手動実行（「Actions」タブ →「event-watcher」→「Run workflow」） | 任意 | 動作確認用。`dry_run` を `true` にすると通知せず収集・保存のみ。`as_backup` を `true` にすると予備の定期実行と同じ判定を試せる。 |

起動が重なっても、`concurrency` で同時には走らず、重複判定があるので同じ記事が二重に届くことはありません。
以前は予備の定期実行も毎回処理していたため、本番（9:00 / 21:00）の後に出た新着が、遅れて動いた予備によって 12:30 頃や深夜 3:00 頃といった中途半端な時刻に届いていました。guard ジョブはこれを防ぐためのものです。
1 回の実行の中で、ポケモン → ハイキューの順に処理します。片方が失敗してももう片方は実行されます。

### cron-job.org の設定（本番の起動）

cron-job.org は「決まった時刻に GitHub の API を呼ぶ」目覚まし時計の役だけをします。記事の収集や Discord への通知は、今までどおり GitHub Actions 上のこのプログラムが行います。

1. **GitHub でトークンを作る**: Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
   - Repository access: **Only select repositories** で `event-watcher` だけ
   - Permissions: Repositories の **Actions** を **Read and write**（他は追加しない）
   - Expiration: 1 年程度（Custom で日付指定）
2. **cron-job.org でジョブを作る**
   - URL: `https://api.github.com/repos/YutoTsuru/event-watcher/actions/workflows/watcher.yml/dispatches`
   - Schedule: Custom で `0 9,21 * * *`（Time zone: `Asia/Tokyo`）
   - Request method: `POST`
   - Headers: `Authorization: Bearer <トークン>` / `Accept: application/vnd.github+json` / `Content-Type: application/json`
   - Request body: `{"ref":"main"}`
   - TEST RUN で **204 No Content** が返れば成功（GitHub の「Actions」タブに `workflow_dispatch` の実行が増える）

**現在のトークンの有効期限は 2027/11/11 です。** 期限が切れると cron-job.org からの起動が失敗し（401）、遅れがちな GitHub の定期実行だけになります。期限前に 1. で新しいトークンを作り、cron-job.org の `Authorization` ヘッダーを差し替えてください。

**重要**: この `.github/` ディレクトリを GitHub Actions に認識させるには、`event-watcher` ディレクトリ自体をリポジトリのルートにする必要があります（`event-watcher` が別リポジトリのサブディレクトリになっている状態では、GitHub Actions はワークフローを検出しません）。

## 10. GitHub Secrets の設定方法

1. GitHub リポジトリの「Settings」→「Secrets and variables」→「Actions」を開く。
2. 「New repository secret」をクリックする。
3. Name に `DISCORD_WEBHOOK_URL`、Value に取得した Webhook URL を入力して保存する。
4. ハイキュー用のチャンネルの Webhook も同様に、Name を `DISCORD_WEBHOOK_URL_HAIKYU` にして登録する（未登録の間、ハイキューの監視は警告を出してスキップされます）。

## 11. 運用（動作確認とトラブルシューティング）

### 実行結果の見方

各話題の実行の最後に、次の 1 行がログに出ます（GitHub の「Actions」タブ → 実行 → `Run Pokemon watcher` / `Run Haikyu watcher`）。

```
実行結果: 取得=541 一致=179 新規保存=2 通知成功=1 通知失敗=0 うち再送=0 類似タイトル除外=0 画像ページで通知せず=0 古い記事で通知せず=0 同じ出来事で通知せず=1 初回のため記録のみ=0
```

| 項目 | 意味 |
| --- | --- |
| 取得 / 一致 | フィードから取れた記事数 / そのうち監視対象のキーワードに一致した数 |
| 新規保存 | 初めて見た記事の数（以下の「〜で通知せず」「記録のみ」の各項目と、通知対象になったものの合計） |
| 類似タイトル除外 | 保存済み記事の転載（見出しが少しだけ違う）とみなして通知しなかった数 |
| 画像ページで通知せず | 元記事の写真ページ（「画像1 / 6」「画像ギャラリー」「1枚目の写真・画像」）だったので通知しなかった数 |
| 古い記事で通知せず | 公開から `max_article_age_days` 日を過ぎていて通知しなかった数 |
| 同じ出来事で通知せず | `window_days` 日以内に通知したコラボと同じとみなして通知しなかった数 |
| 初回のため記録のみ | DB が空の初回実行だったので、記録だけして通知しなかった数（`seed_on_first_run`） |
| 通知成功 / 通知失敗 / うち再送 | Discord への送信結果（前回までの積み残し・再送分を含む） |

新着が無い回は「新規保存=0」で、他の項目もすべて 0 になるのが正常です。

ターミナルからは `gh` で確認できます。

```bash
gh run list --workflow watcher.yml --limit 5               # 最近の実行（event 列で起動方法が分かる）
gh run view <run-id> --log | grep 実行結果                  # 各話題の実行結果
```

### よくある症状

| 症状 | 確認すること |
| --- | --- |
| 通知が予定時刻に来ない / 中途半端な時刻に来る | `gh run list` で `workflow_dispatch` の実行があるか。無ければ cron-job.org のジョブ履歴（History）を見る。401 ならトークンの期限切れ。本番が止まっている間は予備が遅れて動き、GitHub の実行画面に「cron-job.org からの起動が 13 時間以上成功していません」と警告が出る。 |
| ハイキューだけ来ない | ログに「`DISCORD_WEBHOOK_URL_HAIKYU` が未登録」の警告が出ていないか（Secret の登録漏れ）。 |
| 見たかったコラボが来ない | 「同じ出来事で通知せず」で止められた可能性。別のコラボと共通している語を `event_grouping.stopwords` に足す。 |
| 同じコラボが何度も来る | コラボ相手の表記ゆれ（「ミスド」「ミスタードーナツ」）やひらがな表記。現状のロジックではまとめられない（[既知の制約](#12-既知の制約--注意点)）。 |
| 終わったコラボの記事が届いた | Google ニュースが古い記事を新しい日付で出し直したもの。一度保存した記事なら弾けるが、初めて見る記事は判別できない（[既知の制約](#12-既知の制約--注意点)）。 |
| 通知が 1 回まったく来なかった | DB のキャッシュが消えて初回扱いになった可能性（ログに「初回のため記録のみ」）。次の回から通常どおり届く。 |

## 12. 既知の制約 / 注意点

- **SQLite の永続化について**: GitHub Actions のランナーは実行のたびに破棄されるため、重複判定用の SQLite（`data/events.db` / `data/haikyu.db`）は `actions/cache` を使って実行間で引き継いでいます（話題ごとに別のキャッシュ。7 日間使われないキャッシュは GitHub に削除されますが、毎日実行していれば消えません）。キャッシュが失効・削除された場合は重複判定の履歴が失われます。その直後の 1 回は `seed_on_first_run` により記録だけ行うので、過去に通知済みの記事がまとめて再通知されることはありません（代わりにその回の新着は通知されません）。恒久的な運用では、DB ファイルをリポジトリにコミットする方式や、外部データベース（例: マネージドな SQL/KVS サービス）への移行を検討してください。
- **再送上限を超えた記事は諦める**: `max_retry_count` 回の送信試行がすべて失敗した記事は、それ以降は再送されません（無限に再送し続けないため）。該当があれば実行ログに警告が出ます。
- **まとめ送信の粒度**: 通知は 10 件で 1 リクエストのため、成否も 10 件単位でしか分かりません。1 リクエストが失敗すると、その 10 件すべてが「通知失敗」として記録され、次回まとめて再送されます。
- **既存 DB との互換性**: `notifications` テーブルは初回実行時に自動作成されます。この機構より前に保存済みだったイベントには通知状態の行が無いため、再送対象にはなりません（＝過去に通知済みのものが再送される心配はありません）。
- **同じコラボの名寄せは見出しの語だけで判定している**: `event_grouping` は形態素解析も LLM も使わず、文字種の境目で区切った語の一致だけを見ています。そのため、(1) 見出しにコラボ相手の名前がひらがなでしか出ない（「しまむら」など）、共通する固有名詞が 1 つしかない、といった記事はまとめられずに別々に通知されることがあります。逆に (2) 同じコラボ相手の別の商品（「サンキューマート」の第2弾と第3弾など）は、`window_days` の間は 1 件にまとめられます。
- **Google ニュースは古い記事を新しい日付で出し直すことがある**: 記事が更新されると公開日時も新しくなるようで、実測で 10/20 の記事が 11/7 付けで再び現れた例がありました。一度保存した記事は日付が変わっても「保存済み」として弾けますが、**初めて見る記事は本当の公開日が分からない**ため、古い話題が届くことがあります。DB が空の初回は `seed_on_first_run` で通知を止め、写真ページ（最もよく出し直される）は常に通知しないことで、影響を小さくしています。
- **Google ニュース RSS への依存**: 現在の `feeds` の主力は Google ニュースの検索 RSS です。1 クエリあたり最大 100 件で、仕様変更やクエリ構文の解釈に影響を受けます（`when:30d` の遡り幅も同様）。
- **要約が付くのは一部の記事だけ**: 通知本文の要約は、フィードが要約を持っている場合にのみ表示されます。Google ニュースの検索 RSS が `summary` として返すのは**タイトルと媒体名を繰り返す `<a>` タグだけ**で、要約としての中身がありません（記事ページも JS リダイレクトのため取得できません）。要約を持つのは直接ソースのフィード（GAME Watch / ITmedia / PR TIMES / マイナビニュース / ケータイ Watch）だけですが、そこにポケモンのコラボ記事が流れてくる頻度は低く、**実測では通知全体の 1〜2% 程度**にとどまります。中身の無い要約は `clean_summary()` が空文字に落とすため、「タイトルの焼き直し」が本文に出ることはありません。すべての通知に要約を付けるには、記事本文を取得したうえで LLM に要約させる仕組みが必要です。
- **重複排除の範囲**: 重複判定は URL・`content_hash`（タイトル + URL）に加えて、`title_key`（タイトルを正規化した名寄せキー）で行います。`title_key` は全角・半角を揃え、タイトル末尾の配信元表記（「 - 朝日新聞」「｜ファミ通」など）と転載時の付け足し（「（GAME Watch） - Yahoo!ニュース」「(2026年11月4日掲載)」）を落とし、記号と空白を無視したものです。さらに、過去 45 日に保存した記事と `title_key` の類似度が 0.85 以上なら同じ記事の転載とみなします（「10月24日ご来店予定の」と「10月24日にご来店予定の」など）。ただし見出し中の数字が食い違うもの（「第1弾」と「第2弾」、日付違いの告知）は別記事として扱います。転載とみなした記事も保存はします（通知はしない）。見出しがはっきり異なる記事（同じコラボを各媒体が独自の見出しで報じたもの）は、ここではなく `event_grouping` でまとめます。正規化後が短いタイトル（既定 10 文字未満）は誤って別記事どうしを一致させないよう名寄せの対象外です。名寄せキーの正規化を変えたときは `TITLE_KEY_VERSION`（`src/repositories/sqlite.py`）を上げると、既存 DB のキーが起動時に作り直されます。
- **同じ記事のどの版を残すか**: 同じ記事を複数の媒体・フィードが拾った場合は、設定ファイルの `source_priority`（媒体の優先順位）→ 要約の有無 → `feeds` の並び順、の順で 1 つを選んで残し、他は名寄せで弾きます。媒体名は Google ニュース経由なら見出し末尾の「 - 媒体名」、直接ソースならフィード名です。選び直すのは同じ実行の中だけで、**既に通知済みの記事を後から来た良い版に差し替えることはしません**（再通知になってしまうため）。また、見やすさ・情報量を自動で測っているわけではなく、優先順位は設定で決めた好みです。
- **キーワード判定の精度について**: `KeywordFilter` は部分一致による判定であり、形態素解析は行っていません。日本語のキーワードは部分一致で概ね機能しますが、英単語（例: `Pokemon`）は無関係な文脈でも一致してしまう（誤検知する）可能性があります。
- **初回実行時の通知量について**: 初回実行時は SQLite に何も保存されていないため、既存のフィード内の記事が大量に一致・通知され得ます。`max_article_age_days` で古い記事は通知されませんが、加えて `settings.max_notifications_per_run`（デフォルト 20 件）で 1 回あたりの通知件数を制限しています。上限を超えた一致記事は保存したうえで未通知（pending）のまま残り、次回以降の実行で順次送られます（取りこぼしはしませんが、初回の積み残しを送り切るまで数回の実行にまたがります）。

## 13. 今後の拡張候補

- LLM による関連性判定の追加: `src/filters/base.py` の `RelevanceFilter` を実装した `LLMRelevanceFilter`（仮）を追加し、`main.py` 側で `KeywordFilter` と差し替える。
- RSS 以外の Collector の追加: `src/collectors/base.py` の `Collector` を実装し、API 収集やスクレイピングなど別の情報源に対応する。
- Discord 以外の Notifier の追加: `src/notifiers/base.py` の `Notifier` を実装し、Slack やメールなど別の通知先に対応する。
- 同一コラボの名寄せの精度向上: 現在は見出しの特徴語の一致で判定している（`event_grouping`）。ひらがなのコラボ相手や言い換え（「ミスド」と「ミスタードーナツ」）は拾えないので、別名の辞書を設定で持たせるなどの改善余地がある。
- 外部データベースへの移行: SQLite から外部 DB へ移行し、GitHub Actions のキャッシュ失効に依存しない永続化を実現する。
