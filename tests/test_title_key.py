# -*- coding: utf-8 -*-
"""make_title_key（別ソースで同じ記事を拾ったときの名寄せキー）のテスト。"""

from __future__ import annotations

from src.models import MIN_TITLE_KEY_LENGTH, is_similar_title_key, make_title_key


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


# --- 転載の付け足し（Yahoo! / ライブドア）と全角・半角の揺れ ---


def test_yahoo_repost_with_outlet_in_parentheses_shares_a_key() -> None:
    """「タイトル（媒体名） - Yahoo!ニュース」は元記事と同じキーになる。"""
    original = "ミスド「ポケモン」コラボが11月に開催決定！ピカチュウたちが大集合 - GAME Watch"
    via_yahoo = "ミスド「ポケモン」コラボが11月に開催決定！ピカチュウたちが大集合（GAME Watch） - Yahoo!ニュース"

    assert make_title_key(original) == make_title_key(via_yahoo)


def test_livedoor_posted_date_is_ignored() -> None:
    original = "【ミスド×ポケモン】11/4から店頭販売開始！限定グッズセットも登場 - 千葉テレビ放送株式会社"
    via_livedoor = (
        "【ミスド×ポケモン】11/4から店頭販売開始！限定グッズセットも登場 (2026年11月4日掲載) - ライブドアニュース"
    )

    assert make_title_key(original) == make_title_key(via_livedoor)


def test_full_width_and_half_width_punctuation_are_unified() -> None:
    assert make_title_key("歴代ポケモンが大集結！キャンペーンが11月4日スタート！") == make_title_key(
        "歴代ポケモンが大集結!キャンペーンが11月4日スタート!"
    )


def test_parentheses_that_are_part_of_the_headline_are_kept() -> None:
    """Yahoo! 以外の末尾の括弧は見出しの一部（第何弾など）なので剥がさない。"""
    first = make_title_key("ポケモンセンターでコラボグッズが発売（第1弾） - GAME Watch")
    second = make_title_key("ポケモンセンターでコラボグッズが発売（第2弾） - GAME Watch")

    assert first != second


# --- is_similar_title_key（見出しが少しだけ違う転載の名寄せ） ---


def test_slightly_reworded_repost_is_similar() -> None:
    a = make_title_key("10月24日（土）ご来店予定のお客様へ - ポケットモンスターオフィシャルサイト")
    b = make_title_key("10月24日（土）にご来店予定のお客様へ - ポケットモンスターオフィシャルサイト")

    assert a != b
    assert is_similar_title_key(a, b)


def test_headlines_with_conflicting_numbers_are_not_similar() -> None:
    """続報・別日程の告知は見出しが似ていても別記事として扱う。"""
    a = make_title_key("ポケモンセンターで限定コラボグッズの抽選販売を実施します（第1弾）")
    b = make_title_key("ポケモンセンターで限定コラボグッズの抽選販売を実施します（第2弾）")

    assert not is_similar_title_key(a, b)


def test_different_products_from_the_same_outlet_are_not_similar() -> None:
    a = make_title_key(
        "「ピカピカボックス2027」が、ポケモンセンターオンラインに登場！｜ポケットモンスターオフィシャルサイト"
    )
    b = make_title_key(
        "キミだけの「てもち」が作れるカスタマイズグッズがポケモンセンターに登場！｜ポケットモンスターオフィシャルサイト"
    )

    assert not is_similar_title_key(a, b)


def test_empty_keys_are_never_similar() -> None:
    assert not is_similar_title_key("", "")
    assert not is_similar_title_key("", make_title_key("ポケモンのコラボカフェが開店します"))
