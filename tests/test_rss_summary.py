# -*- coding: utf-8 -*-
"""RSS の要約整形（clean_summary）のテスト。

フィードごとに要約の中身の質が違うため、実際に各フィードが返す形を模した
入力で検証する。ネットワークアクセスは行わない。
"""

from __future__ import annotations

from src.collectors.rss import MAX_SUMMARY_LENGTH, clean_summary


def test_google_news_summary_is_dropped_as_restatement() -> None:
    """Google ニュースの要約はタイトルの焼き直しなので、空にして「要約なし」とする。"""
    title = "ポケモン×ポラロイド！ 初コラボでピカチュウモチーフのカメラも登場 - 朝日新聞"
    raw = (
        '<a href="https://news.google.com/rss/articles/CBMiV0FV?oc=5" target="_blank">'
        "ポケモン×ポラロイド！ 初コラボでピカチュウモチーフのカメラも登場</a>"
        '&nbsp;&nbsp;<font color="#6f6f6f">朝日新聞</font>'
    )

    assert clean_summary(raw, title) == ""


def test_pr_times_boilerplate_is_removed() -> None:
    """PR TIMES の先頭に入る [会社名] や [画像1: URL] は落として本文だけ残す。"""
    title = "ポケモンとのコラボ商品を発売"
    raw = (
        "[株式会社サンプル]\n"
        "[画像1: https://prcdn.freetls.fastly.net/release_image/1/2/3.png?width=536 ]\n"
        "9月1日より、全国の店舗でコラボグッズの取り扱いを開始します。"
    )

    assert clean_summary(raw, title) == "9月1日より、全国の店舗でコラボグッズの取り扱いを開始します。"


def test_plain_summary_is_kept_as_is() -> None:
    """GAME Watch / ITmedia のような、そのまま使える要約は触らない。"""
    title = "USJ、パーク拡張に関する報道を否定"
    raw = "ユニバーサル・スタジオ・ジャパンは8月28日、「本日8月28日(金)の報道内容について」を公開した。"

    assert clean_summary(raw, title) == raw


def test_html_entities_and_whitespace_are_normalized() -> None:
    title = "タイトル"
    raw = "  <p>コラボ&amp;キャンペーンが\n\n  開催されます。</p>  "

    assert clean_summary(raw, title) == "コラボ&キャンペーンが 開催されます。"


def test_empty_summary_returns_empty() -> None:
    """4Gamer のように要約を持たないフィードでも落ちない。"""
    assert clean_summary("", "タイトル") == ""
    assert clean_summary("   ", "タイトル") == ""


def test_summary_containing_title_plus_body_is_kept() -> None:
    """タイトルを含んでいても、それ以上の中身があるなら要約として残す。"""
    title = "ポケモンコラボ開催"
    raw = (
        "ポケモンコラボ開催。9月1日から10月31日まで、全国50店舗で限定グッズの販売と"
        "スタンプラリーを実施します。参加は無料で、事前予約は不要です。"
    )

    assert clean_summary(raw, title) == raw


def test_long_summary_is_truncated_for_storage() -> None:
    raw = "あ" * (MAX_SUMMARY_LENGTH + 200)

    assert len(clean_summary(raw, "タイトル")) == MAX_SUMMARY_LENGTH
