# -*- coding: utf-8 -*-
"""FeedRequirementFilter のテスト。

「フィードごとの必須キーワード（AND）で絞ってから、内側のフィルタに渡す」
という合成の挙動を検証する。
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.config import Target
from src.filters.feed_requirement import FeedRequirementFilter
from src.filters.keyword import KeywordFilter
from src.models import Article

POKEMON_TARGET = Target(name="ポケモン", keywords=["ポケモン", "ピカチュウ"])
COLLAB_KEYWORDS = ["コラボ", "キャンペーン"]


def make_article(title: str, source: str, summary: str = "") -> Article:
    return Article(
        title=title,
        url=f"https://example.com/{abs(hash(title))}",
        source=source,
        published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        summary=summary,
    )


def make_filter() -> FeedRequirementFilter:
    return FeedRequirementFilter(
        KeywordFilter([POKEMON_TARGET]),
        {"一般ニュース": COLLAB_KEYWORDS},
    )


def test_article_from_restricted_feed_needs_a_required_keyword() -> None:
    """必須キーワードを持つフィードでは、ポケモン記事でもコラボ語が無ければ通さない。"""
    relevance_filter = make_filter()

    without_collab = make_article("新作ポケモンゲームの発売日が決定", "一般ニュース")
    with_collab = make_article("ポケモンとカフェのコラボが決定", "一般ニュース")

    assert relevance_filter.match(without_collab) == []
    assert relevance_filter.match(with_collab) == ["ポケモン"]


def test_required_keyword_may_come_from_the_summary() -> None:
    """判定はタイトルだけでなく要約も見る（要約を持つフィード向け）。"""
    relevance_filter = make_filter()
    article = make_article(
        "ポケモン、サーモスとの新シリーズを発表",
        "一般ニュース",
        summary="9月1日よりコラボ商品を順次発売します。",
    )

    assert relevance_filter.match(article) == ["ポケモン"]


def test_feed_without_requirements_is_passed_through() -> None:
    """必須キーワードを設定していないフィード（検索型）は素通しする。"""
    relevance_filter = make_filter()
    article = make_article("新作ポケモンゲームの発売日が決定", "検索フィード")

    assert relevance_filter.match(article) == ["ポケモン"]


def test_required_keyword_alone_is_not_enough() -> None:
    """コラボ語だけあってもポケモンでなければ通さない（内側の判定は効いたまま）。"""
    relevance_filter = make_filter()
    article = make_article("サンリオとカフェのコラボが決定", "一般ニュース")

    assert relevance_filter.match(article) == []


def test_matching_is_case_insensitive() -> None:
    relevance_filter = FeedRequirementFilter(
        KeywordFilter([Target(name="ポケモン", keywords=["pokemon"])]),
        {"一般ニュース": ["COLLAB"]},
    )
    article = make_article("Pokemon x Crocs collab announced", "一般ニュース")

    assert relevance_filter.match(article) == ["ポケモン"]


def test_blank_requirements_are_treated_as_no_requirement() -> None:
    """空文字だけの require_any は「条件なし」として扱う（設定ミスで全滅させない）。"""
    relevance_filter = FeedRequirementFilter(
        KeywordFilter([POKEMON_TARGET]), {"一般ニュース": ["", "  "]}
    )
    article = make_article("新作ポケモンゲームの発売日が決定", "一般ニュース")

    assert relevance_filter.match(article) == ["ポケモン"]
