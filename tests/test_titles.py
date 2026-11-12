# -*- coding: utf-8 -*-
"""titles（見出しの配信元表記・画像ページの扱い）のテスト。"""

from __future__ import annotations

import pytest

from src.titles import is_gallery_page, outlet_name, strip_source_suffixes


@pytest.mark.parametrize(
    "title",
    [
        # 実際に古い記事が新しい日付で出し直されていた見出し
        "＜画像1 / 6＞人気アニメ「ハイキュー!!」イベントに楽しいコラボメニュー登場 - ウォーカープラス",
        "画像2 / 11＞人気アニメ『ハイキュー!!』とコールマンが初コラボ！ - ウォーカープラス",
        "画像1 / 7＞毎年人気の「ミスド×ポケモン」コラボ！ - ウォーカープラス",
        "「リラックマ×ハイキュー!!」POP UP STOREのグッズ [画像ギャラリー 16/18] - ナタリー",
        "「ハイキュー!!」烏野・青葉城西がラーメンに！全8商品が登場 1枚目の写真・画像 - アニメ！アニメ！",
    ],
)
def test_gallery_pages_are_detected(title: str) -> None:
    assert is_gallery_page(title)


@pytest.mark.parametrize(
    "title",
    [
        # 写真付きのレポート記事や、「画像」を含むだけの普通の記事は対象外
        "「ハイキュー!!」原画展、描き下ろしイラストで初公開（イベントレポート / 写真27枚） - ナタリー",
        "ミスタードーナツ『ポケモン』コラボ匂わせ告知画像にネット衝撃「まさかの…」 - オリコンニュース",
        "『ハイキュー!!』『イノセンス』のProduction I.G、「京アニ」との“意外すぎる関係”(2/3) - ビジネス+IT",
        "クロックス「ポケモン」コラボの新商品が11月17日発売！ - GAME Watch",
    ],
)
def test_regular_articles_are_not_gallery_pages(title: str) -> None:
    assert not is_gallery_page(title)


def test_strip_source_suffixes_handles_reposts() -> None:
    assert strip_source_suffixes("ミスドとポケモンのコラボ（GAME Watch） - Yahoo!ニュース") == "ミスドとポケモンのコラボ"
    assert strip_source_suffixes("ミスドとポケモンのコラボ (2026年11月4日掲載) - ライブドアニュース") == "ミスドとポケモンのコラボ"


def test_outlet_name_is_normalized() -> None:
    assert outlet_name("見出し - ＧＡＭＥ Watch", "feed") == "game watch"
