"""Filter の共通インターフェース。

Filter の責務は「この記事が対象かどうか判定する」ことだけ。
戻り値は「一致した監視対象名のリスト」で、空なら対象外とみなす。

将来 LLM による関連性判定を追加する場合も、この基底クラスを実装した
LLMRelevanceFilter を作り、main.py 側で差し替えるだけで済む構造にしてある。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import Article


class RelevanceFilter(ABC):
    """対象判定の抽象基底クラス。"""

    @abstractmethod
    def match(self, article: Article) -> list[str]:
        """一致した監視対象名のリストを返す（対象外なら空リスト）。"""
