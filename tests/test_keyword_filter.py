# -*- coding: utf-8 -*-
"""KeywordFilter のテスト。

- キーワード一致 / 不一致
- 複数キーワード・複数対象の同時一致
- 大文字小文字の区別なし（日本語・英語両方）
"""

from __future__ import annotations

from src.config import Target
from src.filters.keyword import KeywordFilter
from tests.conftest import make_article


def test_match_hit_japanese_keyword(pokemon_target: Target) -> None:
    """日本語キーワードがタイトルに含まれていればヒットする。"""
    filt = KeywordFilter([pokemon_target])
    article = make_article(title="新作ポケモンが発表", summary="")

    matched = filt.match(article)

    assert matched == ["ポケモン"]


def test_match_no_hit(pokemon_target: Target) -> None:
    """どのキーワードにも一致しなければ空リストを返す。"""
    filt = KeywordFilter([pokemon_target])
    article = make_article(title="無関係なニュース", summary="今日の天気について")

    matched = filt.match(article)

    assert matched == []


def test_match_hits_multiple_targets_at_once(targets: list[Target]) -> None:
    """1 記事が複数の監視対象に同時に一致する場合、両方の名前が返る。"""
    filt = KeywordFilter(targets)
    article = make_article(
        title="ポケモンとゼルダのクロスオーバー企画",
        summary="",
    )

    matched = filt.match(article)

    assert set(matched) == {"ポケモン", "ゼルダ"}
    assert len(matched) == 2


def test_match_multiple_keywords_same_target(pokemon_target: Target) -> None:
    """同一対象内の複数キーワードのうちどれか一つでも一致すればヒットする。"""
    filt = KeywordFilter([pokemon_target])
    article_en = make_article(title="New Pokemon game announced", summary="")
    article_alt = make_article(title="pikachuが主役の新グッズ", summary="")

    assert filt.match(article_en) == ["ポケモン"]
    assert filt.match(article_alt) == ["ポケモン"]


def test_match_is_case_insensitive_english(pokemon_target: Target) -> None:
    """英語キーワードは大文字小文字を無視して一致する。"""
    filt = KeywordFilter([pokemon_target])
    article_upper = make_article(title="POKEMON NEWS TODAY", summary="")
    article_mixed = make_article(title="PoKeMoN special event", summary="")

    assert filt.match(article_upper) == ["ポケモン"]
    assert filt.match(article_mixed) == ["ポケモン"]


def test_match_uses_summary_as_well_as_title(pokemon_target: Target) -> None:
    """summary（要約）側にキーワードがあってもヒットする（searchable_text はタイトル＋要約）。"""
    filt = KeywordFilter([pokemon_target])
    article = make_article(title="今週の新着情報", summary="今週はポケモン関連の話題が多い")

    matched = filt.match(article)

    assert matched == ["ポケモン"]


def test_match_returns_empty_list_for_empty_targets() -> None:
    """監視対象が 1 件も無ければ何も一致しない。"""
    filt = KeywordFilter([])
    article = make_article(title="ポケモン最新情報", summary="")

    assert filt.match(article) == []


def test_match_ignores_blank_keywords() -> None:
    """空白のみのキーワードは前処理で除外され、一致判定に使われない。"""
    target = Target(name="テスト対象", keywords=["   ", "ゼルダ"])
    filt = KeywordFilter([target])
    article = make_article(title="   の新作発表", summary="")

    # 空白キーワードでは一致せず、ゼルダにも一致しない
    assert filt.match(article) == []
