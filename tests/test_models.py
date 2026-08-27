# -*- coding: utf-8 -*-
"""models.py（Article / Event / make_content_hash）のテスト。"""

from __future__ import annotations

from datetime import datetime, timezone

from src.models import Article, Event, make_content_hash
from tests.conftest import make_article


def test_make_content_hash_is_stable_for_same_input() -> None:
    """同じ title/url からは常に同じハッシュが得られる。"""
    h1 = make_content_hash("ポケモン新作発表", "https://example.com/a")
    h2 = make_content_hash("ポケモン新作発表", "https://example.com/a")

    assert h1 == h2


def test_make_content_hash_differs_for_different_title() -> None:
    """title が違えばハッシュも変わる。"""
    h1 = make_content_hash("ポケモン新作発表", "https://example.com/a")
    h2 = make_content_hash("ゼルダ新作発表", "https://example.com/a")

    assert h1 != h2


def test_make_content_hash_differs_for_different_url() -> None:
    """url が違えばハッシュも変わる。"""
    h1 = make_content_hash("ポケモン新作発表", "https://example.com/a")
    h2 = make_content_hash("ポケモン新作発表", "https://example.com/b")

    assert h1 != h2


def test_make_content_hash_is_case_insensitive() -> None:
    """大文字小文字の違いは正規化されて同じハッシュになる。"""
    h1 = make_content_hash("Pokemon News", "https://example.com/A")
    h2 = make_content_hash("pokemon news", "https://example.com/a")

    assert h1 == h2


def test_make_content_hash_strips_surrounding_whitespace() -> None:
    """前後の空白は正規化されて同じハッシュになる。"""
    h1 = make_content_hash("  ポケモン新作発表  ", "  https://example.com/a  ")
    h2 = make_content_hash("ポケモン新作発表", "https://example.com/a")

    assert h1 == h2


def test_make_content_hash_returns_sha256_hex_digest() -> None:
    """sha256 の 16 進ダイジェスト（64 文字）であること。"""
    h = make_content_hash("タイトル", "https://example.com/x")

    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)


def test_article_searchable_text_combines_title_and_summary() -> None:
    """searchable_text はタイトルと要約を結合したもの。"""
    article = Article(title="タイトル", url="https://example.com/1", source="src", summary="要約")

    assert article.searchable_text == "タイトル\n要約"


def test_event_from_article_computes_content_hash_and_copies_fields() -> None:
    """Event.from_article は Article の情報を引き継ぎつつ、content_hash を計算する。"""
    published = datetime(2026, 1, 1, tzinfo=timezone.utc)
    article = make_article(
        title="ポケモン最新情報",
        url="https://example.com/pokemon",
        source="テストフィード",
        published_at=published,
    )
    detected_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

    event = Event.from_article(article, matched_targets=["ポケモン"], detected_at=detected_at)

    assert event.title == article.title
    assert event.url == article.url
    assert event.source == article.source
    assert event.published_at == published
    assert event.detected_at == detected_at
    assert event.matched_targets == ["ポケモン"]
    assert event.content_hash == make_content_hash(article.title, article.url)


def test_event_from_article_defaults_detected_at_to_now() -> None:
    """detected_at を渡さなければ現在時刻（tz-aware）が使われる。"""
    article = make_article()
    before = datetime.now(timezone.utc)

    event = Event.from_article(article, matched_targets=[])

    after = datetime.now(timezone.utc)
    assert event.detected_at.tzinfo is not None
    assert before <= event.detected_at <= after


def test_event_from_article_copies_matched_targets_list_not_reference() -> None:
    """matched_targets は list() でコピーされ、呼び出し元のリストと別オブジェクトになる。"""
    article = make_article()
    original = ["ポケモン"]

    event = Event.from_article(article, matched_targets=original)
    original.append("ゼルダ")

    assert event.matched_targets == ["ポケモン"]
