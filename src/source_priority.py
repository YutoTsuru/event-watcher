"""同じ記事が複数の媒体から届いたとき、どの媒体の版を残すかの順位付け。

見やすさ・情報量は媒体ごとの好みの問題で、見出しだけからは測れない
（Google ニュースの RSS は本文も要約も持たない）。そこで設定ファイルで
媒体の優先順位を明示してもらい、それに従って残す版を選ぶ。
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from src.models import Event

# Google ニュース経由の見出し末尾「 - 媒体名」
_OUTLET_SUFFIX_PATTERN = re.compile(r"\s-\s([^-]{2,30})$")


def outlet_name(title: str, source: str) -> str:
    """記事の配信元媒体名。

    Google ニュース経由なら見出し末尾の「 - 媒体名」、直接ソースのフィードなら
    フィード名（config の feeds[].name）をそのまま媒体名とみなす。
    """
    match = _OUTLET_SUFFIX_PATTERN.search(title.strip())
    return _normalize(match.group(1) if match else source)


def _normalize(name: str) -> str:
    return unicodedata.normalize("NFKC", name).strip().lower()


class SourcePriority:
    """媒体の優先順位。rank が小さいほど優先して残す。

    preferred      : 上から順に優先する媒体
    deprioritized  : 転載サイトなど、他に版があればそちらを残したい媒体
    どちらにも無い媒体は、その中間として扱う。
    """

    def __init__(
        self, preferred: Sequence[str] = (), deprioritized: Sequence[str] = ()
    ) -> None:
        self._preferred = {_normalize(name): i for i, name in enumerate(preferred)}
        self._deprioritized = {_normalize(name): i for i, name in enumerate(deprioritized)}

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
