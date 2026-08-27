# -*- coding: utf-8 -*-
"""テスト共通のフィクスチャ。

各テストファイルから使えるよう、代表的な Target / Article / Repository の
組み立てをここにまとめる。実ネットワークアクセスは一切行わない。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.config import Target
from src.models import Article
from src.repositories.sqlite import SQLiteEventRepository


@pytest.fixture
def pokemon_target() -> Target:
    """日本語キーワードを含む監視対象。"""
    return Target(name="ポケモン", keywords=["ポケモン", "Pokemon", "pikachu"])


@pytest.fixture
def zelda_target() -> Target:
    return Target(name="ゼルダ", keywords=["ゼルダ", "Zelda"])


@pytest.fixture
def targets(pokemon_target: Target, zelda_target: Target) -> list[Target]:
    return [pokemon_target, zelda_target]


def make_article(
    title: str = "サンプル記事タイトル",
    url: str = "https://example.com/articles/1",
    source: str = "テストフィード",
    summary: str = "",
    published_at: Optional[datetime] = None,
) -> Article:
    """テスト用に Article を組み立てるヘルパー関数（フィクスチャではなく直接呼べる）。"""
    return Article(
        title=title,
        url=url,
        source=source,
        published_at=published_at,
        summary=summary,
    )


@pytest.fixture
def article_factory():
    """Article をカスタムパラメータで組み立てるファクトリを返す。"""
    return make_article


@pytest.fixture
def sample_article() -> Article:
    return make_article(
        title="新作ポケモンゲームが発表されました",
        url="https://example.com/news/pokemon-1",
        source="テストフィード",
        summary="任天堂が新作を発表。",
        published_at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc),
    )


@pytest.fixture
def repository(tmp_path: Path) -> SQLiteEventRepository:
    """tmp_path 配下に作る使い捨ての SQLite リポジトリ（data/ には一切書かない）。"""
    db_path = tmp_path / "events_test.db"
    repo = SQLiteEventRepository(db_path)
    repo.initialize()
    return repo
