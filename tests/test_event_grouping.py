# -*- coding: utf-8 -*-
"""EventGrouper（同じ出来事＝同じコラボを報じた別媒体の記事の判定）のテスト。

見出しは実際に Google ニュースで拾ったものをもとにしている。
"""

from __future__ import annotations

import pytest

from src.event_grouping import EventGrouper

HAIKYU = EventGrouper(stopwords=["ハイキュー", "HAIKYU", "日向", "翔陽", "烏野", "音駒", "稲荷崎"])
POKEMON = EventGrouper(stopwords=["ポケモン", "ポケットモンスター", "ピカチュウ", "ポケモンセンター"])


def same(grouper: EventGrouper, a: str, b: str) -> bool:
    return grouper.is_same_event(grouper.signature(a), grouper.signature(b))


@pytest.mark.parametrize(
    "a, b",
    [
        # 見出しはまったく違うが、モスバーガー / 福袋が共通
        (
            "モスバーガー×ハイキュー!!「2027モス福袋」事前予約は11月25日から！ - グルメ Watch",
            "モスの「ハイキュー!!」福袋が登場 5000円で食事券5000円分＋限定グッズ4種 - おたくま経済新聞",
        ),
        # 「モスフードサービス」と「モス」は先頭一致で同じ語とみなす
        (
            "モスフードサービス、「ハイキュー！！」コラボ福袋を発売 - https://kabu-ir.com/",
            "【モスバーガー】今年の福袋は「ハイキュー！！」コラボ - オトナンサー",
        ),
        # コラボ相手（× の隣）が一致すれば、特徴語が 1 つでも同じ出来事
        (
            "モスバーガーコラボ開催決定！ - ハイキュー!!",
            "ハイキュー!! × モスバーガー 11月4日よりコラボ開始! - コラボカフェ",
        ),
        (
            "ニューエラ（NEW ERA）×「ポケモン」30周年商品の発売日が11月20日に決定！ - GAME Watch",
            "New Era x Pokémon 30周年記念コラボコレクションが国内11月20日より発売 - UP TO DATE",
        ),
        (
            "楽天ブックス×ポケモン30周年記念キャンペーンが開催！ - Nintendo DREAM WEB",
            "「楽天ブックス」がポケモン30周年記念キャンペーン実施 - ライブドアニュース",
        ),
    ],
)
def test_articles_about_the_same_collab_are_grouped(a: str, b: str) -> None:
    grouper = HAIKYU if "ハイキュー" in a else POKEMON
    assert same(grouper, a, b)


@pytest.mark.parametrize(
    "a, b",
    [
        # 学校名（stopwords）が共通していても、コラボ相手が違えば別の出来事
        (
            "『ハイキュー!!』×パシオス、烏野・音駒・稲荷崎のルームウェアが11月4日発売！ - にじめん",
            "「ハイキュー!!」×宅麺コラボラーメン全8商品が11月12日発売！烏野・音駒の4校をイメージ - にじめん",
        ),
        # 「POP UP STORE」のような一般語だけが共通する別のコラボ
        (
            "「リラックマ×ハイキュー!!」POP UP STOREのグッズ - ナタリー",
            "イオンモール盛岡で「ハイキュー！！」POP UP STORE開催！ - じゃんごブログ",
        ),
        # 「周年記念」「コラボアイテム」は一般語なので、それだけでは同じにしない
        (
            "3COINS x ポケモン 30周年記念コラボアイテムが再販受注販売 - UP TO DATE",
            "Pokémon TCG x Santa Cruz ポケモン30周年記念コラボアイテムが国内順次発売中 - UP TO DATE",
        ),
        # 「Pokémon」を pok / mon に割って、無関係な記事どうしを一致させない
        (
            "「My Pokémon Party」がポケモンセンターに登場！ - ポケットモンスターオフィシャルサイト",
            "Pokémon UNITE が2027年3月にサービス終了へ - Hypebeast",
        ),
    ],
)
def test_different_collabs_are_not_grouped(a: str, b: str) -> None:
    grouper = HAIKYU if "ハイキュー" in a else POKEMON
    assert not same(grouper, a, b)


def test_generic_words_and_topic_words_are_not_tokens() -> None:
    signature = HAIKYU.signature("ハイキュー!!×宅麺 コラボラーメン 発売決定！ - コラボカフェ")

    assert "宅麺" in signature.tokens
    assert "ラーメン" in signature.tokens  # 「コラボラーメン」から一般語の「コラボ」だけを削る
    assert "ハイキュー" not in signature.tokens
    assert "コラボ" not in signature.tokens
    assert "発売" not in signature.tokens
    # 媒体名（見出し末尾の「 - コラボカフェ」）は特徴語にしない
    assert "コラボカフェ" not in signature.tokens


def test_partner_is_the_word_next_to_the_cross() -> None:
    assert HAIKYU.signature("『ハイキュー!!』×「モスバーガー」コラボ福袋の予約は11.25から！").partners == {
        "モスバーガー"
    }
    assert POKEMON.signature("「BOSS×ポケモン30周年」缶コーヒーケース購入でメモ帳がもらえる").partners == {
        "boss"
    }
