"""同じ記事が複数の媒体から届いたとき、どの媒体の版を残すかの順位付け。

見やすさ・情報量は媒体ごとの好みの問題で、見出しだけからは測れない
（Google ニュースの RSS は本文も要約も持たない）。そこで設定ファイルで
媒体の優先順位を明示してもらい、それに従って残す版を選ぶ。
"""

from __future__ import annotations

from typing import Sequence

from src.models import Event
from src.titles import normalize_name, outlet_name

__all__ = ["SourcePriority", "outlet_name"]


class SourcePriority:
    """媒体の優先順位。rank が小さいほど優先して残す。

    preferred      : 上から順に優先する媒体
    deprioritized  : 転載サイトなど、他に版があればそちらを残したい媒体
    どちらにも無い媒体は、その中間として扱う。
    """

    def __init__(
        self, preferred: Sequence[str] = (), deprioritized: Sequence[str] = ()
    ) -> None:
        self._preferred = {normalize_name(name): i for i, name in enumerate(preferred)}
        self._deprioritized = {normalize_name(name): i for i, name in enumerate(deprioritized)}

    def rank(self, title: str, source: str) -> int:
        name = outlet_name(title, source)
        if name in self._preferred:
            return self._preferred[name]
        unlisted = len(self._preferred)
        if name in self._deprioritized:
            return unlisted + 1 + self._deprioritized[name]
        return unlisted

    def rank_event(self, event: Event) -> int:
        return self.rank(event.title, event.source)
