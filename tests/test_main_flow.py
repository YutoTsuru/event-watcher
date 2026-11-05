# -*- coding: utf-8 -*-
"""main.run() のエンドツーエンドテスト。

Collector / Notifier はすべてフェイク（メモリ内）実装を使い、
実ネットワークアクセス・実 Discord 通信は一切行わない。
SQLite は pytest の tmp_path 配下のみを使う。
"""

from __future__ import annotations

import zlib

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.collectors.base import Collector
from src.config import Target
from src.filters.keyword import KeywordFilter
from src.main import run
from src.models import Article, make_content_hash
from src.notifiers.base import Notifier
from src.models import Event
from src.repositories.sqlite import SQLiteEventRepository, SQLiteNotificationRepository
from src.source_priority import SourcePriority


class FakeCollector(Collector):
    """記事リストを返す、またはエラーを送出するフェイク Collector。"""

    def __init__(
        self,
        name: str,
        articles: Optional[list[Article]] = None,
        raises: bool = False,
    ) -> None:
        self._name = name
        self._articles = articles or []
        self._raises = raises

    @property
    def name(self) -> str:
        return self._name

    def collect(self) -> list[Article]:
        if self._raises:
            raise RuntimeError(f"{self._name} の収集に失敗しました（テスト用エラー）")
        return self._articles


class FakeNotifier(Notifier):
    """呼び出しを記録するフェイク Notifier。固定の成功/失敗を返す、または関数で制御する。"""

    def __init__(self, succeed: bool = True) -> None:
        self._succeed = succeed
        self.notified_events: list[Event] = []

    def notify(self, event: Event) -> bool:
        self.notified_events.append(event)
        return self._succeed


def _title_number(url: str) -> int:
    """URL ごとに決まる番号（タイトルを記事ごとに変えるためのもの）。"""
    return zlib.crc32(url.encode("utf-8"))


def make_pokemon_article(url: str = "https://example.com/pokemon-1") -> Article:
    # タイトルでも名寄せ（完全一致・類似）されるため、URL が違えば別記事になるよう
    # タイトルも変える。似た見出しでも数字が違えば別記事とみなされるので、URL から番号を作る
    return Article(
        title=f"新作ポケモンゲームが発表されました（第{_title_number(url)}弾）",
        url=url,
        source="フィード1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="任天堂が新作を発表しました。",
    )


def make_unrelated_article(url: str = "https://example.com/unrelated-1") -> Article:
    return Article(
        title="無関係なニュース",
        url=url,
        source="フィード1",
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary="今日の天気予報について。",
    )


@pytest.fixture
def repo(tmp_path: Path) -> SQLiteEventRepository:
    repository = SQLiteEventRepository(tmp_path / "main_flow.db")
    repository.initialize()
    return repository


@pytest.fixture
def notif_repo(tmp_path: Path) -> SQLiteNotificationRepository:
    """events と同じ DB ファイルの notifications テーブルを使う。"""
    repository = SQLiteNotificationRepository(tmp_path / "main_flow.db")
    repository.initialize()
    return repository


@pytest.fixture
def keyword_filter(pokemon_target: Target) -> KeywordFilter:
    return KeywordFilter([pokemon_target])


def test_run_saves_and_notifies_only_new_matched_articles(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """一致した新規記事だけが保存・通知され、対象外の記事は無視される。"""
    matched = make_pokemon_article()
    unmatched = make_unrelated_article()
    collector = FakeCollector("フィード1", articles=[matched, unmatched])
    notifier = FakeNotifier(succeed=True)

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )

    assert summary.collected == 2
    assert summary.matched == 1
    assert summary.saved == 1
    assert summary.notified == 1
    assert summary.notify_failed == 0
    assert repo.count() == 1
    assert len(notifier.notified_events) == 1
    assert notifier.notified_events[0].url == matched.url


def test_run_does_not_renotify_duplicate_events(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """同じ記事を 2 回収集しても、2 回目は保存も通知もされない。"""
    matched = make_pokemon_article()
    notifier = FakeNotifier(succeed=True)

    first_summary = run(
        collectors=[FakeCollector("フィード1", articles=[matched])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )
    second_summary = run(
        collectors=[FakeCollector("フィード1", articles=[matched])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )

    assert first_summary.saved == 1
    assert first_summary.notified == 1
    assert second_summary.matched == 1  # フィルタには一致する
    assert second_summary.saved == 0  # だが重複のため保存されない
    assert second_summary.notified == 0  # 通知もされない
    assert repo.count() == 1
    assert len(notifier.notified_events) == 1  # 通知は 1 回だけ


def test_run_continues_when_one_collector_raises(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """あるフィードの収集が例外を送出しても、他のフィードは処理され続ける。"""
    broken = FakeCollector("壊れたフィード", raises=True)
    healthy_article = make_pokemon_article(url="https://example.com/healthy-1")
    healthy = FakeCollector("正常なフィード", articles=[healthy_article])
    notifier = FakeNotifier(succeed=True)

    summary = run(
        collectors=[broken, healthy],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )

    assert summary.collected == 1  # 壊れたフィードからは 0 件、正常な方から 1 件
    assert summary.matched == 1
    assert summary.saved == 1
    assert summary.notified == 1
    assert repo.count() == 1


def test_run_counts_notify_failed_without_aborting(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """Notifier が False を返しても保存済みのまま処理は継続し、notify_failed に計上される。"""
    article1 = make_pokemon_article(url="https://example.com/pokemon-a")
    article2 = make_pokemon_article(url="https://example.com/pokemon-b")
    collector = FakeCollector("フィード1", articles=[article1, article2])
    notifier = FakeNotifier(succeed=False)

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )

    assert summary.matched == 2
    assert summary.saved == 2  # 通知に失敗しても保存はされている
    assert summary.notified == 0
    assert summary.notify_failed == 2
    assert repo.count() == 2
    assert len(notifier.notified_events) == 2


def test_run_with_no_notifier_still_saves_events(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """notifier=None（--dry-run 相当）でも保存は行われ、通知系カウントは 0 のまま。"""
    article = make_pokemon_article()
    collector = FakeCollector("フィード1", articles=[article])

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=None,
        max_notifications=20,
    )

    assert summary.saved == 1
    assert summary.notified == 0
    assert summary.notify_failed == 0
    assert repo.count() == 1


def test_run_respects_max_notifications_per_run(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """通知数が上限に達したら、以降は保存のみ行い通知はしない。"""
    articles = [make_pokemon_article(url=f"https://example.com/pokemon-{i}") for i in range(3)]
    collector = FakeCollector("フィード1", articles=articles)
    notifier = FakeNotifier(succeed=True)

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=1,
    )

    assert summary.saved == 3
    assert summary.notified == 1
    assert summary.notify_failed == 0
    assert repo.count() == 3


class FakeBatchNotifier(Notifier):
    """notify_batch をオーバーライドし、まとめ送信の呼ばれ方を記録するフェイク。"""

    def __init__(self, succeed: bool = True) -> None:
        self._succeed = succeed
        self.batches: list[list[Event]] = []

    def notify(self, event: Event) -> bool:  # pragma: no cover - 呼ばれない想定
        raise AssertionError("notify_batch がある場合、1 件ずつの notify は使わない")

    def notify_batch(self, events) -> list[bool]:
        self.batches.append(list(events))
        return [self._succeed] * len(events)


def test_run_dispatches_all_events_in_a_single_batch(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """複数フィードにまたがる一致記事も、収集後にまとめて 1 回で通知される。"""
    collectors = [
        FakeCollector(
            f"フィード{i}",
            articles=[make_pokemon_article(url=f"https://example.com/pokemon-{i}")],
        )
        for i in range(3)
    ]
    notifier = FakeBatchNotifier(succeed=True)

    summary = run(
        collectors=collectors,
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    assert summary.saved == 3
    assert summary.notified == 3
    assert len(notifier.batches) == 1  # フィードごとではなく、最後に 1 回
    assert len(notifier.batches[0]) == 3


def test_run_does_not_notify_when_nothing_was_saved(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """保存対象が 0 件なら、通知処理自体を呼ばない（空メッセージを送らない）。"""
    collector = FakeCollector("フィード1", articles=[make_unrelated_article()])
    notifier = FakeBatchNotifier(succeed=True)

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    assert summary.saved == 0
    assert notifier.batches == []


def test_run_counts_batch_failure_as_notify_failed(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """まとめ送信が失敗しても保存は残り、失敗件数として集計される。"""
    articles = [make_pokemon_article(url=f"https://example.com/pokemon-{i}") for i in range(4)]
    notifier = FakeBatchNotifier(succeed=False)

    summary = run(
        collectors=[FakeCollector("フィード1", articles=articles)],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    assert summary.saved == 4
    assert summary.notified == 0
    assert summary.notify_failed == 4


def test_run_notifies_in_oldest_first_order(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """通知は公開日時の古い順に並ぶ（日時不明のものは末尾）。"""
    def article(url: str, published: Optional[datetime]) -> Article:
        return Article(
            title=f"ポケモンコラボ開催のお知らせ（第{_title_number(url)}弾）",
            url=url,
            source="フィード1",
            published_at=published,
            summary="コラボの告知です。",
        )

    articles = [
        article("https://example.com/c", datetime(2026, 3, 3, tzinfo=timezone.utc)),
        article("https://example.com/none", None),
        article("https://example.com/a", datetime(2026, 1, 1, tzinfo=timezone.utc)),
        article("https://example.com/b", datetime(2026, 2, 2, tzinfo=timezone.utc)),
    ]
    notifier = FakeBatchNotifier(succeed=True)

    run(
        collectors=[FakeCollector("フィード1", articles=articles)],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    urls = [event.url for event in notifier.batches[0]]
    assert urls == [
        "https://example.com/a",
        "https://example.com/b",
        "https://example.com/c",
        "https://example.com/none",
    ]


class FlakyBatchNotifier(Notifier):
    """実行ごとに成否を切り替えられるフェイク。まとめ送信の呼ばれ方も記録する。"""

    def __init__(self, succeed: bool = True) -> None:
        self.succeed = succeed
        self.batches: list[list[Event]] = []

    def notify(self, event: Event) -> bool:  # pragma: no cover - 呼ばれない想定
        raise AssertionError("notify_batch がある場合、1 件ずつの notify は使わない")

    def notify_batch(self, events) -> list[bool]:
        self.batches.append(list(events))
        return [self.succeed] * len(events)


def test_failed_notification_is_retried_on_next_run(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """通知に失敗した記事は、保存済みでも次回実行で再送される。"""
    article = make_pokemon_article()
    notifier = FlakyBatchNotifier(succeed=False)

    first = run(
        collectors=[FakeCollector("フィード1", articles=[article])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )
    assert first.saved == 1
    assert first.notify_failed == 1
    assert first.retried == 0

    # 2 回目。記事は重複なので保存はされないが、通知は再送される
    notifier.succeed = True
    second = run(
        collectors=[FakeCollector("フィード1", articles=[article])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    assert second.saved == 0  # 重複なので保存はされない
    assert second.notified == 1  # それでも再送される
    assert second.retried == 1
    assert [event.url for event in notifier.batches[1]] == [article.url]


def test_successfully_notified_event_is_never_resent(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """一度通知に成功した記事は、次回以降の通知対象にならない。"""
    article = make_pokemon_article()
    notifier = FlakyBatchNotifier(succeed=True)

    for _ in range(3):
        run(
            collectors=[FakeCollector("フィード1", articles=[article])],
            relevance_filter=keyword_filter,
            repository=repo,
            notification_repository=notif_repo,
            notifier=notifier,
            max_notifications=200,
        )

    assert len(notifier.batches) == 1  # 送信は初回の 1 回だけ


def test_retry_stops_after_max_retry_count(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """失敗が続いても max_retry_count 回の送信試行で打ち切る（無限に再送しない）。"""
    article = make_pokemon_article()
    notifier = FlakyBatchNotifier(succeed=False)

    for _ in range(5):
        run(
            collectors=[FakeCollector("フィード1", articles=[article])],
            relevance_filter=keyword_filter,
            repository=repo,
            notification_repository=notif_repo,
            notifier=notifier,
            max_notifications=200,
            max_retry_count=3,
        )

    # 初回 + 再送 2 回 = 3 回試行したところで諦める
    assert len(notifier.batches) == 3
    assert notif_repo.count_exhausted(3) == 1


def test_events_over_the_cap_are_notified_on_the_next_run(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """通知上限を超えた分は未通知のまま残り、次回実行で送られる（取りこぼさない）。"""
    articles = [make_pokemon_article(url=f"https://example.com/pokemon-{i}") for i in range(5)]
    notifier = FlakyBatchNotifier(succeed=True)

    first = run(
        collectors=[FakeCollector("フィード1", articles=articles)],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=2,
    )
    assert first.saved == 5
    assert first.notified == 2

    # 2 回目は新しい記事が無くても、積み残した 3 件が送られる
    second = run(
        collectors=[FakeCollector("フィード1", articles=[])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    assert second.collected == 0
    assert second.saved == 0
    assert second.notified == 3
    notified_urls = {event.url for batch in notifier.batches for event in batch}
    assert notified_urls == {article.url for article in articles}


def test_dispatch_skips_states_whose_event_is_missing(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """events 側に無い通知状態は、送りようがないので対象から外す（毎回警告し続けない）。"""
    notif_repo.create_pending("存在しないハッシュ")
    notifier = FlakyBatchNotifier(succeed=True)

    summary = run(
        collectors=[FakeCollector("フィード1", articles=[])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=200,
    )

    assert summary.notified == 0
    assert notifier.batches == []
    assert notif_repo.list_dispatchable(max_retry_count=3, limit=10) == []


# --- 類似タイトルの名寄せと、古い記事の通知抑止 ---


def _article(title: str, url: str, published_at: Optional[datetime]) -> Article:
    return Article(
        title=title, url=url, source="フィード1", published_at=published_at, summary=""
    )


def test_reworded_repost_from_another_feed_is_not_saved_or_notified(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    now = datetime.now(timezone.utc)
    original = _article(
        "10月24日（土）ご来店予定のお客様へ ポケモンセンター - 公式サイト",
        "https://example.com/original",
        now,
    )
    repost = _article(
        "10月24日（土）にご来店予定のお客様へ ポケモンセンター - 別の媒体",
        "https://example.com/repost",
        now,
    )
    notifier = FakeBatchNotifier(succeed=True)

    summary = run(
        collectors=[
            FakeCollector("フィード1", articles=[original]),
            FakeCollector("フィード2", articles=[repost]),
        ],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )

    assert summary.saved == 1
    assert summary.near_duplicates == 1
    assert [e.url for e in notifier.batches[0]] == ["https://example.com/original"]


def test_reworded_repost_of_a_previously_saved_article_is_skipped(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """前回の実行で保存した記事の転載も、次の実行で弾かれる。"""
    now = datetime.now(timezone.utc)
    first_run_article = _article(
        "ポケモンとミスドのコラボが11月に開催決定 ピカチュウたちが大集合",
        "https://example.com/1",
        now,
    )
    second_run_article = _article(
        "ポケモンとミスドのコラボが11月4日に開催決定 ピカチュウたちが大集合",
        "https://example.com/2",
        now,
    )
    for article in (first_run_article, second_run_article):
        summary = run(
            collectors=[FakeCollector("フィード1", articles=[article])],
            relevance_filter=keyword_filter,
            repository=repo,
            notification_repository=notif_repo,
            notifier=FakeBatchNotifier(succeed=True),
            max_notifications=20,
        )

    assert summary.saved == 0
    assert summary.near_duplicates == 1
    assert repo.count() == 1


def test_old_articles_are_saved_but_not_notified(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    now = datetime.now(timezone.utc)
    fresh = _article(
        "ポケモンのコラボカフェが本日オープンしました",
        "https://example.com/fresh",
        now - timedelta(days=1),
    )
    old = _article(
        "ポケモンのポップアップストアが渋谷で開催中です",
        "https://example.com/old",
        now - timedelta(days=10),
    )
    undated = _article(
        "ポケモンの限定グッズが全国の店舗で発売されます", "https://example.com/undated", None
    )
    notifier = FakeBatchNotifier(succeed=True)

    summary = run(
        collectors=[FakeCollector("フィード1", articles=[fresh, old, undated])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
        max_article_age_days=3,
    )

    assert summary.saved == 3  # 古い記事も保存はする（以後の重複判定のため）
    assert summary.too_old == 1
    notified = {e.url for e in notifier.batches[0]}
    # 公開日時が分からない記事は古いとみなさない
    assert notified == {"https://example.com/fresh", "https://example.com/undated"}
    assert notif_repo.get(make_content_hash(old.title, old.url)) is None

    # 次の実行で同じ古い記事が流れてきても、保存済みなので通知されない
    again = run(
        collectors=[FakeCollector("フィード1", articles=[old])],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=FakeBatchNotifier(succeed=True),
        max_notifications=20,
        max_article_age_days=3,
    )
    assert again.saved == 0
    assert again.notified == 0


# --- 媒体の優先順位（同じ記事のどの版を残すか） ---


def test_preferred_outlet_version_is_kept_even_if_it_arrives_later(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    """転載（Yahoo!）が先のフィードに来ても、優先媒体（GAME Watch）の版を残す。"""
    now = datetime.now(timezone.utc)
    via_yahoo = _article(
        "ミスド「ポケモン」コラボが11月に開催決定！ピカチュウたちが大集合（GAME Watch） - Yahoo!ニュース",
        "https://example.com/yahoo",
        now,
    )
    original = _article(
        "ミスド「ポケモン」コラボが11月に開催決定！ピカチュウたちが大集合 - GAME Watch",
        "https://example.com/gamewatch",
        now,
    )
    notifier = FakeBatchNotifier(succeed=True)

    summary = run(
        collectors=[
            FakeCollector("フィード1", articles=[via_yahoo]),
            FakeCollector("フィード2", articles=[original]),
        ],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
        source_priority=SourcePriority(
            preferred=["GAME Watch"], deprioritized=["Yahoo!ニュース"]
        ),
    )

    assert summary.saved == 1
    assert [e.url for e in notifier.batches[0]] == ["https://example.com/gamewatch"]


def test_version_with_summary_wins_when_outlets_rank_equally(
    repo: SQLiteEventRepository,
    notif_repo: SQLiteNotificationRepository,
    keyword_filter: KeywordFilter,
) -> None:
    now = datetime.now(timezone.utc)
    without_summary = _article(
        "ポケモンとクロックスのコラボが決定しました - 媒体A", "https://example.com/a", now
    )
    with_summary = Article(
        title="ポケモンとクロックスのコラボが決定しました - 媒体B",
        url="https://example.com/b",
        source="フィード2",
        published_at=now,
        summary="11月から全国で発売。",
    )
    notifier = FakeBatchNotifier(succeed=True)

    run(
        collectors=[
            FakeCollector("フィード1", articles=[without_summary]),
            FakeCollector("フィード2", articles=[with_summary]),
        ],
        relevance_filter=keyword_filter,
        repository=repo,
        notification_repository=notif_repo,
        notifier=notifier,
        max_notifications=20,
    )

    assert [e.url for e in notifier.batches[0]] == ["https://example.com/b"]
