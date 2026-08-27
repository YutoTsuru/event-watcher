"""キーワードによる対象判定。"""

from __future__ import annotations

import logging
from typing import Iterable

from src.config import Target
from src.filters.base import RelevanceFilter
from src.models import Article

logger = logging.getLogger(__name__)


class KeywordFilter(RelevanceFilter):
    """タイトル＋要約に対する部分一致（大文字小文字を無視）で判定する。

    MVP のため形態素解析は行わない。日本語は部分一致で十分機能する一方、
    英単語は誤検知しうる（例: Pokemon が無関係な文脈に含まれる）ため、
    精度向上は将来の LLM フィルタに委ねる。
    """

    def __init__(self, targets: Iterable[Target]) -> None:
        # (対象名, 小文字化済みキーワード群) に前処理しておく
        self._targets: list[tuple[str, list[str]]] = [
            (target.name, [keyword.lower() for keyword in target.keywords if keyword.strip()])
            for target in targets
        ]

    def match(self, article: Article) -> list[str]:
        text = article.searchable_text.lower()
        matched = [
            name
            for name, keywords in self._targets
            if any(keyword in text for keyword in keywords)
        ]
        if matched:
            logger.debug("一致 targets=%s title=%s", matched, article.title)
        return matched
