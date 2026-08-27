"""Collector の共通インターフェース。

Collector の責務は「情報を取得して Article のリストを返す」ことだけ。
キーワード判定・重複判定・保存・通知は一切行わない。
将来 API 収集やスクレイピングを足す場合も、この基底クラスを実装する。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Article


class Collector(ABC):
    """情報収集元の抽象基底クラス。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """収集元の識別名（Event.source に入る）。"""

    @abstractmethod
    def collect(self) -> list[Article]:
        """記事を取得する。

        取得に失敗した場合は例外を送出せず空リストを返し、
        呼び出し側の処理継続を妨げないこと（失敗理由はログに残す）。
        """
