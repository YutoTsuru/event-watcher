# -*- coding: utf-8 -*-
"""make_title_key（別ソースで同じ記事を拾ったときの名寄せキー）のテスト。"""

from __future__ import annotations

from src.models import MIN_TITLE_KEY_LENGTH, make_title_key


def test_same_article_from_different_sources_shares_a_key() -> None:
    """Google ニュース経由（末尾に媒体名が付く）と直接ソースが同じキーになる。"""
    via_google = "ポケモン×ポラロイド！ 初コラボでピカチュウモチーフのカメラも登場 - 朝日新聞"
    via_source = "ポケモン×ポラロイド！ 初コラボでピカチュウモチーフのカメラも登場"

    assert make_title_key(via_google) == make_title_key(via_source)
    assert make_title_key(via_google) != ""


def test_full_width_separator_is_also_treated_as_a_source_suffix() -> None:
    assert make_title_key("ポケモンとクロックスのコラボが決定｜ファミ通") == make_title_key(
        "ポケモンとクロックスのコラボが決定"
    )


def test_symbols_and_spacing_are_ignored() -> None:
    assert make_title_key("ポケモン コラボ カフェが 開店します") == make_title_key(
        "【ポケモン】コラボ・カフェが開店します！"
    )


def test_different_articles_get_different_keys() -> None:
    first = make_title_key("ポケモンとクロックスのコラボが決定しました")
    second = make_title_key("ポケモンとポラロイドのコラボが決定しました")

    assert first != second


def test_short_titles_are_excluded_from_matching() -> None:
    """短いタイトルは別記事どうしが偶然一致しうるので、名寄せに使わない。"""
    assert make_title_key("ポケモン新作") == ""
    assert make_title_key("") == ""


def test_key_length_threshold_is_applied_after_normalization() -> None:
    """記号を除いた長さで判定する（記号で水増しされたタイトルを通さない）。"""
    assert make_title_key("【ポケモン】！！！") == ""

    long_enough = "ポケモンのコラボが決定しました"
    assert len(make_title_key(long_enough)) >= MIN_TITLE_KEY_LENGTH


def test_suffix_removal_does_not_empty_a_hyphenated_title() -> None:
    """末尾の媒体名除去は最後の 1 区切りだけ。本文まで削って空にはしない。"""
    key = make_title_key("ポケモン スカーレット・バイオレット 最新情報 - ファミ通")

    assert key != ""
    assert "ファミ通" not in key
    assert "ポケモン" in key
