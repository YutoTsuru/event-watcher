# -*- coding: utf-8 -*-
"""SQLiteEventRepository のテスト。

tmp_path 配下の使い捨て DB のみを使い、data/ 配下には一切書き込まない。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.models import Event, make_content_hash
from src.repositories.sqlite import SQLiteEventRepository


_UNSET = object()


def make_event(
    title: str = "ポケモン新作発表",
    url: str = "https://example.com/pokemon-1",
    source: str = "テストフィード",
    published_at=_UNSET,
    detected_at=None,
    matched_targets=None,
) -> Event:
    # published_at は明示的に None を渡せる（未指定時のみデフォルト値を使う）
    if published_at is _UNSET:
        published_at = datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)
    return Event(
        title=title,
        url=url,
        source=source,
        published_at=published_at,
        detected_at=detected_at or datetime(2026, 1, 2, 10, 30, tzinfo=timezone.utc),
        content_hash=make_content_hash(title, url),
        matched_targets=matched_targets if matched_targets is not None else ["ポケモン"],
    )


def test_save_new_event_returns_true_and_increments_count(
    repository: SQLiteEventRepository,
) -> None:
    event = make_event()

    saved = repository.save(event)

    assert saved is True
    assert repository.count() == 1


def test_save_duplicate_same_url_and_hash_returns_false(
    repository: SQLiteEventRepository,
) -> None:
    """同一 url・同一 content_hash の 2 回目の save は False で、件数も増えない。"""
    event = make_event()
    repository.save(event)

    saved_again = repository.save(event)

    assert saved_again is False
    assert repository.count() == 1


def test_save_duplicate_same_url_different_hash_returns_false(
    repository: SQLiteEventRepository,
) -> None:
    """url が UNIQUE 制約のため、hash が違っても同一 url なら保存されない。"""
    event = make_event(url="https://example.com/dup", title="タイトルA")
    other = make_event(url="https://example.com/dup", title="タイトルB")
    repository.save(event)

    saved_again = repository.save(other)

    assert saved_again is False
    assert repository.count() == 1


def test_exists_reflects_saved_state(repository: SQLiteEventRepository) -> None:
    event = make_event()

    assert repository.exists(event.url, event.content_hash) is False

    repository.save(event)

    assert repository.exists(event.url, event.content_hash) is True


def test_exists_matches_on_url_or_hash_independently(
    repository: SQLiteEventRepository,
) -> None:
    """exists は url または content_hash のどちらか一方の一致でも True になる。"""
    event = make_event(url="https://example.com/x", title="X")
    repository.save(event)

    # 同じ url、違う hash
    assert repository.exists("https://example.com/x", "違うハッシュ") is True
    # 違う url、同じ hash
    assert repository.exists("https://example.com/y", event.content_hash) is True
    # どちらも違う
    assert repository.exists("https://example.com/z", "存在しないハッシュ") is False


def test_find_by_hash_round_trips_matched_targets_and_datetimes(
    repository: SQLiteEventRepository,
) -> None:
    """保存・取得を通じて matched_targets（リスト）と日時が正しく往復する。"""
    published_at = datetime(2026, 3, 4, 12, 0, tzinfo=timezone.utc)
    detected_at = datetime(2026, 3, 4, 13, 30, tzinfo=timezone.utc)
    event = make_event(
        title="ポケモンとゼルダの合同企画",
        url="https://example.com/crossover",
        published_at=published_at,
        detected_at=detected_at,
        matched_targets=["ポケモン", "ゼルダ"],
    )
    repository.save(event)

    found = repository.find_by_hash(event.content_hash)

    assert found is not None
    assert found.title == event.title
    assert found.url == event.url
    assert found.source == event.source
    assert found.content_hash == event.content_hash
    assert found.matched_targets == ["ポケモン", "ゼルダ"]
    assert found.published_at == published_at
    assert found.detected_at == detected_at


def test_find_by_hash_returns_none_when_not_found(
    repository: SQLiteEventRepository,
) -> None:
    assert repository.find_by_hash("存在しないハッシュ") is None


def test_find_by_hash_handles_none_published_at(
    repository: SQLiteEventRepository,
) -> None:
    """published_at が None の場合も正しく往復する（RSS で日時が取れないケース）。"""
    event = make_event(published_at=None, url="https://example.com/no-date")
    repository.save(event)

    found = repository.find_by_hash(event.content_hash)

    assert found is not None
    assert found.published_at is None


def test_list_recent_orders_by_detected_at_descending(
    repository: SQLiteEventRepository,
) -> None:
    older = make_event(
        title="古い記事",
        url="https://example.com/old",
        detected_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    newer = make_event(
        title="新しい記事",
        url="https://example.com/new",
        detected_at=datetime(2026, 1, 5, tzinfo=timezone.utc),
    )
    repository.save(older)
    repository.save(newer)

    recent = repository.list_recent(limit=10)

    assert [e.url for e in recent] == [newer.url, older.url]


def test_list_recent_respects_limit(repository: SQLiteEventRepository) -> None:
    for i in range(5):
        repository.save(
            make_event(
                title=f"記事{i}",
                url=f"https://example.com/{i}",
                detected_at=datetime(2026, 1, 1 + i, tzinfo=timezone.utc),
            )
        )

    recent = repository.list_recent(limit=2)

    assert len(recent) == 2
    # 最新2件（記事4, 記事3）が返る
    assert recent[0].url == "https://example.com/4"
    assert recent[1].url == "https://example.com/3"


def test_count_reflects_number_of_saved_events(repository: SQLiteEventRepository) -> None:
    assert repository.count() == 0

    repository.save(make_event(url="https://example.com/1", title="A"))
    assert repository.count() == 1

    repository.save(make_event(url="https://example.com/2", title="B"))
    assert repository.count() == 2


def test_repository_creates_db_file_under_tmp_path(tmp_path: Path) -> None:
    """DB ファイルが tmp_path 配下に作られ、data/ には触れないことを確認。"""
    db_path = tmp_path / "nested" / "events.db"
    repo = SQLiteEventRepository(db_path)
    repo.initialize()

    assert db_path.exists()
