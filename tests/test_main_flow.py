# -*- coding: utf-8 -*-
"""main.run() のエンドツーエンドテスト。

Collector / Notifier はすべてフェイク（メモリ内）実装を使い、
実ネットワークアクセス・実 Discord 通信は一切行わない。
SQLite は pytest の tmp_path 配下のみを使う。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.collectors.base import Collector
from src.config import Target
from src.filters.keyword import KeywordFilter
from src.main import run
from src.models import Article
from src.notifiers.base import Notifier
from src.models import Event
from src.repositories.sqlite import SQLiteEventRepository


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


def make_pokemon_article(url: str = "https://example.com/pokemon-1") -> Article:
    return Article(
        title="新作ポケモンゲームが発表",
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
def keyword_filter(pokemon_target: Target) -> KeywordFilter:
    return KeywordFilter([pokemon_target])


def test_run_saves_and_notifies_only_new_matched_articles(
    repo: SQLiteEventRepository, keyword_filter: KeywordFilter
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
    repo: SQLiteEventRepository, keyword_filter: KeywordFilter
) -> None:
    """同じ記事を 2 回収集しても、2 回目は保存も通知もされない。"""
    matched = make_pokemon_article()
    notifier = FakeNotifier(succeed=True)

    first_summary = run(
        collectors=[FakeCollector("フィード1", articles=[matched])],
        relevance_filter=keyword_filter,
        repository=repo,
        notifier=notifier,
        max_notifications=20,
    )
    second_summary = run(
        collectors=[FakeCollector("フィード1", articles=[matched])],
        relevance_filter=keyword_filter,
        repository=repo,
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
    repo: SQLiteEventRepository, keyword_filter: KeywordFilter
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
        notifier=notifier,
        max_notifications=20,
    )

    assert summary.collected == 1  # 壊れたフィードからは 0 件、正常な方から 1 件
    assert summary.matched == 1
    assert summary.saved == 1
    assert summary.notified == 1
    assert repo.count() == 1


def test_run_counts_notify_failed_without_aborting(
    repo: SQLiteEventRepository, keyword_filter: KeywordFilter
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
    repo: SQLiteEventRepository, keyword_filter: KeywordFilter
) -> None:
    """notifier=None（--dry-run 相当）でも保存は行われ、通知系カウントは 0 のまま。"""
    article = make_pokemon_article()
    collector = FakeCollector("フィード1", articles=[article])

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notifier=None,
        max_notifications=20,
    )

    assert summary.saved == 1
    assert summary.notified == 0
    assert summary.notify_failed == 0
    assert repo.count() == 1


def test_run_respects_max_notifications_per_run(
    repo: SQLiteEventRepository, keyword_filter: KeywordFilter
) -> None:
    """通知数が上限に達したら、以降は保存のみ行い通知はしない。"""
    articles = [make_pokemon_article(url=f"https://example.com/pokemon-{i}") for i in range(3)]
    collector = FakeCollector("フィード1", articles=articles)
    notifier = FakeNotifier(succeed=True)

    summary = run(
        collectors=[collector],
        relevance_filter=keyword_filter,
        repository=repo,
        notifier=notifier,
        max_notifications=1,
    )

    assert summary.saved == 3
    assert summary.notified == 1
    assert summary.notify_failed == 0
    assert repo.count() == 3
