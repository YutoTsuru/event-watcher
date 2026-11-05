# -*- coding: utf-8 -*-
"""SourcePriority（同じ記事のどの媒体の版を残すか）のテスト。"""

from __future__ import annotations

from src.source_priority import SourcePriority, outlet_name


def test_outlet_is_taken_from_google_news_title_suffix() -> None:
    assert outlet_name("ミスドとポケモンのコラボ - GAME Watch", "ポケモン コラボ") == "game watch"


def test_outlet_falls_back_to_feed_name_for_direct_sources() -> None:
    assert outlet_name("ミスドとポケモンのコラボ", "GAME Watch") == "game watch"


def test_yahoo_repost_is_attributed_to_yahoo() -> None:
    title = "ミスドとポケモンのコラボ（GAME Watch） - Yahoo!ニュース"
    assert outlet_name(title, "ポケモン コラボ") == "yahoo!ニュース"


def test_preferred_outranks_unlisted_which_outranks_deprioritized() -> None:
    priority = SourcePriority(
        preferred=["公式サイト", "GAME Watch"], deprioritized=["Yahoo!ニュース"]
    )

    official = priority.rank("見出し - 公式サイト", "feed")
    game_watch = priority.rank("見出し - GAME Watch", "feed")
    unlisted = priority.rank("見出し - どこかの媒体", "feed")
    yahoo = priority.rank("見出し（GAME Watch） - Yahoo!ニュース", "feed")

    assert official < game_watch < unlisted < yahoo


def test_names_are_matched_ignoring_width_and_case() -> None:
    priority = SourcePriority(preferred=["GAME Watch"])

    assert priority.rank("見出し - ＧＡＭＥ watch", "feed") == 0


def test_no_configuration_ranks_everything_equally() -> None:
    priority = SourcePriority()

    assert priority.rank("見出し - A", "feed") == priority.rank("見出し - B", "feed")


def test_shipped_config_loads_source_priority() -> None:
    """同梱の targets.yaml から優先順位が読める（転載ポータルは後回しになっている）。"""
    from src.config import load_config

    config = load_config("config/targets.yaml")
    priority = SourcePriority(
        config.source_priority.preferred, config.source_priority.deprioritized
    )

    assert priority.rank("見出し - GAME Watch", "feed") < priority.rank(
        "見出し（GAME Watch） - Yahoo!ニュース", "feed"
    )
