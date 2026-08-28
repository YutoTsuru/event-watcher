"""フィード単位の必須キーワードによる絞り込み。

収集元によって「どこまで絞り込み済みか」が違うため、その差をここで吸収する。

- 検索型フィード（Google ニュースの検索 RSS）は、クエリ自体が
  「ポケモン かつ コラボ」の絞り込みになっているので、追加の条件は要らない。
- 一般ニュースのフィード（GAME Watch など）は全ジャンルの記事が流れてくるので、
  「コラボ」等のキーワードを必須にしないとポケモン関連ニュース全部が通ってしまう。

`KeywordFilter` はキーワードのいずれかに一致すれば通す（OR）ため、
「ポケモン かつ コラボ」のような AND 判定ができない。そこで判定そのものは
内側のフィルタに任せ、このクラスはその前段として AND 条件だけを足す。
"""

from __future__ import annotations

import logging
from typing import Mapping

from src.filters.base import RelevanceFilter
from src.models import Article

logger = logging.getLogger(__name__)


class FeedRequirementFilter(RelevanceFilter):
    """フィード名ごとの必須キーワードを、内側のフィルタの前段として適用する。"""

    def __init__(
        self,
        inner: RelevanceFilter,
        requirements: Mapping[str, list[str]],
    ) -> None:
        self._inner = inner
        # (フィード名 -> 小文字化済みキーワード群)。空のキーワード群は条件なしとして扱う
        self._requirements: dict[str, list[str]] = {
            feed_name: [keyword.lower() for keyword in keywords if keyword.strip()]
            for feed_name, keywords in requirements.items()
            if any(keyword.strip() for keyword in keywords)
        }

    def match(self, article: Article) -> list[str]:
        required = self._requirements.get(article.source)
        if required:
            text = article.searchable_text.lower()
            if not any(keyword in text for keyword in required):
                logger.debug(
                    "フィードの必須キーワードに一致しないため除外します feed=%s title=%s",
                    article.source,
                    article.title,
                )
                return []
        return self._inner.match(article)
