"""記事の見出しから「配信元の付け足し」を扱うユーティリティ。

同じ記事でも、経路によって見出しの末尾に配信元の表記が付く。

- Google ニュース経由   : 「タイトル - 媒体名」
- カテゴリ付きの媒体    : 「タイトル｜カテゴリ - 媒体名」
- Yahoo!ニュースの転載  : 「タイトル（元の媒体名） - Yahoo!ニュース」
- ライブドアニュース    : 「タイトル (2026年11月4日掲載) - ライブドアニュース」

名寄せキー（models.make_title_key）・同じ出来事の判定（event_grouping）・
媒体の優先順位（source_priority）は、どれもこの付け足しを同じルールで
扱う必要があるので、ここにまとめる。
"""

from __future__ import annotations

import re
import unicodedata

# 見出し末尾の配信元表記（「 - 朝日新聞」「｜ファミ通」など）
_SOURCE_SUFFIX_PATTERN = re.compile(r"\s*[-–—|｜]\s*[^-–—|｜]{2,25}\s*$")
# 転載で末尾に付く括弧書き。「（第2弾）」のように見出しの一部である括弧まで
# 剥がさないよう、確認できている転載の形だけに限る。
_YAHOO_SUFFIX_PATTERN = re.compile(r"\s*[(（][^()（）]{1,25}[)）]\s*-\s*Yahoo!ニュース\s*$")
_POSTED_DATE_PATTERN = re.compile(r"\s*[(（]\d{4}年\d{1,2}月\d{1,2}日掲載[)）]")
# 末尾の配信元表記を剥がす最大回数（「…｜カテゴリ - 媒体名」のように重なることがある）
_MAX_SUFFIX_STRIPS = 2

# Google ニュース経由の見出し末尾「 - 媒体名」（媒体名の取り出し用）
_OUTLET_PATTERN = re.compile(r"\s-\s([^-]{2,30})$")


def strip_source_suffixes(title: str) -> str:
    """見出しから配信元の付け足しを剥がし、全角・半角を揃えた本文部分を返す。

    大文字・小文字はそのまま（呼び出し側の用途に任せる）。
    """
    # 全角・半角の揺れ（「！」と「!」など）を先に揃える
    text = unicodedata.normalize("NFKC", title).strip()
    text = _YAHOO_SUFFIX_PATTERN.sub("", text)
    text = _POSTED_DATE_PATTERN.sub("", text)
    for _ in range(_MAX_SUFFIX_STRIPS):
        stripped = _SOURCE_SUFFIX_PATTERN.sub("", text)
        if stripped == text:
            break
        text = stripped
    return text


def outlet_name(title: str, source: str) -> str:
    """記事の配信元媒体名（全角・半角を揃えて小文字化したもの）。

    Google ニュース経由なら見出し末尾の「 - 媒体名」、直接ソースのフィードなら
    フィード名（config の feeds[].name）をそのまま媒体名とみなす。
    Yahoo!ニュースの転載は「Yahoo!ニュース」が媒体名になる（元の媒体ではない）。
    """
    match = _OUTLET_PATTERN.search(title.strip())
    return normalize_name(match.group(1) if match else source)


def normalize_name(name: str) -> str:
    """媒体名・語の比較用の正規化（全角・半角を揃え、小文字化）。"""
    return unicodedata.normalize("NFKC", name).strip().lower()
