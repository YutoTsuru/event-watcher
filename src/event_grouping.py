"""「同じ出来事（同じコラボ）」を報じた記事の判定。

同じコラボでも、媒体ごとに見出しはまったく違う
（「モスバーガー×ハイキュー!!「2027モス福袋」事前予約は11月25日から！」と
「モスの「ハイキュー!!」福袋が登場 5000円で食事券5000円分」など）。
見出し全体の類似度では拾えないが、コラボ相手や商品の固有名詞
（モスバーガー / 福袋）はほぼ必ず共通している。そこで、

1. 見出しから「特徴語」（カタカナ・漢字・英字の連なり）を取り出し、
2. どの記事にも出てくる一般語（コラボ・グッズ・発売…）と話題そのもの
   （ポケモン・ハイキュー…）を取り除き、
3. 次のどちらかなら同じ出来事とみなす。
   - コラボ相手（見出しの「×」の隣にある語）が一致する
   - 特徴語を 2 つ以上共有する

LLM や形態素解析を使わない、文字種の境目だけを使った軽量な判定なので、
誤判定（別のコラボをまとめてしまう）を減らすには一般語・stopwords の追加で調整する。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Optional

# 同じ出来事とみなすのに必要な共有特徴語の数（コラボ相手が一致しない場合）
MIN_SHARED_TOKENS = 2
MIN_TOKEN_LENGTH = 2

# どの記事の見出しにも出てくる一般語。特徴語から取り除く（部分一致で削る）。
# 「コラボラーメン」→「ラーメン」、「ポケモンキャンペーン」→（空）のように、
# 一般語と固有名詞がつながった語からも一般語の部分だけを削る。
GENERIC_WORDS = """
コラボレーション コラボ グッズ キャンペーン ポップアップ ストア ショップ カフェ イベント
アニメ 限定 発売 登場 開催 予約 販売 開始 決定 新作 公式 情報 全国 店舗 期間 数量 今年
人気 オンライン アイテム デザイン ラインアップ ラインナップ シリーズ コレクション セット
記念 周年 発表 受付 抽選 本日 店頭 先行 通販 新商品 商品 プレゼント 特典 メニュー ドリンク
フード オリジナル イラスト キャラクター 種類 実施 スタート 画像 再販 受注 予定 お知らせ
来店 お客様 オフィシャルサイト 大集合 集合 ファン 話題 注目 必見 最新 今回 初登場 新発売
日常使 大人 実用的 描 第弾 弾 全種 今日 公開 開幕 オープン 中身 詳細 まとめ 可愛
年 月 日 時 円 分
pop up store shop cafe collab collaboration the and ver new official limited goods event
campaign online gallery
""".split()

# 見出し末尾の配信元表記（「 - 媒体名」「｜カテゴリ」）。何重にも付くことがある
_SUFFIX_PATTERN = re.compile(r"\s*[-–—|｜]\s*[^-–—|｜]{2,40}\s*$")
_YAHOO_SUFFIX_PATTERN = re.compile(r"\s*[(（][^()（）]{1,25}[)）]\s*-\s*Yahoo!ニュース\s*$")
_MAX_SUFFIX_STRIPS = 3

# 特徴語の候補: カタカナの連なり / 漢字の連なり / 英字で始まる英数字の連なり
# （× は Latin-1 の範囲にあるので英字から外す）
_TOKEN = r"[ァ-ヴー]{2,}|[一-龥々]{2,}|[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ0-9]+"
_TOKEN_PATTERN = re.compile(_TOKEN)
_TOKEN_AT_END = re.compile(rf"(?:{_TOKEN})$")
_TOKEN_AT_START = re.compile(rf"(?:{_TOKEN})")

# コラボの「×」。英字の x は前後に空白があるときだけ（「New Era x Pokémon」）
_CROSS_PATTERN = re.compile(r"\s*[×✕]\s*|\s+x\s+")
# 「×」と相手の名前の間に挟まる括弧・記号（「『ハイキュー!!』×「モスバーガー」」など）
_CROSS_PADDING = " 　「」『』【】()（）\"'“”‘’!！?？・、。&＆"


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


@dataclass(frozen=True)
class EventSignature:
    """1 記事ぶんの判定材料。"""

    # 見出しの特徴語
    tokens: frozenset[str]
    # そのうちコラボ相手とみられる語
    partners: frozenset[str]


class EventGrouper:
    """見出しの特徴語で「同じ出来事か」を判定する。

    stopwords には話題そのものを表す語（監視対象のキーワード、キャラクター名など）
    を渡す。これらは同じ話題のどの記事にも出てくるので、特徴語にしない。
    """

    def __init__(self, stopwords: Iterable[str] = ()) -> None:
        words = {_normalize(w) for w in [*GENERIC_WORDS, *stopwords] if w.strip()}
        # 長い語から削らないと、「コラボレーション」が「コラボ」で先に削られて
        # 「レーション」が残ってしまう
        self._stopwords = sorted(words, key=len, reverse=True)
        self._stopword_set = set(words)

    def signature(self, title: str) -> EventSignature:
        text = _strip_outlet(title)
        return EventSignature(tokens=self._tokens(text), partners=self._partners(text))

    def is_same_event(self, a: EventSignature, b: EventSignature) -> bool:
        """同じ出来事かどうか。次のどちらかを満たせば同じとみなす。

        - コラボ相手が一致する（片方の相手が、もう片方の特徴語に含まれる）
        - 特徴語を MIN_SHARED_TOKENS 個以上共有する

        「モス」と「モスバーガー」のように片方がもう片方の先頭部分なら同じ語とみなす。
        """
        if _shares_any(a.partners, b.tokens) or _shares_any(b.partners, a.tokens):
            return True
        return _count_shared(a.tokens, b.tokens) >= MIN_SHARED_TOKENS

    def _tokens(self, text: str) -> frozenset[str]:
        found: set[str] = set()
        for raw in _TOKEN_PATTERN.findall(text):
            found.update(self._clean(raw))
        return frozenset(found)

    def _partners(self, text: str) -> frozenset[str]:
        """「×」のすぐ隣にある特徴語。

        「×」の隣には話題そのもの（stopwords で消える）かコラボ相手が来るので、
        消えずに残った語をコラボ相手とみなす。
        """
        found: set[str] = set()
        for cross in _CROSS_PATTERN.finditer(text):
            left = text[: cross.start()].rstrip(_CROSS_PADDING)
            right = text[cross.end():].lstrip(_CROSS_PADDING)
            before = _TOKEN_AT_END.search(left)
            after = _TOKEN_AT_START.match(right)
            if before:
                parts = self._clean(before.group())
                if parts:
                    found.add(parts[-1])  # 「×」に近い側
            if after:
                parts = self._clean(after.group())
                if parts:
                    found.add(parts[0])
        return frozenset(found)

    def _clean(self, raw: str) -> list[str]:
        """1 語から一般語・stopwords を削り、残った断片を出現順に返す。"""
        word = raw.lower()
        for stopword in self._stopwords:
            word = word.replace(stopword, " ")
        return [
            part
            for part in word.split()
            if len(part) >= MIN_TOKEN_LENGTH and part not in self._stopword_set
        ]


def _strip_outlet(title: str) -> str:
    # Yahoo! の表記は大文字小文字を区別して照合するので、小文字化はその後
    text = unicodedata.normalize("NFKC", title).strip()
    text = _YAHOO_SUFFIX_PATTERN.sub("", text).lower()
    for _ in range(_MAX_SUFFIX_STRIPS):
        stripped = _SUFFIX_PATTERN.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text


def _same_word(a: str, b: str) -> bool:
    return a == b or a.startswith(b) or b.startswith(a)


def _find_match(token: str, candidates: Iterable[str], used: set[str]) -> Optional[str]:
    for other in candidates:
        if other not in used and _same_word(token, other):
            return other
    return None


def _shares_any(words: frozenset[str], tokens: frozenset[str]) -> bool:
    return any(_find_match(word, tokens, set()) for word in words)


def _count_shared(a: frozenset[str], b: frozenset[str]) -> int:
    """共有する特徴語の数。相手側の語は 1 回ずつしか使わない（二重に数えない）。"""
    used: set[str] = set()
    for token in a:
        match = _find_match(token, b, used)
        if match is not None:
            used.add(match)
    return len(used)
